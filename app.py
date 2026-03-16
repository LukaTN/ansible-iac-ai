"""
=============================================================
  AI-Powered IaC — Web UI (Flask + MySQL)
  Run  : python app.py
  Open : http://localhost:5000
=============================================================
"""

import os
import sys
from flask import Flask, render_template, request, jsonify
from models import db, Generation

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))

from phase4_generator import generate_playbook, load_knowledge_base, find_best_module
from validator import validate_playbook, load_knowledge_base as load_kb_val, K8S_CORE_MODULES

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = (
    "mysql+pymysql://root@localhost:3306/ansibleai"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle" : 300,
}

db.init_app(app)

with app.app_context():
    db.create_all()
    print("  [DB] Tables created / verified ✓")


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
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  AnsibleAI — Web UI")
    print("  http://localhost:5000")
    print("  Ctrl+C to stop")
    print("=" * 50)
    app.run(debug=True, port=5000)