"""
Ablation: is the repaired overview chunk better than the old one?

The old `build_overview_doc` emitted six fields, three of which were constant or
empty on all 1222 modules ("Category: general", an empty "Use this module to:",
and "Required parameters:" — the last because it read a `required_params` key
the parser never writes). This builds throwaway Chroma collections holding
*only* overview chunks in each format and scores them on the benchmark,
isolating the chunk text from routing, BM25 and reranking.

Usage: python scripts/ablate_overview_chunk.py
"""

from __future__ import annotations

import contextlib
import glob
import json
import shutil
import sys
import tempfile
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from rag.indexer import get_embeddings  # noqa: E402
from rag.ingestion import build_overview_doc  # noqa: E402

DATASET = BACKEND_ROOT / "rag" / "retrieval_benchmark.json"


def legacy_overview(module: dict, collection: str) -> Document:
    """The pre-v4 text, reproduced so the comparison stays runnable."""
    mod = module.get("module", "")
    kws = " | ".join(module.get("task_keywords", []))
    cat = module.get("category", "general")
    req = ", ".join(module.get("required_params", []))  # always empty: wrong key
    text = (
        f"Module: {mod}\n"
        f"Collection: {collection}\n"
        f"Category: {cat}\n"
        f"Description: {module.get('description', '')}\n"
        f"Use this module to: {kws}\n"
        f"Required parameters: {req}"
    )
    return Document(page_content=text, metadata={"module": mod, "collection": collection})


def load_modules() -> list[tuple[dict, str]]:
    out = []
    for f in sorted(glob.glob(str(PROJECT_ROOT / "data" / "parsed" / "*" / "*.json"))):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        coll = d.get("collection") or Path(f).parent.name.replace("_", ".", 1)
        out.append((d, coll))
    return out


def build_store(docs: list[Document], name: str, tmpdir: str, embeddings) -> Chroma:
    vs = Chroma(
        collection_name=name,
        embedding_function=embeddings,
        persist_directory=tmpdir,
        collection_metadata={"hnsw:space": "cosine"},
    )
    for i in range(0, len(docs), 200):
        vs.add_documents(docs[i:i + 200])
        print(f"    {name}: {min(i + 200, len(docs))}/{len(docs)}", end="\r")
    print(f"    {name}: {len(docs)} chunks indexed      ")
    return vs


def score(vs: Chroma, samples: list[dict], k: int = 8) -> tuple[float, float, float]:
    top1 = hit = 0
    rr = 0.0
    for s in samples:
        acc = set(s.get("acceptable", [])) | {s["expected_module"]}
        hits = vs.similarity_search_with_relevance_scores(query=s["query"], k=k)
        mods = [(d.metadata or {}).get("module") for d, _ in hits]
        if mods and mods[0] in acc:
            top1 += 1
        if any(m in acc for m in mods):
            hit += 1
        rank = next((i for i, m in enumerate(mods, 1) if m in acc), 0)
        rr += 1.0 / rank if rank else 0.0
    n = len(samples)
    return top1 / n, hit / n, rr / n


def main() -> int:
    samples = json.loads(DATASET.read_text(encoding="utf-8"))["samples"]
    modules = load_modules()
    print(f"  {len(modules)} modules, {len(samples)} benchmark queries")

    old, new = [], []
    for mod, coll in modules:
        old.append(legacy_overview(mod, coll))
        parts = build_overview_doc(mod, coll)
        if parts:
            d = parts[0]
            new.append(Document(page_content=d.page_content, metadata={
                "module": d.metadata.get("module"), "collection": coll}))

    embeddings = get_embeddings()
    tmpdir = tempfile.mkdtemp(prefix="ablate_overview_")
    try:
        print("\n  Indexing (overview chunks only):")
        vs_old = build_store(old, "old", tmpdir, embeddings)
        vs_new = build_store(new, "new", tmpdir, embeddings)

        print("\n  Scoring:")
        o = score(vs_old, samples)
        n = score(vs_new, samples)

        print("\n" + "=" * 64)
        print("  OVERVIEW-CHUNK ABLATION (overview chunks only, no rerank)")
        print("=" * 64)
        print(f"  {'':<12} {'top1':>8} {'hit@8':>8} {'mrr':>8}")
        print(f"  {'legacy':<12} {o[0]:>7.1%} {o[1]:>8.1%} {o[2]:>8.3f}")
        print(f"  {'repaired':<12} {n[0]:>7.1%} {n[1]:>8.1%} {n[2]:>8.3f}")
        print(f"  {'delta':<12} {n[0]-o[0]:>+7.1%} {n[1]-o[1]:>+8.1%} {n[2]-o[2]:>+8.3f}")
    finally:
        with contextlib.suppress(Exception):
            shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
