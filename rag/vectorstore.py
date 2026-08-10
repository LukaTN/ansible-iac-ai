"""
=============================================================
  AnsibleAI RAG — pgvector vector store

  Replaces ChromaDB with Postgres + pgvector. The embedding table
  lives in the same database as the application, backed up by
  CloudNativePG PITR (Phase 8). At ~8,000 chunks the IVFFlat index
  is overkill; HNSW gives sub-millisecond recall with no nlist
  tuning, and the index is small enough to fit in shared_buffers.

  Query interface mirrors what the retriever expects:
    - similarity_search_with_scores(query_embedding, k, filter)
    - get_all_documents()  (for the BM25 sparse index)
    - count()
=============================================================
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
import structlog
from langchain_core.documents import Document
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    Index,
    Integer,
    String,
    Text,
    and_,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from config import settings
from models import db

log = structlog.get_logger(__name__)

_EMBEDDING_DIM = settings.embedding_dimensions


class DocumentChunk(db.Model):
    """A single indexed chunk with its embedding vector."""

    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True)
    doc_id = Column(String(255), unique=True, nullable=False, index=True)
    collection_name = Column(String(128), nullable=False, default="ansible_docs")
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    embedding = Column(Vector(_EMBEDDING_DIM), nullable=False)

    __table_args__ = (
        Index(
            "ix_chunks_collection",
            "collection_name",
        ),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 128},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        {"extend_existing": True},
    )

    def to_document(self) -> Document:
        return Document(
            page_content=self.content,
            metadata=self.metadata_ or {},
        )


class IndexMeta(db.Model):
    """Tracks the schema version of the indexed vectors."""

    __tablename__ = "index_meta"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False)
    value = Column(Text, nullable=False)


# ─────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    document: Document
    score: float


def count(collection: str | None = None) -> int:
    """Number of chunks in the store."""
    q = select(func.count(DocumentChunk.id))
    if collection:
        q = q.where(DocumentChunk.collection_name == collection)
    return db.session.execute(q).scalar() or 0


def similarity_search_with_scores(
    query_embedding: np.ndarray,
    k: int = 8,
    *,
    filter: dict[str, Any] | None = None,
) -> list[tuple[Document, float]]:
    """
    Cosine similarity search against pgvector.

    `filter` supports the same keys as Chroma metadata filters:
      {"collection": {"$eq": "kubernetes.core"}}
      {"collection": {"$in": ["a", "b"]}}
      {"module": {"$eq": "..."}}
      {"module": {"$contains": "..."}}
      {"$and": [...]}
    """
    vec = query_embedding.tolist() if isinstance(query_embedding, np.ndarray) else query_embedding

    distance_expr = DocumentChunk.embedding.cosine_distance(vec)
    score_expr = (1 - distance_expr).label("score")

    q = (
        select(DocumentChunk, score_expr)
        .order_by(distance_expr)
        .limit(k)
    )

    if filter:
        conditions = _build_filter(filter)
        if conditions is not None:
            q = q.where(conditions)

    rows = db.session.execute(q).all()

    results = []
    for chunk, score in rows:
        doc = chunk.to_document()
        results.append((doc, float(score)))
    return results


def get_all_documents(collection: str | None = None) -> list[Document]:
    """Fetch every chunk as a Document (for BM25 index build)."""
    q = select(DocumentChunk)
    if collection:
        q = q.where(DocumentChunk.collection_name == collection)
    chunks = db.session.execute(q).scalars().all()
    return [c.to_document() for c in chunks]


def upsert_documents(
    doc_ids: list[str],
    documents: list[Document],
    embeddings: np.ndarray,
    collection: str = "ansible_docs",
) -> int:
    """
    Batch upsert chunks. Uses INSERT ... ON CONFLICT UPDATE.
    Returns count of rows affected.
    """
    if not doc_ids:
        return 0

    affected = 0
    for i, (did, doc, emb) in enumerate(zip(doc_ids, documents, embeddings)):
        vec = emb.tolist() if isinstance(emb, np.ndarray) else emb
        meta = doc.metadata or {}
        existing = db.session.execute(
            select(DocumentChunk).where(DocumentChunk.doc_id == did)
        ).scalar_one_or_none()

        if existing:
            existing.content = doc.page_content
            existing.metadata_ = meta
            existing.embedding = vec
            existing.collection_name = collection
        else:
            chunk = DocumentChunk(
                doc_id=did,
                collection_name=collection,
                content=doc.page_content,
                metadata_=meta,
                embedding=vec,
            )
            db.session.add(chunk)
        affected += 1

        if (i + 1) % 200 == 0:
            db.session.flush()

    db.session.commit()
    return affected


def delete_collection(collection: str) -> int:
    """Remove all chunks for a collection. Returns count deleted."""
    result = db.session.execute(
        delete(DocumentChunk).where(DocumentChunk.collection_name == collection)
    )
    db.session.commit()
    return result.rowcount  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────
#  Index metadata (schema version tracking)
# ─────────────────────────────────────────────────────────────────

def get_index_meta(key: str) -> str | None:
    row = db.session.execute(
        select(IndexMeta.value).where(IndexMeta.key == key)
    ).scalar_one_or_none()
    return row


def set_index_meta(key: str, value: str) -> None:
    existing = db.session.execute(
        select(IndexMeta).where(IndexMeta.key == key)
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.session.add(IndexMeta(key=key, value=value))
    db.session.commit()


def check_schema_compatibility() -> list[str]:
    """
    Compare stored vs running schema versions.
    Returns a list of mismatch descriptions (empty = compatible).
    """
    from rag.ingestion import CHUNK_SCHEMA_VERSION

    mismatches = []

    stored_index = get_index_meta("index_schema_version")
    if stored_index and stored_index != settings.vector_index_version:
        mismatches.append(
            f"index_schema_version: stored={stored_index} running={settings.vector_index_version}"
        )

    stored_chunk = get_index_meta("chunk_schema_version")
    if stored_chunk and stored_chunk != CHUNK_SCHEMA_VERSION:
        mismatches.append(
            f"chunk_schema_version: stored={stored_chunk} running={CHUNK_SCHEMA_VERSION}"
        )

    stored_model = get_index_meta("embed_model")
    if stored_model and stored_model != settings.embedding_model:
        mismatches.append(
            f"embed_model: stored={stored_model} running={settings.embedding_model}"
        )

    stored_dim = get_index_meta("embedding_dimensions")
    if stored_dim and int(stored_dim) != settings.embedding_dimensions:
        mismatches.append(
            f"embedding_dimensions: stored={stored_dim} running={settings.embedding_dimensions}"
        )

    return mismatches


# ─────────────────────────────────────────────────────────────────
#  Filter translation (Chroma-style → SQLAlchemy)
# ─────────────────────────────────────────────────────────────────

def _build_filter(f: dict) -> Any:
    """Translate a Chroma-style metadata filter dict to SQLAlchemy conditions."""
    if "$and" in f:
        parts = [_build_filter(sub) for sub in f["$and"] if sub]
        parts = [p for p in parts if p is not None]
        return and_(*parts) if parts else None

    for key, val in f.items():
        if key.startswith("$"):
            continue
        if isinstance(val, dict):
            if "$eq" in val:
                return DocumentChunk.metadata_[key].astext == str(val["$eq"])
            if "$in" in val:
                return DocumentChunk.metadata_[key].astext.in_([str(v) for v in val["$in"]])
            if "$contains" in val:
                return DocumentChunk.metadata_[key].astext.contains(str(val["$contains"]))
        else:
            return DocumentChunk.metadata_[key].astext == str(val)
    return None


__all__ = [
    "DocumentChunk",
    "IndexMeta",
    "SearchResult",
    "check_schema_compatibility",
    "count",
    "delete_collection",
    "get_all_documents",
    "get_index_meta",
    "set_index_meta",
    "similarity_search_with_scores",
    "upsert_documents",
]
