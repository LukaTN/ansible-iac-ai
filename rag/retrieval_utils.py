"""
Shared utilities for retrieval metadata extraction.
Avoids copy-paste of primary_module detection across retriever + tools.
"""
from __future__ import annotations

from typing import Any

from langchain_core.documents import Document


# Decay weights for the per-module rank score: the best chunk dominates, but a
# second and third supporting chunk add real evidence. A module whose synopsis,
# params, and examples all match beats one lucky chunk from a sibling module
# (e.g. ec2_launch_template's overview outscoring ec2_instance's by 0.01 while
# ec2_instance has three chunks in the pack). Weights re-derived against
# rag/retrieval_benchmark.json — see reports/retrieval_baseline.json.
_RANK_DECAY = (1.0, 0.30, 0.10)


def _module_rank_score(sorted_scores: list[float]) -> float:
    """Decayed sum of a module's best chunk scores (best-first input)."""
    return sum(w * s for w, s in zip(_RANK_DECAY, sorted_scores))


def _aggregate_per_module(
    docs: list[Document],
    scores: list[float],
) -> tuple[dict[str, float], dict[str, float], dict[str, int], dict[str, str], dict[str, int], dict[str, float]]:
    """Per-module top score, sum, hit count, collection, first appearance, rank score."""
    module_agg: dict[str, float] = {}
    module_top: dict[str, float] = {}
    module_hits: dict[str, int] = {}
    module_coll: dict[str, str] = {}
    module_first_idx: dict[str, int] = {}
    module_scores: dict[str, list[float]] = {}

    for idx, (doc, score) in enumerate(zip(docs, scores)):
        mod = doc.metadata.get("module")
        if not mod:
            continue
        s = float(score)
        module_agg[mod] = module_agg.get(mod, 0.0) + s
        module_top[mod] = max(s, module_top.get(mod, -999.0))
        module_hits[mod] = module_hits.get(mod, 0) + 1
        module_coll[mod] = doc.metadata.get("collection", "")
        module_scores.setdefault(mod, []).append(s)
        if mod not in module_first_idx:
            module_first_idx[mod] = idx

    module_rank: dict[str, float] = {
        mod: _module_rank_score(sorted(ss, reverse=True))
        for mod, ss in module_scores.items()
    }
    return module_agg, module_top, module_hits, module_coll, module_first_idx, module_rank


def extract_primary_module(
    docs: list[Document],
    scores: list[float],
) -> tuple[str | None, str | None, float]:
    """
    Given ranked (doc, score) pairs, return (primary_module, primary_collection, primary_score).

    Picks the module with the highest *aggregated* rank score (decayed sum of its
    best chunks) rather than the single best chunk, so a module supported by
    several matching chunks beats a sibling with one marginally higher chunk.
    Ties break on higher aggregate score, then earlier position in the ranked list.
    """
    module_agg, module_top, _, module_coll, module_first_idx, module_rank = (
        _aggregate_per_module(docs, scores)
    )

    if not module_top:
        if docs:
            return (
                docs[0].metadata.get("module"),
                docs[0].metadata.get("collection"),
                scores[0] if scores else 0.0,
            )
        return None, None, 0.0

    primary_module = max(
        module_rank,
        key=lambda m: (
            module_rank.get(m, 0.0),
            module_agg.get(m, 0.0),
            -module_first_idx.get(m, 999),
        ),
    )
    return (
        primary_module,
        module_coll.get(primary_module),
        module_top.get(primary_module, 0.0),
    )


def list_ranked_modules(docs: list[Document], scores: list[float], *, limit: int = 8) -> list[dict[str, Any]]:
    """
    Modules appearing in this retrieval pack, sorted best-first for prompts and UI.

    Each entry: module, collection, top_score, aggregate_score, chunk_hits, best_rank (1-based).
    """
    module_agg, module_top, module_hits, module_coll, module_first_idx, module_rank = (
        _aggregate_per_module(docs, scores)
    )
    if not module_top:
        return []

    ranked = sorted(
        module_rank.keys(),
        key=lambda m: (
            module_rank.get(m, 0.0),
            module_agg.get(m, 0.0),
            -module_first_idx.get(m, 999),
        ),
        reverse=True,
    )

    out: list[dict[str, Any]] = []
    for i, m in enumerate(ranked[:limit], start=1):
        out.append(
            {
                "rank": i,
                "module": m,
                "collection": module_coll.get(m, ""),
                "top_score": round(module_top.get(m, 0.0), 3),
                "rank_score": round(module_rank.get(m, 0.0), 3),
                "aggregate_score": round(module_agg.get(m, 0.0), 3),
                "chunk_hits": module_hits.get(m, 0),
                "best_rank": module_first_idx.get(m, 0) + 1,  # 1-based index in chunk list
            }
        )
    return out


def collect_required_params_by_module(docs: list[Document]) -> dict[str, list[str]]:
    """Merge required_params chunks per module (full collection.module key)."""
    out: dict[str, list[str]] = {}
    for d in docs:
        if d.metadata.get("chunk_type") != "required_params":
            continue
        mod = d.metadata.get("module")
        if not mod:
            continue
        raw = d.metadata.get("required_params_list", "") or ""
        params = [x.strip() for x in raw.split(",") if x.strip()]
        if not params:
            continue
        bucket = out.setdefault(mod, [])
        for p in params:
            if p not in bucket:
                bucket.append(p)
    return out


def format_ranked_modules_lines(ranked: list[dict[str, Any]]) -> str:
    """One line per module for the playbook user prompt."""
    if not ranked:
        return "- (none)"
    lines = []
    for e in ranked:
        lines.append(
            f"- #{e['rank']} `{e['module']}` (top_score={e['top_score']}, "
            f"chunks={e['chunk_hits']}, first_seen_rank={e['best_rank']})"
        )
    return "\n".join(lines)


def build_retrieval_meta(
    top_items: list[tuple[Document, float]],
    collection_filter: str | None,
) -> dict:
    """
    Build the standard retrieval metadata dict from ranked (doc, score) pairs.
    Used by both the retriever and the tools layer.
    """
    docs = [d for d, _ in top_items]
    scores = [s for _, s in top_items]

    primary_module, primary_collection, primary_score = extract_primary_module(docs, scores)
    ranked_modules = list_ranked_modules(docs, scores, limit=8)
    required_params_by_module = collect_required_params_by_module(docs)

    module_candidates: list[str] = []
    for d in docs:
        mod = d.metadata.get("module")
        if mod and mod not in module_candidates:
            module_candidates.append(mod)

    required_params: list[str] = []
    if primary_module:
        required_params = list(required_params_by_module.get(primary_module, []))

    source_url = next(
        (
            d.metadata.get("source_url")
            for d in docs
            if d.metadata.get("module") == primary_module and d.metadata.get("source_url")
        ),
        "",
    )

    return {
        "docs": docs,
        "scores": scores,
        "primary_module": primary_module,
        "primary_collection": primary_collection,
        "primary_score": round(primary_score, 3),
        "collection_filter": collection_filter,
        "module_candidates": module_candidates,
        "ranked_modules": ranked_modules,
        "required_params_by_module": required_params_by_module,
        "source_url": source_url,
        "required_params": required_params,
    }
