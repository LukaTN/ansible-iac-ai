"""
=============================================================
  AI-Powered IaC — Web UI (Flask + MySQL)
  Run  : python app.py
  Open : http://localhost:5000
=============================================================
"""

import os
import sys
import json
import shutil
import hashlib
import threading
import queue
from datetime import datetime
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))

# Windows default console is cp1252, which crashes on non-ASCII prints
# (arrows, box-drawing chars, model names with em-dashes, etc.). Force
# UTF-8 output and replace anything that can't be encoded so a stray
# unicode char in a log line never takes down the request.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Load .env before importing anything that reads env vars at import time.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from models import db, Generation, ScrapeSession, ModuleVersion, ChatThread, ChatMessage

from kb_store import load_knowledge_base as load_kb_store, write_manifest

# RAG pipeline — requires: python rag/pipeline.py --build
try:
    from rag.indexer import load_vectorstore  # noqa: F401  (agent tools use it lazily)
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False
from validator import validate_playbook, load_knowledge_base as load_kb_val
from phase2_parser import parse_module_html
from phase3_structurer import clean_parameter, clean_description, get_required_params, get_category, TASK_KEYWORDS

app = Flask(__name__)

_db_url = os.getenv("DATABASE_URL")
if not _db_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Define it in the .env file at the project root "
        "(e.g. DATABASE_URL=mysql+pymysql://root@localhost:3306/ansibleai) "
        "or export it in your shell before running."
    )
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle" : 300,
}

db.init_app(app)

def init_db():
    with app.app_context():
        db.create_all()
        # Avoid Unicode issues on Windows consoles (cp1252)
        print("  [DB] Tables created / verified (ok)")


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


# ─────────────────────────────────────────────
#  DOCS MANAGEMENT — config + helpers
# ─────────────────────────────────────────────

RAW_HTML_DIR = os.path.join("data", "raw_html")
PARSED_DIR = os.path.join("data", "parsed")
KB_MANIFEST_PATH = os.path.join("data", "kb_manifest.json")
KB_VERSIONS_DIR = os.path.join("data", "kb_versions")

# session_id -> Queue[str] for SSE logs
_DOC_LOG_QUEUES: dict[int, "queue.Queue[str]"] = {}


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
    q = _DOC_LOG_QUEUES.get(session_id)
    if not q:
        return
    ts = datetime.utcnow().strftime("%H:%M:%S")
    q.put(f"[{ts}] {msg}")


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
    with open(path, "r", encoding="utf-8") as f:
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
    return render_template("index.html")


# ─────────────────────────────────────────────
#  ROUTES — API
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  ROUTES — AGENT CHAT
# ─────────────────────────────────────────────

