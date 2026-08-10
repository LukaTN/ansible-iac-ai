"""
=============================================================
  AnsibleAI RAG — Embeddings client

  OpenAI-compatible /v1/embeddings client that works with:
    - HuggingFace Text Embeddings Inference (TEI)
    - Ollama (>= 0.1.26, serves /v1/embeddings natively)
    - Any OpenAI-compatible embedding server

  Configuration (via config.py / env):
    EMBEDDING_BASE_URL    http://localhost:11434/v1 (Ollama) or TEI
    EMBEDDING_MODEL       nomic-embed-text
    EMBEDDING_DIMENSIONS  768
    EMBEDDING_BATCH_SIZE  64
    EMBEDDING_API_KEY     (optional, for authenticated endpoints)
=============================================================
"""

from __future__ import annotations

import time
from typing import Sequence

import httpx
import numpy as np
import structlog

from config import settings

log = structlog.get_logger(__name__)

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        base_url = settings.embedding_base_url.strip()
        if not base_url:
            base_url = settings.ollama_base_url.rstrip("/") + "/v1"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {settings.embedding_api_key}"
        _client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=60.0,
        )
    return _client


def embed_texts(
    texts: Sequence[str],
    *,
    model: str | None = None,
    batch_size: int | None = None,
) -> np.ndarray:
    """
    Embed a list of texts via an OpenAI-compatible /v1/embeddings endpoint.

    Returns an (N, D) float32 numpy array. Batches internally to stay
    within server token limits.
    """
    model = model or settings.embedding_model
    batch_size = batch_size or settings.embedding_batch_size
    client = _get_client()

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = list(texts[i : i + batch_size])
        t0 = time.perf_counter()

        resp = client.post(
            "/embeddings",
            json={"input": batch, "model": model},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        batch_vecs = [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]
        all_embeddings.extend(batch_vecs)

        log.debug(
            "embedding.batch",
            batch_idx=i // batch_size,
            size=len(batch),
            elapsed_ms=round((time.perf_counter() - t0) * 1000),
        )

    return np.array(all_embeddings, dtype=np.float32)


def embed_query(text: str, *, model: str | None = None) -> np.ndarray:
    """Embed a single query. Returns a (D,) float32 array."""
    result = embed_texts([text], model=model)
    return result[0]


def check_health() -> dict:
    """Probe the embedding server. Returns status dict."""
    try:
        client = _get_client()
        resp = client.post(
            "/embeddings",
            json={"input": ["health check"], "model": settings.embedding_model},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        dim = len(data[0]["embedding"])
        return {"ok": True, "model": settings.embedding_model, "dimensions": dim}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def reset_client() -> None:
    """Drop the cached client (for testing or config reload)."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


__all__ = ["check_health", "embed_query", "embed_texts", "reset_client"]
