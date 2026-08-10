"""
=============================================================
  AI-Powered IaC — Playbook Validator
  Input  : output/*.yml  OR  a specific playbook path
  Output : validation report (console + reports/validation_report.json)
=============================================================
  Checks performed:
    [1] YAML syntax           → valid YAML (header comments stripped)
    [2] Playbook structure      → list of plays, name/tasks sanity
  [3] kubernetes.core.k8s   → kind/metadata/spec under `definition:`
  [4] Module name           → FQCN or short task key matching KB (multi-collection)
    [5] Required params       → required keys for detected module (aliases OK)
    [6] hosts                 → play has `hosts` when applicable
    [7] Placeholders          → YOUR-*, TODO, CHANGEME; bare var_* only
                                (quoted `{{ var_* }}` / Jinja is OK)
    [8] Hardcoded secrets     → sensitive keys must use Jinja, vault, or var_*
    [9] ansible-lint          → optional quality rules (if installed)

  Sources / inputs:
    - Module inventory: kb_store → knowledge base (parsed Ansible docs)
    - Multi-collection playbooks: any collection FQCN indexed in the KB
      (e.g. azure.azcollection.azure_rm_aks, kubernetes.core.k8s)
    - k8s layout rule: Ansible kubernetes.core k8s module docs
      (resource fields under `definition:`)
=============================================================
"""

import json
import os
import re
import sys
from datetime import datetime

import yaml
from kb_store import load_knowledge_base as load_kb_store

# Always run from project root
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

KNOWLEDGE_BASE  = "data/knowledge_base.json"
OUTPUT_DIR      = "output"
REPORT_FILE     = "reports/validation_report.json"

# Known kubernetes.core modules
K8S_CORE_MODULES = {
    "kubernetes.core.k8s",
    "kubernetes.core.k8s_info",
    "kubernetes.core.k8s_exec",
    "kubernetes.core.k8s_cp",
    "kubernetes.core.k8s_drain",
    "kubernetes.core.k8s_log",
    "kubernetes.core.k8s_scale",
    "kubernetes.core.k8s_rollback",
    "kubernetes.core.k8s_taint",
    "kubernetes.core.k8s_json_patch",
    "kubernetes.core.k8s_service",
    "kubernetes.core.k8s_cluster_info",
    "kubernetes.core.helm",
    "kubernetes.core.helm_info",
    "kubernetes.core.helm_repository",
    "kubernetes.core.helm_plugin",
    "kubernetes.core.helm_plugin_info",
    "kubernetes.core.helm_template",
}

# Regex patterns that indicate unfilled placeholders (see check_no_placeholders)
PLACEHOLDER_PATTERNS = [
    r"your[-_][\w\-]+",        # your-namespace, your_pod_name
    r"<[\w\s\-]+>",            # <namespace>, <pod-name>
    r"PLACEHOLDER",
    r"TODO",
    r"CHANGEME",
    r"example[-_]?\w*",        # example-namespace (but not in descriptions)
]
_VAR_PLACEHOLDER_RE = re.compile(r"\b(var_[a-zA-Z_][a-zA-Z0-9_]*)\b", re.IGNORECASE)
_JINJA_EXPR_RE = re.compile(r"\{\{[^{}]*\}\}")


def _var_match_inside_jinja(line: str, start: int, end: int) -> bool:
    """True if this span lies inside a `{{ ... }}` fragment (Ansible-templated value)."""
    for jm in _JINJA_EXPR_RE.finditer(line):
        if jm.start() <= start < end <= jm.end():
            return True
    return False


# ─────────────────────────────────────────────
#  LOAD HELPERS
# ─────────────────────────────────────────────

def load_knowledge_base():
    return load_kb_store(prefer_parsed=True)


def load_playbook_file(filepath: str) -> str:
    with open(filepath, encoding="utf-8") as f:
        return f.read()


