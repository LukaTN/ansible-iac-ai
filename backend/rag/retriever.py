"""
=============================================================
  AnsibleAI RAG — Step 3 : Retriever (v2 pipeline)
  LangChain retriever with adaptive routing + query-aware reranking.

  Six-stage pipeline:
    1. Query analysis      — intent class (write/read/example/param) + FQCN regex
    2. Adaptive routing    — confidence score -> single ($eq) / multi ($in) / all
    3. Hybrid vector search — enriched embedding query + module-target supplement + RRF
   3b. BM25 fusion         — sparse index over the same chunks, RRF'd into the pool
    4. Query-aware rerank  — chunk-type boosts + coherence + read/write intent
    5. Diversity + coverage — module cap + collection cap + O(n) backfill map
    6. Top-K results
=============================================================
"""

import os
import re as _re
from dataclasses import dataclass, field
from typing import Any

from rag.hybrid_search import (
    enrich_query_for_embedding,
    extract_module_targets,
    fuse_with_sparse,
    merge_vector_and_lexical,
    supplement_with_module_targets,
)
from rag.retrieval_utils import build_retrieval_meta, extract_primary_module

# backend/rag/retriever.py → repository root
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from langchain_core.documents import Document

TOP_K         = 8      # default number of chunks to retrieve
SCORE_THRESH  = 0.34   # base minimum similarity score (cosine-like, 0-1)
# Candidate pool depth before reranking. At 4 the correct module was landing
# just below the cutoff on ~1 query in 7, where no amount of reranking can
# recover it. Widening costs one Chroma query with a larger k — no extra
# embedding call — and buys 12 points of hit@8. Past 8 it flattens out.
# Re-derive with scripts/sweep_pool_depth.py.
QUERY_K_MULTIPLIER = 8

# Diversity caps -------------------------------------------------------------
# When searching unrouted ("all"), avoid one collection filling the entire top_k.
MAX_CHUNKS_PER_COLLECTION_UNFILTERED = 4
# A single module may never occupy more than this many slots in the *initial*
# diversity pass (fixes overview/example chunks dominating top_k). The primary
# module is allowed a larger budget during coverage backfill.
MAX_CHUNKS_PER_MODULE = 2
PRIMARY_MODULE_BUDGET = 6   # primary module slot budget after coverage backfill
PRIMARY_EXAMPLE_CAP = 3     # max example chunks kept for the primary module

# Adaptive-routing thresholds (confidence is normalised, ~0..1+) -------------
ROUTE_STRONG_CONF   = 0.45  # a collection this confident can win outright
ROUTE_MIN_CONF      = 0.22  # minimum confidence to join a multi-collection route
ROUTE_SEPARATION    = 0.18  # gap that makes the leader a clear single winner
ROUTE_MAX_MULTI     = 3     # cap on collections in a multi route

# Confidence weights ---------------------------------------------------------
FQCN_CONF           = 0.80  # explicit FQCN / module-name match for a collection
KEYWORD_CONF_CAP    = 0.50  # max confidence contribution from keyword matches
KEYWORD_SATURATION  = 1.0   # keyword-weight sum that reaches the cap


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def default_apply_auto_collection_filter_for_generation() -> bool:
    """
    Adaptive collection routing is ON by default (single / multi / all).

    Set ``RAG_DISABLE_AUTO_COLLECTION_FILTER=1`` to force unfiltered search
    (useful for debugging or when the agent's own collection voting is preferred).
    """
    if _env_truthy("RAG_DISABLE_AUTO_COLLECTION_FILTER"):
        return False
    return True


