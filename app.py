"""
=============================================================
  AI-Powered IaC — Web UI (Flask + MySQL)
  Run  : python app.py
  Open : http://localhost:5000
=============================================================
  Structure:
    app.py                  <- Flask routes + DB init
    models.py               <- SQLAlchemy models
    templates/index.html    <- HTML markup
    static/css/style.css    <- Styles
    static/js/app.js        <- Frontend logic
=============================================================
"""

import os
import sys
from flask import Flask, render_template, request, jsonify
from models import db, Generation

# Always run from project root
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))

from phase4_generator import generate_playbook
from validator import validate_playbook, load_knowledge_base as load_kb_val, K8S_CORE_MODULES

# ─────────────────────────────────────────────
#  APP + DB CONFIG
# ─────────────────────────────────────────────

app = Flask(__name__)

# MySQL connection string — edit user/password/host/dbname as needed
# Format: mysql+pymysql://<user>:<password>@<host>:<port>/<database>
app.config["SQLALCHEMY_DATABASE_URI"] = (
    "mysql+pymysql://root@localhost:3306/ansibleai"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,   # auto-reconnect if connection drops
    "pool_recycle" : 300,    # recycle connections every 5 min
}

# Bind SQLAlchemy to this app
db.init_app(app)

# Auto-create tables on startup (like spring.jpa.hibernate.ddl-auto=update)
with app.app_context():
    db.create_all()
    print("  [DB] Tables created / verified ✓")


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

        # 4. Write cleaned YAML back so validator reads clean content
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(playbook_clean)

        # 5. Validate
        kb     = load_kb_val()
        result = validate_playbook(output_path, kb["modules"])

        # 6. Detect module
        matches  = [m for m in K8S_CORE_MODULES if m in playbook_clean]
        detected = max(matches, key=len) if matches else "unknown"

        # 7. Save to MySQL
        entry = Generation(
            request  = user_request,
            module   = detected,
            filename = os.path.basename(output_path),
            playbook = playbook_clean,
            is_valid = result.is_valid,
            warnings = len(result.warnings),
            errors   = len(result.errors),
        )
        db.session.add(entry)
        db.session.commit()

        return jsonify({
            "playbook"  : playbook_clean,
            "module"    : detected,
            "file"      : os.path.basename(output_path),
            "id"        : entry.id,
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
    """Return all generations ordered by most recent."""
    entries = Generation.query.order_by(Generation.created_at.desc()).limit(100).all()
    return jsonify([e.to_dict() for e in entries])


@app.route("/history/<int:entry_id>", methods=["DELETE"])
def api_delete(entry_id):
    """Delete a single generation entry."""
    entry = Generation.query.get_or_404(entry_id)
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"deleted": entry_id})


@app.route("/history", methods=["DELETE"])
def api_clear_history():
    """Delete all generation history."""
    Generation.query.delete()
    db.session.commit()
    return jsonify({"cleared": True})


@app.route("/stats", methods=["GET"])
def api_stats():
    """Aggregate statistics from the database."""
    from sqlalchemy import func

    total   = Generation.query.count()
    valid   = Generation.query.filter_by(is_valid=True).count()
    warns   = Generation.query.filter(Generation.warnings > 0).count()
    invalid = total - valid

    # Module usage counts
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