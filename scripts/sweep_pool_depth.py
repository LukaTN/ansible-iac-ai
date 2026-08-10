"""
Sweep QUERY_K_MULTIPLIER — how deep a candidate pool the reranker should see.

scripts/diagnose_retrieval.py shows the correct module sometimes sits just below
the pool cutoff, where no amount of reranking can reach it. Widening the pool
costs one Chroma query with a larger k (no extra embedding call) plus BM25
scoring over more candidates, so it is nearly free; the risk is that extra
low-quality candidates crowd the final top-k.

Usage: python scripts/sweep_pool_depth.py
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import rag.retriever as R  # noqa: E402
from rag.indexer import load_vectorstore  # noqa: E402
from scripts.eval_retrieval import evaluate, load_samples, summarise  # noqa: E402

MULTIPLIERS = [4, 6, 8, 12, 16]


def main() -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        vs = load_vectorstore()

    datasets = {
        "benchmark (56 q)": load_samples(BACKEND_ROOT / "rag" / "retrieval_benchmark.json"),
        "project (20 q)": load_samples(BACKEND_ROOT / "rag" / "test_dataset.json"),
    }
    original = R.QUERY_K_MULTIPLIER

    for title, samples in datasets.items():
        print(f"\n  === {title} ===")
        print(f"  {'pool':<16} {'top1':>7} {'hit@8':>7} {'mrr':>7}")
        for mult in MULTIPLIERS:
            R.QUERY_K_MULTIPLIER = mult
            s = summarise(evaluate(samples, vs, R.TOP_K, routing=True)["rows"])
            label = f"k*{mult} = {mult * R.TOP_K}"
            print(f"  {label:<16} {s['top1']:>6.1%} {s['hit@k']:>7.1%} {s['mrr']:>7.3f}")
        R.QUERY_K_MULTIPLIER = original

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
