"""
=============================================================
  AI-Powered IaC — Phase 3 : Structurer
  Input  : data/parsed/**/*.json
  Output : data/kb_manifest.json
=============================================================
"""

import os
import json
import re
from datetime import datetime
from kb_store import write_manifest

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INPUT_DIR = "data/parsed"
OUTPUT_FILE = "data/kb_manifest.json"
REPORT_FILE = "reports/structure_report.json"

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

CATEGORIES = {
    "k8s": [
        "k8s_module", "k8s_info_module", "k8s_cluster_info_module",
        "k8s_exec_module", "k8s_cp_module", "k8s_drain_module",
        "k8s_log_module", "k8s_scale_module", "k8s_rollback_module",
        "k8s_taint_module", "k8s_json_patch_module", "k8s_service_module"
    ],
    "helm": [
        "helm_module", "helm_info_module", "helm_repository_module",
        "helm_plugin_module", "helm_plugin_info_module", "helm_template_module"
    ],
}

BAD_DEFAULTS = {
    "to", "the", "a", "an", "is", "are", "be", "by",
    "in", "of", "or", "if", "it", "this", "that", "and",
    "false", "true",
}


def clean_default(value, choices):
    if not value:
        return ""
    v = value.strip().lower()
    if v in BAD_DEFAULTS:
        if len(choices) == 1:
            return choices[0]
        return ""
    return value


def clean_description(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


def clean_parameter(param):
    return {
        "name": param.get("name", ""),
        "aliases": param.get("aliases", []),
        "type": param.get("type", ""),
        "required": param.get("required", False),
        "default": clean_default(param.get("default", ""), param.get("choices", [])),
        "choices": param.get("choices", []),
        "description": clean_description(param.get("description", "")),
    }


def get_required_params(parameters):
    return [p["name"] for p in parameters if p.get("required")]


def get_category(slug, collection_name="", module_name=""):
    for cat, slugs in CATEGORIES.items():
        if slug in slugs:
            return cat
    module_short = (module_name or "").split(".")[-1]
    if collection_name == "kubernetes.core":
        return "helm" if module_short.startswith("helm") else "k8s"
    if collection_name.startswith("amazon."):
        return "aws"
    if collection_name.startswith("azure."):
        return "azure"
    if collection_name == "ansible.builtin":
        return "builtin"
    if collection_name.startswith("community."):
        return "community"
    if "." in collection_name:
        return collection_name.split(".", 1)[0]
    return "unknown"


def iter_parsed_json_files(parsed_dir):
    # Legacy flat layout: data/parsed/*.json
    for entry in sorted(os.listdir(parsed_dir)):
        p = os.path.join(parsed_dir, entry)
        if os.path.isfile(p) and p.endswith(".json"):
            yield p
        elif os.path.isdir(p):
            # New layout: data/parsed/<collection_ns>/*.json
            for fn in sorted(os.listdir(p)):
                fp = os.path.join(p, fn)
                if os.path.isfile(fp) and fp.endswith(".json"):
                    yield fp


def build_knowledge_base(parsed_dir):
    knowledge_base = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "total_modules": 0,
            "collections": [],
            "sources": {},
        },
        "modules": {},
    }

    json_paths = list(iter_parsed_json_files(parsed_dir))
    print(f"\n  Found {len(json_paths)} parsed JSON files.\n")

    collections_seen = set()

    for filepath in json_paths:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        slug = data.get("slug") or os.path.basename(filepath).replace(".json", "")
        collection_ns = data.get("collection_ns")
        collection_name = data.get("collection", "unknown.collection")

        if not collection_ns and "." in collection_name:
            collection_ns = collection_name.replace(".", "_")
        if not collection_ns:
            rel = os.path.relpath(filepath, parsed_dir).split(os.sep)
            collection_ns = rel[0] if len(rel) > 1 else "legacy"

        collections_seen.add(collection_name)
        knowledge_base["metadata"]["sources"][collection_name] = (
            "https://docs.ansible.com/ansible/latest/collections/"
            f"{collection_name.replace('.', '/')}/"
        )

        cleaned_params = [clean_parameter(p) for p in data.get("parameters", [])]
        module_key = f"{collection_ns}::{slug}"

        knowledge_base["modules"][module_key] = {
            "module": data.get("module", ""),
            "slug": slug,
            "collection": collection_name,
            "collection_ns": collection_ns,
            "category": get_category(slug, collection_name, data.get("module", "")),
            "description": clean_description(data.get("description", "")),
            "required_params": get_required_params(cleaned_params),
            "parameters": cleaned_params,
            "examples": data.get("examples", []),
            "return_values": data.get("return_values", []),
            "task_keywords": TASK_KEYWORDS.get(slug, []),
            "source_url": data.get("source_url", ""),
        }

        print(f"  [OK]  {collection_name}/{slug}")

    knowledge_base["metadata"]["collections"] = sorted(collections_seen)
    knowledge_base["metadata"]["total_modules"] = len(knowledge_base["modules"])
    return knowledge_base


def main():
    print("=" * 60)
    print("  Ansible Multi-Collection — Phase 3 Structurer")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    os.makedirs("data", exist_ok=True)
    os.makedirs("reports", exist_ok=True)

    if not os.path.exists(INPUT_DIR):
        print(f"\n[ERROR] '{INPUT_DIR}/' not found.")
        print("  -> Make sure you ran phase2_parser.py first.")
        return

    kb = build_knowledge_base(INPUT_DIR)

    manifest = write_manifest(kb, OUTPUT_FILE)

    report = {
        "structure_date": datetime.now().isoformat(),
        "output_file": OUTPUT_FILE,
        "total_modules": kb["metadata"]["total_modules"],
        "collections": kb["metadata"]["collections"],
        "modules_per_collection": manifest.get("modules_per_collection", {}),
    }

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("  STRUCTURING COMPLETE")
    print(f"  Total modules : {kb['metadata']['total_modules']}")
    print(f"  Collections   : {len(kb['metadata']['collections'])}")
    print(f"  Manifest       -> {OUTPUT_FILE}")
    print(f"  Report         -> {REPORT_FILE}")
    print("  Next step      -> run rag/indexer.py --build --reset")
    print("=" * 60)


if __name__ == "__main__":
    main()
