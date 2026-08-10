"""
Cross-platform ansible-lint runner.

ansible-lint depends on Unix-only modules (e.g. ``grp``) and does not run
under native Windows Python. On Windows we delegate to WSL or Docker when
configured / available.

Env:
  ANSIBLE_LINT_MODE   auto | native | wsl | docker | skip  (default: auto)
  ANSIBLE_LINT_CMD    override executable (e.g. custom wrapper script)
  ANSIBLE_LINT_DOCKER_IMAGE  (default: pipelinecomponents/ansible-lint:latest)
  ANSIBLE_LINT_WSL_DISTRO    optional WSL distro name (e.g. Ubuntu)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]*m")

# A real finding in `ansible-lint -p` (parseable) format:
#   path/to/playbook.yml:6:5: syntax-check[unknown-module]: message
#   playbook.yml:12: name[missing]: All tasks should be named
# Everything else on stdout/stderr — Python UserWarnings about cache
# directories, ansible-compat WARNINGs, summary lines — is runner noise.
# Counting that noise as violations both inflated the reported number
# ("14 violation(s)" for one real issue) and fed the repair loop
# unfixable "issues" that burned its iterations.
_FINDING_RE = re.compile(r"^\S+?\.ya?ml:\d+(?::\d+)?:\s*\S+")


_DEFAULT_DOCKER_IMAGE = "pipelinecomponents/ansible-lint:latest"
_LINT_TIMEOUT_S = 180


@dataclass
class AnsibleLintOutcome:
    status: str
    violations: list[str] = field(default_factory=list)
    backend: str = "none"
    message: str | None = None


def _env_mode() -> str:
    return (os.getenv("ANSIBLE_LINT_MODE") or "auto").strip().lower()


def _is_windows() -> bool:
    return sys.platform == "win32"


def _windows_path_to_wsl(path: str) -> str:
    abs_path = os.path.abspath(path)
    if len(abs_path) >= 2 and abs_path[1] == ":":
        drive = abs_path[0].lower()
        rest = abs_path[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return abs_path.replace("\\", "/")


def _find_native_lint() -> str | None:
    override = (os.getenv("ANSIBLE_LINT_CMD") or "").strip()
    if override:
        return override

    lint_bin = shutil.which("ansible-lint")
    if lint_bin:
        return lint_bin

    py_scripts = os.path.join(os.path.dirname(sys.executable), "Scripts")
    candidates = [
        os.path.join(py_scripts, "ansible-lint"),
        os.path.join(py_scripts, "ansible-lint.exe"),
        os.path.join(
            os.path.expanduser("~"),
            "AppData",
            "Roaming",
            "Python",
            f"Python{sys.version_info.major}{sys.version_info.minor}",
            "Scripts",
            "ansible-lint.exe",
        ),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _decode_wsl_output(raw: bytes | None) -> str:
    """WSL CLI on Windows often emits UTF-16-LE (NUL between chars)."""
    if not raw:
        return ""
    if b"\x00" in raw[: min(80, len(raw))]:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _wsl_distro_available() -> str | None:
    """Return default WSL distro name if any distribution is installed."""
    configured = (os.getenv("ANSIBLE_LINT_WSL_DISTRO") or "").strip()
    if configured:
        return configured
    try:
        proc = subprocess.run(
            ["wsl", "-l", "-q"],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    text = _decode_wsl_output(proc.stdout)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    # `wsl -l -q` may prefix distro names with * for default.
    return lines[0].lstrip("*").strip()


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).strip()


def _collect_output(proc: subprocess.CompletedProcess) -> list[str]:
    lines: list[str] = []
    for block in (proc.stdout or "", proc.stderr or ""):
        for line in block.splitlines():
            line = _strip_ansi(line)
            if line:
                lines.append(line)
    return lines


def _interpret_result(
    proc: subprocess.CompletedProcess,
    backend: str,
) -> AnsibleLintOutcome:
    lines = _collect_output(proc)
    if proc.returncode == 0:
        return AnsibleLintOutcome(status="passed", backend=backend)

    combined = "\n".join(lines).lower()
    if "no module named 'grp'" in combined:
        return AnsibleLintOutcome(
            status="unsupported_platform",
            violations=lines[:10],
            backend=backend,
            message="ansible-lint unavailable on native Windows (missing grp module)",
        )
    if "is not recognized" in combined or "command not found" in combined:
        return AnsibleLintOutcome(
            status="not_installed",
            violations=lines[:10],
            backend=backend,
            message="ansible-lint is not installed in the selected runtime",
        )

    findings = [ln for ln in lines if _FINDING_RE.match(ln)]
    if findings:
        return AnsibleLintOutcome(
            status="violations",
            violations=findings[:50],
            backend=backend,
        )

    # Non-zero exit with no parseable findings: the tool itself failed
    # (traceback, config error). Surface the raw output for diagnosis
    # rather than calling it a playbook violation.
    return AnsibleLintOutcome(
        status="failed_to_run",
        violations=lines[:50],
        backend=backend,
        message="ansible-lint exited non-zero without reporting findings",
    )


def _run_subprocess(
    cmd: list[str], cwd: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_LINT_TIMEOUT_S,
        cwd=cwd,
    )


def _run_native(filepath: str) -> AnsibleLintOutcome | None:
    lint_bin = _find_native_lint()
    if not lint_bin:
        return None
    try:
        # cwd is the playbook's own directory: ansible-lint derives its
        # project dir (and cache dir) from the working directory, and in the
        # container the app cwd (/app) is read-only — every run warned
        # "Project directory /app/.ansible cannot be used for caching".
        # The playbook dir is a writable tmpfs.
        proc = _run_subprocess(
            [lint_bin, "-p", filepath],
            cwd=os.path.dirname(os.path.abspath(filepath)) or None,
        )
    except subprocess.TimeoutExpired:
        return AnsibleLintOutcome(status="timeout", backend="native")
    except Exception as exc:
        return AnsibleLintOutcome(
            status="failed_to_run",
            backend="native",
            message=str(exc),
        )
    outcome = _interpret_result(proc, "native")
    if outcome.status == "unsupported_platform" and _is_windows():
        return outcome
    return outcome


def _run_wsl(filepath: str, distro: str | None) -> AnsibleLintOutcome:
    if not distro:
        return AnsibleLintOutcome(
            status="wsl_not_configured",
            backend="wsl",
            message=(
                "WSL has no Linux distribution. Install Ubuntu, then run "
                "`pip install ansible-lint` inside WSL. "
                "Quick setup: `wsl --install -d Ubuntu`"
            ),
        )

    wsl_path = _windows_path_to_wsl(filepath)
    cmd = ["wsl"]
    if distro:
        cmd.extend(["-d", distro])
    cmd.extend(["-e", "ansible-lint", "-p", wsl_path])

    try:
        proc = _run_subprocess(cmd)
    except subprocess.TimeoutExpired:
        return AnsibleLintOutcome(status="timeout", backend="wsl")
    except Exception as exc:
        return AnsibleLintOutcome(
            status="failed_to_run",
            backend="wsl",
            message=str(exc),
        )
    return _interpret_result(proc, "wsl")


def _run_docker(filepath: str) -> AnsibleLintOutcome:
    docker = shutil.which("docker")
    if not docker:
        return AnsibleLintOutcome(
            status="docker_not_available",
            backend="docker",
            message="Docker is not installed or not on PATH",
        )

    abs_path = os.path.abspath(filepath)
    parent = os.path.dirname(abs_path)
    filename = os.path.basename(abs_path)
    image = (os.getenv("ANSIBLE_LINT_DOCKER_IMAGE") or _DEFAULT_DOCKER_IMAGE).strip()

    # Mount the playbook directory; lint the file inside the container.
    if _is_windows():
        # Docker Desktop on Windows accepts Windows paths in -v.
        mount = parent
    else:
        mount = parent

    cmd = [
        docker,
        "run",
        "--rm",
        "-v",
        f"{mount}:/work",
        image,
        "ansible-lint",
        "-p",
        f"/work/{filename}",
    ]

    try:
        proc = _run_subprocess(cmd)
    except subprocess.TimeoutExpired:
        return AnsibleLintOutcome(status="timeout", backend="docker")
    except Exception as exc:
        return AnsibleLintOutcome(
            status="failed_to_run",
            backend="docker",
            message=str(exc),
        )
    return _interpret_result(proc, "docker")


_WARM_PLAYBOOK = (
    "---\n"
    "- name: Warm up\n"
    "  hosts: localhost\n"
    "  gather_facts: false\n"
    "  tasks:\n"
    "    - name: Do nothing\n"
    "      ansible.builtin.debug:\n"
    "        msg: warm\n"
)


def warm_up() -> str:
    """
    Run ansible-lint once on a throwaway playbook so the first real gate does
    not absorb the backend's start-up cost.

    On Windows/WSL that cost is the VM spin-up plus ansible-lint's import
    graph: measured at ~25 s cold against ~3.5 s once warm. Returns the
    resulting status for logging; never raises.
    """
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(_WARM_PLAYBOOK)
            tmp_path = tmp.name
        return run_ansible_lint(tmp_path).status
    except Exception as exc:
        return f"warm_up_failed: {exc}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def run_ansible_lint(filepath: str) -> AnsibleLintOutcome:
    """
    Run ansible-lint on ``filepath`` using the best available backend.
    """
    mode = _env_mode()
    if mode == "skip":
        return AnsibleLintOutcome(status="skipped", backend="skip")

    if mode == "docker":
        return _run_docker(filepath)

    if mode == "wsl":
        return _run_wsl(filepath, _wsl_distro_available())

    if mode == "native":
        outcome = _run_native(filepath)
        if outcome is None:
            return AnsibleLintOutcome(
                status="skipped",
                backend="native",
                message="ansible-lint not installed",
            )
        return outcome

    # auto
    if _is_windows():
        distro = _wsl_distro_available()
        if distro:
            wsl_out = _run_wsl(filepath, distro)
            if wsl_out.status != "wsl_not_configured":
                if wsl_out.status in {"passed", "violations"}:
                    return wsl_out
                if wsl_out.status == "not_installed":
                    wsl_out.message = (
                        "ansible-lint not found inside WSL. "
                        "Run: `wsl -d " + distro + " -e bash -lc "
                        "'pip install ansible-lint'`"
                    )
                    return wsl_out
                if wsl_out.status not in {"failed_to_run", "timeout"}:
                    return wsl_out

        docker_out = _run_docker(filepath)
        if docker_out.status not in {"docker_not_available", "failed_to_run"}:
            return docker_out

        native_out = _run_native(filepath)
        if native_out and native_out.status == "unsupported_platform":
            return AnsibleLintOutcome(
                status="wsl_not_configured",
                backend="auto",
                message=(
                    "ansible-lint cannot run natively on Windows. "
                    "Install WSL Ubuntu (`wsl --install -d Ubuntu`), then "
                    "`pip install ansible-lint` inside WSL, or set "
                    "ANSIBLE_LINT_MODE=docker with Docker Desktop."
                ),
            )
        if native_out:
            return native_out
        return AnsibleLintOutcome(
            status="wsl_not_configured",
            backend="auto",
            message=(
                "ansible-lint not available on Windows. "
                "Set up WSL + ansible-lint or Docker (see README)."
            ),
        )

    outcome = _run_native(filepath)
    if outcome is None:
        return AnsibleLintOutcome(
            status="skipped",
            backend="native",
            message="ansible-lint not installed",
        )
    return outcome
