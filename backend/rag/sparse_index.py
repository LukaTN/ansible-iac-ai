"""
=============================================================
  AnsibleAI RAG — Sparse (BM25) index over the indexed chunks

  The dense embedding misses terms that never made it into the
  short module synopsis: a user asking to "format a block device
  with xfs" gets no vector pull toward `community.general.filesystem`,
  but its parameter list literally contains `fstype`, `dev` and
  `resizefs`. BM25 recovers exactly those cases, and fusing the two
  rankings beats either alone.

  Built from the Chroma collection itself, so it always reflects
  whatever is indexed and needs no separate ingestion step. The
  corpus is a few thousand short chunks, so it is held in memory
  and built once per process.
=============================================================
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter

from langchain_core.documents import Document

# Okapi BM25 constants. k1 controls term-frequency saturation, b controls
# length normalisation; these are the standard defaults and the corpus (short,
# uniform doc-chunks) gives no reason to depart from them.
K1 = 1.5
B = 0.75

_lock = threading.Lock()
_cached: SparseIndex | None = None
_cached_size: int = -1


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, plus the parts of snake_case names.

    ``ec2_instance`` yields ``ec2``, ``instance`` and ``ec2 instance`` never
    appears as one token, which is what lets "launch an instance" match.
    """
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 1]


class SparseIndex:
    """In-memory BM25 over (Document, tokens) pairs."""

    def __init__(self, docs: list[Document]):
        self.docs = docs
        self.corpus = [tokenize(self._searchable_text(d)) for d in docs]
        self.n = len(self.corpus)
        self.lengths = [len(t) for t in self.corpus]
        self.avg_len = (sum(self.lengths) / self.n) if self.n else 0.0
        self.tf: list[Counter] = [Counter(t) for t in self.corpus]

        df: Counter = Counter()
        for tokens in self.corpus:
            df.update(set(tokens))
        self.idf = {
            term: math.log(1 + (self.n - count + 0.5) / (count + 0.5))
            for term, count in df.items()
        }

        # term -> doc indices, so scoring touches only documents that can score.
        self.postings: dict[str, list[int]] = {}
        for i, tokens in enumerate(self.corpus):
            for term in set(tokens):
                self.postings.setdefault(term, []).append(i)

    @staticmethod
    def _searchable_text(doc: Document) -> str:
        """Chunk body plus its module name spelled out as words."""
        md = doc.metadata or {}
        module = str(md.get("module", ""))
        short_words = module.split(".")[-1].replace("_", " ") if module else ""
        return f"{module} {short_words} {doc.page_content or ''}"

    def search(
        self,
        query: str,
        k: int = 32,
        collections: list[str] | None = None,
    ) -> list[tuple[Document, float]]:
        """Top-k chunks by BM25, optionally restricted to given collections."""
        terms = tokenize(query)
        if not terms or not self.n:
            return []

        allowed = set(collections) if collections else None
        scores: dict[int, float] = {}

        for term in terms:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i in self.postings.get(term, ()):
                freq = self.tf[i].get(term, 0)
                if not freq:
                    continue
                norm = 1 - B + B * (self.lengths[i] / self.avg_len if self.avg_len else 1.0)
                scores[i] = scores.get(i, 0.0) + idf * (freq * (K1 + 1)) / (freq + K1 * norm)

        if allowed is not None:
            scores = {
                i: s for i, s in scores.items()
                if (self.docs[i].metadata or {}).get("collection") in allowed
            }
        if not scores:
            return []

        top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [(self.docs[i], score) for i, score in top]


def _load_documents(vectorstore) -> list[Document]:
    """Pull every indexed chunk out of the store as Documents."""
    got = vectorstore.get(include=["documents", "metadatas"])
    texts = got.get("documents") or []
    metas = got.get("metadatas") or []
    return [
        Document(page_content=text or "", metadata=meta or {})
        for text, meta in zip(texts, metas)
    ]


def get_sparse_index(vectorstore) -> SparseIndex | None:
    """
    Build (once) and return the BM25 index for the current vector store.

    Rebuilds if the collection size changed, so re-indexing during a long-lived
    process is picked up. Returns None if the store cannot be read, letting the
    retriever fall back to dense-only search rather than fail the request.
    """
    global _cached, _cached_size

    try:
        size = vectorstore._collection.count()
    except Exception:
        size = -1

    with _lock:
        if _cached is not None and size == _cached_size:
            return _cached
        try:
            docs = _load_documents(vectorstore)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"  [Sparse] Could not build BM25 index: {exc}")
            return None
        if not docs:
            return None
        _cached = SparseIndex(docs)
        _cached_size = size if size >= 0 else len(docs)
        print(f"  [Sparse] BM25 index built over {len(docs)} chunks")
        return _cached


def reset_cache() -> None:
    """Drop the cached index (tests, or after a re-index in the same process)."""
    global _cached, _cached_size
    with _lock:
        _cached = None
        _cached_size = -1