def get_module_slug(module_name: str) -> str:
    """Convert 'kubernetes.core.k8s_exec' → 'k8s_exec_module'"""
    short = module_name.split(".")[-1]
    return short + "_module"


# ─────────────────────────────────────────────
#  VALIDATION CHECKS
# ─────────────────────────────────────────────

class ValidationResult:
    def __init__(self, filepath: str):
        self.filepath   = filepath
        self.filename   = os.path.basename(filepath)
        self.passed     = []
        self.warnings   = []
        self.errors     = []
        self.raw_yaml   = None
        self.parsed     = None   # parsed YAML object
        self.ansible_lint = {
            "status": "not_run",
            "violations": [],
        }

    @property
    def is_valid(self):
        return len(self.errors) == 0

    def ok(self, msg):
        self.passed.append(msg)
        print(f"    ✅ {msg}")

    def warn(self, msg):
        self.warnings.append(msg)
        print(f"    ⚠️  {msg}")

    def fail(self, msg):
        self.errors.append(msg)
        print(f"    ❌ {msg}")


def check_yaml_syntax(result: ValidationResult):
    """Check 1: Is the file valid YAML?"""
    try:
        # Strip header comments before parsing
        content = "\n".join(
            line for line in result.raw_yaml.splitlines()
            if not line.startswith("#")
        )
        result.parsed = yaml.safe_load(content)
        result.ok("YAML syntax is valid")
    except yaml.YAMLError as e:
        result.fail(f"YAML syntax error: {e}")


def check_playbook_structure(result: ValidationResult):
    """Check 2: Does it look like an Ansible playbook?"""
    if result.parsed is None:
        result.fail("Cannot check structure — YAML failed to parse")
        return

    if not isinstance(result.parsed, list):
        result.fail("Playbook must be a YAML list (starts with '-')")
        return

    play = result.parsed[0] if result.parsed else {}

    if not isinstance(play, dict):
        result.fail("First element must be a dict (a play)")
        return

    if "name" not in play:
        result.warn("Play has no 'name' field")
    else:
        result.ok(f"Play name: \"{play['name']}\"")

    if "tasks" not in play and not any(
        k in play for k in K8S_CORE_MODULES | {"kubernetes.core.k8s"}
    ):
        # Could be a single-task play without explicit tasks key
        result.warn("No 'tasks' key found — may be a single-task play")
    else:
        result.ok("Playbook structure is correct")


def _check_k8s_definition_wrapper(task: dict, result: ValidationResult) -> None:
    """
    kubernetes.core.k8s requires resource fields (kind, metadata, spec, data)
    to be inside a `definition:` block, not at the task root.
    """
    module_key = next(
        (
            k
            for k in task
            if "k8s" in k
            and "scale" not in k
            and "rollback" not in k
            and "info" not in k
            and "service" not in k
        ),
        None,
    )
    if not module_key:
        return
    module_params = task.get(module_key, {}) or {}
    if not isinstance(module_params, dict):
        return

    misplaced = [
        k
        for k in ("kind", "metadata", "spec", "data", "stringData")
        if k in module_params and "definition" not in module_params
    ]
    if misplaced:
        result.fail(
            f"Task using `{module_key}` has fields {misplaced} at the module root. "
            "These must be nested inside a `definition:` block. "
            "See: https://docs.ansible.com/ansible/latest/collections/kubernetes/core/k8s_module.html"
        )


def check_k8s_task_definitions(result: ValidationResult) -> None:
    """Run k8s manifest wrapping checks on every task."""
    if result.parsed is None or not isinstance(result.parsed, list):
        return
    for play in result.parsed:
        if not isinstance(play, dict):
            continue
        for task in play.get("tasks") or []:
            if isinstance(task, dict):
                _check_k8s_definition_wrapper(task, result)


