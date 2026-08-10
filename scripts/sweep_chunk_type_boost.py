"""
Sweep the stage-4 CHUNK_TYPE_BOOST table.

The shipped table gave `overview` the largest bonus and penalised
`optional_params`. Measured against how often each chunk type is the first to
surface the correct module (scripts/diagnose_retrieval.py), normalised by how
many chunks of that type exist in the index, that ordering is backwards:

    chunk type        share of index   first-hit share   lift
    example                   23.2%             36.5%   1.58
    required_params           11.0%             15.4%   1.40
    optional_params           50.6%             42.3%   0.84
    overview                  15.2%              5.8%   0.38

This scores candidate tables on both datasets so the replacement is chosen on
evidence rather than intuition.

Outcome so far: the table was left as shipped. The two datasets disagree about
`overview` — halving its boost is the best table on the benchmark (26.8% top-1)
and the worst on the project set (35% vs 55%) — because the project's questions
paraphrase module descriptions while the benchmark's describe tasks. At 56 and
20 queries a four-query swing moves the project number 20 points, so none of
these deltas is separable from noise. Re-run this once the benchmark is larger.

Usage: python scripts/sweep_chunk_type_boost.py
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

CANDIDATES: dict[str, dict[str, float]] = {
    "shipped": {"overview": 0.12, "example": 0.05, "required_params": 0.02, "optional_params": -0.03},
    "all zero": {"overview": 0.0, "example": 0.0, "required_params": 0.0, "optional_params": 0.0},
    "lift-aligned mild": {"overview": 0.0, "example": 0.05, "required_params": 0.04, "optional_params": 0.01},
    "lift-aligned": {"overview": -0.02, "example": 0.06, "required_params": 0.05, "optional_params": 0.0},
    "lift-aligned strong": {"overview": -0.05, "example": 0.08, "required_params": 0.07, "optional_params": 0.0},
    # The two datasets disagree about `overview`: paraphrase-style questions
    # (the project set) reward it, task-style ones (the benchmark) do not.
    # These keep it and only correct the other three.
    "keep ov, drop opt penalty": {"overview": 0.12, "example": 0.05, "required_params": 0.02, "optional_params": 0.0},
    "keep ov, lift req+ex": {"overview": 0.12, "example": 0.07, "required_params": 0.06, "optional_params": 0.0},
    "half ov, lift req+ex": {"overview": 0.06, "example": 0.07, "required_params": 0.06, "optional_params": 0.0},
}


def main() -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        vs = load_vectorstore()

    datasets = {
        "benchmark (56 q)": load_samples(BACKEND_ROOT / "rag" / "retrieval_benchmark.json"),
        "project (20 q)": load_samples(BACKEND_ROOT / "rag" / "test_dataset.json"),
    }
    original = dict(R.CHUNK_TYPE_BOOST)

    for title, samples in datasets.items():
        print(f"\n  === {title} ===")
        print(f"  {'table':<22} {'top1':>7} {'hit@8':>7} {'mrr':>7}")
        for label, table in CANDIDATES.items():
            R.CHUNK_TYPE_BOOST.clear()
            R.CHUNK_TYPE_BOOST.update(table)
            s = summarise(evaluate(samples, vs, R.TOP_K, routing=True)["rows"])
            print(f"  {label:<22} {s['top1']:>6.1%} {s['hit@k']:>7.1%} {s['mrr']:>7.3f}")
        R.CHUNK_TYPE_BOOST.clear()
        R.CHUNK_TYPE_BOOST.update(original)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
