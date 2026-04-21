"""
=============================================================
  AnsibleAI RAG — Step 3 : Retriever
  LangChain retriever with metadata filtering + reranking.
=============================================================
"""

import os
from typing import List, Optional

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from langchain_chroma import Chroma
except ImportError:
    from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

TOP_K         = 6     # default number of chunks to retrieve
SCORE_THRESH  = 0.35  # minimum similarity score (cosine, 0-1)


# ─────────────────────────────────────────────
#  COLLECTION DETECTOR
# ─────────────────────────────────────────────

COLLECTION_KEYWORDS = {
    "amazon.aws"          : ["ec2", "s3", "aws", "amazon", "lambda", "iam", "rds", "vpc", "cloudwatch"],
    "azure.azcollection"  : ["azure", "vm", "resource group", "blob", "cosmos", "aks", "arm"],
    "kubernetes.core"     : ["kubernetes", "k8s", "pod", "deployment", "namespace", "helm", "kubectl", "cluster"],
    "community.general"   : ["docker", "systemd", "git", "ini", "cron", "user", "group", "file"],
    "ansible.builtin"     : ["copy", "template", "shell", "command", "service", "package", "apt", "yum"],
}


def detect_collection(query: str) -> Optional[str]:
    """
    Detect the most likely collection from the query.
    Returns collection name or None (search all).
    """
    query_lower = query.lower()
    scores = {}
    for coll, keywords in COLLECTION_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in query_lower)
        if score > 0:
            scores[coll] = score

    if not scores:
        return None  # search all collections

    best = max(scores, key=lambda k: scores[k])
    return best


# ─────────────────────────────────────────────
#  RETRIEVER
# ─────────────────────────────────────────────

def retrieve(
    query: str,
    vectorstore: Chroma,
    top_k: int = TOP_K,
    collection_filter: Optional[str] = None,
) -> List[Document]:
    """
    Retrieve top-K relevant documents for a query.

    Strategy:
    1. Detect collection from query keywords
    2. Apply metadata filter if collection detected
    3. Similarity search with score threshold
    4. Deduplicate by module (keep best chunk per module)
    5. Return ranked results
    """

    final, collection_filter = _retrieve_ranked(
        query=query,
        vectorstore=vectorstore,
        top_k=top_k,
        collection_filter=collection_filter,
    )

    print(f"  [Retriever] Results ({len(final)}):")
    for doc, score in final:
        print(f"    {score:.3f}  {doc.metadata.get('module'):<45} [{doc.metadata.get('chunk_type')}]")

    return [doc for doc, _ in final]


def _retrieve_ranked(
    query: str,
    vectorstore: Chroma,
    top_k: int = TOP_K,
    collection_filter: Optional[str] = None,
) -> tuple[list[tuple[Document, float]], Optional[str]]:
    """Shared retrieval logic used by both retrieve() and metadata path."""
    if collection_filter is None:
        collection_filter = detect_collection(query)

    print(f"\n  [Retriever] Query: '{query[:60]}...'")
    print(f"  [Retriever] Collection filter: {collection_filter or 'all'}")

    where = {"collection": {"$eq": collection_filter}} if collection_filter else None

    try:
        results = vectorstore.similarity_search_with_relevance_scores(
            query=query,
            k=top_k * 3,
            filter=where,
        )
    except Exception:
        results = vectorstore.similarity_search_with_relevance_scores(
            query=query,
            k=top_k * 3,
        )

    filtered = [(doc, score) for doc, score in results if score >= SCORE_THRESH]
    if not filtered:
        print(f"  [Retriever] No results above threshold {SCORE_THRESH}, relaxing...")
        filtered = results[:top_k * 2]

    seen_modules = {}
    reranked: list[tuple[Document, float]] = []

    for doc, score in filtered:
        mod = doc.metadata.get("module", "unknown")
        ctype = doc.metadata.get("chunk_type", "")

        # Keep high-value chunks for grounding/generation.
        if ctype in ("required_params", "example"):
            reranked.append((doc, score))
            continue

        if mod not in seen_modules or score > seen_modules[mod]:
            seen_modules[mod] = score
            reranked.append((doc, score))

    reranked.sort(key=lambda x: x[1], reverse=True)
    ranked = reranked[:top_k]

    primary_module = None
    for doc, _ in ranked:
        if doc.metadata.get("chunk_type") == "overview":
            primary_module = doc.metadata.get("module")
            break
    if not primary_module and ranked:
        primary_module = ranked[0][0].metadata.get("module")

    # Deterministic coverage: keep at least one required_params chunk and
    # up to three example chunks for the primary module when available.
    if primary_module:
        def _has(kind: str) -> bool:
            return any(
                d.metadata.get("module") == primary_module
                and d.metadata.get("chunk_type") == kind
                for d, _ in ranked
            )

        if not _has("required_params"):
            rp = next(
                (
                    it for it in reranked
                    if it[0].metadata.get("module") == primary_module
                    and it[0].metadata.get("chunk_type") == "required_params"
                ),
                None,
            )
            if rp:
                ranked = ranked[:-1] + [rp] if len(ranked) >= top_k else ranked + [rp]

        ex_items = [
            it for it in reranked
            if it[0].metadata.get("module") == primary_module
            and it[0].metadata.get("chunk_type") == "example"
        ]
        ex_ids = {
            id(d)
            for d, _ in ranked
            if d.metadata.get("module") == primary_module and d.metadata.get("chunk_type") == "example"
        }
        for ex in ex_items[:3]:
            if id(ex[0]) in ex_ids:
                continue
            if len(ranked) < top_k:
                ranked.append(ex)
            else:
                ranked[-1] = ex
            ex_ids.add(id(ex[0]))

        ranked.sort(key=lambda x: x[1], reverse=True)
        ranked = ranked[:top_k]

    print("  [Retriever][Debug] Ranked chunks sent to agent:")
    if not ranked:
        print("    - (none)")
    for i, (doc, score) in enumerate(ranked, start=1):
        md = doc.metadata or {}
        ex_i = md.get("example_index", "-")
        req = md.get("required_params_list", "")
        req_preview = req[:90] + ("..." if len(req) > 90 else "") if req else "-"
        print(
            "    {idx}. module={module} | chunk_type={ctype} | score={score:.3f} | ex_idx={exi} | required_params={req}".format(
                idx=i,
                module=md.get("module", "unknown"),
                ctype=md.get("chunk_type", "unknown"),
                score=float(score),
                exi=ex_i,
                req=req_preview,
            )
        )

    return ranked, collection_filter


