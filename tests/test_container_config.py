"""
Guards for the container build (Phase 1).

These assert invariants that are silent when broken: nothing fails at
import time if gunicorn is given four workers, and a CRLF line ending on
the entrypoint only shows up as an unhelpful "no such file or directory"
from the kernel, on Linux, at container start.

They are pure file inspections — no Docker daemon required — so they run
in the same suite as everything else.
"""

from __future__ import annotations

import re
import runpy
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _requirement_names(path: Path) -> set[str]:
    """Distribution names from a requirements file, ignoring comments and pins."""
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # Strip the environment marker, then the version specifier.
        spec = line.split(";", 1)[0].strip()
        names.add(re.split(r"[=<>!~\[]", spec, maxsplit=1)[0].strip().lower())
    return names


@pytest.fixture(scope="module")
def gunicorn_conf() -> dict[str, Any]:
    """Execute gunicorn.conf.py the way gunicorn does and return its globals."""
    return runpy.run_path(str(ROOT / "backend" / "gunicorn.conf.py"))


# ── gunicorn ─────────────────────────────────────────────────────

def test_binds_all_interfaces(gunicorn_conf: dict[str, Any]) -> None:
    """127.0.0.1 would be unreachable from outside the container."""
    assert gunicorn_conf["bind"].startswith("0.0.0.0:")


def test_single_worker_by_default(gunicorn_conf: dict[str, Any]) -> None:
    """
    Phase 2 made a second worker *possible* — emits, cancellation and SSE
    log tailing all go through Redis now — but Socket.IO still needs a
    session's requests to reach the process that accepted them, and
    gunicorn cannot pin them. The default stays 1 until an ingress does
    sticky sessions.
    """
    assert gunicorn_conf["workers"] == 1


def test_request_timeout_exceeds_agent_timeout(gunicorn_conf: dict[str, Any]) -> None:
    """
    /api/chat no longer holds a request across the draft/repair loop, but
    the SSE log stream and the RAG-index endpoints still run long, so the
    timeout has to stay above a single LLM call.
    """
    from config import settings

    assert gunicorn_conf["timeout"] > settings.agent_request_timeout


def test_app_is_not_preloaded(gunicorn_conf: dict[str, Any]) -> None:
    """
    gevent monkey-patches inside the worker. Preloading imports psycopg2
    and redis in the master first, leaving unpatched blocking sockets.
    """
    assert gunicorn_conf["preload_app"] is False


def test_gunicorn_access_log_disabled(gunicorn_conf: dict[str, Any]) -> None:
    """logging_setup already emits one structured line per response."""
    assert gunicorn_conf["accesslog"] is None


def test_gevent_websocket_stays_uninstalled() -> None:
    """
    Older Flask-SocketIO docs recommend gevent-websocket and its
    GeventWebSocketWorker. The package has been unmaintained since 2017,
    current python-engineio uses simple-websocket instead, and having both
    installed makes the server close every socket straight after the 101
    handshake — which looks like a network fault, not a bad dependency.
    """
    specs = _requirement_names(ROOT / "requirements.txt")
    assert "gevent-websocket" not in specs
    assert "simple-websocket" in specs


# ── lint toolchain ───────────────────────────────────────────────

def test_image_sets_pythonpath_for_celery_forks() -> None:
    """
    Celery prefork children resolve local packages via PYTHONPATH, not via
    the empty '' sys.path entry (which tracks cwd and can leave /app).
    Without this, worker tasks raise ModuleNotFoundError: agent.
    """
    body = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "PYTHONPATH=/app/backend" in body


def test_celery_modules_bootstrap_sys_path() -> None:
    """Belt-and-braces: tasks.py and celery_app.py insert their own root."""
    for name in ("celery_app.py", "tasks.py"):
        body = (ROOT / "backend" / name).read_text(encoding="utf-8")
        assert "sys.path.insert" in body
        assert "Path(__file__)" in body


def test_worker_entrypoint_exports_pythonpath() -> None:
    body = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    worker_block = body.split("worker)", 1)[1].split(";;", 1)[0]
    assert "PYTHONPATH" in worker_block
    assert "/app/backend" in worker_block
    assert "cd /app" in worker_block


