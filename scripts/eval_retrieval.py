"""
=============================================================
  AnsibleAI — Retrieval-only benchmark

  Scores the retriever in isolation: does the correct Ansible
  module reach the top of the ranked pack for a natural-language
  task? No LLM is involved, so a run costs only embedding calls
  and the numbers move only when retrieval changes.

  Metrics
    top1    — primary_module equals the expected module
    hit@k   — expected module appears anywhere in the pack
    mrr     — 1/rank of the expected module in ranked_modules
    route_ok— stage-2 routing kept the expected collection reachable

  Usage
    python scripts/eval_retrieval.py
    python scripts/eval_retrieval.py --dataset rag/test_dataset.json
    python scripts/eval_retrieval.py --no-routing --json out.json
=============================================================
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rag.indexer import load_vectorstore  # noqa: E402
from rag.retriever import TOP_K, get_retrieval_metadata  # noqa: E402

DEFAULT_DATASET = PROJECT_ROOT / "rag" / "retrieval_benchmark.json"


def load_samples(path: Path) -> list[dict]:
    """Read either benchmark ('query') or RAGAS ('question') dataset shape."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    samples = raw["samples"] if isinstance(raw, dict) else raw

    out = []
    for i, s in enumerate(samples):
        query = s.get("query") or s.get("question")
        expected = s.get("expected_module")
        if not query or not expected:
            continue
        out.append(
            {
                "id": s.get("id", i),
                "query": query,
                "expected": expected,
                "acceptable": set(s.get("acceptable", [])) | {expected},
                "collection": s.get("collection", expected.rsplit(".", 1)[0]),
            }
        )
    return out


def indexed_modules(vectorstore) -> set[str]:
    """Every module name present in the vector store, for dataset validation."""
    got = vectorstore.get(include=["metadatas"])
    return {m.get("module") for m in got["metadatas"] if m.get("module")}


def evaluate(samples: list[dict], vectorstore, top_k: int, routing: bool) -> dict:
    rows = []
    for s in samples:
        # The retriever logs its whole pipeline to stdout; keep the report clean.
        with contextlib.redirect_stdout(io.StringIO()):
            meta = get_retrieval_metadata(
                s["query"],
                vectorstore,
                top_k=top_k,
                apply_auto_collection_filter=routing,
            )

        ranked = [e["module"] for e in meta.get("ranked_modules", [])]
        candidates = meta.get("module_candidates", [])
        acceptable = s["acceptable"]

        rank = next((i for i, m in enumerate(ranked, 1) if m in acceptable), 0)
        routed = meta.get("routing", {}).get("collections") or []

        rows.append(
            {
                "id": s["id"],
                "query": s["query"],
                "expected": s["expected"],
                "got": meta.get("primary_module"),
                "top1": meta.get("primary_module") in acceptable,
                "hit": any(m in acceptable for m in candidates),
                "rr": 1.0 / rank if rank else 0.0,
                "rank": rank,
                "route_mode": meta.get("routing", {}).get("mode"),
                "route_ok": (not routed) or (s["collection"] in routed),
                "collection": s["collection"],
                "top_score": meta.get("primary_score"),
            }
        )
    return {"rows": rows, "n": len(rows)}


def summarise(rows: list[dict], key: str | None = None) -> dict:
    n = len(rows) or 1
    return {
        "n": len(rows),
        "top1": sum(r["top1"] for r in rows) / n,
        "hit@k": sum(r["hit"] for r in rows) / n,
        "mrr": sum(r["rr"] for r in rows) / n,
        "route_ok": sum(r["route_ok"] for r in rows) / n,
    }


def report(result: dict, verbose: bool) -> None:
    rows = result["rows"]
    overall = summarise(rows)

    print("\n" + "=" * 72)
    print(f"  RETRIEVAL BENCHMARK — {overall['n']} queries")
    print("=" * 72)
    print(f"  top1     {overall['top1']:.1%}   (correct module ranked first)")
    print(f"  hit@k    {overall['hit@k']:.1%}   (correct module anywhere in pack)")
    print(f"  MRR      {overall['mrr']:.3f}")
    print(f"  route_ok {overall['route_ok']:.1%}   (routing kept the right collection)")

    print("\n  Per collection:")
    print(f"    {'collection':<26} {'n':>3}  {'top1':>6} {'hit@k':>6} {'mrr':>6} {'route':>6}")
    by_coll: dict[str, list[dict]] = {}
    for r in rows:
        by_coll.setdefault(r["collection"], []).append(r)
    for coll in sorted(by_coll):
        s = summarise(by_coll[coll])
        print(
            f"    {coll:<26} {s['n']:>3}  {s['top1']:>5.0%} {s['hit@k']:>6.0%} "
            f"{s['mrr']:>6.2f} {s['route_ok']:>5.0%}"
        )

    misses = [r for r in rows if not r["top1"]]
    if misses:
        print(f"\n  Misses ({len(misses)}):")
        for r in misses:
            found = f"rank {r['rank']}" if r["rank"] else "NOT IN PACK"
            print(f"    [{r['id']}] {r['query'][:58]}")
            print(f"         expected {r['expected']}")
            print(f"         got      {r['got']}  ({found}, route={r['route_mode']})")

    if verbose:
        print("\n  All results:")
        for r in rows:
            mark = "OK  " if r["top1"] else "MISS"
            print(f"    {mark} [{r['id']}] {r['got']} (expected {r['expected']})")


def main() -> int:
    ap = argparse.ArgumentParser(description="AnsibleAI retrieval-only benchmark")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--top-k", type=int, default=TOP_K)
    ap.add_argument(
        "--no-routing",
        action="store_true",
        help="disable stage-2 collection routing (search all collections)",
    )
    ap.add_argument("--json", type=Path, help="write raw per-query results here")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    samples = load_samples(args.dataset)
    if not samples:
        print(f"No usable samples in {args.dataset}", file=sys.stderr)
        return 1

    # pgvector access goes through Flask-SQLAlchemy; scripts need an app context.
    from app import app

    with app.app_context():
        with contextlib.redirect_stdout(io.StringIO()):
            vectorstore = load_vectorstore()

        known = indexed_modules(vectorstore)
        unknown = [s for s in samples if s["expected"] not in known]
        if unknown:
            print("  [WARN] expected modules missing from the index (excluded):")
            for s in unknown:
                print(f"    [{s['id']}] {s['expected']}")
            samples = [s for s in samples if s["expected"] in known]

        print(f"  Dataset: {args.dataset.name}  |  top_k={args.top_k}  |  "
              f"routing={'off' if args.no_routing else 'on'}")

        result = evaluate(samples, vectorstore, args.top_k, routing=not args.no_routing)
        report(result, args.verbose)

        if args.json:
            args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(f"\n  Raw results -> {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
