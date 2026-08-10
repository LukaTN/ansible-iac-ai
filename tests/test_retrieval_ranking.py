"""
Tests for the module-ranking improvements measured against
rag/retrieval_benchmark.json (see reports/retrieval_baseline.json vs
reports/retrieval_after_rank_fix.json).

  1. Primary-module selection aggregates evidence across a module's chunks
     instead of trusting the single best chunk.
  2. Read-only (*_info / *_facts) modules are demoted on action queries,
     including neutral phrasings like "store a secret in the vault".
"""

from __future__ import annotations

from langchain_core.documents import Document

from rag.retrieval_utils import extract_primary_module, list_ranked_modules
from rag.retriever import _compute_intent_boost, analyze_query


def _doc(module: str, chunk_type: str = "overview", collection: str = "amazon.aws") -> Document:
    return Document(
        page_content="x",
        metadata={"module": module, "chunk_type": chunk_type, "collection": collection},
    )


class TestPrimaryModuleAggregation:
    def test_multiple_supporting_chunks_beat_one_lucky_chunk(self):
        """ec2_instance with 3 chunks at 0.80/0.78/0.75 must beat
        ec2_launch_template's single 0.82 chunk."""
        docs = [
            _doc("amazon.aws.ec2_launch_template"),
            _doc("amazon.aws.ec2_instance", "overview"),
            _doc("amazon.aws.ec2_instance", "example"),
            _doc("amazon.aws.ec2_instance", "required_params"),
        ]
        scores = [0.82, 0.80, 0.78, 0.75]
        primary, coll, _ = extract_primary_module(docs, scores)
        assert primary == "amazon.aws.ec2_instance"
        assert coll == "amazon.aws"

    def test_clearly_better_single_chunk_still_wins(self):
        """Aggregation must not let two weak chunks outvote one strong one."""
        docs = [
            _doc("amazon.aws.s3_bucket"),
            _doc("amazon.aws.s3_object", "overview"),
            _doc("amazon.aws.s3_object", "example"),
        ]
        scores = [0.95, 0.55, 0.50]
        primary, _, _ = extract_primary_module(docs, scores)
        assert primary == "amazon.aws.s3_bucket"

    def test_ranked_modules_use_the_same_ordering(self):
        docs = [
            _doc("amazon.aws.ec2_launch_template"),
            _doc("amazon.aws.ec2_instance", "overview"),
            _doc("amazon.aws.ec2_instance", "example"),
        ]
        scores = [0.82, 0.80, 0.78]
        ranked = list_ranked_modules(docs, scores)
        assert ranked[0]["module"] == "amazon.aws.ec2_instance"
        assert ranked[0]["rank"] == 1
        assert "rank_score" in ranked[0]


class TestReadonlyModuleShaping:
    def test_action_verb_outside_legacy_list_is_write_intent(self):
        assert analyze_query("store a database connection string in the vault").write_intent
        assert analyze_query("attach an extra disk to the server").write_intent
        assert analyze_query("schedule a maintenance script every night").write_intent

    def test_pure_read_query_promotes_info_modules(self):
        analysis = analyze_query("list every running virtual machine in eu-west-1")
        assert analysis.read_intent and not analysis.write_intent
        assert _compute_intent_boost(analysis, "amazon.aws.ec2_instance_info") > 0

    def test_write_query_demotes_info_modules(self):
        analysis = analyze_query("create a key vault secret for the database")
        assert _compute_intent_boost(analysis, "azure.azcollection.azure_rm_keyvaultsecret_info") < 0

    def test_neutral_query_mildly_demotes_info_modules(self):
        analysis = analyze_query("the nginx configuration on my web tier")
        assert not analysis.write_intent and not analysis.read_intent
        boost = _compute_intent_boost(analysis, "amazon.aws.ec2_instance_info")
        assert -0.18 < boost < 0

    def test_facts_suffix_treated_like_info(self):
        analysis = analyze_query("create a new virtual machine")
        assert _compute_intent_boost(analysis, "azure.azcollection.azure_rm_virtualmachine_facts") < 0
