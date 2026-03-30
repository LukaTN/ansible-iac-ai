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
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from models import db, Generation, ScrapeSession, ModuleVersion

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))

from phase4_generator import generate_playbook, load_knowledge_base, find_best_module
from validator import validate_playbook, load_knowledge_base as load_kb_val, K8S_CORE_MODULES
from phase2_parser import parse_module_html
from phase3_structurer import clean_parameter, clean_description, get_required_params, get_category, TASK_KEYWORDS

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.getenv("DATABASE_URL")
)
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
        return {"module": module_name, "found": False}

    # Build the official docs URL
    # Pattern: https://docs.ansible.com/ansible/latest/collections/kubernetes/core/<slug>.html
    doc_url = entry.get("source_url") or (
        f"https://docs.ansible.com/ansible/latest/collections/kubernetes/core/{slug}.html"
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

KB_PATH = os.path.join("data", "knowledge_base.json")
RAW_HTML_DIR = os.path.join("data", "raw_html")
PARSED_DIR = os.path.join("data", "parsed")
KB_VERSIONS_DIR = os.path.join("data", "kb_versions")
ANSIBLE_DOCS_BASE = "https://docs.ansible.com/ansible/latest/collections/kubernetes/core/"

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
    entry = {
        "module": parsed.get("module", ""),
        "slug": slug,
        "category": get_category(slug),
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


def _backup_kb() -> str:
    _ensure_dirs()
    if not os.path.exists(KB_PATH):
        return ""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"knowledge_base_{ts}.json"
    dst = os.path.join(KB_VERSIONS_DIR, fname)
    shutil.copy2(KB_PATH, dst)
    return fname


def _load_kb_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_kb(kb: dict):
    kb.setdefault("metadata", {})
    kb["metadata"]["generated_at"] = datetime.utcnow().isoformat()
    kb["metadata"]["total_modules"] = len(kb.get("modules") or {})
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(kb, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
#  ROUTES — PAGES
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─────────────────────────────────────────────
#  ROUTES — API
# ─────────────────────────────────────────────

@app.route("/generate", methods=["POST"])
def api_generate():
    data = request.get_json()
    user_request = (data or {}).get("request", "").strip()

    if not user_request:
        return jsonify({"error": "Empty request"}), 400

    try:
        # 1. Generate playbook
        output_path = generate_playbook(user_request)

        # 2. Read raw output
        with open(output_path, "r", encoding="utf-8") as f:
            raw = f.read()

        # 3. Strip header comments + everything before first '---'
        lines = [l for l in raw.splitlines() if not l.startswith("#")]
        start = next((i for i, l in enumerate(lines) if l.strip() == "---"), None)
        if start is not None:
            lines = lines[start:]
        playbook_clean = "\n".join(lines).strip()

        # 4. Write cleaned YAML back
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(playbook_clean)

        # 5. Validate
        kb     = load_kb_val()
        result = validate_playbook(output_path, kb["modules"])

        # 6. Detect module
        matches  = [m for m in K8S_CORE_MODULES if m in playbook_clean]
        detected = max(matches, key=len) if matches else "unknown"

        # 7. Build module reference (intent matching details + doc link)
        kb_full   = load_knowledge_base()
        module_ref = build_module_reference(detected, kb_full["modules"])

        # 8. Save to MySQL
        entry = Generation(
            request    = user_request,
            module     = detected,
            filename   = os.path.basename(output_path),
            playbook   = playbook_clean,
            is_valid   = result.is_valid,
            warnings   = len(result.warnings),
            errors     = len(result.errors),
            module_ref = module_ref,
        )
        db.session.add(entry)
        db.session.commit()

        return jsonify({
            "playbook"  : playbook_clean,
            "module"    : detected,
            "file"      : os.path.basename(output_path),
            "id"        : entry.id,
            "module_ref": module_ref,
            "validation": {
                "is_valid"   : result.is_valid,
                "passed"     : len(result.passed),
                "passed_msgs": result.passed,
                "warnings"   : result.warnings,
                "errors"     : result.errors,
            }
        })

    except ConnectionError as e:
        return jsonify({"error": f"Cannot connect to Ollama: {e}"}), 503
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/history", methods=["GET"])
def api_history():
    entries = Generation.query.order_by(Generation.created_at.desc()).limit(100).all()
    return jsonify([e.to_dict() for e in entries])


@app.route("/history/<int:entry_id>", methods=["DELETE"])
def api_delete(entry_id):
    entry = Generation.query.get_or_404(entry_id)
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"deleted": entry_id})


@app.route("/history", methods=["DELETE"])
def api_clear_history():
    Generation.query.delete()
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
    """Return full reference info for a module slug."""
    kb  = load_knowledge_base()
    ref = build_module_reference(f"kubernetes.core.{slug.replace('_module','')}", kb["modules"])
    return jsonify(ref)


# ─────────────────────────────────────────────
#  ROUTES — DOCS MANAGEMENT PANEL (API)
# ─────────────────────────────────────────────

@app.route("/docs/status", methods=["GET"])
def api_docs_status():
    _ensure_dirs()
    kb_exists = os.path.exists(KB_PATH)
    kb = _load_kb_file(KB_PATH) if kb_exists else None
    last = ScrapeSession.query.order_by(ScrapeSession.triggered_at.desc()).first()

    # module health snapshot from current KB
    health = []
    if kb:
        for slug, entry in (kb.get("modules") or {}).items():
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
        "kb_exists": kb_exists,
        "kb_metadata": (kb.get("metadata") if kb else {}),
        "module_health": health,
        "last_session": last.to_dict() if last else None,
    })


