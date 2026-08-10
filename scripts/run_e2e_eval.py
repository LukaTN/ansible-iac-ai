#!/usr/bin/env python3
"""
Run full E2E evaluation for AnsibleAI.

Examples:
  # API mode (Flask must be running on :5000)
  python scripts/run_e2e_eval.py --mode api

  # In-process pipeline (no HTTP; still needs Ollama + Chroma index)
  python scripts/run_e2e_eval.py --mode pipeline

  # One collection only (5 cases)
  python scripts/run_e2e_eval.py --mode api --collection amazon.aws

  # Core scenarios only
  python scripts/run_e2e_eval.py --mode api --suite core
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.e2e.report import write_report
from tests.e2e.runner import run_suite


def main() -> int:
    p = argparse.ArgumentParser(description="AnsibleAI E2E platform evaluation")
    p.add_argument(
        "--mode",
        choices=("api", "pipeline"),
        default=os.getenv("E2E_MODE", "api"),
        help="api=POST /api/chat; pipeline=in-process RAG",
    )
    p.add_argument(
        "--base-url",
        default=os.getenv("E2E_BASE_URL", "http://127.0.0.1:5000"),
    )
    p.add_argument("--timeout", type=float, default=float(os.getenv("E2E_TIMEOUT", "900")))
    p.add_argument(
        "--suite",
        choices=("core", "collections", "all"),
        default="all",
        help="core=5 cross-cloud cases; collections=25 (5 per collection); all=30",
    )
    p.add_argument("--collection", default=None, help="Run only one collection's 5 tests")
    p.add_argument("--case-id", action="append", dest="case_ids", help="Repeatable filter")
    args = p.parse_args()

    suite = None if args.suite == "all" else args.suite

    print("=" * 60)
    print("  AnsibleAI E2E Evaluation")
    print(f"  Mode: {args.mode} | Suite: {args.suite}")
    if args.collection:
        print(f"  Collection filter: {args.collection}")
    print("=" * 60)

    try:
        summary = run_suite(
            mode=args.mode,
            base_url=args.base_url,
            timeout_sec=args.timeout,
            suite=suite,
            collection_filter=args.collection,
            case_ids=args.case_ids,
        )
    except RuntimeError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1

    json_path, md_path = write_report(summary)
    g = summary.get("global") or {}
    print("\n" + "=" * 60)
    print(f"  Done. Avg score: {g.get('avg_overall_score')}/100")
    print(f"  Pass rate (≥70): {g.get('pass_rate_70')}%")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