def get_retrieval_metadata(
    query: str,
    vectorstore: Chroma,
    top_k: int = TOP_K,
) -> dict:
    """
    Retrieve docs + return metadata for API response.
    """
    top_items, collection_filter = _retrieve_ranked(
        query=query,
        vectorstore=vectorstore,
        top_k=top_k,
        collection_filter=None,
    )
    docs = [d for d, _ in top_items]
    scores = [s for _, s in top_items]

    # Primary module = highest scored overview
    primary_module = primary_collection = None
    primary_score  = 0.0
    for doc, score in zip(docs, scores):
        if doc.metadata.get("chunk_type") == "overview" and score > primary_score:
            primary_module     = doc.metadata.get("module")
            primary_collection = doc.metadata.get("collection")
            primary_score      = score

    if not primary_module and docs:
        primary_module     = docs[0].metadata.get("module")
        primary_collection = docs[0].metadata.get("collection")
        primary_score      = scores[0]

    # Include module candidates to reduce generator hallucination.
    module_candidates = []
    for doc, _ in top_items:
        mod = doc.metadata.get("module")
        if mod and mod not in module_candidates:
            module_candidates.append(mod)

    # Collect required params from retrieved chunk metadata for the primary module.
    required_params = []
    for d in docs:
        if d.metadata.get("module") != primary_module:
            continue
        if d.metadata.get("chunk_type") != "required_params":
            continue
        raw = d.metadata.get("required_params_list", "")
        if not raw:
            continue
        for p in [x.strip() for x in raw.split(",") if x.strip()]:
            if p not in required_params:
                required_params.append(p)

    example_modules = []
    for d in docs:
        if d.metadata.get("module") != primary_module:
            continue
        if d.metadata.get("chunk_type") == "example":
            ex_i = d.metadata.get("example_index", "?")
            example_modules.append(f"{d.metadata.get('module')}#example_{ex_i}")
            if len(example_modules) >= 3:
                break

    print(
        f"  [Retriever][Meta] primary_module={primary_module} "
        f"required_params={required_params or []} "
        f"examples={example_modules or []}"
    )

    return {
        "docs"               : docs,
        "scores"             : scores,
        "primary_module"     : primary_module,
        "primary_collection" : primary_collection,
        "primary_score"      : round(primary_score, 3),
        "collection_filter"  : collection_filter,
        "module_candidates"  : module_candidates,
        "source_url"         : next(
            (d.metadata.get("source_url") for d in docs
             if d.metadata.get("module") == primary_module and d.metadata.get("source_url")),
            ""
        ),
        "required_params"    : required_params,
    }
