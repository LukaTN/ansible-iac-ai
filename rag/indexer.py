"""
=============================================================
  AnsibleAI RAG — Step 2 : Indexer
  Embeds documents and stores in ChromaDB via LangChain.
=============================================================
  Usage:
    python rag/indexer.py                  # index all collections
    python rag/indexer.py --reset          # wipe and rebuild
    python rag/indexer.py --collection kubernetes.core
=============================================================
"""

import os
import argparse
import hashlib
import json
import sys
from datetime import datetime

FILE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(FILE_DIR)
os.chdir(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from langchain_chroma import Chroma
except ImportError:
    from langchain_community.vectorstores import Chroma

try:
    from langchain_ollama import OllamaEmbeddings
except ImportError:
    from langchain_community.embeddings import OllamaEmbeddings

from langchain_core.documents import Document

try:
    from rag.ingestion import load_all_collections, load_collection, CHUNK_SCHEMA_VERSION
except ImportError:
    from ingestion import load_all_collections, load_collection, CHUNK_SCHEMA_VERSION

CHROMA_DIR   = "data/chromadb"
COLLECTION   = "ansible_docs"
EMBED_MODEL  = "nomic-embed-text"
OLLAMA_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
BATCH_SIZE   = 100   # docs per ChromaDB batch
INDEX_SCHEMA_VERSION = "v2"
INDEX_META_FILE = os.path.join(CHROMA_DIR, "index_meta.json")


# ─────────────────────────────────────────────
#  EMBEDDINGS
# ─────────────────────────────────────────────

def get_embeddings() -> OllamaEmbeddings:
    """Return LangChain OllamaEmbeddings for nomic-embed-text."""
    return OllamaEmbeddings(
        model=EMBED_MODEL,
        base_url=OLLAMA_URL,
    )


# ─────────────────────────────────────────────
#  VECTOR STORE
# ─────────────────────────────────────────────

def get_vectorstore(embeddings: OllamaEmbeddings, reset: bool = False) -> Chroma:
    """
    Initialize or load the ChromaDB vector store.
    If reset=True, delete and recreate the collection.
    """
    if reset and os.path.exists(CHROMA_DIR):
        import shutil
        shutil.rmtree(CHROMA_DIR)
        print("  [ChromaDB] Collection wiped.")

    os.makedirs(CHROMA_DIR, exist_ok=True)

    vectorstore = Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=CHROMA_DIR,
        collection_metadata={"hnsw:space": "cosine"},
    )
    return vectorstore


def _build_doc_id(doc: Document, fallback_index: int) -> str:
    """Build a deterministic and unique ID for each document chunk."""
    meta = doc.metadata or {}
    coll = str(meta.get("collection", "unknown")).replace(".", "_")
    slug = str(meta.get("slug", f"doc_{fallback_index}"))
    ctype = str(meta.get("chunk_type", "chunk"))

    # Prefer explicit chunk indexes when available.
    if "example_index" in meta:
        suffix = f"example_{meta.get('example_index')}_{meta.get('example_part', '0')}"
    elif "optional_group_index" in meta:
        suffix = f"optgrp_{meta.get('optional_group_index')}"
    elif "required_part" in meta and ctype == "required_params":
        suffix = f"required_{meta.get('required_part')}"
    elif "overview_part" in meta and ctype == "overview":
        suffix = f"overview_{meta.get('overview_part')}"
    elif "required_params_list" in meta and ctype == "required_params":
        suffix = "required"
    else:
        digest = hashlib.md5(doc.page_content.encode("utf-8")).hexdigest()[:10]
        suffix = digest

    return f"{coll}::{slug}::{ctype}::{suffix}"