def _make_thread_title(message: str) -> str:
    clean = " ".join((message or "").split()).strip()
    if not clean:
        return "New chat"
    return clean[:50] + ("…" if len(clean) > 50 else "")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    Send a user message to the agent.
    Body: { thread_id?: int, message: str }
    Returns: { thread, user_message, assistant_message }
    """
    from agent.orchestrator import handle_message

    data = request.get_json() or {}
    message   = (data.get("message") or "").strip()
    thread_id = data.get("thread_id")

    if not message:
        return jsonify({"error": "Empty message"}), 400

    try:
        if thread_id:
            thread = ChatThread.query.get(thread_id)
            if not thread:
                return jsonify({"error": "Thread not found"}), 404
        else:
            thread = ChatThread(title=_make_thread_title(message))
            db.session.add(thread)
            db.session.flush()

        # Persist user message.
        user_msg = ChatMessage(
            thread_id=thread.id,
            role="user",
            content=message,
        )
        db.session.add(user_msg)
        db.session.flush()

        # Build conversation history for the agent (before this turn's assistant reply).
        history = [m.to_dict() for m in thread.messages if m.id != user_msg.id]
        history.append(user_msg.to_dict())

        response = handle_message(
            thread_id    = thread.id,
            user_message = message,
            history      = history,
            db_session   = db.session,
        )

        assistant_msg = ChatMessage(
            thread_id=thread.id,
            **response.to_message_kwargs(),
        )
        db.session.add(assistant_msg)

        # Bump thread timestamp + auto-title if still default.
        thread.updated_at = datetime.utcnow()
        if (thread.title or "").strip().lower() in ("", "new chat"):
            thread.title = _make_thread_title(message)

        db.session.commit()

        return jsonify({
            "thread"           : thread.to_dict(),
            "user_message"     : user_msg.to_dict(),
            "assistant_message": assistant_msg.to_dict(),
        })

    except ConnectionError as e:
        db.session.rollback()
        return jsonify({"error": f"Cannot connect to Ollama: {e}"}), 503
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/threads", methods=["GET"])
def api_threads_list():
    threads = ChatThread.query.order_by(ChatThread.updated_at.desc()).limit(200).all()
    return jsonify([t.to_dict() for t in threads])


@app.route("/api/threads/<int:thread_id>", methods=["GET"])
def api_thread_detail(thread_id: int):
    thread = ChatThread.query.get_or_404(thread_id)
    return jsonify(thread.to_dict(include_messages=True))


@app.route("/api/threads/<int:thread_id>", methods=["DELETE"])
def api_thread_delete(thread_id: int):
    thread = ChatThread.query.get_or_404(thread_id)
    db.session.delete(thread)
    db.session.commit()
    return jsonify({"deleted": thread_id})


@app.route("/api/threads/<int:thread_id>", methods=["PATCH"])
def api_thread_rename(thread_id: int):
    thread = ChatThread.query.get_or_404(thread_id)
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    thread.title = title[:255]
    thread.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(thread.to_dict())


@app.route("/api/threads", methods=["DELETE"])
def api_threads_clear():
    ChatThread.query.delete()
    db.session.commit()
    return jsonify({"cleared": True})


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
    data = request.get_json() or {}
    filename = (data.get("filename") or "").strip()
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400
    src = os.path.join(KB_VERSIONS_DIR, filename)
    if not os.path.exists(src):
        return jsonify({"error": "Backup not found"}), 404

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
    q = _DOC_LOG_QUEUES.get(session_id)
    if not q:
        # allow late attach: create queue so UI can connect anytime
        q = queue.Queue()
        _DOC_LOG_QUEUES[session_id] = q

    @stream_with_context
    def gen():
        yield "retry: 1000\n\n"
        while True:
            try:
                line = q.get(timeout=30)
                safe = line.replace("\n", "\\n")
                yield f"data: {safe}\n\n"
            except queue.Empty:
                yield "event: ping\ndata: ok\n\n"

    return Response(gen(), mimetype="text/event-stream")


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
                        _log(session_id, f"  -> changed (remote != local)")
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
    row = ScrapeSession(triggered_by="ui", status="running", summary={"type": "check_updates"})
    db.session.add(row)
    db.session.commit()

    _DOC_LOG_QUEUES[row.id] = queue.Queue()
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
    data = request.get_json() or {}
    modules_to_rescrape = data.get("modules") or []
    if not isinstance(modules_to_rescrape, list) or not all(isinstance(s, str) and s.strip() for s in modules_to_rescrape):
        return jsonify({"error": "modules must be a list of module identifiers"}), 400
    modules_to_rescrape = [s.strip() for s in modules_to_rescrape]

    row = ScrapeSession(triggered_by="ui", status="running", summary={"type": "rescrape", "requested": modules_to_rescrape})
    db.session.add(row)
    db.session.commit()

    _DOC_LOG_QUEUES[row.id] = queue.Queue()
    t = threading.Thread(target=_rescrape_worker, args=(row.id, modules_to_rescrape), daemon=True)
    t.start()
    return jsonify({"session_id": row.id})


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

@app.route("/rag/status", methods=["GET"])
def api_rag_status():
    """Return RAG system availability and ChromaDB chunk count."""
    status = {"available": RAG_AVAILABLE}
    if RAG_AVAILABLE:
        try:
            import chromadb
            from chromadb.config import Settings
            client = chromadb.PersistentClient(
                path="data/chromadb",
                #settings=Settings(anonymized_telemetry=False)
            )
            col = client.get_collection("ansible_docs")
            status["chunks"]      = col.count()
            status["embed_model"] = "nomic-embed-text"
        except Exception as e:
            status["error"]  = str(e)
            status["chunks"] = 0
    return jsonify(status)


if __name__ == "__main__":
    print("=" * 50)
    print("  AnsibleAI — Web UI")
    print("  http://localhost:5000")
    print("  Ctrl+C to stop")
    print("=" * 50)
    init_db()
    app.run(debug=True, port=5000)