@app.route("/docs/rollback/list", methods=["GET"])
def api_docs_rollback_list():
    _ensure_dirs()
    versions = []
    for name in sorted(os.listdir(KB_VERSIONS_DIR), reverse=True):
        if not name.endswith(".json"):
            continue
        path = os.path.join(KB_VERSIONS_DIR, name)
        versions.append({
            "filename": name,
            "size": os.path.getsize(path),
            "modified_at": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(),
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
    shutil.copy2(src, KB_PATH)
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
            kb = _load_kb_file(KB_PATH) if os.path.exists(KB_PATH) else {"modules": {}}
            slugs = sorted((kb.get("modules") or {}).keys())
            if not slugs:
                _log(session_id, "No modules found in current knowledge base.")
                row = ScrapeSession.query.get(session_id)
                row.status = "failed"
                row.summary = {"error": "knowledge_base.json has no modules"}
                db.session.commit()
                return

            changed, unchanged, failed = [], [], []
            for slug in slugs:
                url = f"{ANSIBLE_DOCS_BASE}{slug}.html"
                local_html = os.path.join(RAW_HTML_DIR, f"{slug}.html")
                local_hash = _sha256_file(local_html) if os.path.exists(local_html) else ""
                try:
                    _log(session_id, f"Checking {slug} ...")
                    r = requests.get(url, timeout=20, headers={"User-Agent": "AnsibleAI-DocsManager/1.0"})
                    r.raise_for_status()
                    remote_hash = _sha256_text(r.text)
                    if not local_hash or local_hash != remote_hash:
                        changed.append({"slug": slug, "local_hash": local_hash, "remote_hash": remote_hash})
                        _log(session_id, f"  -> changed (remote != local)")
                    else:
                        unchanged.append(slug)
                except Exception as e:
                    failed.append({"slug": slug, "error": str(e)})
                    _log(session_id, f"  -> failed: {e}")

            row = ScrapeSession.query.get(session_id)
            row.status = "success" if not failed else ("partial" if changed or unchanged else "failed")
            row.modules_updated = [c["slug"] for c in changed]
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


def _rescrape_worker(session_id: int, slugs: list[str]):
    with app.app_context():
        _ensure_dirs()
        row = ScrapeSession.query.get(session_id)
        row.status = "running"
        db.session.commit()

        _log(session_id, f"Starting re-scrape for {len(slugs)} module(s)...")
        backup_name = _backup_kb()
        if backup_name:
            _log(session_id, f"Rollback backup created: {backup_name}")
        row.kb_version = backup_name or None
        db.session.commit()

        kb = _load_kb_file(KB_PATH) if os.path.exists(KB_PATH) else {"metadata": {}, "modules": {}}
        kb.setdefault("metadata", {})
        kb.setdefault("modules", {})

        updated, failed, diffs = [], [], []
        for slug in slugs:
            url = f"{ANSIBLE_DOCS_BASE}{slug}.html"
            try:
                _log(session_id, f"Downloading {slug} ...")
                r = requests.get(url, timeout=25, headers={"User-Agent": "AnsibleAI-DocsManager/1.0"})
                r.raise_for_status()

                html_path = os.path.join(RAW_HTML_DIR, f"{slug}.html")
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                remote_hash = _sha256_text(r.text)

                _log(session_id, f"Parsing {slug} ...")
                parsed = parse_module_html(html_path, slug)
                parsed_path = os.path.join(PARSED_DIR, f"{slug}.json")
                with open(parsed_path, "w", encoding="utf-8") as f:
                    json.dump(parsed, f, indent=2, ensure_ascii=False)

                new_entry = _build_kb_module_entry(parsed)
                old_entry = kb["modules"].get(slug)
                diff = _diff_summary(old_entry, new_entry)
                kb["modules"][slug] = new_entry

                health = _compute_health_score(new_entry)
                mv = ModuleVersion(
                    scrape_session_id=session_id,
                    module_slug=slug,
                    param_count=len(new_entry.get("parameters") or []),
                    example_count=len(new_entry.get("examples") or []),
                    required_count=len(new_entry.get("required_params") or []),
                    health_score=health,
                    content_hash=remote_hash,
                    diff_summary=diff,
                )
                db.session.add(mv)

                updated.append(slug)
                diffs.append({"module_slug": slug, "diff_summary": diff, "health_score": health})
                _log(session_id, f"Updated {slug} (health={health}%) — {diff}")
            except Exception as e:
                failed.append({"slug": slug, "error": str(e)})
                _log(session_id, f"FAILED {slug}: {e}")

        # Save KB + finalize session
        _save_kb(kb)
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
    slugs = data.get("modules") or []
    if not isinstance(slugs, list) or not all(isinstance(s, str) and s.strip() for s in slugs):
        return jsonify({"error": "modules must be a list of slugs"}), 400
    slugs = [s.strip() for s in slugs]

    row = ScrapeSession(triggered_by="ui", status="running", summary={"type": "rescrape", "requested": slugs})
    db.session.add(row)
    db.session.commit()

    _DOC_LOG_QUEUES[row.id] = queue.Queue()
    t = threading.Thread(target=_rescrape_worker, args=(row.id, slugs), daemon=True)
    t.start()
    return jsonify({"session_id": row.id})


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  AnsibleAI — Web UI")
    print("  http://localhost:5000")
    print("  Ctrl+C to stop")
    print("=" * 50)
    init_db()
    app.run(debug=True, port=5000)