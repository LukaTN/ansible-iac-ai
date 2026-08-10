"""
Platform E2E tests (live agent).

Skipped unless E2E_RUN=1 and backend is up (api mode) or E2E_MODE=pipeline.

  set E2E_RUN=1
  py app.py   # terminal 1 (api mode)
  pytest tests/test_e2e_platform.py -v -s
"""

from __future__ import annotations

import os

import pytest

from tests.e2e.dataset import iter_cases, layer_weights
from tests.e2e.layers import evaluate_case
from tests.e2e.runner import health_check, run_case_api, run_case_pipeline

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


def _e2e_enabled() -> bool:
    return os.getenv("E2E_RUN", "").strip().lower() in ("1", "true", "yes")


@pytest.fixture(scope="module")
def e2e_mode() -> str:
    return os.getenv("E2E_MODE", "api")


@pytest.fixture(scope="module")
def base_url() -> str:
    return os.getenv("E2E_BASE_URL", "http://127.0.0.1:5000")


@pytest.mark.skipif(not _e2e_enabled(), reason="Set E2E_RUN=1 to run live E2E tests")
def test_single_core_ec2_case(e2e_mode: str, base_url: str):
    cases = [c for c in iter_cases(suite="core") if c.id == "core-ec2-apache"]
    assert cases, "golden case core-ec2-apache missing"
    case = cases[0]
    weights = layer_weights()

    if e2e_mode == "api":
        if not health_check(base_url):
            pytest.skip(f"Backend not running at {base_url}")
        raw = run_case_api(case, base_url, timeout_sec=900.0)
    else:
        raw = run_case_pipeline(case)

    assert raw.get("error") is None, raw.get("error")
    assert raw.get("playbook"), "expected playbook in response"

    ev = evaluate_case(
        case,
        query=case.query,
        playbook=raw["playbook"],
        retrieval_meta=raw.get("retrieval_meta"),
        validation=raw.get("validation"),
        detected_module=raw.get("detected_module"),
        weights=weights,
    )
    assert ev["overall_score"] >= 50, ev
