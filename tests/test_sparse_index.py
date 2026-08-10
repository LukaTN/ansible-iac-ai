"""
Unit tests for the BM25 sparse index and its fusion into the retrieval pool.

These run without Ollama or a built vector store — the index is constructed
directly from Documents, and fusion is exercised against a stub store.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document

from rag.hybrid_search import fuse_with_sparse
from rag.sparse_index import SparseIndex, get_sparse_index, reset_cache, tokenize


def _doc(module: str, collection: str, text: str, chunk_type: str = "overview") -> Document:
    return Document(
        page_content=text,
        metadata={"module": module, "collection": collection, "chunk_type": chunk_type},
    )


@pytest.fixture
def corpus() -> list[Document]:
    return [
        _doc("community.general.filesystem", "community.general",
             "Module community.general.filesystem - optional parameters: fstype dev resizefs"),
        _doc("amazon.aws.s3_bucket", "amazon.aws",
             "Module amazon.aws.s3_bucket - create and manage S3 buckets with versioning"),
        _doc("ansible.builtin.cron", "ansible.builtin",
             "Module ansible.builtin.cron - manage crontab entries with minute hour job"),
        _doc("kubernetes.core.k8s_scale", "kubernetes.core",
             "Module kubernetes.core.k8s_scale - set a new size for a deployment replicas"),
    ]


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


def test_tokenize_splits_snake_case_and_drops_single_chars():
    assert tokenize("ec2_instance a Foo-Bar") == ["ec2", "instance", "foo", "bar"]


def test_finds_module_by_parameter_vocabulary(corpus):
    # "fstype" appears only in a parameter list, never in a synopsis — exactly
    # the case dense search misses.
    index = SparseIndex(corpus)
    hits = index.search("format a device using fstype xfs", k=3)
    assert hits
    assert hits[0][0].metadata["module"] == "community.general.filesystem"


def test_matches_module_name_split_into_words(corpus):
    index = SparseIndex(corpus)
    hits = index.search("scale a deployment", k=3)
    assert hits[0][0].metadata["module"] == "kubernetes.core.k8s_scale"


def test_collection_filter_restricts_results(corpus):
    index = SparseIndex(corpus)
    hits = index.search("versioning buckets", k=5, collections=["ansible.builtin"])
    assert all(d.metadata["collection"] == "ansible.builtin" for d, _ in hits)


def test_unknown_terms_return_nothing(corpus):
    index = SparseIndex(corpus)
    assert index.search("zzzz qqqq", k=5) == []


def test_empty_query_returns_nothing(corpus):
    index = SparseIndex(corpus)
    assert index.search("", k=5) == []


def test_scores_are_positive_and_descending(corpus):
    index = SparseIndex(corpus)
    hits = index.search("manage crontab entries", k=4)
    scores = [s for _, s in hits]
    assert scores and all(s > 0 for s in scores)
    assert scores == sorted(scores, reverse=True)


# ── fusion ───────────────────────────────────────────────────────────────

def _stub_store(corpus: list[Document]) -> MagicMock:
    store = MagicMock()
    store._collection.count.return_value = len(corpus)
    store.get.return_value = {
        "documents": [d.page_content for d in corpus],
        "metadatas": [d.metadata for d in corpus],
    }
    return store


def test_fusion_adds_sparse_only_candidates(corpus):
    store = _stub_store(corpus)
    dense = [(corpus[1], 0.71)]  # only s3_bucket found by the vector search

    fused = fuse_with_sparse(
        "format a device using fstype xfs", dense, store, collections=None, limit=10
    )

    modules = [d.metadata["module"] for d, _ in fused]
    assert "community.general.filesystem" in modules
    assert "amazon.aws.s3_bucket" in modules


def test_fusion_preserves_dense_scores_and_gives_sparse_the_median(corpus):
    store = _stub_store(corpus)
    dense = [(corpus[1], 0.80), (corpus[2], 0.40)]

    fused = fuse_with_sparse(
        "fstype resizefs", dense, store, collections=None, limit=10
    )
    by_module = {d.metadata["module"]: s for d, s in fused}

    assert by_module["amazon.aws.s3_bucket"] == 0.80
    assert by_module["ansible.builtin.cron"] == 0.40
    # No dense score of its own, so it inherits the pool median: sorted
    # [0.40, 0.80] indexed at len // 2 == 1.
    assert by_module["community.general.filesystem"] == 0.80


def test_fusion_is_a_no_op_when_the_index_is_unavailable():
    store = MagicMock()
    store._collection.count.return_value = 0
    store.get.return_value = {"documents": [], "metadatas": []}

    dense = [(_doc("a.b.c", "a.b", "text"), 0.5)]
    assert fuse_with_sparse("anything", dense, store, limit=5) == dense


def test_index_is_cached_and_rebuilt_when_the_collection_changes(corpus):
    store = _stub_store(corpus)

    first = get_sparse_index(store)
    second = get_sparse_index(store)
    assert first is second
    assert store.get.call_count == 1

    store._collection.count.return_value = len(corpus) + 1
    third = get_sparse_index(store)
    assert third is not first
