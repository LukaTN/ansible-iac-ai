"""Unit tests for hybrid lexical + vector retrieval helpers."""

from __future__ import annotations

from langchain_core.documents import Document

from rag.hybrid_search import (
    enrich_query_for_embedding,
    extract_module_targets,
    lexical_score,
    merge_vector_and_lexical,
    reciprocal_rank_fusion,
)
from rag.retriever import analyze_query, route_collections


def _doc(module: str, content: str = "x") -> Document:
    return Document(page_content=content, metadata={"module": module, "chunk_type": "overview"})


def test_extract_full_fqcn_module():
    targets = extract_module_targets("use amazon.aws.ec2_instance to launch")
    assert "amazon.aws.ec2_instance" in targets


def test_extract_short_module_prefix():
    targets = extract_module_targets("configure azure_rm_deployment slot")
    assert "azure_rm_deployment" in targets


def test_lexical_score_prefers_exact_module_match():
    q = "launch ec2_instance with amazon.aws.ec2_instance"
    exact = _doc("amazon.aws.ec2_instance", "Module ec2 instance launch")
    other = _doc("amazon.aws.ec2_key", "ssh key pair")
    assert lexical_score(q, exact) > lexical_score(q, other)


def test_enrich_query_adds_module_and_intent_hints():
    query = "create an ec2 instance"
    analysis = analyze_query(query)
    route = route_collections(query)
    enriched = enrich_query_for_embedding(query, analysis, route)
    assert "ec2_instance" in enriched or "ec2" in enriched
    assert "amazon.aws" in enriched or "Search scope" in enriched


def test_rrf_promotes_lexical_winner_for_ambiguous_query():
    """RRF helps when no explicit module name is in the query."""
    dense = [
        (_doc("amazon.aws.s3_bucket", "s3 bucket encrypted storage"), 0.9),
        (_doc("amazon.aws.ec2_instance", "generic compute"), 0.85),
    ]
    sparse = [
        (_doc("amazon.aws.s3_bucket", "s3 bucket encrypted storage"), 0.95),
        (_doc("amazon.aws.ec2_instance", "generic compute"), 0.1),
    ]
    fused = reciprocal_rank_fusion(dense, sparse)
    assert fused[0][0].metadata["module"] == "amazon.aws.s3_bucket"


def test_merge_vector_and_lexical_reorders_for_module_query():
    query = "deploy with amazon.aws.ec2_instance"
    vector_results = [
        (_doc("amazon.aws.s3_bucket", "s3 bucket storage"), 0.92),
        (_doc("amazon.aws.ec2_instance", "ec2 instance launch"), 0.88),
    ]
    merged = merge_vector_and_lexical(query, vector_results)
    modules = [d.metadata["module"] for d, _ in merged]
    assert modules[0] == "amazon.aws.ec2_instance"
