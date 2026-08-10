"""
=============================================================
  AnsibleAI RAG — Step 2: Indexer (pgvector)

  Embeds documents and upserts into Postgres via pgvector.
  Replaces the ChromaDB-based indexer from Phase 0–2.

  Usage:
    python rag/indexer.py                  # index all collections
    python rag/indexer.py --reset          # wipe and rebuild
    python rag/indexer.py --collection kubernetes.core
=============================================================
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
from datetime import datetime

FILE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(FILE_DIR)
os.chdir(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from langchain_core.documents import Document

try:
    from rag.ingestion import CHUNK_SCHEMA_VERSION, load_all_collections, load_collection
except ImportError:
    from ingestion import CHUNK_SCHEMA_VERSION, load_all_collections, load_collection

from config import settings

EMBED_MODEL = settings.embedding_model
BATCH_SIZE = 100
INDEX_SCHEMA_VERSION = settings.vector_index_version
COLLECTION_NAME = settings.vector_collection


# ─────────────────────────────────────────────
#  Document ID generation (deterministic, same logic as before)
# ─────────────────────────────────────────────

def _build_doc_id(doc: Document, fallback_index: int) -> str:
    """Build a deterministic and unique ID for each document chunk."""
    meta = doc.metadata or {}
    coll = str(meta.get("collection", "unknown")).replace(".", "_")
    slug = str(meta.get("slug", f"doc_{fallback_index}"))
    ctype = str(meta.get("chunk_type", "chunk"))

    if "example_index" in meta:
        suffix = f"example_{meta.get('example_index')}_{meta.get('example_part', '0')}"
    elif "optional_group_index" in meta:
        suffix = f"optgrp_{meta.get('optional_group_index')}"
    elif "required_part" in meta and ctype == "required_params":
        suffix = f"required_{meta.get('required_part')}"
    elif "overview_part" in meta and ctype == "overview":
        suffix = f"overview_{meta.get('overview_part')}"
    elif "purpose_part" in meta and ctype == "purpose":
        suffix = f"purpose_{meta.get('purpose_part')}"
    elif "required_params_list" in meta and ctype == "required_params":
        suffix = "required"
    else:
        digest = hashlib.md5(
            doc.page_content.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:10]
        suffix = digest

    return f"{coll}::{slug}::{ctype}::{suffix}"


# ─────────────────────────────────────────────
#  Schema compatibility gate
# ─────────────────────────────────────────────

def _validate_index_compatibility(reset: bool) -> None:
    """Refuse to index if schema versions don't match (unless --reset)."""
    from rag.vectorstore import check_schema_compatibility

    mismatches = check_schema_compatibility()
    if mismatches and not reset:
        raise ValueError(
            "Index compatibility mismatch detected:\n"
            + "\n".join(f"  - {m}" for m in mismatches)
            + "\nRun with --reset to rebuild with consistent embeddings/chunk schema."
        )


# ─────────────────────────────────────────────
#  Indexing
# ─────────────────────────────────────────────

def index_documents(docs: list[Document], batch_size: int = BATCH_SIZE) -> int:
    """
    Embed and upsert documents into pgvector in batches.
    Returns count of new/updated documents.
    """
    from rag.embeddings import embed_texts
    from rag.vectorstore import upsert_documents

    ids = [_build_doc_id(doc, i) for i, doc in enumerate(docs)]

    total = 0
    for i in range(0, len(docs), batch_size):
        batch_docs = docs[i : i + batch_size]
        batch_ids = ids[i : i + batch_size]

        texts = [d.page_content for d in batch_docs]
        embeddings = embed_texts(texts)

        affected = upsert_documents(
            doc_ids=batch_ids,
            documents=batch_docs,
            embeddings=embeddings,
            collection=COLLECTION_NAME,
        )
        total += affected

        pct = round(min(i + batch_size, len(docs)) / len(docs) * 100)
        print(f"    [{pct:>3}%] {min(i + batch_size, len(docs))}/{len(docs)} indexed", end="\r")

    print(f"\n  Done: {total} documents upserted.")
    return total


