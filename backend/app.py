"""
=============================================================
  AI-Powered IaC — Web UI (Flask + MySQL)
  Run  : npm run build   (first time / after UI changes)
         python app.py
  Dev  : python app.py  +  npm run dev  (Vite on :5173, API proxy to :5000)
  Open : http://localhost:5000
=============================================================
"""

import hashlib
import json
import os
import shutil
import sys
import threading
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from flask_login import current_user
from flask_socketio import SocketIO, join_room

_BACKEND_ROOT = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_BACKEND_ROOT)
os.chdir(_REPO_ROOT)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)
sys.path.insert(0, os.path.join(_BACKEND_ROOT, "pipeline"))

# pydantic-settings reads .env for its own model but does not export into
# os.environ, and the agent/rag/pipeline modules still read os.getenv.
# Keep this until those are migrated onto `config.settings`.
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

# Windows default console is cp1252, which crashes on non-ASCII output
# (arrows, box-drawing chars, model names with em-dashes, etc.). Force
# UTF-8 and replace anything that can't be encoded so a stray unicode
# char in a log line never takes down the request.
try:
    # Not present on every stream type (e.g. when stdout is captured),
    # which the except clause below covers.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

# config reads .env and validates everything at import time, so a
# missing DATABASE_URL or SECRET_KEY fails here rather than mid-request.
import logstream
import realtime
from config import env_summary, flask_config, settings
from logging_setup import configure_logging, get_logger, install_flask_logging

configure_logging(level=settings.log_level, fmt=settings.log_format)
log = get_logger("app")

from kb_store import load_knowledge_base as load_kb_store
from kb_store import write_manifest

from auth import audit, register_auth
from models import (
    ChatMessage,
    ChatThread,
    Generation,
    ModuleVersion,
    ScrapeSession,
    db,
    utcnow,
)

# RAG pipeline — requires: python rag/pipeline.py --build
try:
    from rag.indexer import load_vectorstore  # noqa: F401  (agent tools use it lazily)
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False
from phase2_parser import parse_module_html
from phase3_structurer import (
    TASK_KEYWORDS,
    clean_description,
    clean_parameter,
    get_category,
    get_required_params,
)
from validator import load_knowledge_base as load_kb_val

app = Flask(__name__)
app.config.update(flask_config())

db.init_app(app)

install_flask_logging(app)

# Sessions, Flask-Login, CSRF, security headers, rate limiting, and the
# default-deny authentication hook. Must come before the routes below so
# every endpoint declared in this module is covered.
register_auth(app)

socketio = SocketIO(
    app,
    cors_allowed_origins=settings.cors_origin_list,
    # "threading" for the dev server, "gevent" under the container's
    # gunicorn WebSocket worker. See Settings.socketio_async_mode.
    async_mode=settings.socketio_async_mode,
    # Subscribes this process to the Redis channel the Celery worker
    # publishes on, so progress emitted where the work happens is
    # delivered to the client's socket wherever that happens to live.
    # Empty in single-process development, where there is no second
    # process to hear from.
    message_queue=settings.socketio_message_queue or None,
)

# Emission is shared with the Celery worker, which has no server of its
# own; registering here keeps same-process emits from taking a needless
# round trip through Redis.
realtime.bind_server(socketio)


# ─────────────────────────────────────────────
#  WebSocket — authentication and per-user rooms
# ─────────────────────────────────────────────

_user_room = realtime.user_room


@socketio.on("connect")
def _on_connect(_auth=None):
    """
    Reject unauthenticated sockets and put each client in its own room.

    Flask-SocketIO shares the HTTP session, so `current_user` resolves
    from the same cookie the REST API uses. Returning False refuses the
    connection. Without this, generation events would be broadcast to
    every connected browser.
    """
    if not current_user.is_authenticated:
        log.info("socket.connect.rejected", reason="unauthenticated")
        return False
    join_room(_user_room(current_user.id))
    log.info("socket.connect", user_id=current_user.id)
    return None


# Emission itself lives in realtime.py so the Celery worker can produce
# the same events; these aliases keep the call sites below unchanged.
_emit_to_user = realtime.emit_to_user
_emit_generation_failed = realtime.emit_generation_failed
_emit_generation_cancelled = realtime.emit_generation_cancelled
_emit_generation_progress = realtime.emit_generation_progress


