"""Phase 6b — eval_gate, baselines, and kb_coverage (no LLM)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gate():
    return _load_script("eval_gate.py")


@pytest.fixture
def coverage():
    return _load_script("kb_coverage.py")


def test_committed_baselines_have_required_keys():
    retrieval = json.loads((ROOT / "evals" / "baselines" / "retrieval.json").read_text())
    golden = json.loads((ROOT / "evals" / "baselines" / "golden.json").read_text())
    models = json.loads((ROOT / "evals" / "baselines" / "models.json").read_text())

    assert retrieval["kind"] == "retrieval"
    assert set(retrieval["thresholds"]) == {"top1", "hit@k", "mrr", "route_ok"}
    assert all(0 < float(v) <= 1 for v in retrieval["thresholds"].values())
    assert retrieval["thresholds"]["top1"] <= retrieval["measured"]["top1"]
    assert retrieval["thresholds"]["hit@k"] <= retrieval["measured"]["hit@k"]

    assert golden["kind"] == "e2e_golden"
    assert set(golden["thresholds"]) == {"pass_rate_70", "avg_overall_score"}
    weights = golden["layer_weights"]
    assert abs(sum(weights.values()) - 1.0) < 1e-9

    assert models["kind"] == "model_selection"
    assert models["env"]["agent"] == "AGENT_MODEL"
    assert models["pairs"]


def test_retrieval_gate_passes_at_floor(gate):
    floors = {
        "top1": 0.50,
        "hit@k": 0.75,
        "mrr": 0.55,
        "route_ok": 0.80,
    }
    metrics = {"top1": 0.60, "hit@k": 0.80, "mrr": 0.65, "route_ok": 0.90}
    assert gate.check(metrics, floors, "retrieval") == []


def test_retrieval_gate_passes_exactly_at_floor(gate):
    floors = {"top1": 0.50}
    assert gate.check({"top1": 0.50}, floors, "retrieval") == []


def test_retrieval_gate_fails_below_floor(gate):
    metrics = {"top1": 0.10, "hit@k": 0.20, "mrr": 0.10, "route_ok": 0.10}
    floors = {"top1": 0.50, "hit@k": 0.75, "mrr": 0.55, "route_ok": 0.80}
    failures = gate.check(metrics, floors, "retrieval")
    assert len(failures) == 4
    assert all(line.startswith("retrieval:") for line in failures)


def test_check_reports_missing_metric(gate):
    failures = gate.check({}, {"top1": 0.5}, "retrieval")
    assert failures == ["retrieval: missing metric top1"]


def test_e2e_overall_from_global(gate):
    got = gate.e2e_overall({"global": {"pass_rate_70": 80.0, "avg_overall_score": 72.5}})
    assert got == {"pass_rate_70": 80.0, "avg_overall_score": 72.5}


def test_e2e_overall_defaults_when_global_missing(gate):
    assert gate.e2e_overall({}) == {"pass_rate_70": 0.0, "avg_overall_score": 0.0}


def test_retrieval_overall_from_rows(gate):
    report = {
        "rows": [
            {"top1": True, "hit": True, "rr": 1.0, "route_ok": True},
            {"top1": False, "hit": True, "rr": 0.5, "route_ok": True},
        ]
    }
    m = gate.retrieval_overall(report)
    assert m["top1"] == 0.5
    assert m["hit@k"] == 1.0
    assert m["mrr"] == 0.75
    assert m["route_ok"] == 1.0


def test_retrieval_overall_prefers_top_level_metrics(gate):
    report = {
        "top1": 0.9,
        "hit@k": 0.95,
        "mrr": 0.8,
        "route_ok": 1.0,
        "rows": [{"top1": False, "hit": False, "rr": 0, "route_ok": False}],
    }
    assert gate.retrieval_overall(report)["top1"] == 0.9


def test_retrieval_overall_from_nested_overall_and_hit_at_k_alias(gate):
    report = {
        "overall": {"top1": 0.4, "hit_at_k": 0.7, "mrr": 0.5, "route_ok": 0.8}
    }
    m = gate.retrieval_overall(report)
    assert m["hit@k"] == 0.7
    assert m["top1"] == 0.4


def test_cli_passes_on_committed_floors(gate, tmp_path, monkeypatch, capsys):
    report = tmp_path / "retrieval.json"
    report.write_text(
        json.dumps(
            {"overall": {"top1": 0.60, "hit@k": 0.80, "mrr": 0.65, "route_ok": 0.90}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["eval_gate.py", "--retrieval", str(report), "--baselines", str(ROOT / "evals" / "baselines")],
    )
    assert gate.main() == 0
    assert "EVAL GATE PASSED" in capsys.readouterr().out


def test_cli_fails_when_below_floor(gate, tmp_path, monkeypatch, capsys):
    report = tmp_path / "retrieval.json"
    report.write_text(
        json.dumps({"overall": {"top1": 0.1, "hit@k": 0.1, "mrr": 0.1, "route_ok": 0.1}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["eval_gate.py", "--retrieval", str(report), "--baselines", str(ROOT / "evals" / "baselines")],
    )
    assert gate.main() == 1
    err = capsys.readouterr().err
    assert "EVAL GATE FAILED" in err
    assert "top1" in err


def test_cli_fails_when_e2e_below_floor(gate, tmp_path, monkeypatch, capsys):
    report = tmp_path / "e2e.json"
    report.write_text(
        json.dumps({"global": {"pass_rate_70": 10.0, "avg_overall_score": 20.0}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["eval_gate.py", "--e2e", str(report), "--baselines", str(ROOT / "evals" / "baselines")],
    )
    assert gate.main() == 1
    assert "e2e:" in capsys.readouterr().err


def test_cli_missing_report_is_not_a_pass(gate, tmp_path, monkeypatch, capsys):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr("sys.argv", ["eval_gate.py", "--retrieval", str(missing)])
    assert gate.main() == 2
    assert "missing retrieval report" in capsys.readouterr().err


def test_cli_requires_a_report(gate, monkeypatch):
    monkeypatch.setattr("sys.argv", ["eval_gate.py"])
    with pytest.raises(SystemExit) as exc:
        gate.main()
    assert exc.value.code == 2


def test_kb_coverage_inventory(coverage, tmp_path):
    ns = tmp_path / "amazon.aws"
    ns.mkdir()
    (ns / "s3_bucket.json").write_text(
        json.dumps({"module": "amazon.aws.s3_bucket"}),
        encoding="utf-8",
    )
    (ns / "broken.json").write_text("{not json", encoding="utf-8")
    (ns / "empty.json").write_text("{}", encoding="utf-8")
    inv = coverage.parsed_inventory(tmp_path)
    assert inv["amazon.aws"] == ["amazon.aws.s3_bucket"]


def test_kb_coverage_empty_when_dir_missing(coverage, tmp_path):
    assert coverage.parsed_inventory(tmp_path / "absent") == {}


def test_benchmark_expected_includes_acceptable(coverage, tmp_path):
    path = tmp_path / "bench.json"
    path.write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "expected_module": "amazon.aws.s3_bucket",
                        "acceptable": ["amazon.aws.s3_object"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert coverage.benchmark_expected(path) == [
        "amazon.aws.s3_bucket",
        "amazon.aws.s3_object",
    ]


def test_module_from_json_skips_corrupt_file(coverage, tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("not-json", encoding="utf-8")
    assert coverage._module_from_json(bad) == ""
