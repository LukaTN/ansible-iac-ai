"""
Unit tests for the v2 retriever pipeline:
  * specificity-weighted collection scoring + FQCN detection (issue #1)
  * adaptive single/multi/all routing with $eq/$in filters (issue #2)
  * module-level diversity cap so example chunks don't dominate (issue #3)
  * query-aware chunk-type boosts (issue #4)
  * O(n) coverage backfill via prebuilt index map (issue #5)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.documents import Document

from rag.retriever import (
    _chunk_type_boost,
    _retrieve_ranked,
    analyze_query,
    detect_collection,
    route_collections,
    score_collections,
)


def _doc(module: str, collection: str, ctype: str = "overview", **extra) -> Document:
    md = {"module": module, "collection": collection, "chunk_type": ctype}
    md.update(extra)
    return Document(page_content="x", metadata=md)


# ─────────────────────────────────────────────
#  Issue #1 — robust collection scoring
# ─────────────────────────────────────────────

def test_unique_short_keyword_beats_shared_long_phrase():
    # "pod" (unique to kubernetes.core) must score on a k8s query even though
    # it is short; specificity weighting prevents long shared phrases winning.
    scores = score_collections("create a pod")
    assert scores.get("kubernetes.core", 0) > 0
    assert detect_collection("create a pod") == "kubernetes.core"


def test_security_group_routes_aws_not_builtin():
    # "group" is shared (builtin/community) but "security group" is specific to AWS.
    assert detect_collection("create a security group") == "amazon.aws"


def test_fqcn_module_prefix_scores_azure():
    # A bare module name with no keyword must still route to its collection.
    scores = score_collections("create an azure_rm_deployment")
    assert scores["azure.azcollection"] >= 0.8
    assert detect_collection("create an azure_rm_deployment") == "azure.azcollection"


def test_full_fqcn_detected():
    a = analyze_query("use amazon.aws.ec2_instance to launch a vm")
    assert "amazon.aws" in a.fqcn_collections


# ─────────────────────────────────────────────
#  Issue #2 — adaptive routing
# ─────────────────────────────────────────────

def test_single_route_for_clear_cloud_query():
    d = route_collections("provision two EC2 instances")
    assert d.mode == "single"
    assert d.collections == ["amazon.aws"]
    assert d.where == {"collection": {"$eq": "amazon.aws"}}


def test_multi_route_for_cross_collection_query():
    d = route_collections("configure authentication in Kubernetes and AWS")
    assert d.mode == "multi"
    assert set(d.collections) == {"amazon.aws", "kubernetes.core"}
    assert d.where["collection"]["$in"]
    # proportional quotas assigned to each routed collection
    assert all(q >= 1 for q in d.quotas.values())


def test_no_signal_routes_all():
    d = route_collections("hello there please help me")
    assert d.mode == "all"
    assert d.where is None


def test_multi_route_applies_in_filter_when_auto_enabled():
    vs = MagicMock()
    seen: list = []

    def capture(query, k, filter=None):
        seen.append(filter)
        return [
            (_doc("amazon.aws.ec2_instance", "amazon.aws"), 0.95),
            (_doc("kubernetes.core.k8s", "kubernetes.core"), 0.94),
        ]

    vs.similarity_search_with_relevance_scores.side_effect = capture
    _retrieve_ranked(
        "configure authentication in Kubernetes and AWS",
        vs,
        top_k=6,
        collection_filter=None,
        apply_auto_collection_filter=True,
    )
    assert seen and seen[0] == {"collection": {"$in": ["amazon.aws", "kubernetes.core"]}}


# ─────────────────────────────────────────────
#  Issue #4 — query-aware chunk boosts
# ─────────────────────────────────────────────

def test_example_intent_boosts_example_chunks():
    a = analyze_query("show me an example of creating an EC2 instance")
    assert a.example_intent
    assert _chunk_type_boost("example", a) > _chunk_type_boost("required_params", a)


def test_param_intent_boosts_required_params():
    a = analyze_query("what parameters are required for k8s_service")
    assert a.param_intent
    assert _chunk_type_boost("required_params", a) > _chunk_type_boost("example", a)


# ─────────────────────────────────────────────
#  Issue #3 — module diversity cap
# ─────────────────────────────────────────────

def test_one_module_examples_do_not_dominate_topk():
    vs = MagicMock()
    # 8 example chunks for the same module + 3 other modules.
    docs = [
        (_doc("amazon.aws.s3_bucket", "amazon.aws", "example", example_index=str(i)), 0.9 - i * 0.001)
        for i in range(8)
    ]
    docs += [
        (_doc("amazon.aws.s3_object", "amazon.aws", "overview"), 0.80),
        (_doc("amazon.aws.iam_user", "amazon.aws", "overview"), 0.79),
        (_doc("amazon.aws.ec2_vpc_net", "amazon.aws", "overview"), 0.78),
    ]
    vs.similarity_search_with_relevance_scores.return_value = docs

    ranked, _, _ = _retrieve_ranked(
        "create an encrypted s3 bucket",
        vs,
        top_k=6,
        collection_filter="amazon.aws",
        apply_auto_collection_filter=True,
    )
    modules = [d.metadata["module"] for d, _ in ranked]
    # s3_bucket is primary so it gets a coverage budget, but it must not occupy
    # all 6 slots; other modules have to appear.
    assert modules.count("amazon.aws.s3_bucket") <= 4
    assert len(set(modules)) >= 2


# ─────────────────────────────────────────────
#  Issue #5 — coverage backfill from raw results (below threshold)
# ─────────────────────────────────────────────

def test_coverage_backfills_primary_required_params_from_raw():
    vs = MagicMock()
    docs = [
        (_doc("amazon.aws.ec2_instance", "amazon.aws", "overview"), 0.95),
        (_doc("amazon.aws.ec2_instance", "amazon.aws", "example", example_index="0"), 0.90),
        # required_params chunk is below threshold but must be backfilled.
        (
            _doc(
                "amazon.aws.ec2_instance",
                "amazon.aws",
                "required_params",
                required_params_list="image_id,instance_type",
            ),
            0.10,
        ),
    ]
    vs.similarity_search_with_relevance_scores.return_value = docs

    ranked, _, _ = _retrieve_ranked(
        "launch an ec2 instance",
        vs,
        top_k=6,
        collection_filter="amazon.aws",
        apply_auto_collection_filter=True,
    )
    ctypes = {d.metadata["chunk_type"] for d, _ in ranked}
    assert "required_params" in ctypes
