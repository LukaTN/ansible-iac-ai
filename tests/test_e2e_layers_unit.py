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
