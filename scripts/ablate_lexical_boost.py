"""
Ablation guarding stage 4's `_compute_intent_boost`.

`_compute_lexical_boost` used to score query/module-name similarity (Jaccard
overlap, exact short-name match, cloud-token mismatch) on top of the lexical
fusion already done in stage 3. Measuring it showed the double-counting cost
3.6 points of top-1. This script reproduces that result and acts as a guard
against reintroducing name similarity into the reranker.

Usage: python scripts/ablate_lexical_boost.py
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import rag.retriever as R  # noqa: E402
from rag.indexer import load_vectorstore  # noqa: E402
from scripts.eval_retrieval import evaluate, load_samples, summarise  # noqa: E402

CURRENT = R._compute_intent_boost


def name_similarity(query_lower: str, module_name: str) -> float:
    """The removed term: how much the module's name looks like the query."""
    short = module_name.split(".")[-1].lower()
    mod_tokens = {t for t in short.split("_") if len(t) > 2}
    q_tokens = {t for t in re.split(r"[^a-z0-9]+", query_lower) if len(t) > 2}
    if "kubernetes" in q_tokens:
        q_tokens.add("k8s")
    if not mod_tokens or not q_tokens:
        return 0.0

    overlap = mod_tokens & q_tokens
    union = mod_tokens | q_tokens
    boost = 0.0
    if overlap:
        boost += min(0.10, (len(overlap) / len(union)) * 0.25)
    if short in query_lower:
        boost += 0.05

    cloud = {"ec2", "s3", "rds", "iam", "vpc", "aks", "ecs", "eks",
             "lambda", "sns", "sqs", "route53", "blob", "cosmos"}
    if (cloud & q_tokens) and (cloud & mod_tokens) and not (cloud & q_tokens & mod_tokens):
        boost -= 0.10

    return max(-0.15, min(0.12, boost))


def with_name_similarity(query_lower: str, module_name: str) -> float:
    """The old shipped behaviour, for comparison."""
    if not module_name:
        return 0.0
    return CURRENT(query_lower, module_name) + name_similarity(query_lower, module_name)


def disabled(query_lower: str, module_name: str) -> float:
    return 0.0


def main() -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        vs = load_vectorstore()

    datasets = {
        "retrieval_benchmark.json (56 q, module never named)":
            load_samples(PROJECT_ROOT / "rag" / "retrieval_benchmark.json"),
        "test_dataset.json (20 q, project's own)":
            load_samples(PROJECT_ROOT / "rag" / "test_dataset.json"),
    }
    variants = [
        ("intent rules (current)", CURRENT),
        ("+ name similarity (old)", with_name_similarity),
        ("no boost at all", disabled),
    ]

    for title, samples in datasets.items():
        print(f"\n  === {title} ===")
        for label, fn in variants:
            R._compute_intent_boost = fn
            s = summarise(evaluate(samples, vs, R.TOP_K, routing=True)["rows"])
            print(
                f"  {label:<26} top1 {s['top1']:>5.1%}  "
                f"hit@8 {s['hit@k']:>5.1%}  mrr {s['mrr']:.3f}"
            )
        R._compute_intent_boost = CURRENT

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
