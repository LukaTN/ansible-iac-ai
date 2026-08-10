"""
Lightweight hybrid retrieval helpers (vector + lexical) for Ansible module docs.

No extra dependencies — uses token overlap and reciprocal rank fusion (RRF)
on the vector candidate pool, plus optional module-targeted Chroma lookups
when the query names a module explicitly.
"""
from __future__ import annotations

import re
from typing import Any

from langchain_core.documents import Document

# Full Ansible module FQCN: collection.namespace.module_name
_FQCN_MODULE_RE = re.compile(r"\b([a-z0-9]+\.[a-z0-9]+\.[a-z0-9_]+)\b")

# Short module-name prefixes (matched against module metadata suffix).
_SHORT_MODULE_RES = [
    re.compile(r"\b(azure_rm_[a-z0-9_]+)\b"),
    re.compile(r"\b(ec2_[a-z0-9_]+)\b"),
    re.compile(r"\b(s3_[a-z0-9_]+)\b"),
    re.compile(r"\b(iam_[a-z0-9_]+)\b"),
    re.compile(r"\b(rds_[a-z0-9_]+)\b"),
    re.compile(r"\b(elb_[a-z0-9_]+|elb[a-z0-9_]*)\b"),
    re.compile(r"\b(route53[a-z0-9_]*)\b"),
    re.compile(r"\b(aws_[a-z0-9_]+)\b"),
    re.compile(r"\b(k8s(?:_[a-z0-9_]+)?)\b"),
    re.compile(r"\b(helm(?:_[a-z0-9_]+)?)\b"),
]

RRF_K = 60
RRF_DENSE_WEIGHT = 0.62


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 1}


def extract_module_targets(query: str, routed_collections: list[str] | None = None) -> list[str]:
    """
    Pull explicit module identifiers from the query.

    Returns full FQCN strings when present; otherwise short names like
    ``ec2_instance`` that can be matched with a Chroma ``$contains`` filter.
    """
    q = (query or "").lower()
    found: list[str] = []

    for m in _FQCN_MODULE_RE.finditer(q):
        found.append(m.group(1))

    for rx in _SHORT_MODULE_RES:
        for m in rx.finditer(q):
            short = m.group(1)
            if short not in found:
                found.append(short)

    # De-dupe preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def enrich_query_for_embedding(
    query: str,
    analysis: Any,
    route: Any | None = None,
    module_targets: list[str] | None = None,
) -> str:
    """
    Append routing / module hints so the embedding model sees Ansible-specific
    vocabulary (cheap recall boost — still one embedding call).
    """
    parts = [query.strip()]
    targets = module_targets if module_targets is not None else extract_module_targets(query)

    if targets:
        parts.append("Modules: " + ", ".join(targets[:3]))

    if analysis.fqcn_collections:
        parts.append("Collections: " + ", ".join(sorted(analysis.fqcn_collections)))

    if route and route.collections:
        parts.append("Search scope: " + ", ".join(route.collections))

    if analysis.example_intent:
        parts.append("ansible playbook example yaml")
    elif analysis.param_intent:
        parts.append("required parameters options")
    elif analysis.write_intent:
        parts.append("ansible task module usage")

    enriched = " | ".join(p for p in parts if p)
    return enriched if enriched else query


def lexical_score(query: str, doc: Document, module_targets: list[str] | None = None) -> float:
    """
    BM25-like lexical score using token overlap on page_content + module name.
    Strong boost when the document module matches an explicit query target.
    """
    md = doc.metadata or {}
    module = str(md.get("module", ""))
    short_mod = module.split(".")[-1].lower() if module else ""
    content = f"{module} {doc.page_content or ''}".lower()

    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0

    doc_tokens = _tokenize(content)
    mod_tokens = _tokenize(short_mod)
    if not doc_tokens:
        return 0.0

    overlap = q_tokens & doc_tokens
    if not overlap:
        base = 0.0
    else:
        # Normalised overlap — favours queries where most terms hit the doc.
        precision = len(overlap) / len(q_tokens)
        recall = len(overlap) / len(doc_tokens)
        base = 0.55 * precision + 0.45 * min(recall * 3.0, 1.0)

    boost = 0.0
    targets = module_targets if module_targets is not None else extract_module_targets(query)
    for target in targets:
        t_lower = target.lower()
        if module.lower() == t_lower or module.lower().endswith("." + t_lower):
            boost = max(boost, 0.45)
        elif t_lower in short_mod or short_mod in t_lower:
            boost = max(boost, 0.28)
        elif t_lower in content:
            boost = max(boost, 0.12)

    mod_overlap = q_tokens & mod_tokens
    if mod_overlap:
        boost = max(boost, min(0.22, 0.06 * len(mod_overlap)))

    return min(1.0, base + boost)


def _doc_key(doc: Document) -> str:
    """Stable identity for a chunk, used to dedupe across ranked lists.

    Must name every metadata field that distinguishes two chunks of the same
    module and type, or distinct chunks collapse into one during fusion —
    ``optional_group_index`` in particular, since a module can have many
    optional-parameter groups.
    """
    md = doc.metadata or {}
    return "|".join(
        str(md.get(k, ""))
        for k in (
            "collection", "module", "chunk_type", "example_index", "example_part",
            "required_part", "overview_part", "optional_group_index", "purpose_part",
        )
    )