def test_lint_toolchain_is_declared() -> None:
    """
    The production gate shells out to ansible-lint, but it was absent from
    requirements.txt before Phase 1. A fresh install therefore produced an
    app whose gate reported "not installed" and passed playbooks through
    unlinted.
    """
    assert {"ansible-core", "ansible-lint"} <= _requirement_names(ROOT / "requirements.txt")


def test_image_runs_lint_natively() -> None:
    """The WSL hop is a Windows workaround; in the image ansible-lint is present."""
    assert "ANSIBLE_LINT_MODE=native" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


# ── entrypoint ───────────────────────────────────────────────────

def test_entrypoint_uses_lf_endings() -> None:
    """
    .gitattributes pins this, but a stray editor save can undo it and the
    failure only reproduces inside Linux.
    """
    raw = (ROOT / "docker" / "entrypoint.sh").read_bytes()
    assert b"\r\n" not in raw


def test_entrypoint_covers_every_role() -> None:
    body = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    for role in ("api)", "migrate)", "worker)", "smoke)", "healthcheck)", "exec)"):
        assert role in body


def test_healthcheck_is_role_aware() -> None:
    """
    The worker serves no HTTP. A shared /healthz probe marks it unhealthy
    forever, and anything gating on `service_healthy` never starts.
    """
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "entrypoint.sh" in dockerfile.split("HEALTHCHECK", 1)[1].split("\n\n", 1)[0]

    body = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    probe = body.split("healthcheck)", 1)[1].split("\n    ;;", 1)[0]
    assert "inspect ping" in probe
    assert "/healthz" in probe


def test_compose_allows_socketio_from_the_container_ui(compose: dict[str, Any]) -> None:
    """
    The SPA is served from the API on :5000. Socket.IO checks Origin and
    returns 400 when that origin is missing from CORS_ORIGINS — which is
    exactly the 'Reconnecting… / stuck on Understand' failure mode.
    """
    origins = compose["services"]["api"]["environment"]["CORS_ORIGINS"]
    assert "http://localhost:5000" in origins
    assert "http://127.0.0.1:5000" in origins


def test_default_cors_includes_container_and_vite_origins() -> None:
    from config import Settings

    origins = Settings.model_fields["cors_origins"].default
    for needed in (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5000",
        "http://127.0.0.1:5000",
    ):
        assert needed in origins


def test_worker_service_declares_its_role_for_the_probe(compose: dict[str, Any]) -> None:
    """The HEALTHCHECK is a separate process; it can only read the env."""
    assert compose["services"]["worker"]["environment"]["APP_ROLE"] == "worker"



def test_api_role_does_not_run_migrations() -> None:
    """
    Replicas racing on `alembic upgrade` is how schemas get corrupted.
    Migrations belong to the one-shot `migrate` role.
    """
    body = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    api_block = body.split("api)", 1)[1].split(";;", 1)[0]
    assert "alembic" not in api_block


# ── build context ────────────────────────────────────────────────

def test_dockerignore_excludes_secrets_and_heavy_artifacts() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    # .env would otherwise be baked into a layer; the rest are hundreds of
    # megabytes of regenerable build artifacts.
    for required in (".env", "output/", "frontend/node_modules/", ".git"):
        assert required in patterns, f"{required} missing from .dockerignore"


def test_dockerignore_keeps_files_the_image_needs() -> None:
    """A broad ignore pattern that swallows these breaks the build silently."""
    patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for needed in ("backend/", "scripts/", "alembic.ini", "docker/"):
        assert needed not in patterns