def _thread_history(thread_id: int) -> list[dict]:
    rows = (
        ChatMessage.query
        .filter_by(thread_id=thread_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return [r.to_dict() for r in rows]


def _enrich_agent_rag_meta(agent_resp) -> dict:
    rag_meta = dict(agent_resp.rag_meta or {})
    rag_meta["intent"] = agent_resp.intent
    if agent_resp.awaiting_user:
        rag_meta["awaiting_user"] = True
    return rag_meta


def _resolve_agent_module_ref(agent_resp, rag_meta: dict) -> dict | None:
    if agent_resp.module_ref:
        return agent_resp.module_ref
    if not agent_resp.module:
        return None
    kb = load_kb_val()
    return build_chat_module_ref(
        agent_resp.module,
        rag_meta.get("ranked_modules"),
        kb.get("modules", {}),
        primary_rag_module=rag_meta.get("primary_module"),
    )


# ─────────────────────────────────────────────
#  Thread ownership
# ─────────────────────────────────────────────

def _owned_threads():
    """Base query scoped to the signed-in user."""
    return ChatThread.query.filter_by(user_id=current_user.id)


def _get_owned_thread_or_404(thread_id: int):
    """
    Fetch a thread the current user owns, or abort 404.

    404 rather than 403 on purpose: a 403 would confirm that the thread
    exists, letting someone walk the ID space to learn how much other
    people have generated.
    """
    from flask import abort

    thread = _owned_threads().filter_by(id=thread_id).first()
    if thread is None:
        audit.record(
            audit.ACCESS_DENIED,
            user=current_user,
            outcome=audit.OUTCOME_FAILURE,
            resource="chat_thread",
            thread_id=thread_id,
        )
        abort(404)
    return thread


_DIST_DIR = os.path.join(_REPO_ROOT, "static", "dist")


@app.before_request
def _http_metrics_start():
    request._ansibleai_metrics_t0 = time.perf_counter()  # type: ignore[attr-defined]


@app.after_request
def _cors_for_vite_dev(response):
    """
    Allow the Vite dev server to call the API directly during development.

    Credentials must be allowed for cookie-based auth to work cross-origin,
    which in turn means the origin has to be echoed explicitly (a wildcard
    is rejected by browsers when credentials are included).
    """
    if not settings.is_development:
        return response
    origin = request.headers.get("Origin", "")
    if origin and origin in settings.cors_origin_list:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-CSRFToken, X-CSRF-Token, X-Request-ID"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PATCH, DELETE, OPTIONS"
        )
        response.headers.add("Vary", "Origin")
    return response


@app.after_request
def _http_metrics_observe(response):
    # Skip the scrape endpoint itself so Prometheus does not measure itself.
    if request.endpoint == "metrics":
        return response
    t0 = getattr(request, "_ansibleai_metrics_t0", None)
    if t0 is None:
        return response
    try:
        from observability.metrics import observe_http_request

        observe_http_request(
            method=request.method,
            endpoint=request.endpoint or "unknown",
            status=response.status_code,
            duration_s=time.perf_counter() - t0,
        )
    except Exception:  # noqa: BLE001
        pass
    return response


@app.route("/", methods=["OPTIONS"])
@app.route("/api/<path:_path>", methods=["OPTIONS"])
@app.route("/stats", methods=["OPTIONS"])
@app.route("/rag/<path:_path>", methods=["OPTIONS"])
@app.route("/docs/<path:_path>", methods=["OPTIONS"])
def _cors_preflight(**_kwargs):
    return "", 204


# ─────────────────────────────────────────────
#  HEALTH PROBES
# ─────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    """
    Liveness: is this process alive and serving?

    Deliberately checks nothing external. A dependency outage must not
    make Kubernetes restart an otherwise healthy pod — that is what the
    readiness probe is for.
    """
    return jsonify({"status": "ok"})


@app.get("/metrics")
def metrics():
    """Prometheus text exposition (public; scrape from the observability network)."""
    from observability.metrics import render_metrics

    body, content_type = render_metrics()
    return Response(body, mimetype=content_type)


def _check_database() -> tuple[bool, str | None]:
    from sqlalchemy import text

    try:
        db.session.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:
        return False, str(exc)[:200]


def _check_schema() -> tuple[bool, str | None]:
    """
    Confirm the migrations have been applied.

    `users` only exists after the Phase 0 migration, so its absence means
    the pod is running against an un-migrated database and must not take
    traffic.
    """
    from sqlalchemy import inspect

    try:
        tables = set(inspect(db.engine).get_table_names())
        missing = {"users", "chat_threads", "chat_messages"} - tables
        if missing:
            return False, f"missing tables: {', '.join(sorted(missing))}"
        return True, None
    except Exception as exc:
        return False, str(exc)[:200]


def _check_knowledge_base() -> tuple[bool, str | None]:
    try:
        kb = load_kb_val() or {}
        count = len(kb.get("modules") or {})
        if count == 0:
            return False, "knowledge base is empty"
        return True, None
    except Exception as exc:
        return False, str(exc)[:200]


def _check_vectorstore() -> tuple[bool, str | None]:
    if not RAG_AVAILABLE:
        return False, "rag dependencies unavailable"
    try:
        from rag.vectorstore import count

        n = count()
        if n == 0:
            return False, "vector index is empty"
        return True, None
    except Exception as exc:
        return False, str(exc)[:200]


def _check_broker() -> tuple[bool, str | None]:
    """
    Can this pod hand a generation to a worker?

    Chat is the application, and since Phase 2 every turn goes through the
    broker. A pod that cannot enqueue answers nothing useful, so this is a
    hard dependency even though the process itself is perfectly healthy.
    """
    if settings.celery_task_always_eager:
        return True, "eager mode: turns run in this process"
    try:
        from celery_app import celery

        connection = celery.connection_for_write()
        try:
            # max_retries=0: a readiness probe must answer now, not retry
            # for longer than the probe's own timeout.
            connection.ensure_connection(max_retries=0, timeout=2)
        finally:
            connection.release()
        return True, None
    except Exception as exc:
        return False, str(exc)[:200]


@app.get("/readyz")
def readyz():
    """
    Readiness: can this pod serve a real request?

    Reports every dependency so a failing probe is self-diagnosing.
    Returns 503 when a hard dependency is down so the pod is pulled from
    the Service endpoints rather than serving errors.
    """
    checks: dict[str, dict] = {}
    hard_failures = 0

    probes = (
        ("database", _check_database, True),
        ("schema", _check_schema, True),
        ("knowledge_base", _check_knowledge_base, True),
        ("broker", _check_broker, True),
        # Retrieval degrades quality but the chat surface still answers,
        # so an empty index should not remove the pod from rotation.
        ("vector_store", _check_vectorstore, False),
    )

    for name, probe, required in probes:
        ok, detail = probe()
        checks[name] = {"ok": ok, "required": required}
        if detail:
            checks[name]["detail"] = detail
        if required and not ok:
            hard_failures += 1

    ready = hard_failures == 0
    if not ready:
        log.warning("readyz.not_ready", checks=checks)
    return jsonify({"ready": ready, "checks": checks}), (200 if ready else 503)


# ─────────────────────────────────────────────
#  HELPER — build module reference metadata
# ─────────────────────────────────────────────

def build_module_reference(module_name: str, kb_modules: dict) -> dict:
    """
    Return rich metadata about the module used for generation.
    This powers the 'Sources' panel in the UI.
    """
    # Find slug from full module name (e.g. kubernetes.core.k8s_taint → k8s_taint_module)
    short  = module_name.split(".")[-1]
    slug   = short + "_module"
    entry  = kb_modules.get(slug, {})
    if not entry:
        for key, candidate in kb_modules.items():
            if key.endswith(f"::{slug}") or candidate.get("module") == module_name:
                entry = candidate
                break

    if not entry:
        return {"module": module_name, "found": False}

    # Build the official docs URL for the module collection
    collection = entry.get("collection", "") or entry.get("collection_ns", "").replace("_", ".", 1)
    module_short = module_name.split(".")[-1] if module_name else ""
    module_doc_slug = f"{module_short}_module" if module_short else slug
    doc_url = entry.get("source_url") or (
        f"https://docs.ansible.com/ansible/latest/collections/{collection.replace('.', '/')}/{module_doc_slug}.html"
        if collection
        else ""
    )

    # Required params with types
    required = [
        {"name": p["name"], "type": p.get("type", "any"), "description": p.get("description", "")[:120]}
        for p in entry.get("parameters", [])
        if p.get("required")
    ]

    # Top 5 optional useful params
    skip = {"api_key","ca_cert","client_cert","client_key","proxy","proxy_headers",
            "basic_auth","validate_certs","host","username","password"}
    optional = [
        {"name": p["name"], "type": p.get("type","any")}
        for p in entry.get("parameters", [])
        if not p.get("required") and p["name"] not in skip
    ][:5]

    # Intent keywords that matched
    keywords = entry.get("task_keywords", [])

    # Category
    category = entry.get("category", "k8s")

    return {
        "found"          : True,
        "module"         : module_name,
        "slug"           : slug,
        "description"    : entry.get("description", ""),
        "doc_url"        : doc_url,
        "category"       : category,
        "required_params": required,
        "optional_params": optional,
        "keywords"       : keywords,
        "total_params"   : len(entry.get("parameters", [])),
    }


def build_module_sources_from_ranked(
    ranked_modules: list | None,
    kb_modules: dict,
    *,
    primary_rag_module: str | None = None,
    playbook_module: str | None = None,
    limit: int = 8,
) -> list[dict]:
    """
    One rich reference dict per distinct module in retrieval ranking (UI Sources stack).
    """
    out: list[dict] = []
    seen: set[str] = set()
    for entry in ranked_modules or []:
        if not isinstance(entry, dict):
            continue
        mod = entry.get("module")
        if not mod or mod in seen:
            continue
        seen.add(mod)
        ref = build_module_reference(mod, kb_modules)
        ref["retrieval_rank"] = entry.get("rank")
        ref["retrieval_top_score"] = entry.get("top_score")
        ref["retrieval_collection"] = entry.get("collection") or ""
        ref["chunk_hits"] = entry.get("chunk_hits")
        ref["is_rag_primary"] = bool(primary_rag_module and mod == primary_rag_module)
        ref["is_playbook_module"] = bool(playbook_module and mod == playbook_module)
        out.append(ref)
        if len(out) >= limit:
            break
    return out


def build_chat_module_ref(
    detected_module: str,
    ranked_modules: list | None,
    kb_modules: dict,
    *,
    primary_rag_module: str | None = None,
) -> dict:
    """
    Single-module shape (backward compatible) or multi-source when retrieval
    ranked several modules — powers stacked Source cards in the UI.
    """
    pb_mod = None if not detected_module or detected_module == "unknown" else detected_module
    sources = build_module_sources_from_ranked(
        ranked_modules,
        kb_modules,
        primary_rag_module=primary_rag_module,
        playbook_module=pb_mod,
        limit=8,
    )
    if len(sources) >= 2:
        return {
            # Wrapper is always "found" so the UI renders the stack; each card
            # still reflects KB lookup via its own `found` flag.
            "found": True,
            "module": detected_module,
            "sources": sources,
        }
    if len(sources) == 1:
        return sources[0]
    return build_module_reference(detected_module, kb_modules)


# ─────────────────────────────────────────────
#  DOCS MANAGEMENT — config + helpers
# ─────────────────────────────────────────────

RAW_HTML_DIR = os.path.join("data", "raw_html")
PARSED_DIR = os.path.join("data", "parsed")
KB_MANIFEST_PATH = os.path.join("data", "kb_manifest.json")
KB_VERSIONS_DIR = os.path.join("data", "kb_versions")



def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _path_size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total


def _ensure_dirs():
    os.makedirs(RAW_HTML_DIR, exist_ok=True)
    os.makedirs(PARSED_DIR, exist_ok=True)
    os.makedirs(KB_VERSIONS_DIR, exist_ok=True)


def _log(session_id: int, msg: str):
    """Append a line to this scrape's log stream (see logstream.py)."""
    logstream.publish(session_id, msg)


def _compute_health_score(module_entry: dict) -> int:
    params = module_entry.get("parameters", []) or []
    examples = module_entry.get("examples", []) or []
    req = module_entry.get("required_params", []) or []

    param_count = len(params)
    example_count = len(examples)
    required_count = len(req)

    # Heuristic: params (0..50), examples (0 or 30), required detection (0 or 20)
    s_params = min(50, param_count * 5)  # 10 params => 50
    s_examples = 30 if example_count > 0 else 0
    s_required = 20 if required_count > 0 else 0
    return int(min(100, s_params + s_examples + s_required))


def _build_kb_module_entry(parsed: dict) -> dict:
    cleaned_params = [clean_parameter(p) for p in parsed.get("parameters", [])]
    slug = parsed.get("slug", "")
    collection_ns = parsed.get("collection_ns", "")
    collection = parsed.get("collection", "")
    entry = {
        "module": parsed.get("module", ""),
        "slug": slug,
        "collection_ns": collection_ns,
        "collection": collection,
        "category": get_category(slug, collection, parsed.get("module", "")),
        "description": clean_description(parsed.get("description", "")),
        "required_params": get_required_params(cleaned_params),
        "parameters": cleaned_params,
        "examples": parsed.get("examples", []),
        "return_values": parsed.get("return_values", []),
        "task_keywords": TASK_KEYWORDS.get(slug, []),
        "source_url": parsed.get("source_url", ""),
    }
    return entry


def _diff_summary(old: dict | None, new: dict) -> str:
    if not old:
        return "New module entry created."

    old_params = {p.get("name") for p in (old.get("parameters") or []) if p.get("name")}
    new_params = {p.get("name") for p in (new.get("parameters") or []) if p.get("name")}
    added = sorted(new_params - old_params)
    removed = sorted(old_params - new_params)

    parts = []
    if old.get("description", "").strip() != new.get("description", "").strip():
        parts.append("description modified")
    if added:
        parts.append(f"{len(added)} parameter(s) added")
    if removed:
        parts.append(f"{len(removed)} parameter(s) removed")

    old_req = set(old.get("required_params") or [])
    new_req = set(new.get("required_params") or [])
    if old_req != new_req:
        parts.append("required params changed")

    if (len(old.get("examples") or []) != len(new.get("examples") or [])):
        parts.append("examples count changed")

    return ", ".join(parts) if parts else "No structural change detected."


def _module_token(collection_ns: str, slug: str) -> str:
    return f"{collection_ns}::{slug}"


def _split_module_token(token: str) -> tuple[str, str]:
    """
    Parse '<collection_ns>::<slug>' token.
    Backward compatibility: plain slug means kubernetes_core::<slug>.
    """
    if "::" in token:
        collection_ns, slug = token.split("::", 1)
        return collection_ns.strip(), slug.strip()
    return "kubernetes_core", token.strip()


def _raw_html_path(collection_ns: str, slug: str) -> str:
    return os.path.join(RAW_HTML_DIR, collection_ns, f"{slug}.html")


def _parsed_json_path(collection_ns: str, slug: str) -> str:
    return os.path.join(PARSED_DIR, collection_ns, f"{slug}.json")


def _source_url_for_entry(entry: dict, collection_ns: str, slug: str) -> str:
    src = (entry or {}).get("source_url")
    if src:
        return src
    collection = (entry or {}).get("collection") or collection_ns.replace("_", ".", 1)
    module_name = (entry or {}).get("module", "")
    if module_name and "." in module_name:
        module_slug = f"{module_name.split('.')[-1]}_module"
    else:
        module_slug = slug.split("#", 1)[0]
    return f"https://docs.ansible.com/ansible/latest/collections/{collection.replace('.', '/')}/{module_slug}.html"


def _load_docs_modules() -> dict:
    """
    Load all modules from parsed-backed knowledge source.
    Returns dict keyed by '<collection_ns>::<slug>'.
    """
    kb = load_kb_store(prefer_parsed=True)
    modules = {}
    for key, entry in (kb.get("modules") or {}).items():
        collection_ns = entry.get("collection_ns")
        slug = entry.get("slug")
        if not collection_ns or not slug:
            if "::" in key:
                collection_ns, slug = key.split("::", 1)
        if not collection_ns or not slug:
            continue
        modules[_module_token(collection_ns, slug)] = entry
    return modules


def _refresh_manifest():
    kb = load_kb_store(prefer_parsed=True)
    write_manifest(kb, KB_MANIFEST_PATH)


def _backup_kb() -> str:
    """
    Create rollback snapshot for docs-managed sources.
    Snapshot includes parsed docs tree + manifest.
    """
    _ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_name = f"docs_snapshot_{ts}"
    snap_dir = os.path.join(KB_VERSIONS_DIR, snap_name)
    os.makedirs(snap_dir, exist_ok=True)

    if os.path.exists(PARSED_DIR):
        shutil.copytree(PARSED_DIR, os.path.join(snap_dir, "parsed"), dirs_exist_ok=True)
    if os.path.exists(KB_MANIFEST_PATH):
        shutil.copy2(KB_MANIFEST_PATH, os.path.join(snap_dir, "kb_manifest.json"))

    return snap_name


def _load_kb_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_parsed_module(collection_ns: str, slug: str, parsed: dict):
    out_dir = os.path.join(PARSED_DIR, collection_ns)
    os.makedirs(out_dir, exist_ok=True)
    parsed.setdefault("slug", slug)
    parsed.setdefault("collection_ns", collection_ns)
    parsed.setdefault("collection", collection_ns.replace("_", ".", 1))
    with open(os.path.join(out_dir, f"{slug}.json"), "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
#  ROUTES — PAGES
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(_DIST_DIR, "index.html")


@app.route("/assets/<path:filename>")
def vite_assets(filename):
    return send_from_directory(os.path.join(_DIST_DIR, "assets"), filename)


# ─────────────────────────────────────────────
#  ROUTES — API
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  ROUTES — AGENT CHAT
# ─────────────────────────────────────────────

# A request far longer than this is either a mistake or an attempt to
# burn GPU time; the prompt is truncated well below it anyway.
_MAX_CHAT_MESSAGE_CHARS = 8000


def _make_thread_title(message: str) -> str:
    clean = " ".join((message or "").split()).strip()
    if not clean:
        return "New chat"
    return clean[:50] + ("…" if len(clean) > 50 else "")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    Accept a user message and hand the agent turn to a background worker.

    Body: { thread_id?: int, message: str }
    Returns 202 { job_id, thread, user_message }

    The answer is not in this response and cannot be: a turn runs the
    LangGraph loop (reason → tools → draft → gate → repair) for minutes.
    The client renders the user message from this payload and waits for
    `generation_complete` on its socket, falling back to polling
    /api/chat/status if the socket is down.
    """
    # Imported here rather than at module scope: `agent` pulls in LangGraph
    # and the RAG stack, which should not be paid for at web-server startup.
    from agent import cancel

    data = request.get_json(silent=True) or {}
    message   = (data.get("message") or "").strip()
    thread_id = data.get("thread_id")

    if not message:
        return jsonify({"error": "Empty message"}), 400
    if len(message) > _MAX_CHAT_MESSAGE_CHARS:
        return jsonify({
            "error": f"Message is too long (limit {_MAX_CHAT_MESSAGE_CHARS} characters)."
        }), 413

    owner_id = current_user.id

    if thread_id:
        # Scoped lookup: posting into someone else's thread must be
        # indistinguishable from posting into one that does not exist.
        thread = _owned_threads().filter_by(id=thread_id).first()
        if not thread:
            audit.record(
                audit.ACCESS_DENIED,
                user=current_user,
                outcome=audit.OUTCOME_FAILURE,
                resource="chat_thread",
                thread_id=thread_id,
            )
            return jsonify({"error": "Thread not found"}), 404
    else:
        thread = ChatThread(user_id=owner_id, title=_make_thread_title(message))
        db.session.add(thread)
        db.session.flush()

    # One turn at a time per thread. Without this, two rapid sends race to
    # append assistant messages into the same conversation and the second
    # one's history is already stale when it starts.
    if cancel.is_running(thread.id):
        return jsonify({
            "error": "This conversation is already generating a reply.",
            "code": "already_running",
            "thread_id": thread.id,
        }), 409

    try:
        user_msg = ChatMessage(thread_id=thread.id, role="user", content=message)
        db.session.add(user_msg)
        db.session.flush()
        thread.updated_at = utcnow()
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("chat.persist_failed", thread_id=thread_id, user_id=owner_id)
        return jsonify({"error": "Could not save your message. Please try again."}), 500

    # Marked before enqueueing, so a Stop pressed while the job is still
    # queued is recorded rather than dropped on the floor.
    cancel.begin(thread.id)
    _emit_to_user("thread_upserted", thread.to_dict(), owner_id)

    try:
        from tasks import run_generation

        async_result = run_generation.delay(
            thread_id=thread.id, user_id=owner_id, message=message
        )
        job_id = async_result.id
    except Exception:
        # The broker is down. Undo the run marker and tell the user
        # plainly, rather than leaving a thread stuck "thinking" forever.
        cancel.end(thread.id)
        log.exception("chat.enqueue_failed", thread_id=thread.id, user_id=owner_id)
        _emit_generation_failed(
            thread.id, "Could not start generation. Please try again.", owner_id
        )
        return jsonify({
            "error": "Generation service is unavailable. Please try again shortly.",
            "code": "enqueue_failed",
            "thread_id": thread.id,
        }), 503

    log.info(
        "chat.request.queued",
        thread_id=thread.id,
        user_id=owner_id,
        job_id=job_id,
        message_chars=len(message),
    )
    return jsonify({
        "job_id": job_id,
        "thread": thread.to_dict(),
        "user_message": user_msg.to_dict(),
    }), 202


@app.route("/api/chat/cancel", methods=["POST"])
def api_chat_cancel():
    """
    Stop an in-flight generation for a thread.
    Body: { thread_id: int }
    """
    data = request.get_json(silent=True) or {}
    raw_thread_id = data.get("thread_id")
    if raw_thread_id is None:
        return jsonify({"error": "thread_id is required"}), 400
    try:
        thread_id = int(raw_thread_id)
    except (TypeError, ValueError):
        return jsonify({"error": "thread_id must be an integer"}), 400

    # Cancellation is a write against someone's in-flight generation, so
    # it needs the same ownership check as the thread itself.
    _get_owned_thread_or_404(thread_id)

    from agent.cancel import request_cancel

    # The flag is set whether or not a worker has picked the job up yet.
    # `found` reports only whether the turn is live, which is what lets the
    # UI distinguish "stopping" from "there was nothing to stop".
    found = request_cancel(thread_id)
    if found:
        _emit_generation_progress(
            thread_id,
            "synthesizing",
            "Stopping generation…",
            user_id=current_user.id,
        )
    log.info("chat.cancel.requested", thread_id=thread_id, was_running=found)
    return jsonify({"thread_id": thread_id, "cancelling": found}), 200


@app.route("/api/chat/status/<int:thread_id>", methods=["GET"])
def api_chat_status(thread_id: int):
    """
    Whether a turn is still queued or running for this thread.

    Backs the client's polling fallback: Socket.IO carries progress
    normally, but a dropped socket would otherwise leave the composer
    disabled with no way to learn that the answer had landed.
    """
    from agent import cancel

    _get_owned_thread_or_404(thread_id)
    return jsonify({
        "thread_id": thread_id,
        "running": cancel.is_running(thread_id),
        "cancelling": cancel.is_cancelled(thread_id),
    }), 200


@app.route("/api/threads", methods=["GET"])
def api_threads_list():
    threads = (
        _owned_threads()
        .order_by(ChatThread.updated_at.desc())
        .limit(200)
        .all()
    )
    return jsonify([t.to_dict() for t in threads])


@app.route("/api/threads/<int:thread_id>", methods=["GET"])
def api_thread_detail(thread_id: int):
    thread = _get_owned_thread_or_404(thread_id)
    return jsonify(thread.to_dict(include_messages=True))


@app.route("/api/threads/<int:thread_id>", methods=["DELETE"])
def api_thread_delete(thread_id: int):
    thread = _get_owned_thread_or_404(thread_id)
    db.session.delete(thread)
    db.session.commit()
    _emit_to_user("thread_deleted", {"id": thread_id}, current_user.id)
    return jsonify({"deleted": thread_id})


@app.route("/api/threads/<int:thread_id>", methods=["PATCH"])
def api_thread_rename(thread_id: int):
    thread = _get_owned_thread_or_404(thread_id)
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    thread.title = title[:255]
    thread.updated_at = utcnow()
    db.session.commit()
    _emit_to_user("thread_updated", thread.to_dict(), current_user.id)
    return jsonify(thread.to_dict())


@app.route("/api/threads", methods=["DELETE"])
def api_threads_clear():
    """
    Delete every thread belonging to the caller.

    Previously this wiped the table for all users with no authentication
    at all. It is now scoped to the caller and additionally listed in
    ADMIN_ENDPOINTS, so only an administrator can invoke it.
    """
    deleted = _owned_threads().delete(synchronize_session=False)
    db.session.commit()
    audit.record(audit.THREADS_CLEARED, user=current_user, deleted=deleted)
    _emit_to_user("threads_cleared", None, current_user.id)
    return jsonify({"cleared": True, "deleted": deleted})


@app.route("/stats", methods=["GET"])
def api_stats():
    from sqlalchemy import func
    total   = Generation.query.count()
    valid   = Generation.query.filter_by(is_valid=True).count()
    warns   = Generation.query.filter(Generation.warnings > 0).count()
    invalid = total - valid
    module_counts = (
        db.session.query(Generation.module, func.count(Generation.id).label("cnt"))
        .group_by(Generation.module)
        .order_by(func.count(Generation.id).desc())
        .all()
    )
    return jsonify({
        "total"  : total,
        "valid"  : valid,
        "invalid": invalid,
        "warns"  : warns,
        "modules": [{"module": m, "count": c} for m, c in module_counts],
    })


@app.route("/module/<slug>", methods=["GET"])
def api_module_info(slug):
    """Return full reference info for a module slug/token/module-name."""
    kb = load_kb_store(prefer_parsed=True)
    if "." in slug and "::" not in slug:
        module_name = slug
    elif "::" in slug:
        collection_ns, raw_slug = _split_module_token(slug)
        module_name = f"{collection_ns.replace('_', '.', 1)}.{raw_slug.replace('_module', '')}"
    else:
        module_name = f"kubernetes.core.{slug.replace('_module','')}"
    ref = build_module_reference(module_name, kb["modules"])
    return jsonify(ref)


# ─────────────────────────────────────────────
#  ROUTES — DOCS MANAGEMENT PANEL (API)
# ─────────────────────────────────────────────

@app.route("/docs/status", methods=["GET"])
def api_docs_status():
    _ensure_dirs()
    kb = load_kb_store(prefer_parsed=True)
    modules = _load_docs_modules()
    last = ScrapeSession.query.order_by(ScrapeSession.triggered_at.desc()).first()

    # Module health snapshot from parsed docs data
    health = []
    for slug, entry in modules.items():
        score = _compute_health_score(entry)
        health.append({
            "slug": slug,
            "health_score": score,
            "param_count": len(entry.get("parameters") or []),
            "example_count": len(entry.get("examples") or []),
            "required_count": len(entry.get("required_params") or []),
        })
    health.sort(key=lambda x: x["health_score"])

    return jsonify({
        "kb_exists": bool(modules),
        "kb_metadata": (kb.get("metadata") if kb else {}),
        "module_health": health,
        "last_session": last.to_dict() if last else None,
    })


@app.route("/docs/rollback/list", methods=["GET"])
def api_docs_rollback_list():
    _ensure_dirs()
    versions = []
    for name in sorted(os.listdir(KB_VERSIONS_DIR), reverse=True):
        path = os.path.join(KB_VERSIONS_DIR, name)
        if not (os.path.isdir(path) or name.endswith(".json")):
            continue
        versions.append({
            "filename": name,
            "size": _path_size(path),
            "modified_at": datetime.utcfromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds") + "Z",
        })
    return jsonify({"versions": versions})


@app.route("/docs/rollback/restore", methods=["POST"])
def api_docs_rollback_restore():
    """Restore a knowledge-base snapshot. Administrators only."""
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "").strip()
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400
    src = os.path.join(KB_VERSIONS_DIR, filename)
    # Defence in depth: confirm the resolved path really is inside the
    # snapshot directory, not just that the input looked clean.
    if os.path.commonpath([
        os.path.abspath(src), os.path.abspath(KB_VERSIONS_DIR)
    ]) != os.path.abspath(KB_VERSIONS_DIR):
        return jsonify({"error": "Invalid filename"}), 400
    if not os.path.exists(src):
        return jsonify({"error": "Backup not found"}), 404

    audit.record(audit.DOCS_ROLLBACK, user=current_user, filename=filename)

    if os.path.isdir(src):
        snap_parsed = os.path.join(src, "parsed")
        snap_manifest = os.path.join(src, "kb_manifest.json")
        if os.path.exists(snap_parsed):
            shutil.copytree(snap_parsed, PARSED_DIR, dirs_exist_ok=True)
        if os.path.exists(snap_manifest):
            shutil.copy2(snap_manifest, KB_MANIFEST_PATH)
        else:
            _refresh_manifest()
    else:
        # Legacy JSON backup compatibility: keep old behavior by restoring
        # into legacy file location.
        legacy_path = os.path.join("data", "knowledge_base.json")
        shutil.copy2(src, legacy_path)

    return jsonify({"restored": filename})


@app.route("/docs/sessions", methods=["GET"])
def api_docs_sessions():
    limit = int(request.args.get("limit", 30))
    limit = max(1, min(200, limit))
    rows = ScrapeSession.query.order_by(ScrapeSession.triggered_at.desc()).limit(limit).all()
    return jsonify([r.to_dict() for r in rows])


@app.route("/docs/sessions/<int:session_id>", methods=["GET"])
def api_docs_session_detail(session_id: int):
    row = ScrapeSession.query.get_or_404(session_id)
    mods = ModuleVersion.query.filter_by(scrape_session_id=session_id).order_by(ModuleVersion.id.desc()).all()
    return jsonify({
        "session": row.to_dict(),
        "module_versions": [m.to_dict() for m in mods],
    })


@app.route("/docs/stream/<int:session_id>", methods=["GET"])
def api_docs_stream(session_id: int):
    """
    Tail one scrape session's log over SSE.

    The reader and the scrape are usually in different processes, so the
    lines come from the shared stream rather than a local queue. Attaching
    late is fine: the Redis backend replays the run from the beginning.
    """
    logstream.create(session_id)

    def gen() -> Iterator[str]:
        yield "retry: 1000\n\n"
        for line in logstream.tail(session_id):
            if line is None:
                # Idle tick. Proxies and load balancers close a connection
                # that has been silent for too long.
                yield "event: ping\ndata: ok\n\n"
                continue
            safe = line.replace("\n", "\\n")
            yield f"data: {safe}\n\n"

    return Response(stream_with_context(gen()), mimetype="text/event-stream")


def _check_updates_worker(session_id: int):
    with app.app_context():
        _ensure_dirs()
        _log(session_id, "Fetching index modules list...")
        try:
            modules = _load_docs_modules()
            module_tokens = sorted(modules.keys())
            if not module_tokens:
                _log(session_id, "No modules found in parsed docs source.")
                row = ScrapeSession.query.get(session_id)
                row.status = "failed"
                row.summary = {"error": "No modules found in data/parsed"}
                db.session.commit()
                return

            changed, unchanged, failed = [], [], []
            for token in module_tokens:
                collection_ns, slug = _split_module_token(token)
                entry = modules.get(token, {})
                url = _source_url_for_entry(entry, collection_ns, slug)
                local_html = _raw_html_path(collection_ns, slug)
                local_hash = _sha256_file(local_html) if os.path.exists(local_html) else ""
                try:
                    _log(session_id, f"Checking {token} ...")
                    r = requests.get(url, timeout=20, headers={"User-Agent": "AnsibleAI-DocsManager/1.0"})
                    r.raise_for_status()
                    remote_hash = _sha256_text(r.text)
                    if not local_hash or local_hash != remote_hash:
                        changed.append({
                            "module_slug": token,
                            "slug": token,
                            "collection_ns": collection_ns,
                            "module_name": entry.get("module", ""),
                            "source_url": url,
                            "local_hash": local_hash,
                            "remote_hash": remote_hash,
                        })
                        _log(session_id, "  -> changed (remote != local)")
                    else:
                        unchanged.append(token)
                except Exception as e:
                    failed.append({"module_slug": token, "slug": token, "error": str(e)})
                    _log(session_id, f"  -> failed: {e}")

            row = ScrapeSession.query.get(session_id)
            row.status = "success" if not failed else ("partial" if changed or unchanged else "failed")
            row.modules_updated = [c["module_slug"] for c in changed]
            row.modules_failed = failed
            row.summary = {
                "changed": changed,
                "unchanged_count": len(unchanged),
                "failed_count": len(failed),
            }
            db.session.commit()
            _log(session_id, f"Done. changed={len(changed)} unchanged={len(unchanged)} failed={len(failed)}")
        finally:
            _log(session_id, "STREAM_END")


@app.route("/docs/check-updates", methods=["POST"])
def api_docs_check_updates():
    """Compare the local knowledge base against upstream docs. Administrators only."""
    audit.record(audit.DOCS_RESCRAPE, user=current_user, action="check_updates")
    row = ScrapeSession(
        triggered_by=current_user.email, status="running", summary={"type": "check_updates"}
    )
    db.session.add(row)
    db.session.commit()

    logstream.create(row.id)
    t = threading.Thread(target=_check_updates_worker, args=(row.id,), daemon=True)
    t.start()
    return jsonify({"session_id": row.id})


def _rescrape_worker(session_id: int, modules_to_rescrape: list[str]):
    with app.app_context():
        _ensure_dirs()
        row = ScrapeSession.query.get(session_id)
        row.status = "running"
        db.session.commit()

        _log(session_id, f"Starting re-scrape for {len(modules_to_rescrape)} module(s)...")
        backup_name = _backup_kb()
        if backup_name:
            _log(session_id, f"Rollback backup created: {backup_name}")
        row.kb_version = backup_name or None
        db.session.commit()

        current_modules = _load_docs_modules()

        updated, failed, diffs = [], [], []
        for token in modules_to_rescrape:
            collection_ns, slug = _split_module_token(token)
            old_entry = current_modules.get(token, {})
            url = _source_url_for_entry(old_entry, collection_ns, slug)
            try:
                _log(session_id, f"Downloading {token} ...")
                r = requests.get(url, timeout=25, headers={"User-Agent": "AnsibleAI-DocsManager/1.0"})
                r.raise_for_status()

                html_path = _raw_html_path(collection_ns, slug)
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                remote_hash = _sha256_text(r.text)

                _log(session_id, f"Parsing {token} ...")
                parsed = parse_module_html(html_path, slug, collection_ns)
                _save_parsed_module(collection_ns, slug, parsed)

                new_entry = _build_kb_module_entry(parsed)
                diff = _diff_summary(old_entry, new_entry)

                health = _compute_health_score(new_entry)
                mv = ModuleVersion(
                    scrape_session_id=session_id,
                    module_slug=token,
                    param_count=len(new_entry.get("parameters") or []),
                    example_count=len(new_entry.get("examples") or []),
                    required_count=len(new_entry.get("required_params") or []),
                    health_score=health,
                    content_hash=remote_hash,
                    diff_summary=diff,
                )
                db.session.add(mv)

                updated.append(token)
                diffs.append({"module_slug": token, "diff_summary": diff, "health_score": health})
                _log(session_id, f"Updated {token} (health={health}%) — {diff}")
            except Exception as e:
                failed.append({"module_slug": token, "slug": token, "error": str(e)})
                _log(session_id, f"FAILED {token}: {e}")

        # Refresh lightweight manifest from parsed source
        _refresh_manifest()
        row.modules_updated = updated
        row.modules_failed = failed
        row.summary = {
            "type": "rescrape",
            "updated_count": len(updated),
            "failed_count": len(failed),
            "diffs": diffs,
        }
        if not failed:
            row.status = "success"
        elif updated:
            row.status = "partial"
        else:
            row.status = "failed"

        db.session.commit()

        # Auto-rollback hint: if health regressed a lot
        _log(session_id, f"Done. updated={len(updated)} failed={len(failed)}")
        _log(session_id, "STREAM_END")


@app.route("/docs/rescrape", methods=["POST"])
def api_docs_rescrape():
    """Re-scrape upstream module docs into the knowledge base. Administrators only."""
    data = request.get_json(silent=True) or {}
    modules_to_rescrape = data.get("modules") or []
    if not isinstance(modules_to_rescrape, list) or not all(isinstance(s, str) and s.strip() for s in modules_to_rescrape):
        return jsonify({"error": "modules must be a list of module identifiers"}), 400
    modules_to_rescrape = [s.strip() for s in modules_to_rescrape]
    if len(modules_to_rescrape) > 2000:
        return jsonify({"error": "Too many modules requested in one call"}), 400

    audit.record(
        audit.DOCS_RESCRAPE,
        user=current_user,
        action="rescrape",
        module_count=len(modules_to_rescrape),
    )

    row = ScrapeSession(
        triggered_by=current_user.email,
        status="running",
        summary={"type": "rescrape", "requested": modules_to_rescrape},
    )
    db.session.add(row)
    db.session.commit()

    logstream.create(row.id)
    t = threading.Thread(target=_rescrape_worker, args=(row.id, modules_to_rescrape), daemon=True)
    t.start()
    return jsonify({"session_id": row.id})


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

@app.route("/rag/status", methods=["GET"])
def api_rag_status():
    """Return RAG system availability and pgvector chunk count."""
    status: dict[str, Any] = {"available": RAG_AVAILABLE}
    if RAG_AVAILABLE:
        try:
            from rag.vectorstore import count, get_index_meta

            status["chunks"] = count()
            status["embed_model"] = get_index_meta("embed_model") or settings.embedding_model
            status["backend"] = "pgvector"
            status["index_version"] = get_index_meta("index_schema_version")
        except Exception as e:
            status["error"] = str(e)
            status["chunks"] = 0
    return jsonify(status)


def _verify_schema_on_boot() -> None:
    """
    Refuse to start against an un-migrated database.

    The schema is owned by Alembic (`alembic upgrade head`), not by
    `db.create_all()`. Creating tables implicitly at startup is what makes
    a multi-replica rollout race, and it silently skips the data backfills
    the Phase 0 migration performs.
    """
    with app.app_context():
        ok, detail = _check_schema()
        if ok:
            return
        log.error("startup.schema_missing", detail=detail)
        raise SystemExit(
            "Database schema is not up to date "
            f"({detail}).\n\nRun the migrations first:\n"
            "    alembic upgrade head\n"
            "    python -m scripts.seed_admin\n"
        )


def _warm_backends_async() -> None:
    """
    Load the Ollama weights and spin up the ansible-lint backend in the
    background so the first chat request doesn't pay for either.

    Both are slow and neither is needed to serve traffic, so this runs off
    the main thread and failures are logged rather than raised.
    """
    def _run() -> None:
        from agent.llm import warm_up as warm_llm
        from pipeline.ansible_lint_runner import warm_up as warm_lint

        started = time.perf_counter()
        llm_result = warm_llm()
        lint_status = warm_lint()
        log.info(
            "startup.warm_up.done",
            models=llm_result.get("warmed") or [],
            ansible_lint=lint_status,
            seconds=round(time.perf_counter() - started, 1),
        )

    threading.Thread(target=_run, name="warm-backends", daemon=True).start()


if __name__ == "__main__":
    log.info("startup.begin", **env_summary())
    _verify_schema_on_boot()
    _warm_backends_async()

    from rag.invalidation import start_invalidation_listener
    start_invalidation_listener()

    if settings.is_development:
        # Debug reloader restarts the process when unrelated files change
        # (e.g. stdlib netrc.py on Windows), which drops in-flight
        # POST /api/chat and shows "Cannot reach the server" in the UI.
        # Opt in with FLASK_USE_RELOADER=1.
        use_reloader = os.getenv("FLASK_USE_RELOADER", "").strip().lower() in (
            "1", "true", "yes",
        )
        log.info("startup.listening", url=f"http://localhost:{settings.port}")
        socketio.run(
            app,
            debug=settings.debug,
            port=settings.port,
            use_reloader=use_reloader,
            allow_unsafe_werkzeug=True,
        )
    else:
        # Phase 1 replaces this path entirely with gunicorn + gevent in the
        # container image. Reaching it in production means the process was
        # started the wrong way.
        raise SystemExit(
            "Refusing to start the Werkzeug development server with "
            f"APP_ENV={settings.env}. Serve the app with gunicorn instead, e.g.\n"
            "    gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker "
            "--bind 0.0.0.0:5000 app:app\n"
        )
