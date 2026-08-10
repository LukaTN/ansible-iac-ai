"""
Ablation: how much does a real sparse (BM25) stage add?

The diagnostic showed the correct module reaches the 32-candidate pool 86% of the
time but only becomes primary_module ~20% of the time, so the bottleneck is
ranking, not recall. This compares, at module level on the same benchmark:

  dense      — current embedding search, best chunk per module
  bm25       — BM25 over a per-module text profile (name, description, params)
  rrf        — reciprocal-rank fusion of the two

BM25 is implemented inline so the script has no new dependency.

Usage: python scripts/ablate_bm25_fusion.py
"""

from __future__ import annotations

import contextlib
import glob
import io
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from rag.indexer import load_vectorstore  # noqa: E402
from rag.ingestion import SKIP_PARAMS  # noqa: E402

DATASET = BACKEND_ROOT / "rag" / "retrieval_benchmark.json"
K1, B = 1.5, 0.75
RRF_K = 60


def tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 1]


class BM25:
    def __init__(self, corpus: list[list[str]]):
        self.corpus = corpus
        self.n = len(corpus)
        self.lens = [len(d) for d in corpus]
        self.avg = sum(self.lens) / max(1, self.n)
        self.tf = [Counter(d) for d in corpus]
        df = Counter()
        for d in corpus:
            df.update(set(d))
        self.idf = {
            t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()
        }

    def scores(self, query: list[str]) -> list[float]:
        out = [0.0] * self.n
        for i, tf in enumerate(self.tf):
            dl = self.lens[i]
            s = 0.0
            for t in query:
                f = tf.get(t)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * (f * (K1 + 1)) / (
                    f + K1 * (1 - B + B * dl / self.avg)
                )
            out[i] = s
        return out


def module_profiles() -> tuple[list[str], list[str]]:
    """One searchable text profile per module."""
    names, texts = [], []
    for f in sorted(glob.glob(str(PROJECT_ROOT / "data" / "parsed" / "*" / "*.json"))):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        mod = d.get("module", "")
        if not mod or mod in names:
            continue
        short = mod.split(".")[-1].replace("_", " ")
        params = " ".join(
            p["name"].replace("_", " ")
            for p in d.get("parameters", [])
            if p.get("name") and p["name"] not in SKIP_PARAMS
        )
        pdesc = " ".join(
            (p.get("description") or "")[:120]
            for p in d.get("parameters", [])
            if p.get("required")
        )
        names.append(mod)
        texts.append(f"{short} {d.get('collection','')} {d.get('description','')} {params} {pdesc}")
    return names, texts


def rank_from_scores(names: list[str], scores: list[float], top: int) -> list[str]:
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [names[i] for i in order[:top] if scores[i] > 0]


def metrics(ranked_per_query: list[list[str]], samples: list[dict], k: int = 8) -> tuple:
    top1 = hit = 0
    rr = 0.0
    for ranked, s in zip(ranked_per_query, samples):
        acc = set(s.get("acceptable", [])) | {s["expected_module"]}
        r = ranked[:k]
        if r and r[0] in acc:
            top1 += 1
        if any(m in acc for m in r):
            hit += 1
        pos = next((i for i, m in enumerate(r, 1) if m in acc), 0)
        rr += 1.0 / pos if pos else 0.0
    n = len(samples)
    return top1 / n, hit / n, rr / n


def main() -> int:
    samples = json.loads(DATASET.read_text(encoding="utf-8"))["samples"]
    names, texts = module_profiles()
    print(f"  {len(names)} module profiles, {len(samples)} queries")

    bm25 = BM25([tokenize(t) for t in texts])
    with contextlib.redirect_stdout(io.StringIO()):
        vs = load_vectorstore()

    dense_ranks, sparse_ranks, fused_ranks = [], [], []
    for s in samples:
        hits = vs.similarity_search_with_relevance_scores(query=s["query"], k=60)
        dense: list[str] = []
        for doc, _sc in hits:
            m = (doc.metadata or {}).get("module")
            if m and m not in dense:
                dense.append(m)
        sparse = rank_from_scores(names, bm25.scores(tokenize(s["query"])), 60)

        fuse: dict[str, float] = {}
        for r, m in enumerate(dense, 1):
            fuse[m] = fuse.get(m, 0.0) + 1.0 / (RRF_K + r)
        for r, m in enumerate(sparse, 1):
            fuse[m] = fuse.get(m, 0.0) + 1.0 / (RRF_K + r)
        fused = sorted(fuse, key=lambda m: fuse[m], reverse=True)

        dense_ranks.append(dense)
        sparse_ranks.append(sparse)
        fused_ranks.append(fused)

    print("\n" + "=" * 64)
    print("  MODULE-LEVEL RANKING ABLATION")
    print("=" * 64)
    print(f"  {'method':<10} {'top1':>8} {'hit@8':>8} {'mrr':>8}")
    for label, ranks in (
        ("dense", dense_ranks),
        ("bm25", sparse_ranks),
        ("rrf", fused_ranks),
    ):
        t, h, m = metrics(ranks, samples)
        print(f"  {label:<10} {t:>7.1%} {h:>8.1%} {m:>8.3f}")

    print("\n  This ablation motivated rag/sparse_index.py; the pipeline now fuses")
    print("  BM25 into the candidate pool at stage 3b. Run scripts/eval_retrieval.py")
    print("  for the end-to-end numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