# ── compose ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_api_container_is_hardened(compose: dict[str, Any]) -> None:
    api = compose["services"]["api"]
    assert api["read_only"] is True
    assert api["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in api["security_opt"]


def test_api_waits_for_migrations(compose: dict[str, Any]) -> None:
    depends = compose["services"]["api"]["depends_on"]
    assert depends["migrate"]["condition"] == "service_completed_successfully"
    assert depends["db"]["condition"] == "service_healthy"
    assert depends["redis"]["condition"] == "service_healthy"


def test_socketio_async_mode_matches_worker_class(
    compose: dict[str, Any], gunicorn_conf: dict[str, Any]
) -> None:
    """
    Flask-SocketIO and gunicorn have to agree on the concurrency model.
    "gevent" with a threaded worker (or the reverse) yields hangs that
    look like network faults.
    """
    assert compose["services"]["api"]["environment"]["SOCKETIO_ASYNC_MODE"] == "gevent"
    assert "gevent" in gunicorn_conf["worker_class"].lower()


# ── compose: Phase 2 stateless stack ─────────────────────────────

def test_worker_service_runs_the_worker_role(compose: dict[str, Any]) -> None:
    """Same image as the API, different role — that is what keeps the
    Kubernetes Deployment and the worker Deployment near-identical."""
    worker = compose["services"]["worker"]
    assert worker["command"] == ["worker"]
    assert worker["image"] == compose["services"]["api"]["image"]


def test_worker_and_api_share_a_socketio_message_queue(compose: dict[str, Any]) -> None:
    """
    The worker produces progress; the API holds the client's socket.
    Without a shared queue the browser sits on "thinking" until the
    answer is already in the database.
    """
    api_env = compose["services"]["api"]["environment"]
    worker_env = compose["services"]["worker"]["environment"]
    assert api_env["SOCKETIO_MESSAGE_QUEUE"] == worker_env["SOCKETIO_MESSAGE_QUEUE"]
    assert api_env["SOCKETIO_MESSAGE_QUEUE"].startswith("redis://")


def test_compose_does_not_run_the_agent_inline(compose: dict[str, Any]) -> None:
    """Eager mode would put the multi-minute turn back inside the request."""
    assert compose["services"]["api"]["environment"]["CELERY_TASK_ALWAYS_EAGER"] == "false"


def test_cross_process_state_uses_redis(compose: dict[str, Any]) -> None:
    """Stop and log tailing have to work whichever process serves them."""
    env = compose["services"]["api"]["environment"]
    assert env["CANCEL_BACKEND"] == "redis"
    assert env["LOG_STREAM_BACKEND"] == "redis"


def test_broker_and_sessions_use_separate_redis_databases(compose: dict[str, Any]) -> None:
    """`celery purge` must never be one keyspace away from every session."""
    env = compose["services"]["api"]["environment"]
    assert env["REDIS_URL"] != env["CELERY_BROKER_URL"]


def test_worker_waits_for_its_dependencies(compose: dict[str, Any]) -> None:
    depends = compose["services"]["worker"]["depends_on"]
    assert depends["migrate"]["condition"] == "service_completed_successfully"
    for service in ("db", "redis", "minio"):
        assert depends[service]["condition"] == "service_healthy"


def test_worker_has_no_output_mount(compose: dict[str, Any]) -> None:
    """
    Playbooks go to object storage. A local output/ mount on the worker
    would put the durable copy on a disk no other replica can read.
    """
    targets = {entry.split(":")[1] for entry in compose["services"]["worker"]["volumes"]}
    assert "/app/output" not in targets
    assert compose["services"]["api"]["environment"]["ARTIFACT_BACKEND"] == "s3"


def test_worker_is_hardened_like_the_api(compose: dict[str, Any]) -> None:
    worker = compose["services"]["worker"]
    assert worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in worker["security_opt"]


def test_celery_hard_limit_exceeds_the_soft_limit() -> None:
    """
    The soft limit is what lets a timed-out task persist a note. If the
    hard limit fired first, the thread would end on the user's message
    and the UI would wait forever.
    """
    from config import settings

    assert settings.celery_time_limit > settings.celery_soft_time_limit


def test_read_only_rootfs_has_writable_mounts_for_every_written_path(
    compose: dict[str, Any],
) -> None:
    """
    With read_only: true, any path the app writes to needs a volume or a
    tmpfs. These are the ones app.py, the scraper and the lint runner touch.
    """
    api = compose["services"]["api"]
    targets = {entry.split(":")[1] for entry in api["volumes"]}
    tmpfs = {entry.split(":")[0] for entry in api["tmpfs"]}

    assert {"/app/output", "/app/reports"} <= targets
    # tempfile, plus ANSIBLE_HOME and the cache dirs the Dockerfile points
    # at /tmp; /home/app is ansible-core's writable HOME requirement.
    assert {"/tmp", "/home/app"} <= tmpfs
