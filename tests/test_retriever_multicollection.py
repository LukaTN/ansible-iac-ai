"""Multi-collection retrieval: vector filter must be None when disabled."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document

from rag.retriever import (
    _retrieve_ranked,
    default_apply_auto_collection_filter_for_generation,
    get_retrieval_metadata,
)


def _stub_doc(module: str, collection: str, ctype: str = "overview") -> Document:
    return Document(
        page_content="x",
        metadata={"module": module, "collection": collection, "chunk_type": ctype},
    )


@pytest.fixture
def mock_vs_single_high_score():
    """Return one strong hit so reranking does not empty the list."""
    vs = MagicMock()

    def search(query, k, filter=None):
        return [
            (_stub_doc("amazon.aws.s3_bucket", "amazon.aws"), 0.95),
            (_stub_doc("kubernetes.core.k8s", "kubernetes.core"), 0.94),
        ]

    vs.similarity_search_with_relevance_scores.side_effect = search
    return vs


def test_retrieve_ranked_apply_auto_false_uses_no_chroma_filter():
    vs = MagicMock()
    seen: list = []
    docs = [
        (_stub_doc("amazon.aws.s3_bucket", "amazon.aws"), 0.95),
        (_stub_doc("kubernetes.core.k8s", "kubernetes.core"), 0.94),
    ]

    def capture(query, k, filter=None):
        seen.append(filter)
        return list(docs)

    vs.similarity_search_with_relevance_scores.side_effect = capture
    _retrieve_ranked(
        "create an aks cluster and kubernetes version",
        vs,
        top_k=4,
        collection_filter=None,
        apply_auto_collection_filter=False,
    )
    assert seen and seen[0] is None


def test_retrieve_ranked_explicit_collection_uses_eq_filter(mock_vs_single_high_score):
    seen = []

    def capture(query, k, filter=None):
        seen.append(filter)
        return [
            (_stub_doc("kubernetes.core.k8s", "kubernetes.core"), 0.95),
        ]

    mock_vs_single_high_score.similarity_search_with_relevance_scores.side_effect = capture
    _retrieve_ranked(
        "deploy nginx",
        mock_vs_single_high_score,
        top_k=4,
        collection_filter="kubernetes.core",
        apply_auto_collection_filter=True,
    )
    assert seen and seen[0] == {"collection": {"$eq": "kubernetes.core"}}


def test_get_retrieval_metadata_defaults_to_routing_on_when_env_unset(monkeypatch):
    monkeypatch.delenv("RAG_DISABLE_AUTO_COLLECTION_FILTER", raising=False)
    monkeypatch.delenv("RAG_APPLY_AUTO_COLLECTION_FILTER", raising=False)
    assert default_apply_auto_collection_filter_for_generation() is True

    vs = MagicMock()
    filters: list = []

    def capture(query, k, filter=None):
        filters.append(filter)
        return [
            (_stub_doc("amazon.aws.ec2_instance", "amazon.aws"), 0.95),
            (_stub_doc("amazon.aws.ec2_key", "amazon.aws"), 0.94),
        ]

    vs.similarity_search_with_relevance_scores.side_effect = capture
    # "ec2" routes single -> amazon.aws, so filter must be $eq
    get_retrieval_metadata("create an ec2 instance", vs, top_k=4)
    assert filters and filters[0] == {"collection": {"$eq": "amazon.aws"}}


def test_get_retrieval_metadata_disable_routing_via_env(monkeypatch):
    monkeypatch.setenv("RAG_DISABLE_AUTO_COLLECTION_FILTER", "1")
    assert default_apply_auto_collection_filter_for_generation() is False

    vs = MagicMock()
    filters: list = []

    def capture(query, k, filter=None):
        filters.append(filter)
        return [(_stub_doc("kubernetes.core.k8s", "kubernetes.core"), 0.95)]

    vs.similarity_search_with_relevance_scores.side_effect = capture
    get_retrieval_metadata("deploy nginx in k8s", vs, top_k=4)
    assert filters and filters[0] is None
