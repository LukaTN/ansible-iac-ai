"""
=============================================================
  AI-Powered IaC — Phase 3 : Structurer
  Input  : data/parsed/*.json     (Phase 2 output)
  Output : data/knowledge_base.json
=============================================================
  What it does:
    - Loads all parsed module JSONs
    - Cleans remaining artifacts (bad defaults, empty fields)
    - Organizes by category (helm / k8s)
    - Builds a single knowledge_base.json optimized for LLM prompting
    - Adds task_keywords to each module for intent matching
=============================================================
"""

import os
import json
import re
from datetime import datetime

# Always run from project root
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

INPUT_DIR   = "data/parsed"
OUTPUT_FILE = "data/knowledge_base.json"
REPORT_FILE = "reports/structure_report.json"


# ─────────────────────────────────────────────
#  TASK KEYWORDS MAP
#  Used by Phase 4 to match user input → module
# ─────────────────────────────────────────────

TASK_KEYWORDS = {
    "k8s_module": [
        "create", "apply", "deploy", "delete", "update", "patch",
        "resource", "deployment", "service", "pod", "namespace",
        "configmap", "secret", "ingress", "statefulset", "daemonset"
    ],
    "k8s_info_module": [
        "get", "list", "read", "info", "show", "describe",
        "fetch", "query", "check", "status"
    ],
    "k8s_cluster_info_module": [
        "cluster info", "api versions", "cluster status",
        "server version", "api resources"
    ],
    "k8s_exec_module": [
        "exec", "execute", "run command", "command in pod",
        "shell", "terminal", "run script"
    ],
    "k8s_cp_module": [
        "copy", "upload", "download", "transfer file",
        "copy to pod", "copy from pod"
    ],
    "k8s_drain_module": [
        "drain", "evict", "maintenance", "drain node",
        "cordon", "evacuate"
    ],
    "k8s_log_module": [
        "logs", "log", "output", "stdout", "stderr",
        "container logs", "pod logs"
    ],
    "k8s_scale_module": [
        "scale", "replicas", "resize", "scale up",
        "scale down", "autoscale"
    ],
    "k8s_rollback_module": [
        "rollback", "revert", "undo", "previous version",
        "roll back deployment"
    ],
    "k8s_taint_module": [
        "taint", "untaint", "node taint", "toleration"
    ],
    "k8s_json_patch_module": [
        "json patch", "patch resource", "modify field",
        "strategic patch"
    ],
    "k8s_service_module": [
        "service", "expose", "loadbalancer", "clusterip",
        "nodeport", "create service"
    ],
    "helm_module": [
        "helm install", "helm deploy", "chart", "release",
        "helm upgrade", "install chart", "deploy chart"
    ],
    "helm_info_module": [
        "helm info", "helm status", "release info",
        "chart status", "helm get"
    ],
    "helm_repository_module": [
        "helm repo", "add repo", "repository",
        "helm repository", "chart repo"
    ],
    "helm_plugin_module": [
        "helm plugin", "install plugin", "plugin"
    ],
    "helm_plugin_info_module": [
        "helm plugin info", "list plugins", "plugin list"
    ],
    "helm_template_module": [
        "helm template", "render chart", "template",
        "dry run", "manifest"
    ],
}

# Module categories
CATEGORIES = {
    "k8s" : ["k8s_module", "k8s_info_module", "k8s_cluster_info_module",
              "k8s_exec_module", "k8s_cp_module", "k8s_drain_module",
              "k8s_log_module", "k8s_scale_module", "k8s_rollback_module",
              "k8s_taint_module", "k8s_json_patch_module", "k8s_service_module"],
    "helm": ["helm_module", "helm_info_module", "helm_repository_module",
             "helm_plugin_module", "helm_plugin_info_module", "helm_template_module"],
}


# ─────────────────────────────────────────────
#  CLEANERS
# ─────────────────────────────────────────────

# Words that should never be a "default" value (parsing artifacts)
BAD_DEFAULTS = {
    "to", "the", "a", "an", "is", "are", "be", "by",
    "in", "of", "or", "if", "it", "this", "that", "and",
    "false", "true",   # these belong in choices, not default
}

def clean_default(value, choices):
    """
    Fix bad default values extracted by the parser.
    e.g. 'to' from 'Default to false' → ''
    But keep 'false'/'true' only if they're real defaults (not in bad list).
    """
    if not value:
        return ""
    v = value.strip().lower()
    if v in BAD_DEFAULTS:
        # Try to find a real default in choices
        if len(choices) == 1:
            return choices[0]
        return ""
    return value