def _max_chunks_per_collection_unfiltered() -> int:
    raw = (os.getenv("RAG_MAX_CHUNKS_PER_COLLECTION") or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return MAX_CHUNKS_PER_COLLECTION_UNFILTERED


def _max_chunks_per_module() -> int:
    raw = (os.getenv("RAG_MAX_CHUNKS_PER_MODULE") or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return MAX_CHUNKS_PER_MODULE


# ─────────────────────────────────────────────
#  STAGE 1 — QUERY ANALYSIS
# ─────────────────────────────────────────────

WRITE_INTENT_TERMS = (
    "create", "deploy", "provision", "configure", "update",
    "delete", "remove", "patch", "scale", "apply", "launch", "set up",
)
READ_INTENT_TERMS = (
    "info", "list", "show", "get", "describe", "status", "query", "fetch",
)

# Token-matched action verbs (checked against the tokenised query, so "add"
# cannot match "address"). These extend WRITE_INTENT_TERMS: most real tasks say
# "attach a disk" or "schedule a script", not "create"/"deploy", and without
# them the *_info demotion in _compute_intent_boost almost never fired.
WRITE_INTENT_TOKENS = frozenset({
    "add", "adds", "attach", "attaches", "allow", "allows", "open", "opens",
    "enable", "enables", "disable", "disables", "install", "installs",
    "mount", "mounts", "format", "formats", "grant", "grants",
    "publish", "publishes", "upload", "uploads", "download", "downloads",
    "schedule", "schedules", "reserve", "reserves", "store", "stores",
    "render", "renders", "clone", "clones", "extract", "extracts",
    "increase", "increases", "raise", "raises", "change", "changes",
    "resize", "restart", "restarts", "start", "starts", "stop", "stops",
    "run", "make", "ensure", "register", "registers", "rotate", "rotates",
    "upgrade", "upgrades", "expand", "expands", "evict", "evicts",
    "drain", "drains", "mark", "marks", "transfer", "transfers",
})
READ_INTENT_TOKENS = frozenset({
    "read", "reads", "view", "views", "inspect", "inspects",
    "display", "displays", "retrieve", "retrieves",
})
EXAMPLE_INTENT_TERMS = (
    "example", "examples", "sample", "snippet", "demo", "how to", "how do i",
    "show me", "playbook for", "playbook that",
)
PARAM_INTENT_TERMS = (
    "parameter", "parameters", "required field", "required fields", "options",
    "arguments", "argument", "what fields", "which fields", "settings",
    "what parameters", "required parameter",
)

# Base chunk-type rerank deltas (query-neutral). Query-aware deltas are layered
# on top of these in ``_chunk_type_boost`` based on the analysed intent.
#
# These track how often each chunk type is the first to surface the correct
# module, normalised by how many chunks of that type exist. `overview` used to
# carry +0.12, which was propping up a chunk that was three-quarters constant
# boilerplate; once the v4 chunks carried real content that bonus became pure
# crowding. Re-derive with scripts/sweep_chunk_type_boost.py after any change
# to the chunk schema — the two are coupled.
CHUNK_TYPE_BOOST = {
    "overview": 0.0,
    "example": 0.05,
    "required_params": 0.04,
    "optional_params": 0.01,
}


@dataclass
class QueryAnalysis:
    """Structured view of a user query used by routing and reranking."""
    raw: str
    lower: str
    tokens: set[str]
    write_intent: bool
    read_intent: bool
    example_intent: bool
    param_intent: bool
    fqcn_collections: set[str] = field(default_factory=set)


def _contains_any(text: str, terms) -> bool:
    return any(t in text for t in terms)


def analyze_query(query: str) -> QueryAnalysis:
    """Stage 1: classify intent and detect explicit FQCN / module-name signals."""
    lower = (query or "").lower()
    tokens = {t for t in _re.split(r"[^a-z0-9]+", lower) if len(t) > 1}
    if "kubernetes" in tokens:
        tokens.add("k8s")

    return QueryAnalysis(
        raw=query or "",
        lower=lower,
        tokens=tokens,
        write_intent=_contains_any(lower, WRITE_INTENT_TERMS) or bool(tokens & WRITE_INTENT_TOKENS),
        read_intent=_contains_any(lower, READ_INTENT_TERMS) or bool(tokens & READ_INTENT_TOKENS),
        example_intent=_contains_any(lower, EXAMPLE_INTENT_TERMS),
        param_intent=_contains_any(lower, PARAM_INTENT_TERMS),
        fqcn_collections=_detect_fqcn_collections(lower),
    )


def _chunk_type_boost(ctype: str, analysis: QueryAnalysis) -> float:
    """
    Stage 4 helper: query-aware chunk-type boost.

    Layers intent-specific deltas on top of the neutral CHUNK_TYPE_BOOST so that
    an "example" query promotes example chunks and a "parameters" query promotes
    required/optional param chunks, instead of one static boost for every query.
    """
    boost = CHUNK_TYPE_BOOST.get(ctype, 0.0)

    if analysis.example_intent:
        if ctype == "example":
            boost += 0.12
        elif ctype in ("required_params", "optional_params"):
            boost -= 0.02

    if analysis.param_intent:
        if ctype == "required_params":
            boost += 0.12
        elif ctype == "optional_params":
            boost += 0.06
        elif ctype == "example":
            boost -= 0.02

    if analysis.write_intent and not analysis.example_intent:
        if ctype == "example":
            boost += 0.03

    if analysis.read_intent and ctype == "overview":
        boost += 0.02

    return max(-0.15, min(0.28, boost))


# ─────────────────────────────────────────────
#  STAGE 2 — COLLECTION DETECTION / ADAPTIVE ROUTING
# ─────────────────────────────────────────────

COLLECTION_KEYWORDS = {
    "amazon.aws": [
        "ec2", "s3", "aws", "amazon", "lambda", "iam", "rds", "vpc",
        "cloudwatch", "route53", "elb", "alb", "ecs", "eks", "sns",
        "sqs", "cloudfront", "dynamodb", "elasticache", "kinesis",
        "secretsmanager", "ssm", "cloudformation", "autoscaling",
        "security group", "subnet", "internet gateway", "nat gateway",
        "load balancer", "hosted zone", "record set", "bucket",
        "instance", "ami", "key pair", "elastic ip", "postgresql",
        "object storage",
    ],
    "azure.azcollection": [
        "azure", "vm", "resource group", "blob", "cosmos", "aks", "arm",
        "virtual network", "vnet", "subnet", "public ip", "nsg",
        "key vault", "storage account", "app service", "function",
        "container", "acr", "load balancer", "dns", "cdn",
        "service bus", "event hub", "sql", "mysql", "postgresql",
        "managed disk", "availability set", "scale set", "aks cluster",
    ],
    "kubernetes.core": [
        "kubernetes", "k8s", "pod", "deployment", "namespace", "helm",
        "kubectl", "cluster", "configmap", "secret", "service",
        "ingress", "daemonset", "statefulset", "replicaset", "job",
        "cronjob", "persistentvolume", "pvc", "serviceaccount", "rbac",
        "clusterrole", "role", "rollout", "rollback", "replica",
    ],
    # A keyword belongs here only if this collection can actually serve it.
    # "docker" used to route here and nothing else, which locked every container
    # query out of the index entirely — the docker modules live in
    # community.docker, which is not scraped. Same story for "synchronize"
    # (ansible.posix). tests/test_routing_keywords.py guards against reintroducing
    # keywords with no backing module.
    "community.general": [
        "systemd", "git", "ini", "cron", "user", "group",
        "file", "ufw", "firewall", "timezone", "locale",
        "networkmanager", "nmcli",
    ],
    "ansible.builtin": [
        "copy", "template", "shell", "command", "service", "package",
        "apt", "yum", "dnf", "pip", "file", "lineinfile", "blockinfile",
        "fetch", "cron", "user", "group", "hostname",
    ],
}


def _build_keyword_specificity() -> dict[str, float]:
    """
    Issue #1: weight each keyword by how *discriminating* it is.

    A keyword present in N collections is worth 1/N (a term shared by
    ``ansible.builtin`` and ``community.general`` counts half; a unique term
    counts full). Built once at import.
    """
    counts: dict[str, int] = {}
    for kws in COLLECTION_KEYWORDS.values():
        for kw in kws:
            counts[kw] = counts.get(kw, 0) + 1
    return {kw: 1.0 / n for kw, n in counts.items()}


_KEYWORD_SPECIFICITY = _build_keyword_specificity()

# Module-name / FQCN signals. These are strong, unambiguous routing hints
# (issue #1: "azure_rm_deployment" must score for Azure even with no keyword).
# Patterns are matched against the lowercased query. Extend per collection.
_MODULE_PREFIX_PATTERNS: dict[str, list[str]] = {
    "amazon.aws": [
        r"\bec2_[a-z0-9_]+", r"\bs3_[a-z0-9_]+", r"\brds_[a-z0-9_]+",
        r"\biam_[a-z0-9_]+", r"\belb_[a-z0-9_]+", r"\baws_[a-z0-9_]+",
        r"\bcloudformation_[a-z0-9_]+", r"\broute53[a-z0-9_]*",
    ],
    "azure.azcollection": [
        r"\bazure_rm_[a-z0-9_]+",
    ],
    "kubernetes.core": [
        r"\bk8s(?:_[a-z0-9_]+)?\b", r"\bhelm(?:_[a-z0-9_]+)?\b",
    ],
    "community.general": [
        r"\bcommunity_general_[a-z0-9_]+",
    ],
    "ansible.builtin": [
        r"\bansible_builtin_[a-z0-9_]+",
    ],
}
_MODULE_PREFIX_RE = {
    coll: [_re.compile(p) for p in pats]
    for coll, pats in _MODULE_PREFIX_PATTERNS.items()
}

# Generic FQCN: ``<a>.<b>.<module>`` where ``<a>.<b>`` is a known collection.
_KNOWN_COLLECTIONS = set(COLLECTION_KEYWORDS.keys())
_FQCN_RE = _re.compile(r"\b([a-z0-9]+\.[a-z0-9]+)\.[a-z0-9_]+\b")


def _detect_fqcn_collections(query_lower: str) -> set[str]:
    """Detect explicit collection references (full FQCN or module-name prefix)."""
    found: set[str] = set()

    for m in _FQCN_RE.finditer(query_lower):
        coll = m.group(1)
        if coll in _KNOWN_COLLECTIONS:
            found.add(coll)

    for coll, regexes in _MODULE_PREFIX_RE.items():
        if any(rx.search(query_lower) for rx in regexes):
            found.add(coll)

    return found


def score_collections(query: str) -> dict[str, float]:
    """
    Issue #1: normalised, specificity-weighted confidence per collection.

    Confidence = FQCN/module signal (up to FQCN_CONF) + saturating keyword
    score (up to KEYWORD_CONF_CAP). Keyword contributions are weighted by how
    unique each keyword is, so "pod" (unique to kubernetes.core) is no longer
    drowned out by long shared phrases.
    """
    query_lower = (query or "").lower()
    fqcn = _detect_fqcn_collections(query_lower)

    scores: dict[str, float] = {}
    for coll, keywords in COLLECTION_KEYWORDS.items():
        weight_sum = 0.0
        for kw in keywords:
            if kw in query_lower:
                spec = _KEYWORD_SPECIFICITY.get(kw, 0.5)
                # Multi-word phrases are inherently more specific.
                phrase_bonus = 0.4 if " " in kw else 0.0
                weight_sum += spec + phrase_bonus

        kw_conf = 0.0
        if weight_sum > 0:
            kw_conf = KEYWORD_CONF_CAP * min(1.0, weight_sum / KEYWORD_SATURATION)

        fqcn_conf = FQCN_CONF if coll in fqcn else 0.0

        conf = round(min(1.3, fqcn_conf + kw_conf), 4)
        if conf > 0:
            scores[coll] = conf

    return scores


@dataclass
class RouteDecision:
    """Outcome of adaptive routing (stage 2)."""
    mode: str                       # "single" | "multi" | "all"
    collections: list[str]          # routed collections (empty for "all")
    confidence: dict[str, float]    # per-collection confidence
    quotas: dict[str, int] = field(default_factory=dict)  # multi-mode per-coll caps

    @property
    def where(self) -> dict | None:
        """Chroma metadata filter for this route."""
        if self.mode == "single" and self.collections:
            return {"collection": {"$eq": self.collections[0]}}
        if self.mode == "multi" and self.collections:
            return {"collection": {"$in": list(self.collections)}}
        return None


def _allocate_quotas(ranked: list[tuple[str, float]], top_k: int) -> dict[str, int]:
    """Proportional per-collection slot budgets for a multi-collection route."""
    total = sum(c for _, c in ranked) or 1.0
    quotas: dict[str, int] = {}
    for coll, conf in ranked:
        quotas[coll] = max(1, int(round(top_k * (conf / total))))
    return quotas


def route_collections(query: str, top_k: int = TOP_K) -> RouteDecision:
    """
    Issue #2: adaptive routing with a middle ground.

      * single  — one clearly dominant collection (Chroma ``$eq``)
      * multi   — 2-3 comparable collections (Chroma ``$in`` + proportional quotas)
      * all     — no usable signal (unfiltered search)
    """
    scores = score_collections(query)
    if not scores:
        return RouteDecision("all", [], {})

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_coll, best_conf = ranked[0]
    second_conf = ranked[1][1] if len(ranked) > 1 else 0.0

    # Clear single winner: strong and well separated from the runner-up.
    if best_conf >= ROUTE_STRONG_CONF and (best_conf - second_conf) >= ROUTE_SEPARATION:
        return RouteDecision("single", [best_coll], scores)

    # Several comparable collections -> search them together with quotas.
    contenders = [(c, s) for c, s in ranked if s >= ROUTE_MIN_CONF][:ROUTE_MAX_MULTI]
    if len(contenders) >= 2:
        colls = [c for c, _ in contenders]
        quotas = _allocate_quotas(contenders, top_k)
        return RouteDecision("multi", colls, scores, quotas)

    # A single contender that is decent but not separated enough still routes single.
    if best_conf >= ROUTE_STRONG_CONF:
        return RouteDecision("single", [best_coll], scores)

    return RouteDecision("all", [], scores)


def detect_collection(query: str) -> str | None:
    """
    Backward-compatible single-collection detector.

    Returns a collection name only when adaptive routing is confident enough to
    pick exactly one collection; otherwise None (ambiguous / multi / all).
    """
    decision = route_collections(query)
    if decision.mode == "single" and decision.collections:
        return decision.collections[0]
    return None


# ─────────────────────────────────────────────
#  STAGE 4 — INTENT SHAPING (read vs write, module variant)
# ─────────────────────────────────────────────

def _is_readonly_module(short_mod: str) -> bool:
    """`*_info` and `*_facts` modules only query state — they never change it."""
    return short_mod.endswith("_info") or short_mod.endswith("_facts")


def _compute_intent_boost(analysis: QueryAnalysis, module_name: str) -> float:
    """
    Intent-based shaping of a candidate's score from its module name.

    Deliberately does *not* score query/module-name similarity. Lexical
    alignment is already applied in ``merge_vector_and_lexical`` (stage 3), and
    re-applying a second, differently-normalised version here double-counted it:
    on the 56-query retrieval benchmark the name-similarity terms cost 3.6 points
    of top-1 (23.2% -> 19.6%) and 0.043 MRR. See scripts/ablate_lexical_boost.py.

    What remains are read/write intent rules, which are about *which* module the
    user wants rather than how its name is spelled.
    """
    if not module_name:
        return 0.0

    query_lower = analysis.lower
    short_mod = module_name.split(".")[-1].lower()
    boost = 0.0

    # Read-only module variants: promote for pure read queries, demote for
    # action queries, and mildly demote on neutral queries — a user who says
    # "store a secret in the vault" wants keyvaultsecret, not keyvaultsecret_info.
    if _is_readonly_module(short_mod):
        if analysis.read_intent and not analysis.write_intent:
            boost += 0.08
        elif analysis.write_intent:
            boost -= 0.18
        else:
            boost -= 0.08

    # Write intent + *snapshot* modules: demote unless the user asked for a snapshot/backup.
    if analysis.write_intent and "snapshot" in short_mod:
        if "snapshot" not in query_lower and "backup" not in query_lower:
            boost -= 0.14

    # AKS intent shaping: prefer cluster module for cluster creation requests.
    if "aks" in analysis.tokens and "cluster" in analysis.tokens:
        if short_mod == "azure_rm_aks":
            boost += 0.08
        if "aksagentpool" in short_mod and not any(
            t in query_lower for t in ("agent pool", "node pool", "nodepool")
        ):
            boost -= 0.10

    return boost


# ─────────────────────────────────────────────
#  STAGE 5 — DIVERSITY + COVERAGE PRIMITIVES
# ─────────────────────────────────────────────

@dataclass
class Cand:
    """A scored retrieval candidate (stage 4 output)."""
    doc: Document
    score: float       # weighted/reranked score
    raw: float         # raw similarity score
    reason: str
    module: str
    ctype: str
    collection: str


def _make_cand(doc: Document, score: float, raw: float, reason: str) -> Cand:
    md = doc.metadata or {}
    return Cand(
        doc=doc,
        score=score,
        raw=raw,
        reason=reason,
        module=md.get("module", "unknown"),
        ctype=md.get("chunk_type", ""),
        collection=md.get("collection") or "unknown",
    )


def _select_with_caps(
    pool: list[Cand],
    top_k: int,
    module_cap: int,
    collection_cap: dict[str, int] | int | None,
) -> list[Cand]:
    """
    Issue #3: enforce module-level and collection-level diversity so no single
    module's overview/example chunks monopolise top_k.

    ``collection_cap`` may be a per-collection dict (multi-route quotas), a flat
    int (unrouted search), or None (single-route, no collection cap).
    """
    def cap_for(coll: str) -> int | None:
        if collection_cap is None:
            return None
        if isinstance(collection_cap, int):
            return collection_cap
        return collection_cap.get(coll, 1)

    picked: list[Cand] = []
    seen: set[int] = set()
    mod_counts: dict[str, int] = {}
    coll_counts: dict[str, int] = {}

    def _try_add(cand: Cand, ignore_caps: bool) -> bool:
        if id(cand.doc) in seen:
            return False
        if not ignore_caps:
            if mod_counts.get(cand.module, 0) >= module_cap:
                return False
            cap = cap_for(cand.collection)
            if cap is not None and coll_counts.get(cand.collection, 0) >= cap:
                return False
        picked.append(cand)
        seen.add(id(cand.doc))
        mod_counts[cand.module] = mod_counts.get(cand.module, 0) + 1
        coll_counts[cand.collection] = coll_counts.get(cand.collection, 0) + 1
        return True

    for cand in pool:
        if len(picked) >= top_k:
            break
        _try_add(cand, ignore_caps=False)

    # Backfill to fill remaining slots if caps left us short (still de-dup'd).
    if len(picked) < top_k:
        for cand in pool:
            if len(picked) >= top_k:
                break
            _try_add(cand, ignore_caps=True)

    return picked[:top_k]


def _build_chunk_index(
    pool: list[Cand],
    raw_results: list[tuple[Document, float]],
    analysis: QueryAnalysis,
) -> dict[tuple[str, str], list[Cand]]:
    """
    Issue #5: build a single ``(module, chunk_type) -> [Cand]`` map once so
    coverage backfill is O(n) instead of re-scanning the pool per chunk type.

    Includes reranked candidates first, then raw (possibly sub-threshold)
    similarity hits as fallbacks, preserving score order within each bucket.
    """
    index: dict[tuple[str, str], list[Cand]] = {}
    seen_docs: set[int] = set()

    for cand in pool:
        index.setdefault((cand.module, cand.ctype), []).append(cand)
        seen_docs.add(id(cand.doc))

    for doc, raw in raw_results:
        if id(doc) in seen_docs:
            continue
        md = doc.metadata or {}
        mod = md.get("module", "unknown")
        ctype = md.get("chunk_type", "")
        boost = _chunk_type_boost(ctype, analysis)
        cand = _make_cand(doc, float(raw) + boost, float(raw), "coverage_backfill")
        index.setdefault((mod, ctype), []).append(cand)
        seen_docs.add(id(doc))

    return index


def _ensure_coverage(
    selected: list[Cand],
    primary_module: str,
    chunk_index: dict[tuple[str, str], list[Cand]],
    top_k: int,
) -> list[Cand]:
    """
    Deterministic coverage for the primary module: keep at least one overview
    and required_params chunk plus up to PRIMARY_EXAMPLE_CAP example chunks,
    using the prebuilt index (O(1) lookups per chunk type).
    """
    if not primary_module:
        return selected[:top_k]

    out = list(selected)
    present: set[int] = {id(c.doc) for c in out}
    protected: set[int] = set()

    def _present_count(kind: str) -> int:
        return sum(
            1 for c in out
            if c.module == primary_module and c.ctype == kind
        )

    def _add(cand: Cand):
        if id(cand.doc) in present:
            protected.add(id(cand.doc))
            return
        out.append(cand)
        present.add(id(cand.doc))
        protected.add(id(cand.doc))

    for kind in ("overview", "required_params"):
        if _present_count(kind) == 0:
            bucket = chunk_index.get((primary_module, kind))
            if bucket:
                _add(bucket[0])
        else:
            for c in out:
                if c.module == primary_module and c.ctype == kind:
                    protected.add(id(c.doc))

    have_examples = _present_count("example")
    if have_examples < PRIMARY_EXAMPLE_CAP:
        for cand in chunk_index.get((primary_module, "example"), []):
            if have_examples >= PRIMARY_EXAMPLE_CAP:
                break
            if id(cand.doc) in present:
                protected.add(id(cand.doc))
                continue
            _add(cand)
            have_examples += 1
    else:
        for c in out:
            if c.module == primary_module and c.ctype == "example":
                protected.add(id(c.doc))

    out.sort(key=lambda c: c.score, reverse=True)
    if len(out) <= top_k:
        return out

    # Trim lowest-scored, never dropping protected primary-coverage chunks.
    kept: list[Cand] = [c for c in out if id(c.doc) in protected]
    rest = [c for c in out if id(c.doc) not in protected]
    room = max(0, top_k - len(kept))
    kept_plus = kept + rest[:room]
    kept_plus.sort(key=lambda c: c.score, reverse=True)
    return kept_plus[:top_k]


# ─────────────────────────────────────────────
#  RETRIEVER
# ─────────────────────────────────────────────

def retrieve(
    query: str,
    vectorstore: Any,
    top_k: int = TOP_K,
    collection_filter: str | None = None,
) -> list[Document]:
    """
    Retrieve top-K relevant documents for a query (legacy entry point).

    Uses adaptive auto-routing when no explicit collection filter is supplied.
    """
    final, collection_filter, _route = _retrieve_ranked(
        query=query,
        vectorstore=vectorstore,
        top_k=top_k,
        collection_filter=collection_filter,
        apply_auto_collection_filter=True,
    )

    print(f"  [Retriever] Results ({len(final)}):")
    for doc, score in final:
        print(f"    {score:.3f}  {doc.metadata.get('module'):<45} [{doc.metadata.get('chunk_type')}]")

    return [doc for doc, _ in final]


def _resolve_route(
    query: str,
    collection_filter: str | None,
    apply_auto_collection_filter: bool,
    top_k: int,
) -> RouteDecision:
    """
    Stage 2 entry: turn the caller's request into a concrete RouteDecision.

    - Explicit non-empty collection_filter  -> single ($eq) on that collection.
    - None + apply_auto=True                 -> detect_collection (single) else
                                                adaptive multi/all routing.
    - None + apply_auto=False                -> all (no Chroma filter).
    """
    if collection_filter is not None and str(collection_filter).strip():
        coll = str(collection_filter).strip()
        return RouteDecision("single", [coll], {coll: 1.0})

    if not apply_auto_collection_filter:
        return RouteDecision("all", [], {})

    # ``detect_collection`` is consulted first so callers/tests can monkeypatch it.
    single = detect_collection(query)
    if single:
        return RouteDecision("single", [single], {single: 1.0})

    return route_collections(query, top_k=top_k)


def _retrieve_ranked(
    query: str,
    vectorstore: Any,
    top_k: int = TOP_K,
    collection_filter: str | None = None,
    apply_auto_collection_filter: bool = True,
) -> tuple[list[tuple[Document, float]], str | None, RouteDecision]:
    """
    Shared retrieval logic used by both retrieve() and the metadata path.

    Implements stages 2-6 of the pipeline. The returned ``collection_filter`` is
    the single routed collection name, or None for multi/all routes.
    Also returns the ``RouteDecision`` to avoid recomputing routing metadata.
    """
    analysis = analyze_query(query)
    route = _resolve_route(query, collection_filter, apply_auto_collection_filter, top_k)
    where = route.where
    module_targets = extract_module_targets(query)
    search_query = enrich_query_for_embedding(query, analysis, route, module_targets)

    route_label = route.mode
    if route.collections:
        route_label += ":" + ",".join(route.collections)
    print(f"\n  [Retriever] Query: '{query[:60]}...'")
    if search_query != query:
        print(f"  [Retriever] Enriched: '{search_query[:80]}...'")
    if module_targets:
        print(f"  [Retriever] Module targets: {module_targets[:3]}")
    print(f"  [Retriever] Route: {route_label} "
          f"(intent: {'write' if analysis.write_intent else 'read' if analysis.read_intent else 'neutral'}"
          f"{', example' if analysis.example_intent else ''}"
          f"{', param' if analysis.param_intent else ''})")

    # ── Stage 3: vector search (+ hybrid supplement / RRF) ─────────
    try:
        results = vectorstore.similarity_search_with_relevance_scores(
            query=search_query,
            k=top_k * QUERY_K_MULTIPLIER,
            filter=where,
        )
    except Exception:
        results = vectorstore.similarity_search_with_relevance_scores(
            query=search_query,
            k=top_k * QUERY_K_MULTIPLIER,
        )

    if module_targets:
        before = len(results)
        results = supplement_with_module_targets(
            query, vectorstore, results, where=where, max_targets=2,
        )
        if len(results) > before:
            print(f"  [Retriever] Module-targeted supplement: +{len(results) - before} chunks")

    results = merge_vector_and_lexical(query, results, module_targets=module_targets)

    filtered = [(doc, score) for doc, score in results if score >= SCORE_THRESH]
    min_above_thresh = max(2, top_k // 2)
    if len(filtered) < min_above_thresh:
        adaptive_thresh = max(0.20, SCORE_THRESH - 0.08)
        adaptive = [(doc, score) for doc, score in results if score >= adaptive_thresh]
        if len(adaptive) > len(filtered):
            print(
                f"  [Retriever] Adaptive threshold {SCORE_THRESH:.2f} -> "
                f"{adaptive_thresh:.2f} ({len(filtered)} -> {len(adaptive)} candidates)"
            )
            filtered = adaptive
    if not filtered:
        print("  [Retriever] No results above adaptive threshold, using top candidates...")
        filtered = results[:top_k * 2]

    # ── Stage 3b: BM25 recall + fusion ────────────────────────────
    # Runs after thresholding on purpose: the threshold is calibrated for cosine
    # relevance and says nothing about a BM25 score, so sparse hits should not
    # have to clear it.
    before_sparse = {id(doc) for doc, _ in filtered}
    filtered = fuse_with_sparse(
        query,
        filtered,
        vectorstore,
        collections=route.collections or None,
        limit=top_k * QUERY_K_MULTIPLIER,
    )
    added = sum(1 for doc, _ in filtered if id(doc) not in before_sparse)
    if added:
        print(f"  [Retriever] BM25 fusion: +{added} candidates the vector search missed")

    # ── Stage 4: query-aware rerank (no early dedup bypass) ───────
    module_hit_count: dict[str, int] = {}
    for doc, _score in filtered:
        mod = (doc.metadata or {}).get("module", "unknown")
        module_hit_count[mod] = module_hit_count.get(mod, 0) + 1

    pool: list[Cand] = []
    for doc, raw_score in filtered:
        md = doc.metadata or {}
        mod = md.get("module", "unknown")
        ctype = md.get("chunk_type", "")

        boost = _chunk_type_boost(ctype, analysis)
        weighted = float(raw_score) + boost
        reasons = [f"boost:{boost:+.2f}"]

        hits = module_hit_count.get(mod, 1)
        coherence_boost = min(0.06, 0.015 * max(0, hits - 1))
        if coherence_boost:
            weighted += coherence_boost
            reasons.append(f"coherence:+{coherence_boost:.2f}")

        intent_boost = _compute_intent_boost(analysis, mod)
        if intent_boost:
            weighted += intent_boost
            reasons.append(f"intent:{intent_boost:+.2f}")

        pool.append(_make_cand(doc, weighted, float(raw_score), "|".join(reasons)))

    pool.sort(key=lambda c: c.score, reverse=True)

    # Demote read-only (*_info / *_facts) modules on action queries: keep them
    # in the pool, but never let them outrank an action module. Applies to any
    # query that is not purely read-intent — neutral phrasings ("store a secret
    # in the vault") still overwhelmingly want the action module.
    if analysis.write_intent or not analysis.read_intent:
        action_first = [c for c in pool if not _is_readonly_module(c.module.split(".")[-1].lower())]
        readonly_last = [c for c in pool if _is_readonly_module(c.module.split(".")[-1].lower())]
        pool = action_first + readonly_last

    # ── Stage 5: diversity caps + coverage backfill ───────────────
    if route.mode == "multi":
        collection_cap: dict[str, int] | int | None = route.quotas
    elif route.mode == "single":
        collection_cap = None
    else:
        collection_cap = _max_chunks_per_collection_unfiltered()

    selected = _select_with_caps(
        pool,
        top_k,
        module_cap=_max_chunks_per_module(),
        collection_cap=collection_cap,
    )

    sel_docs = [c.doc for c in selected]
    sel_scores = [c.score for c in selected]
    primary_module, _, _ = extract_primary_module(sel_docs, sel_scores)

    chunk_index = _build_chunk_index(pool, results, analysis)
    final_cands = _ensure_coverage(selected, primary_module, chunk_index, top_k)

    ranked = [(c.doc, c.score) for c in final_cands]

    print("  [Retriever][Debug] Ranked chunks sent to agent:")
    if not final_cands:
        print("    - (none)")
    for i, cand in enumerate(final_cands, start=1):
        md = cand.doc.metadata or {}
        req = md.get("required_params_list", "")
        req_preview = req[:90] + ("..." if len(req) > 90 else "") if req else "-"
        print(
            "    {idx}. module={module} | chunk_type={ctype} | raw={raw:.3f} | "
            "score={score:.3f} | reason={reason} | ex_idx={exi} | required_params={req}".format(
                idx=i,
                module=cand.module,
                ctype=cand.ctype or "unknown",
                raw=cand.raw,
                score=cand.score,
                reason=cand.reason,
                exi=md.get("example_index", "-"),
                req=req_preview,
            )
        )

    resolved_filter = route.collections[0] if route.mode == "single" and route.collections else None
    return ranked, resolved_filter, route


def get_retrieval_metadata(
    query: str,
    vectorstore: Any,
    top_k: int = TOP_K,
    apply_auto_collection_filter: bool | None = None,
) -> dict:
    """
    Retrieve docs + return metadata for API response.

    By default uses multi-collection retrieval (no Chroma collection filter) unless
    ``RAG_APPLY_AUTO_COLLECTION_FILTER`` is set. Pass ``apply_auto_collection_filter=True``
    to force legacy keyword-based single-collection filtering.
    """
    if apply_auto_collection_filter is None:
        apply_auto_collection_filter = default_apply_auto_collection_filter_for_generation()
    top_items, collection_filter, route = _retrieve_ranked(
        query=query,
        vectorstore=vectorstore,
        top_k=top_k,
        collection_filter=None,
        apply_auto_collection_filter=apply_auto_collection_filter,
    )
    meta = build_retrieval_meta(top_items, collection_filter)
    docs = meta["docs"]
    primary_module = meta["primary_module"]

    example_modules = []
    chunk_type_counts: dict[str, int] = {}
    for d in docs:
        ctype = d.metadata.get("chunk_type", "unknown")
        chunk_type_counts[ctype] = chunk_type_counts.get(ctype, 0) + 1
        if d.metadata.get("module") != primary_module:
            continue
        if d.metadata.get("chunk_type") == "example":
            ex_i = d.metadata.get("example_index", "?")
            example_modules.append(f"{d.metadata.get('module')}#example_{ex_i}")
            if len(example_modules) >= 3:
                break

    required_params = meta["required_params"]

    meta["routing"] = {
        "mode": route.mode,
        "collections": route.collections,
        "confidence": {k: round(v, 3) for k, v in route.confidence.items()},
    }

    rm = meta.get("ranked_modules") or []
    rm_preview = ", ".join(f"{e.get('module')}@{e.get('top_score')}" for e in rm[:4])
    print(
        f"  [Retriever][Meta] primary_module={primary_module} "
        f"route={route.mode} "
        f"required_params={required_params or []} "
        f"ranked_modules=[{rm_preview}] "
        f"examples={example_modules or []} "
        f"chunk_type_counts={chunk_type_counts}"
    )

    meta["chunk_type_counts"] = chunk_type_counts
    return meta
