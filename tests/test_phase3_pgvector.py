"""
Tests for Phase 3: pgvector vector store, embeddings client, and cache invalidation.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────
#  Embeddings client
# ─────────────────────────────────────────────


class TestEmbeddingsClient:
    """Tests for rag/embeddings.py"""

    def test_embed_texts_returns_numpy_array(self, monkeypatch):
        """embed_texts should return an (N, D) float32 numpy array."""
        fake_response = {
            "data": [
                {"index": 0, "embedding": [0.1] * 768},
                {"index": 1, "embedding": [0.2] * 768},
            ]
        }

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = fake_response
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp

        from rag import embeddings

        monkeypatch.setattr(embeddings, "_client", mock_client)

        result = embeddings.embed_texts(["hello", "world"])
        assert result.shape == (2, 768)
        assert result.dtype == np.float32

    def test_embed_query_returns_1d(self, monkeypatch):
        """embed_query should return a (D,) array for a single query."""
        fake_response = {
            "data": [{"index": 0, "embedding": [0.5] * 768}]
        }

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = fake_response
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp

        from rag import embeddings

        monkeypatch.setattr(embeddings, "_client", mock_client)

        result = embeddings.embed_query("test")
        assert result.shape == (768,)

    def test_embed_texts_batches_correctly(self, monkeypatch):
        """Large inputs should be split into batches."""
        call_count = {"n": 0}

        def fake_post(url, json=None, **kwargs):
            call_count["n"] += 1
            batch_size = len(json["input"])
            resp = MagicMock()
            resp.json.return_value = {
                "data": [{"index": i, "embedding": [0.1] * 768} for i in range(batch_size)]
            }
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = MagicMock()
        mock_client.post = fake_post

        from rag import embeddings

        monkeypatch.setattr(embeddings, "_client", mock_client)

        texts = [f"text_{i}" for i in range(150)]
        result = embeddings.embed_texts(texts, batch_size=64)
        assert result.shape == (150, 768)
        assert call_count["n"] == 3  # 64 + 64 + 22

    def test_check_health_returns_status(self, monkeypatch):
        """check_health should return {ok, model, dimensions}."""
        fake_response = {
            "data": [{"index": 0, "embedding": [0.1] * 768}]
        }

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = fake_response
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp

        from rag import embeddings

        monkeypatch.setattr(embeddings, "_client", mock_client)

        status = embeddings.check_health()
        assert status["ok"] is True
        assert status["dimensions"] == 768

    def test_check_health_handles_failure(self, monkeypatch):
        """check_health should return {ok: False} on exception."""
        mock_client = MagicMock()
        mock_client.post.side_effect = Exception("connection refused")

        from rag import embeddings

        monkeypatch.setattr(embeddings, "_client", mock_client)

        status = embeddings.check_health()
        assert status["ok"] is False
        assert "connection refused" in status["error"]


# ─────────────────────────────────────────────
#  Vector store filter translation
# ─────────────────────────────────────────────


class TestFilterTranslation:
    """Tests for the Chroma→SQLAlchemy filter translation.

    These tests just verify the filter builder produces SQLAlchemy
    conditions, not that they execute correctly (that requires Postgres).
    """

    @pytest.fixture(autouse=True)
    def _skip_db(self):
        """These tests don't need the DB fixture."""
        pass

    def test_eq_filter(self):
        from rag.vectorstore import _build_filter

        condition = _build_filter({"collection": {"$eq": "kubernetes.core"}})
        assert condition is not None

    def test_in_filter(self):
        from rag.vectorstore import _build_filter

        condition = _build_filter({"collection": {"$in": ["a", "b"]}})
        assert condition is not None

    def test_contains_filter(self):
        from rag.vectorstore import _build_filter

        condition = _build_filter({"module": {"$contains": "ec2"}})
        assert condition is not None

    def test_and_filter(self):
        from rag.vectorstore import _build_filter

        condition = _build_filter({
            "$and": [
                {"collection": {"$eq": "amazon.aws"}},
                {"module": {"$contains": "ec2"}},
            ]
        })
        assert condition is not None

    def test_empty_filter_returns_none(self):
        from rag.vectorstore import _build_filter

        condition = _build_filter({"$and": []})
        assert condition is None


# ─────────────────────────────────────────────
#  Cache invalidation
# ─────────────────────────────────────────────