# Playbook keys that match ``^\s*<short>\\s*:`` at the start of a line but are
# play-level directives, not task module invocations. Treating them as module
# short names causes false positives (e.g. ``gather_facts: no`` vs
# ``ansible.builtin.gather_facts``), which can beat real cloud modules in
# ``max(matches, key=len)`` because the builtin FQCN is longer.
_PLAY_LEVEL_SHORT_DENY = frozenset({
    "gather_facts",
    "gather_subset",
    "gather_timeout",
    "fact_path",
    "hosts",
    "connection",
    "become",
    "become_user",
    "become_method",
    "become_flags",
    "become_exe",
    "vars",
    "vars_files",
    "vars_prompt",
    "roles",
    "tasks",
    "handlers",
    "pre_tasks",
    "post_tasks",
    "strategy",
    "serial",
    "max_failures",
    "any_errors_fatal",
    "order",
    "environment",
    "module_defaults",
    "collections",
    "import_playbook",
    "tags",
    "force_handlers",
    "remote_user",
    "user",
    "port",
    "delegate_to",
    "delegate_facts",
    "run_once",
    "no_log",
    "check_mode",
    "diff",
})


def check_module_present(result: ValidationResult, kb_modules: dict):
    """Check 4: Is a known collection module used? Skips play-level YAML keys that look like short module names."""
    raw = result.raw_yaml or ""
    known_modules = {
        entry.get("module", "")
        for entry in (kb_modules or {}).values()
        if entry.get("module")
    }
    matches: list[str] = []
    for mod in known_modules:
        if not mod:
            continue
        if mod in raw:
            matches.append(mod)
            continue
        short = mod.split(".")[-1]
        if short in _PLAY_LEVEL_SHORT_DENY:
            continue
        if re.search(rf"(?m)^\s*{re.escape(short)}\s*:", raw):
            matches.append(mod)
    found = max(matches, key=len) if matches else None

    if found:
        result.ok(f"Module found: {found}")
        result._detected_module = found
    else:
        result.fail("No known collection module detected in playbook")
        result._detected_module = None


def _refine_required_params(entry: dict, required: list[str]) -> list[str]:
    """
    Reduce false positives caused by scraped nested-option "required" flags.
    Keep required params that appear in module examples when available.
    """
    if not required:
        return []

    examples = entry.get("examples", []) or []
    if not examples:
        return required

    all_params = entry.get("parameters", []) or []
    alias_map = {}
    for p in all_params:
        name = p.get("name")
        if not name:
            continue
        alias_map[name] = [name] + (p.get("aliases", []) or [])

    refined = []
    for param in required:
        names = alias_map.get(param, [param])
        present_in_examples = any(
            re.search(rf"(?m)^\s*{re.escape(n)}\s*:", ex)
            for n in names
            for ex in examples
        )
        if present_in_examples:
            refined.append(param)

    return refined or required


def check_required_params(result: ValidationResult, kb_modules: dict):
    """Check 5: Are all required parameters present?"""
    module_name = getattr(result, "_detected_module", None)
    if not module_name:
        result.warn("Skipping required params check — no module detected")
        return

    slug = get_module_slug(module_name)
    entry = kb_modules.get(slug)
    if not entry:
        for key, mod_entry in kb_modules.items():
            if key.endswith(f"::{slug}") or mod_entry.get("module") == module_name:
                entry = mod_entry
                break
    if not entry:
        result.warn(f"Module '{module_name}' not in knowledge data")
        return

    required = _refine_required_params(entry, entry.get("required_params", []))

    # field_manager is only needed with server_side_apply — skip it
    OPTIONAL_IN_PRACTICE = {"field_manager", "value"}
    required = [p for p in required if p not in OPTIONAL_IN_PRACTICE]

    if not required:
        result.ok("No required parameters for this module")
        return

    raw = result.raw_yaml or ""

    # Build alias map: param_name → [param_name, alias1, alias2, ...]
    all_params = entry.get("parameters", [])
    alias_map = {}
    for p in all_params:
        names = [p["name"]] + p.get("aliases", [])
        alias_map[p["name"]] = names

    missing = []
    for param in required:
        # Accept param name OR any of its aliases
        accepted_names = alias_map.get(param, [param])
        found = any(
            re.search(rf"\b{re.escape(n)}\s*:", raw)
            for n in accepted_names
        )
        if not found:
            missing.append(param)

    if missing:
        result.fail(f"Missing required params: {missing}")
    else:
        result.ok(f"All required params present: {required}")


