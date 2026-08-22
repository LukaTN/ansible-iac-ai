"""Phase 6b — model bake-off pairing and winner rules (no LLM)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    path = ROOT / "scripts" / "model_bakeoff.py"
    spec = importlib.util.spec_from_file_location("model_bakeoff", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bakeoff():
    return _load()


def test_parse_pair_flag(bakeoff):
    p = bakeoff.parse_pair_flag("gemma3:12b=qwen2.5-coder:14b")
    assert p["agent"] == "gemma3:12b"
    assert p["playbook"] == "qwen2.5-coder:14b"
    assert p["id"] == "gemma3-12b__qwen2.5-coder-14b"


def test_parse_pair_flag_rejects_bare_tag(bakeoff):
    with pytest.raises(ValueError):
        bakeoff.parse_pair_flag("gemma3:12b")


def test_load_pairs_from_committed_models_json(bakeoff):
    pairs = bakeoff.load_pairs(ROOT / "evals" / "baselines" / "models.json")
    ids = {p["id"] for p in pairs}
    assert "incumbent" in ids
    assert "coder7b" in ids


def test_pick_winner_requires_gate_pass(bakeoff):
    rows = [
        {"id": "a", "avg_overall_score": 99, "pass_rate_70": 100, "gate": "failed"},
        {"id": "b", "avg_overall_score": 80, "pass_rate_70": 80, "gate": "passed",
         "agent": "x", "playbook": "y"},
    ]
    w = bakeoff.pick_winner(rows)
    assert w is not None
    assert w["id"] == "b"


def test_pick_winner_none_when_all_fail(bakeoff):
    assert bakeoff.pick_winner([{"id": "a", "gate": "failed", "avg_overall_score": 99}]) is None


def test_pick_winner_breaks_tie_on_pass_rate(bakeoff):
    rows = [
        {"id": "low", "avg_overall_score": 80, "pass_rate_70": 70, "gate": "passed"},
        {"id": "high", "avg_overall_score": 80, "pass_rate_70": 100, "gate": "passed"},
    ]
    assert bakeoff.pick_winner(rows)["id"] == "high"


def test_host_report_path_maps_container_reports(bakeoff):
    p = bakeoff.host_report_path("/app/reports/e2e_bakeoff_x_1.json")
    assert p == ROOT / "reports" / "e2e_bakeoff_x_1.json"


def test_parse_report_path_from_stdout(bakeoff):
    text = "noise\n  JSON: /app/reports/e2e_platform_eval_1.json\n"
    assert bakeoff.parse_report_path(text).name == "e2e_platform_eval_1.json"


def test_compose_argv_sets_models_and_does_not_write_env(bakeoff):
    argv = bakeoff.compose_argv(
        {"id": "incumbent", "agent": "gemma3:12b", "playbook": "qwen2.5-coder:14b"},
        suite="core",
        extra=[],
        report_prefix="e2e_bakeoff_incumbent",
    )
    assert "AGENT_MODEL=gemma3:12b" in argv
    assert "PLAYBOOK_MODEL=qwen2.5-coder:14b" in argv
    assert "--env-file" in argv
    joined = " ".join(argv)
    assert ".env " not in joined or "--env-file" in argv
    assert "sed" not in argv


def test_scores_from_report(bakeoff, tmp_path):
    path = tmp_path / "e2e.json"
    path.write_text(
        json.dumps({"global": {"avg_overall_score": 98.3, "pass_rate_70": 100.0}, "total_cases": 7}),
        encoding="utf-8",
    )
    s = bakeoff.scores_from_report(path)
    assert s["avg_overall_score"] == 98.3
    assert s["pass_rate_70"] == 100.0
    assert s["total_cases"] == 7