def reciprocal_rank_fusion(
    dense_ranked: list[tuple[Document, float]],
    sparse_ranked: list[tuple[Document, float]],
    *,
    k: int = RRF_K,
    dense_weight: float = RRF_DENSE_WEIGHT,
) -> list[tuple[Document, float]]:
    """
    Merge dense (vector) and sparse (lexical) ranked lists with RRF.
    Returns (doc, fused_score) pairs sorted best-first.
    """
    if not sparse_ranked:
        return list(dense_ranked)
    if not dense_ranked:
        return list(sparse_ranked)

    sparse_weight = 1.0 - dense_weight
    scores: dict[str, float] = {}
    docs: dict[str, Document] = {}

    for rank, (doc, _raw) in enumerate(dense_ranked, 1):
        key = _doc_key(doc)
        scores[key] = scores.get(key, 0.0) + dense_weight / (k + rank)
        docs[key] = doc

    for rank, (doc, _raw) in enumerate(sparse_ranked, 1):
        key = _doc_key(doc)
        scores[key] = scores.get(key, 0.0) + sparse_weight / (k + rank)
        docs[key] = doc

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [(docs[key], score) for key, score in ordered]


def merge_vector_and_lexical(
    query: str,
    vector_results: list[tuple[Document, float]],
    *,
    module_targets: list[str] | None = None,
) -> list[tuple[Document, float]]:
    """
    Re-order vector hits using lexical signals.

    When the query names a module explicitly, lexical score dominates (FQCN and
    short-prefix matches are high-precision). Otherwise fuse with RRF.
    """
    if not vector_results:
        return []

    targets = module_targets if module_targets is not None else extract_module_targets(query)
    if targets:
        scored = [
            (doc, raw, lexical_score(query, doc, targets))
            for doc, raw in vector_results
        ]
        scored.sort(key=lambda x: (x[2], x[1]), reverse=True)
        return [(doc, raw) for doc, raw, _ in scored]

    lexical = [
        (doc, lexical_score(query, doc, targets))
        for doc, _ in vector_results
    ]
    lexical.sort(key=lambda x: x[1], reverse=True)

    fused = reciprocal_rank_fusion(vector_results, lexical)
    raw_by_doc = {id(doc): raw for doc, raw in vector_results}
    return [(doc, raw_by_doc.get(id(doc), score)) for doc, score in fused]


def fuse_with_sparse(
    query: str,
    dense_results: list[tuple[Document, float]],
    vectorstore,
    *,
    collections: list[str] | None = None,
    limit: int = 32,
) -> list[tuple[Document, float]]:
    """
    Widen the candidate pool with BM25 hits and re-order the whole thing by RRF.

    The dense and sparse rankings fail on different queries, so fusing them
    recovers modules the embedding never surfaces — typically ones whose
    distinguishing vocabulary lives in parameter names rather than in the
    one-line synopsis.

    Scores: a chunk that also ranked densely keeps its relevance score, since
    downstream stages compare against it. A sparse-only chunk has no comparable
    score, so it takes the median of the dense pool — middling by construction,
    left for the reranker to promote or bury on its own merits.
    """
    from rag.sparse_index import get_sparse_index

    index = get_sparse_index(vectorstore)
    if index is None:
        return dense_results

    sparse = index.search(query, k=limit, collections=collections)
    if not sparse:
        return dense_results

    fused = reciprocal_rank_fusion(dense_results, sparse)

    dense_by_key = {_doc_key(doc): score for doc, score in dense_results}
    dense_scores = sorted(dense_by_key.values())
    median = dense_scores[len(dense_scores) // 2] if dense_scores else 0.5

    return [
        (doc, dense_by_key.get(_doc_key(doc), median))
        for doc, _fused_score in fused
    ][:limit]


def module_targeted_search(
    vectorstore,
    module_target: str,
    *,
    where: dict | None,
    k: int = 12,
) -> list[tuple[Document, float]]:
    """
    Fetch chunks for an explicitly named module (full FQCN or short suffix).
    One extra Chroma query — only called when module targets are detected.
    """
    target = (module_target or "").strip().lower()
    if not target:
        return []

    if "." in target:
        mod_filter: dict = {"module": {"$eq": module_target}}
    else:
        mod_filter = {"module": {"$contains": target}}

    filt = mod_filter
    if where:
        filt = {"$and": [where, mod_filter]}

    try:
        hits = vectorstore.similarity_search_with_relevance_scores(
            query=module_target,
            k=k,
            filter=filt,
        )
        return list(hits)
    except Exception:
        try:
            hits = vectorstore.similarity_search_with_relevance_scores(
                query=module_target,
                k=k,
                filter=mod_filter,
            )
            return list(hits)
        except Exception:
            return []


def supplement_with_module_targets(
    query: str,
    vectorstore,
    vector_results: list[tuple[Document, float]],
    *,
    where: dict | None,
    max_targets: int = 2,
) -> list[tuple[Document, float]]:
    """
    When the user names a module, pull its chunks directly and prepend to the
    candidate pool (deduped). Improves accuracy when vector search misses FQCN.
    """
    targets = extract_module_targets(query)[:max_targets]
    if not targets:
        return vector_results

    seen = {_doc_key(doc) for doc, _ in vector_results}
    extra: list[tuple[Document, float]] = []

    for target in targets:
        for doc, score in module_targeted_search(vectorstore, target, where=where):
            key = _doc_key(doc)
            if key in seen:
                continue
            seen.add(key)
            # Lexical certainty bonus so targeted hits compete in rerank.
            lex = lexical_score(query, doc, targets)
            extra.append((doc, max(float(score), 0.5 + lex * 0.3)))

    if not extra:
        return vector_results

    extra.sort(key=lambda x: x[1], reverse=True)
    return extra + list(vector_results)