def check_no_placeholders(result: ValidationResult):
    """Check 7: Unfilled template tokens — not `{{ var_* }}` (valid Ansible Jinja)."""
    raw = result.raw_yaml or ""
    found_placeholders: list[str] = []

    for pattern in PLACEHOLDER_PATTERNS:
        matches = re.findall(pattern, raw, re.IGNORECASE)
        for match in matches:
            for line in raw.splitlines():
                if match in line and not line.strip().startswith("#"):
                    found_placeholders.append(match)
                    break

    # Bare var_* (YAML string literal) is suspicious; inside `{{ var_* }}` is fine.
    for line in raw.splitlines():
        ls = line.strip()
        if not ls or ls.startswith("#"):
            continue
        for vm in _VAR_PLACEHOLDER_RE.finditer(line):
            tok = vm.group(1)
            if _var_match_inside_jinja(line, vm.start(1), vm.end(1)):
                continue
            found_placeholders.append(tok)

    found_placeholders = list(dict.fromkeys(found_placeholders))

    if found_placeholders:
        result.warn(f"Possible placeholder values found: {found_placeholders}")
    else:
        result.ok("No placeholder values detected")


def check_hardcoded_secrets(result: ValidationResult):
    """Flag likely literal secrets copied from examples (not vault/lookup/var_)."""
    raw = result.raw_yaml or ""
    secret_key_re = re.compile(
        r"(?i)\b(admin_password|password|client_secret|api_key|secret_key|access_token|"
        r"shared_secret|connection_password)\s*:",
    )
    bad: list[str] = []
    for line in raw.splitlines():
        ls = line.strip()
        if not ls or ls.startswith("#"):
            continue
        if not secret_key_re.search(ls):
            continue
        if "{{" in ls or "lookup(" in ls or "vault" in ls.lower():
            continue
        if re.search(
            r":\s*(?:[\"']?\{\{\s*var_[a-z0-9_]+\s*\}\}[\"']?|var_[a-z0-9_]+)\s*$",
            ls,
            re.IGNORECASE,
        ):
            continue
        parts = ls.split(":", 1)
        if len(parts) < 2:
            continue
        val = parts[1].strip().strip("'\"")
        if len(val) > 5 and not val.startswith("{{"):
            bad.append(ls[:160])
            if len(bad) >= 4:
                break
    if bad:
        result.fail(f"Possible hardcoded secret/password in playbook: {bad}")
    else:
        result.ok("No obvious hardcoded secrets in sensitive fields")


# Task-level keywords that are NOT module invocations.
_TASK_DIRECTIVES = frozenset({
    "name", "register", "tags", "when", "become", "become_user", "become_method",
    "no_log", "loop", "with_items", "with_dict", "notify", "vars", "changed_when",
    "failed_when", "ignore_errors", "delegate_to", "run_once", "environment",
    "until", "retries", "delay", "check_mode", "diff", "args", "loop_control",
    "listen", "block", "rescue", "always", "any_errors_fatal",
})

_SECRET_PARAM_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api_key|access_key|secret_key|"
    r"private_key|client_secret|shared_secret|credential)"
)

_BUILTIN_PRIVILEGED_RE = re.compile(
    r"ansible\.builtin\.(package|apt|yum|dnf|service|systemd|user|group|"
    r"mount|sysctl|seboolean|firewalld|hostname)\b"
)


