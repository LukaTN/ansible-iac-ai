"""
Diagnose *where* retrieval loses the correct module.

For each benchmark query it asks three questions:
  1. Recall ceiling  — at what depth of a raw, unfiltered vector search does the
     correct module first appear? If it is deep or absent, the problem is the
     embedded text, not the ranking logic.
  2. Pool loss       — was it inside the 32-candidate pool the reranker sees?
  3. Chunk type      — which chunk type surfaces the module first?

Usage: python scripts/diagnose_retrieval.py [--depth 200]
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rag.indexer import load_vectorstore  # noqa: E402
from rag.retriever import QUERY_K_MULTIPLIER, TOP_K  # noqa: E402

DATASET = PROJECT_ROOT / "rag" / "retrieval_benchmark.json"
POOL_SIZE = TOP_K * QUERY_K_MULTIPLIER


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=200)
    args = ap.parse_args()

    samples = json.loads(DATASET.read_text(encoding="utf-8"))["samples"]
    with contextlib.redirect_stdout(io.StringIO()):
        vs = load_vectorstore()

    depths: list[int | None] = []
    first_ctype = Counter()
    rows = []

    for s in samples:
        acceptable = set(s.get("acceptable", [])) | {s["expected_module"]}
        hits = vs.similarity_search_with_relevance_scores(
            query=s["query"], k=args.depth, filter=None
        )

        depth = None
        ctype = None
        for i, (doc, _score) in enumerate(hits, 1):
            if (doc.metadata or {}).get("module") in acceptable:
                depth = i
                ctype = (doc.metadata or {}).get("chunk_type")
                break

        depths.append(depth)
        if ctype:
            first_ctype[ctype] += 1
        rows.append((s["id"], s["expected_module"], depth, ctype))

    found = [d for d in depths if d is not None]
    in_pool = [d for d in found if d <= POOL_SIZE]
    in_top8 = [d for d in found if d <= TOP_K]

    print("=" * 72)
    print(f"  RECALL CEILING — raw unfiltered vector search, depth {args.depth}")
    print("=" * 72)
    print(f"  queries                       {len(depths)}")
    print(f"  correct module found at all   {len(found)} ({len(found)/len(depths):.0%})")
    print(f"  ... within raw top-{TOP_K:<3}          {len(in_top8)} ({len(in_top8)/len(depths):.0%})")
    print(f"  ... within rerank pool ({POOL_SIZE})    {len(in_pool)} ({len(in_pool)/len(depths):.0%})")
    print(f"  ... only deeper than {POOL_SIZE:<3}       {len(found)-len(in_pool)}")
    print(f"  never found in {args.depth} chunks   {len(depths)-len(found)}")
    if found:
        found.sort()
        print(f"\n  median first-hit depth        {found[len(found)//2]}")
    print(f"\n  chunk type that surfaces it first: {dict(first_ctype)}")

    print("\n  Queries where the correct module never entered the rerank pool:")
    for qid, mod, depth, _c in rows:
        if depth is None or depth > POOL_SIZE:
            print(f"    [{qid:<8}] {mod:<52} depth={depth if depth else '>' + str(args.depth)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
