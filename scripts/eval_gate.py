#!/usr/bin/env python3
"""Compare a retrieval or E2E report against committed Phase 6b floors.

Exit 0 if every named threshold is met. Exit 1 on regression.
Phase 7 CI will invoke this; do not treat a missing report as a pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINES = ROOT / "evals" / "baselines"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def retrieval_overall(report: dict) -> dict[str, float]:
    if all(k in report for k in ("top1", "hit@k", "mrr", "route_ok")):
        return {k: float(report[k]) for k in ("top1", "hit@k", "mrr", "route_ok")}
    if "overall" in report and isinstance(report["overall"], dict):
        o = report["overall"]
        return {
            "top1": float(o["top1"]),
            "hit@k": float(o.get("hit@k", o.get("hit_at_k", 0))),
            "mrr": float(o["mrr"]),
            "route_ok": float(o["route_ok"]),
        }
    rows = report.get("rows") or []
    n = len(rows) or 1
    return {
        "top1": sum(bool(r.get("top1")) for r in rows) / n,
        "hit@k": sum(bool(r.get("hit")) for r in rows) / n,
        "mrr": sum(float(r.get("rr") or 0) for r in rows) / n,
        "route_ok": sum(bool(r.get("route_ok")) for r in rows) / n,
    }


def e2e_overall(report: dict) -> dict[str, float]:
    g = report.get("global") or {}
    return {
        "pass_rate_70": float(g.get("pass_rate_70") or 0),
        "avg_overall_score": float(g.get("avg_overall_score") or 0),
    }


def check(metrics: dict[str, float], thresholds: dict, label: str) -> list[str]:
    failures: list[str] = []
    for key, floor in thresholds.items():
        got = metrics.get(key)
        if got is None:
            failures.append(f"{label}: missing metric {key}")
            continue
        if float(got) + 1e-9 < float(floor):
            failures.append(f"{label}: {key} {got:.4f} < floor {float(floor):.4f}")
    return failures


def main() -> int:
    p = argparse.ArgumentParser(description="Gate a run against evals/baselines")
    p.add_argument("--baselines", type=Path, default=DEFAULT_BASELINES)
    p.add_argument("--retrieval", type=Path, help="eval_retrieval.py JSON")
    p.add_argument("--e2e", type=Path, help="run_e2e_eval.py JSON")
    args = p.parse_args()

    if not args.retrieval and not args.e2e:
        p.error("pass --retrieval and/or --e2e")

    failures: list[str] = []
    if args.retrieval:
        if not args.retrieval.is_file():
            print(f"missing retrieval report: {args.retrieval}", file=sys.stderr)
            return 2
        floors = _load(args.baselines / "retrieval.json")["thresholds"]
        failures.extend(check(retrieval_overall(_load(args.retrieval)), floors, "retrieval"))
    if args.e2e:
        if not args.e2e.is_file():
            print(f"missing e2e report: {args.e2e}", file=sys.stderr)
            return 2
        floors = _load(args.baselines / "golden.json")["thresholds"]
        failures.extend(check(e2e_overall(_load(args.e2e)), floors, "e2e"))

    if failures:
        print("EVAL GATE FAILED", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("EVAL GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
