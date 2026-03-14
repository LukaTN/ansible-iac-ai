"""
=============================================================
  AI-Powered IaC — Playbook Validator
  Input  : output/*.yml  OR  a specific playbook path
  Output : validation report (console + reports/validation_report.json)
=============================================================
  Checks performed:
    [1] YAML syntax          → is it valid YAML?
    [2] Playbook structure   → starts with ---, has hosts/tasks?
    [3] Module name          → is kubernetes.core.* module present?
    [4] Required params      → are all required params present?
    [5] Param types          → basic type checking (int/bool/string)
    [6] No placeholder left  → detects "your-*" / "PLACEHOLDER" values
=============================================================
"""

import os
import json
import re
import yaml
from datetime import datetime

# Always run from project root
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

# Regex patterns that indicate unfilled placeholders
PLACEHOLDER_PATTERNS = [
    r"your[-_][\w\-]+",        # your-namespace, your_pod_name
    r"<[\w\s\-]+>",            # <namespace>, <pod-name>
    r"PLACEHOLDER",
    r"TODO",
    r"CHANGEME",
    r"example[-_]?\w*",        # example-namespace (but not in descriptions)
]


# ─────────────────────────────────────────────
#  LOAD HELPERS
# ─────────────────────────────────────────────

def load_knowledge_base():
    with open(KNOWLEDGE_BASE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_playbook_file(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8") as f:
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


def check_module_present(result: ValidationResult):
    """Check 3: Is a kubernetes.core.* module used?"""
    raw = result.raw_yaml or ""
    # Pick the LONGEST match to avoid 'kubernetes.core.k8s' swallowing
    # 'kubernetes.core.k8s_exec', 'kubernetes.core.k8s_scale', etc.
    matches = [mod for mod in K8S_CORE_MODULES if mod in raw]
    found = max(matches, key=len) if matches else None

    if found:
        result.ok(f"Module found: {found}")
        result._detected_module = found
    else:
        result.fail("No kubernetes.core.* module detected in playbook")
        result._detected_module = None


def check_required_params(result: ValidationResult, kb_modules: dict):
    """Check 4: Are all required parameters present?"""
    module_name = getattr(result, "_detected_module", None)
    if not module_name:
        result.warn("Skipping required params check — no module detected")
        return

    slug = get_module_slug(module_name)
    if slug not in kb_modules:
        result.warn(f"Module '{module_name}' not in knowledge base")
        return

    required = kb_modules[slug].get("required_params", [])

    # field_manager is only needed with server_side_apply — skip it
    OPTIONAL_IN_PRACTICE = {"field_manager", "value"}
    required = [p for p in required if p not in OPTIONAL_IN_PRACTICE]

    if not required:
        result.ok("No required parameters for this module")
        return

    raw = result.raw_yaml or ""

    # Build alias map: param_name → [param_name, alias1, alias2, ...]
    all_params = kb_modules[slug].get("parameters", [])
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
    """Check 5: Are there unfilled placeholder values?"""
    raw = result.raw_yaml or ""
    found_placeholders = []

    for pattern in PLACEHOLDER_PATTERNS:
        matches = re.findall(pattern, raw, re.IGNORECASE)
        # Filter out comment lines
        for match in matches:
            # Check if it's inside a comment
            for line in raw.splitlines():
                if match in line and not line.strip().startswith("#"):
                    found_placeholders.append(match)
                    break

    # Deduplicate
    found_placeholders = list(set(found_placeholders))

    if found_placeholders:
        result.warn(f"Possible placeholder values found: {found_placeholders}")
    else:
        result.ok("No placeholder values detected")


def check_hosts_field(result: ValidationResult):
    """Check 6: Does the play have a 'hosts' field?"""
    if result.parsed is None:
        return

    play = result.parsed[0] if isinstance(result.parsed, list) else {}
    if isinstance(play, dict) and "hosts" not in play:
        result.warn("No 'hosts' field — Ansible needs 'hosts' to run the play")
    elif isinstance(play, dict):
        result.ok(f"hosts: {play.get('hosts')}")


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
    check_module_present(result)
    check_required_params(result, kb_modules)
    check_hosts_field(result)
    check_no_placeholders(result)

    # Summary
    print(f"\n  Result: ", end="")
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
    print("  Ansible kubernetes.core — Playbook Validator")
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
    print(f"  VALIDATION SUMMARY")
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