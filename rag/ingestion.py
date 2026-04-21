"""
=============================================================
  AnsibleAI RAG — Step 1 : Ingestion
  Loads parsed module JSONs into LangChain Documents
  with rich metadata for filtering and retrieval.
=============================================================
"""

import os
import json
from typing import List
from langchain_core.documents import Document

# Always run from project root
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PARSED_DIR  = "data/parsed"
KB_FILE     = "data/knowledge_base.json"   # legacy kubernetes.core fallback

CHUNK_SCHEMA_VERSION = "v2_section_overlap"
CHUNK_SIZE  = 900
CHUNK_OVERLAP = 140
SKIP_PARAMS = {     # params to exclude from context (noise)
    "api_key", "ca_cert", "client_cert", "client_key",
    "proxy", "proxy_headers", "basic_auth", "validate_certs",
    "host", "username", "password", "no_proxy", "user_agent",
}


def _split_with_overlap(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        part = text[start:end]
        chunks.append(part)
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


# ─────────────────────────────────────────────
#  DOCUMENT BUILDERS
#  Each function returns a list of LangChain Documents
#  from a single module dict.
# ─────────────────────────────────────────────

def _base_meta(module: dict, collection: str, chunk_type: str) -> dict:
    """Build shared metadata dict for a module chunk."""
    return {
        "collection" : collection,
        "module"     : module.get("module", ""),
        "slug"       : module.get("slug", ""),
        "category"   : module.get("category", "general"),
        "source_url" : module.get("source_url", ""),
        "chunk_type" : chunk_type,
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
    }


def build_overview_doc(module: dict, collection: str) -> List[Document]:
    """
    Chunk 1: Module overview.
    Combines description + keywords + category into one searchable chunk.
    """
    mod   = module.get("module", "")
    desc  = module.get("description", "")
    kws   = " | ".join(module.get("task_keywords", []))
    cat   = module.get("category", "general")
    req   = ", ".join(module.get("required_params", []))

    text = (
        f"Module: {mod}\n"
        f"Collection: {collection}\n"
        f"Category: {cat}\n"
        f"Description: {desc}\n"
        f"Use this module to: {kws}\n"
        f"Required parameters: {req}"
    )
    parts = _split_with_overlap(text)
    docs = []
    for i, part in enumerate(parts):
        meta = _base_meta(module, collection, "overview")
        meta["overview_part"] = str(i)
        docs.append(Document(page_content=part, metadata=meta))
    return docs


def build_required_params_doc(module: dict, collection: str) -> List[Document]:
    """
    Chunk 2: Required parameters (most important for generation quality).
    """
    params   = module.get("parameters", [])
    required = [p for p in params if p.get("required") and p["name"] not in SKIP_PARAMS]
    if not required:
        return []

    mod  = module.get("module", "")
    text = f"Module {mod} — REQUIRED parameters (must always be provided):\n"
    for p in required:
        text += (
            f"- {p['name']} ({p.get('type', 'any')}): "
            f"{p.get('description', '')[:150]}\n"
        )

    parts = _split_with_overlap(text)
    docs = []
    for i, part in enumerate(parts):
        meta = _base_meta(module, collection, "required_params")
        meta["required_params_list"] = ",".join(p["name"] for p in required)
        meta["required_part"] = str(i)
        docs.append(Document(page_content=part, metadata=meta))
    return docs


def build_optional_params_docs(module: dict, collection: str) -> List[Document]:
    """
    Chunk 3+: Optional parameters grouped by 5.
    """
    params   = module.get("parameters", [])
    optional = [
        p for p in params
        if not p.get("required") and p["name"] not in SKIP_PARAMS
    ]
    docs = []
    mod  = module.get("module", "")

    for i in range(0, len(optional), 5):
        group = optional[i:i + 5]
        text  = f"Module {mod} — optional parameters (group {i // 5 + 1}):\n"
        for p in group:
            default = f" (default: {p['default']})" if p.get("default") else ""
            choices = f" choices: {p['choices']}" if p.get("choices") else ""
            text += (
                f"- {p['name']} ({p.get('type', 'any')}){default}{choices}: "
                f"{p.get('description', '')[:100]}\n"
            )
        meta = _base_meta(module, collection, "optional_params")
        meta["optional_group_index"] = str(i // 5)
        docs.append(Document(page_content=text, metadata=meta))
    return docs


def build_example_docs(module: dict, collection: str) -> List[Document]:
    """
    Chunk 4+: YAML examples (most useful for generation).
    """
    docs = []
    mod  = module.get("module", "")

    for j, ex in enumerate(module.get("examples", [])[:3]):
        full = f"Example usage of {mod} (example {j + 1}):\n{ex}"
        parts = _split_with_overlap(full)
        for k, part in enumerate(parts):
            meta = _base_meta(module, collection, "example")
            meta["example_index"] = str(j)
            meta["example_part"] = str(k)
            docs.append(Document(page_content=part, metadata=meta))
    return docs


def module_to_documents(module: dict, collection: str) -> List[Document]:
    """Convert a single module dict into all its LangChain Documents."""
    docs = []

    docs.extend(build_overview_doc(module, collection))
    docs.extend(build_required_params_doc(module, collection))

    docs.extend(build_optional_params_docs(module, collection))
    docs.extend(build_example_docs(module, collection))

    return docs


# ─────────────────────────────────────────────
#  COLLECTION LOADERS
# ─────────────────────────────────────────────

def load_collection(collection_name: str) -> List[Document]:
    """
    Load all modules for a collection and convert to Documents.
    Supports both directory-based (parsed/) and legacy KB format.
    """
    ns         = collection_name.replace(".", "_")
    parsed_dir = os.path.join(PARSED_DIR, ns)
    documents  = []

    # ── Directory-based (new format) ──
    if os.path.exists(parsed_dir):
        files = sorted(f for f in os.listdir(parsed_dir) if f.endswith(".json"))
        print(f"  Loading {collection_name}: {len(files)} modules from {parsed_dir}")
        for fname in files:
            try:
                with open(os.path.join(parsed_dir, fname), encoding="utf-8") as f:
                    module = json.load(f)
                documents.extend(module_to_documents(module, collection_name))
            except Exception as e:
                print(f"    [WARN] Could not load {fname}: {e}")

    # ── Legacy KB format (kubernetes.core only) ──
    elif collection_name == "kubernetes.core" and os.path.exists(KB_FILE):
        print(f"  Loading kubernetes.core from {KB_FILE} (legacy)")
        with open(KB_FILE, encoding="utf-8") as f:
            kb = json.load(f)
        for module in kb["modules"].values():
            documents.extend(module_to_documents(module, collection_name))

    else:
        print(f"  [WARN] No data found for {collection_name} — run scraper first.")

    print(f"    → {len(documents)} documents created")
    return documents


def load_all_collections() -> List[Document]:
    """
    Auto-detect and load all available collections.
    Returns combined list of Documents.
    """
    collections = []

    # Detect from parsed/ subdirectories
    if os.path.exists(PARSED_DIR):
        for d in sorted(os.listdir(PARSED_DIR)):
            full = os.path.join(PARSED_DIR, d)
            if os.path.isdir(full) and any(f.endswith(".json") for f in os.listdir(full)):
                collections.append(d.replace("_", ".", 1))

    # Always include kubernetes.core via KB if not already found
    if "kubernetes.core" not in collections and os.path.exists(KB_FILE):
        collections.insert(0, "kubernetes.core")

    if not collections:
        raise FileNotFoundError(
            "No parsed data found.\n"
            "→ Run pipeline/phase1_scraper_multi.py + phase2_parser.py first."
        )

    print(f"\n  Collections found: {collections}")
    all_docs = []
    for coll in collections:
        all_docs.extend(load_collection(coll))

    print(f"\n  Total documents loaded: {len(all_docs)}")
    return all_docs


if __name__ == "__main__":
    docs = load_all_collections()
    print(f"\nSample document:")
    print(f"  Content: {docs[0].page_content[:200]}...")
    print(f"  Metadata: {docs[0].metadata}")