def _dict_has_secret_key(obj) -> bool:
    """Recursively check a module's params for a sensitive-looking key."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_PARAM_RE.search(k):
                return True
            if _dict_has_secret_key(v):
                return True
    elif isinstance(obj, list):
        return any(_dict_has_secret_key(i) for i in obj)
    return False


def _module_key_of_task(task: dict) -> str | None:
    """Return the task's module key (the one directive-free key)."""
    for k in task:
        if k not in _TASK_DIRECTIVES:
            return k
    return None


def check_production_standards(result: ValidationResult):
    """
    Production-quality lint (WARNINGS only — never flips validity).
    Surfaces hygiene gaps: secrets without no_log, privileged tasks
    without become, and total absence of task tags.
    """
    if result.parsed is None or not isinstance(result.parsed, list):
        return

    secret_no_log_hits: list[str] = []
    privileged_no_become: list[str] = []
    total_tasks = 0
    tagged_tasks = 0

    for play in result.parsed:
        if not isinstance(play, dict):
            continue
        play_become = bool(play.get("become"))
        for task in play.get("tasks") or []:
            if not isinstance(task, dict) or "block" in task:
                continue
            total_tasks += 1
            if task.get("tags"):
                tagged_tasks += 1

            mod_key = _module_key_of_task(task)
            params = task.get(mod_key) if mod_key else None

            if _dict_has_secret_key(params) and not task.get("no_log"):
                secret_no_log_hits.append(task.get("name") or mod_key or "task")

            if mod_key and _BUILTIN_PRIVILEGED_RE.search(mod_key):
                if not (play_become or task.get("become")):
                    privileged_no_become.append(task.get("name") or mod_key)

    if secret_no_log_hits:
        result.warn(
            "Production: secret/credential field without `no_log: true` in "
            f"task(s): {secret_no_log_hits[:3]}"
        )
    if privileged_no_become:
        result.warn(
            "Production: host-level task(s) without privilege escalation "
            f"(`become: true`): {privileged_no_become[:3]}"
        )
    if total_tasks and tagged_tasks == 0:
        result.warn(
            "Production: no task tags found — add action tags so runs can be "
            "filtered (e.g. `tags: [create]`)"
        )
    if total_tasks and not secret_no_log_hits and not privileged_no_become and tagged_tasks == total_tasks:
        result.ok("Production hygiene checks passed (no_log, become, tags)")


def check_hosts_field(result: ValidationResult):
    """Check 6: Does the play have a 'hosts' field?"""
    if result.parsed is None:
        return

    play = result.parsed[0] if isinstance(result.parsed, list) else {}
    if isinstance(play, dict) and "hosts" not in play:
        result.warn("No 'hosts' field — Ansible needs 'hosts' to run the play")
    elif isinstance(play, dict):
        result.ok(f"hosts: {play.get('hosts')}")


def check_ansible_lint(result: ValidationResult):
    """
    Check 9: Run ansible-lint for quality/security rules.
    Uses native binary on Linux/macOS; WSL or Docker on Windows.
    """
    from ansible_lint_runner import run_ansible_lint

    outcome = run_ansible_lint(result.filepath)

    if outcome.status == "passed":
        backend = outcome.backend
        suffix = f" via {backend}" if backend not in ("native", "none") else ""
        result.ok(f"ansible-lint passed (no lint violations){suffix}")
        result.ansible_lint = {"status": "passed", "violations": [], "backend": outcome.backend}
        return

    if outcome.status == "skipped":
        result.warn(outcome.message or "ansible-lint not installed; linting skipped")
        result.ansible_lint = {"status": "skipped", "violations": [], "backend": outcome.backend}
        return

    if outcome.status == "timeout":
        result.warn("ansible-lint timed out after 180s")
        result.ansible_lint = {"status": "timeout", "violations": [], "backend": outcome.backend}
        return

    if outcome.status in {"unsupported_platform", "wsl_not_configured", "docker_not_available", "not_installed"}:
        msg = outcome.message or "ansible-lint unavailable on this platform/runtime"
        result.warn(msg)
        result.ansible_lint = {
            "status": outcome.status,
            "violations": outcome.violations[:10],
            "backend": outcome.backend,
        }
        return

    if outcome.status == "failed_to_run":
        result.warn(f"ansible-lint execution failed: {outcome.message or 'unknown error'}")
        result.ansible_lint = {
            "status": "failed_to_run",
            "violations": outcome.violations[:10],
            "backend": outcome.backend,
        }
        return

    violations = outcome.violations or []
    result.fail(f"ansible-lint reported {len(violations)} violation(s)")
    result.ansible_lint = {
        "status": "violations",
        "violations": violations,
        "backend": outcome.backend,
    }