def _read_index_meta() -> dict | None:
    if not os.path.exists(INDEX_META_FILE):
        return None
    with open(INDEX_META_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_index_meta(collection_name: str | None, total_docs: int):
    meta = {
        "indexed_at": datetime.now().isoformat(),
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "embed_model": EMBED_MODEL,
        "collection": collection_name or "all",
        "total_docs": total_docs,
    }
    with open(INDEX_META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _validate_index_compatibility(reset: bool):
    existing = _read_index_meta()
    if not existing:
        return

    mismatches = []
    if existing.get("embed_model") != EMBED_MODEL:
        mismatches.append(f"embed_model: {existing.get('embed_model')} -> {EMBED_MODEL}")
    if existing.get("chunk_schema_version") != CHUNK_SCHEMA_VERSION:
        mismatches.append(
            f"chunk_schema_version: {existing.get('chunk_schema_version')} -> {CHUNK_SCHEMA_VERSION}"
        )
    if existing.get("index_schema_version") != INDEX_SCHEMA_VERSION:
        mismatches.append(
            f"index_schema_version: {existing.get('index_schema_version')} -> {INDEX_SCHEMA_VERSION}"
        )

    if mismatches and not reset:
        raise ValueError(
            "Index compatibility mismatch detected:\n"
            + "\n".join(f"- {m}" for m in mismatches)
            + "\nRun with --reset to rebuild ChromaDB with consistent embeddings/chunk schema."
        )


def index_documents(
    docs: list[Document],
    vectorstore: Chroma,
    batch_size: int = BATCH_SIZE
) -> int:
    """
    Add documents to ChromaDB in batches.
    Skips documents already indexed (by ID).
    Returns count of new documents added.
    """
    # Build deterministic unique IDs from metadata/content
    ids = [_build_doc_id(doc, i) for i, doc in enumerate(docs)]

    # Check which IDs already exist
    existing = set()
    try:
        result = vectorstore._collection.get(ids=ids)
        existing = set(result["ids"])
    except Exception:
        pass

    new_docs = [(doc, id_) for doc, id_ in zip(docs, ids) if id_ not in existing]

    if not new_docs:
        print(f"  All {len(docs)} documents already indexed.")
        return 0

    print(f"  Indexing {len(new_docs)} new documents ({len(existing)} already exist)...")

    added = 0
    for i in range(0, len(new_docs), batch_size):
        batch = new_docs[i:i + batch_size]
        b_docs, b_ids = zip(*batch)

        # Ensure all metadata values are strings (ChromaDB requirement)
        clean_docs = []
        for doc in b_docs:
            clean_meta = {k: str(v) for k, v in doc.metadata.items()}
            clean_docs.append(Document(
                page_content=doc.page_content,
                metadata=clean_meta
            ))

        vectorstore.add_documents(documents=clean_docs, ids=list(b_ids))
        added += len(batch)
        pct = round(added / len(new_docs) * 100)
        print(f"    [{pct:>3}%] {added}/{len(new_docs)} indexed", end="\r")

    print(f"\n  ✓ {added} new documents added to ChromaDB.")
    return added


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def build_index(collection_name: str = None, reset: bool = False) -> Chroma:
    """
    Full indexing pipeline.
    Returns the initialized vectorstore.
    """
    print(f"\n{'='*60}")
    print(f"  AnsibleAI RAG — Indexer")
    print(f"  Embed model : {EMBED_MODEL}")
    print(f"  Vector store: ChromaDB @ {CHROMA_DIR}")
    print(f"  Started     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if not reset:
        _validate_index_compatibility(reset=False)

    # 1. Load documents
    print(f"\n  [1/3] Loading documents...")
    if collection_name:
        docs = load_collection(collection_name)
    else:
        docs = load_all_collections()

    if not docs:
        raise ValueError("No documents to index.")

    # 2. Init embeddings + vectorstore
    print(f"\n  [2/3] Initializing ChromaDB + OllamaEmbeddings...")
    print(f"  → Pulling {EMBED_MODEL} if needed (first time ~274MB)...")
    embeddings  = get_embeddings()
    vectorstore = get_vectorstore(embeddings, reset=reset)

    current_count = vectorstore._collection.count()
    print(f"  → Current DB size: {current_count} documents")

    # 3. Index
    print(f"\n  [3/3] Indexing {len(docs)} documents...")
    added = index_documents(docs, vectorstore)

    final_count = vectorstore._collection.count()
    print(f"\n{'='*60}")
    print(f"  INDEXING COMPLETE")
    print(f"  New docs added : {added}")
    print(f"  Total in DB    : {final_count}")
    print(f"{'='*60}")

    # Save report
    os.makedirs("reports", exist_ok=True)
    report = {
        "indexed_at"    : datetime.now().isoformat(),
        "embed_model"   : EMBED_MODEL,
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "collection"    : collection_name or "all",
        "new_docs"      : added,
        "total_in_db"   : final_count,
        "chroma_dir"    : CHROMA_DIR,
    }
    with open("reports/indexing_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"  Report → reports/indexing_report.json")
    _write_index_meta(collection_name=collection_name, total_docs=final_count)

    return vectorstore


def load_vectorstore() -> Chroma:
    """Load existing vectorstore (no re-indexing)."""
    if not os.path.exists(CHROMA_DIR):
        raise FileNotFoundError(
            f"ChromaDB not found at '{CHROMA_DIR}'.\n"
            "→ Run: python rag/indexer.py"
        )
    embeddings = get_embeddings()
    return Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=CHROMA_DIR,
        collection_metadata={"hnsw:space": "cosine"},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AnsibleAI RAG Indexer")
    parser.add_argument("--collection", type=str, default=None)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    build_index(collection_name=args.collection, reset=args.reset)