def build_index(
    collection_name: str | None = None,
    reset: bool = False,
    parsed_dir: str | None = None,
) -> int:
    """
    Full indexing pipeline — embed and store in pgvector.
    Returns total document count in the store.
    """
    from app import app
    from rag.vectorstore import count, delete_collection, set_index_meta

    with app.app_context():
        print(f"\n{'=' * 60}")
        print("  AnsibleAI RAG — Indexer (pgvector)")
        print(f"  Embed model : {EMBED_MODEL}")
        print(f"  Vector store: Postgres pgvector")
        print(f"  Parsed root : {parsed_dir or settings.rag_parsed_dir}")
        print(f"  Started     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 60}")

        if not reset:
            _validate_index_compatibility(reset=False)

        if reset:
            deleted = delete_collection(COLLECTION_NAME)
            print(f"  [pgvector] Deleted {deleted} existing chunks.")

        # 1. Load documents
        print("\n  [1/3] Loading documents...")
        if collection_name:
            docs = load_collection(collection_name, parsed_dir=parsed_dir)
        else:
            docs = load_all_collections(parsed_dir=parsed_dir)

        if not docs:
            raise ValueError("No documents to index.")

        current = count(COLLECTION_NAME)
        print(f"  → Current DB size: {current} chunks")

        # 2. Embed and index
        print(f"\n  [2/3] Embedding + indexing {len(docs)} documents...")
        added = index_documents(docs)

        # 3. Write meta
        print("\n  [3/3] Updating index metadata...")
        set_index_meta("index_schema_version", INDEX_SCHEMA_VERSION)
        set_index_meta("chunk_schema_version", CHUNK_SCHEMA_VERSION)
        set_index_meta("embed_model", EMBED_MODEL)
        set_index_meta("embedding_dimensions", str(settings.embedding_dimensions))
        set_index_meta("indexed_at", datetime.now().isoformat())
        set_index_meta("collection", collection_name or "all")

        final = count(COLLECTION_NAME)
        print(f"\n{'=' * 60}")
        print("  INDEXING COMPLETE")
        print(f"  Docs upserted : {added}")
        print(f"  Total in DB   : {final}")
        print(f"{'=' * 60}")

        # Notify all pods to refresh their caches
        from rag.invalidation import publish_invalidation
        publish_invalidation()

        # Save report
        os.makedirs("reports", exist_ok=True)
        report = {
            "indexed_at": datetime.now().isoformat(),
            "embed_model": EMBED_MODEL,
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "collection": collection_name or "all",
            "docs_upserted": added,
            "total_in_db": final,
            "backend": "pgvector",
        }
        with open("reports/indexing_report.json", "w") as f:
            json.dump(report, f, indent=2)
        print("  Report → reports/indexing_report.json")

        return final


# ─────────────────────────────────────────────
#  Legacy compatibility
# ─────────────────────────────────────────────

def load_vectorstore():
    """
    Legacy entry point used by agent/tools.py.

    Returns a lightweight proxy that the retriever can call
    similarity_search_with_relevance_scores on.
    """
    return _PgVectorProxy()


class _PgVectorProxy:
    """
    Drop-in shim so the retriever's existing interface keeps working.

    Provides .similarity_search_with_relevance_scores() and .get()
    to match what the retriever and sparse_index expect from Chroma.
    """

    def similarity_search_with_relevance_scores(
        self,
        query: str,
        k: int = 8,
        *,
        filter: dict | None = None,
        **kwargs,
    ) -> list[tuple[Document, float]]:
        from rag.embeddings import embed_query
        from rag.vectorstore import similarity_search_with_scores

        query_vec = embed_query(query)
        return similarity_search_with_scores(query_vec, k=k, filter=filter)

    def get(self, include: list[str] | None = None) -> dict:
        """Fetch all documents (for BM25 sparse index construction)."""
        from rag.vectorstore import get_all_documents

        docs = get_all_documents()
        return {
            "documents": [d.page_content for d in docs],
            "metadatas": [d.metadata for d in docs],
            "ids": [str(i) for i in range(len(docs))],
        }

    class _collection_shim:
        @staticmethod
        def count() -> int:
            from rag.vectorstore import count
            return count()

    _collection = _collection_shim()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="AnsibleAI RAG Indexer (pgvector)")
    parser.add_argument("--collection", type=str, default=None)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--parsed-dir", type=str, default=None)
    args = parser.parse_args()
    build_index(collection_name=args.collection, reset=args.reset, parsed_dir=args.parsed_dir)