# ─────────────────────────────────────────────
#  MAIN VALIDATOR
# ─────────────────────────────────────────────

def validate_playbook(filepath: str, kb_modules: dict) -> ValidationResult:
    result = ValidationResult(filepath)

    print(f"\n  {'─'*54}")
    print(f"  📄 {result.filename}")
    print(f"  {'─'*54}")

    # Load file
    try:
        result.raw_yaml = load_playbook_file(filepath)
    except Exception as e:
        result.fail(f"Cannot read file: {e}")
        return result

    # Run all checks
    check_yaml_syntax(result)
    check_playbook_structure(result)
    check_k8s_task_definitions(result)
    check_module_present(result, kb_modules)
    check_required_params(result, kb_modules)
    check_hosts_field(result)
    check_no_placeholders(result)
    check_hardcoded_secrets(result)
    check_production_standards(result)
    check_ansible_lint(result)

    # Summary
    print("\n  Result: ", end="")
    if result.is_valid:
        status = "✅ VALID" if not result.warnings else "✅ VALID (with warnings)"
    else:
        status = "❌ INVALID"
    print(status)
    print(f"  Checks: {len(result.passed)} passed, "
          f"{len(result.warnings)} warnings, "
          f"{len(result.errors)} errors")

    return result


def validate_all(target_path: str = None):
    """
    Validate one file or all files in output/.
    """
    print("=" * 60)
    print("  Ansible Multi-Collection — Playbook Validator")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Load knowledge base
    kb = load_knowledge_base()
    kb_modules = kb["modules"]

    # Collect files to validate
    if target_path:
        if not os.path.exists(target_path):
            print(f"\n[ERROR] File not found: {target_path}")
            return
        files = [target_path]
    else:
        if not os.path.exists(OUTPUT_DIR):
            print(f"\n[ERROR] '{OUTPUT_DIR}/' directory not found.")
            return
        files = [
            os.path.join(OUTPUT_DIR, f)
            for f in sorted(os.listdir(OUTPUT_DIR))
            if f.endswith(".yml") or f.endswith(".yaml")
        ]
        if not files:
            print(f"\n[INFO] No .yml files found in {OUTPUT_DIR}/")
            return

    print(f"\n  Found {len(files)} playbook(s) to validate.\n")

    # Validate each file
    results = [validate_playbook(f, kb_modules) for f in files]

    # Global summary
    total   = len(results)
    valid   = sum(1 for r in results if r.is_valid)
    invalid = total - valid

    print(f"\n{'=' * 60}")
    print("  VALIDATION SUMMARY")
    print(f"  Total    : {total}")
    print(f"  ✅ Valid  : {valid}")
    print(f"  ❌ Invalid: {invalid}")
    print(f"{'=' * 60}")

    # Save report
    os.makedirs("reports", exist_ok=True)
    report = {
        "validated_at": datetime.now().isoformat(),
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "results": [
            {
                "file"    : r.filename,
                "valid"   : r.is_valid,
                "passed"  : r.passed,
                "warnings": r.warnings,
                "errors"  : r.errors,
                "ansible_lint": r.ansible_lint,
            }
            for r in results
        ]
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n  Report → {REPORT_FILE}")


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # Validate a specific file
        validate_all(target_path=sys.argv[1])
    else:
        # Validate all files in output/
        validate_all()