class TestInvalidation:
    """Tests for rag/invalidation.py"""

    def test_publish_invalidation_calls_redis_publish(self, monkeypatch):
        """publish_invalidation should publish to the channel."""
        mock_redis = MagicMock()
        mock_redis.publish.return_value = 2

        with patch("redis.from_url", return_value=mock_redis):
            from rag.invalidation import publish_invalidation

            result = publish_invalidation()
            assert result is True
            mock_redis.publish.assert_called_once()

    def test_publish_returns_false_on_failure(self, monkeypatch):
        """publish_invalidation should return False on Redis failure."""
        with patch("redis.from_url", side_effect=Exception("conn refused")):
            from rag.invalidation import publish_invalidation

            result = publish_invalidation()
            assert result is False

    def test_on_invalidation_clears_caches(self, monkeypatch):
        """The invalidation handler should clear all caches."""
        cleared = {"tools": False, "sparse": False, "collections": False}

        monkeypatch.setattr(
            "agent.tools.invalidate_caches",
            lambda: cleared.update({"tools": True}),
        )
        monkeypatch.setattr(
            "rag.sparse_index.reset_cache",
            lambda: cleared.update({"sparse": True}),
        )
        monkeypatch.setattr(
            "agent.collections.reload_collection_allowlist",
            lambda: cleared.update({"collections": True}) or frozenset(),
        )

        from rag.invalidation import _on_invalidation_message

        _on_invalidation_message({"type": "message", "data": "invalidate"})
        assert all(cleared.values())


# ─────────────────────────────────────────────
#  Schema compatibility
# ─────────────────────────────────────────────


class TestSchemaCompatibility:
    """Tests for vectorstore schema version checks."""

    def test_no_mismatches_when_empty(self, monkeypatch):
        """No mismatches when no meta is stored yet."""
        monkeypatch.setattr(
            "rag.vectorstore.get_index_meta",
            lambda key: None,
        )
        from rag.vectorstore import check_schema_compatibility

        mismatches = check_schema_compatibility()
        assert mismatches == []

    def test_detects_model_mismatch(self, monkeypatch):
        """Should report a mismatch when embed model differs."""
        def fake_meta(key):
            if key == "embed_model":
                return "old-model"
            return None

        monkeypatch.setattr("rag.vectorstore.get_index_meta", fake_meta)
        from rag.vectorstore import check_schema_compatibility

        mismatches = check_schema_compatibility()
        assert any("embed_model" in m for m in mismatches)


# ─────────────────────────────────────────────
#  Indexer doc ID generation
# ─────────────────────────────────────────────


class TestDocIdGeneration:
    """Tests for deterministic doc IDs in the indexer."""

    def test_build_doc_id_deterministic(self):
        from langchain_core.documents import Document
        from rag.indexer import _build_doc_id

        doc = Document(
            page_content="Test content",
            metadata={
                "collection": "kubernetes.core",
                "slug": "k8s_module",
                "chunk_type": "overview",
                "overview_part": "0",
            },
        )
        id1 = _build_doc_id(doc, 0)
        id2 = _build_doc_id(doc, 0)
        assert id1 == id2
        assert "kubernetes_core" in id1
        assert "overview_0" in id1

    def test_build_doc_id_example_suffix(self):
        from langchain_core.documents import Document
        from rag.indexer import _build_doc_id

        doc = Document(
            page_content="- name: example",
            metadata={
                "collection": "amazon.aws",
                "slug": "ec2_instance",
                "chunk_type": "example",
                "example_index": "1",
                "example_part": "0",
            },
        )
        doc_id = _build_doc_id(doc, 5)
        assert "example_1_0" in doc_id


# ─────────────────────────────────────────────
#  Docker compose assertions
# ─────────────────────────────────────────────


class TestComposePhase3:
    """Compose-level assertions for the Phase 3 migration."""

    @pytest.fixture()
    def compose(self) -> dict[str, Any]:
        import yaml

        return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    def test_db_uses_pgvector_image(self, compose):
        assert "pgvector" in compose["services"]["db"]["image"]

    def test_database_url_is_postgresql(self, compose):
        env = compose.get("x-app-env", {})
        db_url = env.get("DATABASE_URL", "")
        assert "postgresql" in db_url

    def test_no_chromadb_volumes(self, compose):
        for svc_name, svc in compose["services"].items():
            volumes = svc.get("volumes") or []
            for vol in volumes:
                if isinstance(vol, str):
                    assert "chromadb" not in vol, f"{svc_name} still mounts chromadb"

    def test_embedding_env_vars_present(self, compose):
        env = compose.get("x-app-env", {})
        assert "EMBEDDING_BASE_URL" in env
        assert "EMBEDDING_MODEL" in env
        assert "EMBEDDING_DIMENSIONS" in env


# ─────────────────────────────────────────────
#  Requirements assertions
# ─────────────────────────────────────────────


class TestRequirementsPhase3:
    """requirements.txt should reflect the pgvector migration."""

    @pytest.fixture()
    def reqs(self) -> str:
        return (ROOT / "requirements.txt").read_text(encoding="utf-8")

    def test_chromadb_removed(self, reqs):
        for line in reqs.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "chromadb" not in stripped, f"chromadb still listed: {stripped}"

    def test_langchain_chroma_removed(self, reqs):
        assert "langchain-chroma" not in reqs

    def test_langchain_ollama_removed(self, reqs):
        assert "langchain-ollama" not in reqs

    def test_psycopg2_present(self, reqs):
        assert "psycopg2" in reqs

    def test_pgvector_present(self, reqs):
        assert "pgvector" in reqs

    def test_httpx_present(self, reqs):
        assert "httpx" in reqs

    def test_pymysql_removed(self, reqs):
        assert "pymysql" not in reqs