def clean_description(text):
    """Remove excessive whitespace from descriptions."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


def clean_parameter(param):
    """Clean and normalize a single parameter dict."""
    return {
        "name"       : param.get("name", ""),
        "aliases"    : param.get("aliases", []),
        "type"       : param.get("type", ""),
        "required"   : param.get("required", False),
        "default"    : clean_default(
                           param.get("default", ""),
                           param.get("choices", [])
                       ),
        "choices"    : param.get("choices", []),
        "description": clean_description(param.get("description", "")),
    }


def get_required_params(parameters):
    """Return list of required parameter names."""
    return [p["name"] for p in parameters if p.get("required")]


def get_category(slug):
    """Return 'k8s', 'helm', or 'unknown' for a module slug."""
    for cat, slugs in CATEGORIES.items():
        if slug in slugs:
            return cat
    return "unknown"


# ─────────────────────────────────────────────
#  MAIN STRUCTURE BUILDER
# ─────────────────────────────────────────────

def build_knowledge_base(parsed_dir):
    """
    Load all parsed JSONs and build the knowledge base structure.
    """
    knowledge_base = {
        "metadata": {
            "collection"  : "kubernetes.core",
            "generated_at": datetime.now().isoformat(),
            "total_modules": 0,
            "source"      : "https://docs.ansible.com/ansible/latest/collections/kubernetes/core/",
        },
        "modules": {}
    }

    json_files = sorted([
        f for f in os.listdir(parsed_dir)
        if f.endswith(".json")
    ])

    print(f"\n  Found {len(json_files)} parsed JSON files.\n")

    for filename in json_files:
        slug     = filename.replace(".json", "")
        filepath = os.path.join(parsed_dir, filename)

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Clean parameters
        cleaned_params = [clean_parameter(p) for p in data.get("parameters", [])]

        # Build module entry
        module_entry = {
            "module"         : data.get("module", ""),
            "slug"           : slug,
            "category"       : get_category(slug),
            "description"    : clean_description(data.get("description", "")),
            "required_params": get_required_params(cleaned_params),
            "parameters"     : cleaned_params,
            "examples"       : data.get("examples", []),
            "return_values"  : data.get("return_values", []),
            "task_keywords"  : TASK_KEYWORDS.get(slug, []),
            "source_url"     : data.get("source_url", ""),
        }

        knowledge_base["modules"][slug] = module_entry

        param_count = len(cleaned_params)
        req_count   = len(module_entry["required_params"])
        kw_count    = len(module_entry["task_keywords"])

        print(f"  [OK]  {slug}")
        print(f"         params={param_count}  "
              f"required={module_entry['required_params']}  "
              f"keywords={kw_count}")

    knowledge_base["metadata"]["total_modules"] = len(knowledge_base["modules"])
    return knowledge_base


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Ansible kubernetes.core — Phase 3 Structurer")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    os.makedirs("data", exist_ok=True)
    os.makedirs("reports", exist_ok=True)

    if not os.path.exists(INPUT_DIR):
        print(f"\n[ERROR] '{INPUT_DIR}/' not found.")
        print("  → Make sure you ran phase2_parser.py first.")
        return

    # Build knowledge base
    kb = build_knowledge_base(INPUT_DIR)

    # Save knowledge_base.json
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(kb, f, indent=2, ensure_ascii=False)

    # Build & save report
    report = {
        "structure_date": datetime.now().isoformat(),
        "output_file"   : OUTPUT_FILE,
        "total_modules" : kb["metadata"]["total_modules"],
        "modules"       : [
            {
                "slug"           : slug,
                "module"         : entry["module"],
                "category"       : entry["category"],
                "param_count"    : len(entry["parameters"]),
                "required_params": entry["required_params"],
                "example_count"  : len(entry["examples"]),
                "keyword_count"  : len(entry["task_keywords"]),
            }
            for slug, entry in kb["modules"].items()
        ]
    }

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Summary by category
    helm_mods = [s for s, e in kb["modules"].items() if e["category"] == "helm"]
    k8s_mods  = [s for s, e in kb["modules"].items() if e["category"] == "k8s"]

    print(f"""
{'=' * 60}
  STRUCTURING COMPLETE

  Total modules : {kb['metadata']['total_modules']}
  k8s modules   : {len(k8s_mods)}
  helm modules  : {len(helm_mods)}

  Knowledge base → {OUTPUT_FILE}
  Report         → {REPORT_FILE}

  Next step      → run phase4_generator.py
{'=' * 60}
""")


if __name__ == "__main__":
    main()
