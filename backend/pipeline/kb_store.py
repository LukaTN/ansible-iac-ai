import json
import os
from datetime import datetime
from pathlib import Path

# backend/pipeline/kb_store.py → repository root. Paths must be absolute:
# importing validator/retriever used to chdir into backend/, and a relative
# "data/parsed" then resolved to backend/data/parsed (missing), so the
# production gate reported "No known collection module detected" for valid
# FQCNs such as azure.azcollection.azure_rm_virtualmachine.
_REPO_ROOT = Path(__file__).resolve().parents[2]
PARSED_DIR = str(_REPO_ROOT / "data" / "parsed")
LEGACY_KB_FILE = str(_REPO_ROOT / "data" / "knowledge_base.json")
MANIFEST_FILE = str(_REPO_ROOT / "data" / "kb_manifest.json")


def _iter_parsed_json_files(parsed_dir: str):
    for entry in sorted(os.listdir(parsed_dir)):
        p = os.path.join(parsed_dir, entry)
        if os.path.isfile(p) and p.endswith(".json"):
            yield p
            continue
        if os.path.isdir(p):
            for fn in sorted(os.listdir(p)):
                fp = os.path.join(p, fn)
                if os.path.isfile(fp) and fp.endswith(".json"):
                    yield fp


def _infer_collection(data: dict, filepath: str) -> tuple[str, str]:
    collection = data.get("collection")
    collection_ns = data.get("collection_ns")

    if collection and not collection_ns:
        collection_ns = collection.replace(".", "_")

    if not collection_ns:
        rel = os.path.relpath(filepath, PARSED_DIR).split(os.sep)
        collection_ns = rel[0] if len(rel) > 1 else "legacy"

    if not collection:
        collection = collection_ns.replace("_", ".", 1)

    return collection, collection_ns


def _build_module_entry(data: dict, collection: str, collection_ns: str, slug: str) -> dict:
    params = data.get("parameters", []) or []
    required = data.get("required_params")
    if required is None:
        required = [p.get("name") for p in params if p.get("required") and p.get("name")]

    module_name = data.get("module", "")
    module_short = module_name.split(".")[-1] if module_name else ""
    category = data.get("category")
    if not category:
        if collection == "kubernetes.core":
            category = "helm" if module_short.startswith("helm") else "k8s"
        elif collection.startswith("amazon."):
            category = "aws"
        elif collection.startswith("azure."):
            category = "azure"
        elif collection == "ansible.builtin":
            category = "builtin"
        elif collection.startswith("community."):
            category = "community"
        elif "." in collection:
            category = collection.split(".", 1)[0]
        else:
            category = "unknown"

    return {
        "module": data.get("module", ""),
        "slug": slug,
        "collection": collection,
        "collection_ns": collection_ns,
        "category": category,
        "description": data.get("description", ""),
        "required_params": required,
        "parameters": params,
        "examples": data.get("examples", []) or [],
        "return_values": data.get("return_values", []) or [],
        "task_keywords": data.get("task_keywords", []) or [],
        "source_url": data.get("source_url", ""),
    }


def load_kb_from_parsed(parsed_dir: str = PARSED_DIR) -> dict:
    modules = {}
    collections = set()

    if not os.path.exists(parsed_dir):
        return {"metadata": {"generated_at": datetime.utcnow().isoformat(), "total_modules": 0, "collections": []}, "modules": {}}

    for filepath in _iter_parsed_json_files(parsed_dir):
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        slug = data.get("slug") or os.path.basename(filepath).replace(".json", "")
        collection, collection_ns = _infer_collection(data, filepath)
        key = f"{collection_ns}::{slug}"
        modules[key] = _build_module_entry(data, collection, collection_ns, slug)
        collections.add(collection)

    return {
        "metadata": {
            "generated_at": datetime.utcnow().isoformat(),
            "source": "parsed_files",
            "total_modules": len(modules),
            "collections": sorted(collections),
        },
        "modules": modules,
    }


def load_knowledge_base(prefer_parsed: bool = True) -> dict:
    if prefer_parsed and os.path.exists(PARSED_DIR):
        kb = load_kb_from_parsed(PARSED_DIR)
        if kb.get("modules"):
            return kb

    if os.path.exists(LEGACY_KB_FILE):
        with open(LEGACY_KB_FILE, encoding="utf-8") as f:
            return json.load(f)

    return {"metadata": {"generated_at": datetime.utcnow().isoformat(), "total_modules": 0, "collections": []}, "modules": {}}


def write_manifest(kb: dict, manifest_path: str = MANIFEST_FILE):
    modules = kb.get("modules", {})
    by_collection = {}
    for entry in modules.values():
        coll = entry.get("collection", "unknown.collection")
        by_collection[coll] = by_collection.get(coll, 0) + 1

    manifest = {
        "generated_at": datetime.utcnow().isoformat(),
        "source": kb.get("metadata", {}).get("source", "parsed_files"),
        "total_modules": len(modules),
        "collections": sorted(by_collection.keys()),
        "modules_per_collection": by_collection,
    }
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest

