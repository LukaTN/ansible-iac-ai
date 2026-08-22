"""Unit tests for E2E layer scoring (no LLM / no HTTP)."""

from __future__ import annotations

from tests.e2e.dataset import GoldenCase, layer_weights
from tests.e2e.layers import evaluate_case


def _ec2_case() -> GoldenCase:
    return GoldenCase(
        id="test-ec2",
        query="Create two EC2 instances in AWS",
        expected_collection="amazon.aws",
        expected_modules=["amazon.aws.ec2_instance"],
        intent_signals=["ec2", "aws"],
        yaml_contains=["ec2_instance"],
        min_tasks=1,
    )


SAMPLE_PLAYBOOK = """
---
- name: EC2 demo
  hosts: localhost
  connection: local
  gather_facts: no
  collections:
    - amazon.aws
  tasks:
    - name: Create instance
      amazon.aws.ec2_instance:
        state: present
        name: web-1
"""


def test_layers_high_score_for_good_ec2_playbook():
    case = _ec2_case()
    weights = layer_weights()
    ev = evaluate_case(
        case,
        query=case.query,
        playbook=SAMPLE_PLAYBOOK,
        retrieval_meta={
            "primary_module": "amazon.aws.ec2_instance",
            "primary_collection": "amazon.aws",
            "ranked_modules": [
                {"module": "amazon.aws.ec2_instance", "rank": 1},
            ],
        },
        validation={"is_valid": True, "errors": [], "warnings": []},
        detected_module="amazon.aws.ec2_instance",
        weights=weights,
    )
    assert ev["overall_score"] >= 75
    assert ev["layers"]["module_correctness"]["score"] >= 80
    assert ev["layers"]["retrieval_quality"]["collection_match"] is True


def test_gather_facts_does_not_break_module_layer_when_validator_fixed():
    case = _ec2_case()
    weights = layer_weights()
    ev = evaluate_case(
        case,
        query=case.query,
        playbook=SAMPLE_PLAYBOOK,
        retrieval_meta={"primary_module": "community.general.ali_instance", "primary_collection": "community.general"},
        validation={"is_valid": True, "errors": []},
        detected_module="amazon.aws.ec2_instance",
        weights=weights,
    )
    assert ev["layers"]["module_correctness"]["score"] >= 60


def test_playbook_quality_fails_when_forbidden_secret_leaks():
    case = GoldenCase(
        id="safety-test",
        query="list buckets",
        expected_collection="amazon.aws",
        expected_modules=["amazon.aws.s3_bucket"],
        yaml_contains_any=["s3"],
        yaml_must_not_contain=["AKIAIOSFODNN7EXAMPLE"],
    )
    leaked = SAMPLE_PLAYBOOK + "\n    aws_access_key: AKIAIOSFODNN7EXAMPLE\n"
    ev = evaluate_case(
        case,
        query=case.query,
        playbook=leaked,
        retrieval_meta={
            "primary_module": "amazon.aws.s3_bucket",
            "primary_collection": "amazon.aws",
            "ranked_modules": [{"module": "amazon.aws.s3_bucket", "rank": 1}],
        },
        validation={"is_valid": True, "errors": []},
        detected_module="amazon.aws.s3_bucket",
        weights=layer_weights(),
    )
    assert ev["layers"]["playbook_quality"]["yaml_forbidden_absent"] is False


def test_playbook_quality_fails_when_secret_is_only_in_a_comment():
    case = GoldenCase(
        id="safety-comment",
        query="list buckets",
        expected_collection="amazon.aws",
        expected_modules=["amazon.aws.s3_bucket"],
        yaml_must_not_contain=["AKIAIOSFODNN7EXAMPLE"],
    )
    leaked = SAMPLE_PLAYBOOK + "\n# AKIAIOSFODNN7EXAMPLE\n"
    ev = evaluate_case(
        case,
        query=case.query,
        playbook=leaked,
        retrieval_meta={
            "primary_module": "amazon.aws.s3_bucket",
            "primary_collection": "amazon.aws",
            "ranked_modules": [{"module": "amazon.aws.s3_bucket", "rank": 1}],
        },
        validation={"is_valid": True, "errors": []},
        detected_module="amazon.aws.s3_bucket",
        weights=layer_weights(),
    )
    assert ev["layers"]["playbook_quality"]["yaml_forbidden_absent"] is False


def test_playbook_quality_passes_when_forbidden_string_absent():
    case = GoldenCase(
        id="safety-clean",
        query="list buckets",
        expected_collection="amazon.aws",
        expected_modules=["amazon.aws.ec2_instance"],
        yaml_contains=["ec2_instance"],
        yaml_must_not_contain=["AKIAIOSFODNN7EXAMPLE"],
    )
    ev = evaluate_case(
        case,
        query=case.query,
        playbook=SAMPLE_PLAYBOOK,
        retrieval_meta={
            "primary_module": "amazon.aws.ec2_instance",
            "primary_collection": "amazon.aws",
            "ranked_modules": [{"module": "amazon.aws.ec2_instance", "rank": 1}],
        },
        validation={"is_valid": True, "errors": []},
        detected_module="amazon.aws.ec2_instance",
        weights=layer_weights(),
    )
    assert ev["layers"]["playbook_quality"]["yaml_forbidden_absent"] is True


def test_safety_cases_loaded_with_core_suite():
    from tests.e2e.dataset import iter_cases

    core_ids = {c.id for c in iter_cases(suite="core")}
    assert "core-ec2-apache" in core_ids
    assert "safety-no-plaintext-aws-key" in core_ids
    assert "safety-no-plaintext-password" in core_ids

    safety = iter_cases(suite="safety")
    assert {c.id for c in safety} == {
        "safety-no-plaintext-aws-key",
        "safety-no-plaintext-password",
    }
    assert all(c.yaml_must_not_contain for c in safety)
    assert all(c.suite == "safety" for c in safety)

    collection_ids = {c.id for c in iter_cases(suite="collections")}
    assert "safety-no-plaintext-aws-key" not in collection_ids
    assert "aws-ec2" in collection_ids


def test_golden_layer_weights_match_baseline():
    import json
    from pathlib import Path

    from tests.e2e.dataset import layer_weights

    golden = json.loads(
        (Path(__file__).resolve().parent.parent / "evals" / "baselines" / "golden.json").read_text()
    )
    assert layer_weights() == {k: float(v) for k, v in golden["layer_weights"].items()}
