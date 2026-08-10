"""
=============================================================
  AnsibleAI Agent — Tools layer

  Thin, callable wrappers around the existing pipeline, invoked
  directly by the LangGraph nodes (agent/graph.py):
    - search_docs        : semantic search over ChromaDB (RAG retriever)
    - draft_playbook     : ONE draft/repair YAML pass (agent LLM)
    - validate_playbook_file : full validator + ansible-lint on a file
    - validate_yaml      : validator on a YAML string or file
    - get_module_info    : structured info about an Ansible module
=============================================================
"""

from __future__ import annotations

import os
import re
import tempfile

from logging_setup import get_logger
from rag.retrieval_utils import build_retrieval_meta as _meta_from_ranked

log = get_logger(__name__)


# ─────────────────────────────────────────────
#  Lazy singletons (the vectorstore + KB are heavy to load)
# ─────────────────────────────────────────────

_VECTORSTORE = None
_KB_CACHE = None


def _get_vectorstore():
    global _VECTORSTORE
    if _VECTORSTORE is None:
        from rag.indexer import load_vectorstore
        _VECTORSTORE = load_vectorstore()
    return _VECTORSTORE


def _get_kb() -> dict:
    global _KB_CACHE
    if _KB_CACHE is None:
        from kb_store import load_knowledge_base as load_kb_store
        _KB_CACHE = load_kb_store(prefer_parsed=True)
    return _KB_CACHE


def invalidate_caches() -> None:
    """Allow callers (e.g. docs rescrape) to force a reload."""
    global _VECTORSTORE, _KB_CACHE
    _VECTORSTORE = None
    _KB_CACHE = None


# ─────────────────────────────────────────────
#  Collection resolution (RAG-driven)
# ─────────────────────────────────────────────
#
#  The reasoning LLM's free-form `collection` field is unreliable; it often
#  picks the wrong cloud or hallucinates a name. This helper lets the
#  *retriever* (the RAG system) decide which collection to use, by voting
#  with the actual embeddings, and falls back to sanitized hints.

def _majority_collection_from_docs(
    docs_scores: list[tuple],
    supermajority_of: int = 5,
    threshold: int = 4,
) -> str | None:
    """
    Supermajority vote: return the collection that appears in at least
    `threshold` of the top `supermajority_of` docs. Otherwise None.
    """
    if not docs_scores:
        return None
    top = docs_scores[:supermajority_of]
    counts: dict[str, int] = {}
    for doc, _ in top:
        coll = (doc.metadata or {}).get("collection")
        if not coll or coll == "unknown.collection":
            continue
        counts[coll] = counts.get(coll, 0) + 1
    if not counts:
        return None
    winner, votes = max(counts.items(), key=lambda kv: kv[1])
    return winner if votes >= threshold else None


def _search_result_from_meta(query: str, meta: dict) -> dict:
    """Convert retrieval metadata into the public search_docs tool payload."""
    docs    = meta.get("docs", [])
    scores  = meta.get("scores", [])

    chunks = []
    for doc, score in zip(docs, scores):
        md = doc.metadata or {}
        chunks.append({
            "module"    : md.get("module", "unknown"),
            "collection": md.get("collection", "unknown"),
            "chunk_type": md.get("chunk_type", "text"),
            "score"     : round(float(score), 3),
            "text"      : (doc.page_content or "")[:600],
        })

    required_params = list(meta.get("required_params") or [])
    primary_module = meta.get("primary_module")
    if primary_module:
        kb_req = _required_params_from_kb(primary_module)
        for p in kb_req:
            if p not in required_params:
                required_params.append(p)

    log.debug(
        "agent.search_docs.resolved",
        primary_module=primary_module,
        required_params=required_params or [],
    )

    return {
        "query"             : query,
        "primary_module"    : primary_module,
        "primary_collection": meta.get("primary_collection"),
        "score"             : meta.get("primary_score"),
        "source_url"        : meta.get("source_url"),
        "required_params"   : required_params,
        "required_params_detailed": _required_params_detailed(primary_module),
        "module_candidates" : meta.get("module_candidates", []),
        "ranked_modules"    : meta.get("ranked_modules", []),
        "required_params_by_module": meta.get("required_params_by_module", {}),
        "chunks"            : chunks,
        "_retrieval_meta"   : meta,
    }


def resolve_collection_with_prefetch(
    query: str,
    planner_hint: str | None,
    pinned: str | None,
    pivot: bool,
    top_k: int = 8,
) -> tuple[str | None, str, dict | None]:
    """
    Decide which collection filter to pass to the RAG retriever.

    Resolution order:
      1. If the thread has a `pinned` collection AND the user didn't pivot,
         use the pin.
      2. Run an unfiltered search over the whole index and
         accept the result ONLY IF:
           - >= 4 of the top 5 chunks are from the same collection, AND
           - the primary module's collection matches that winner
      3. If there is no safe vote, fall back to the LLM hint only if
         the hint is in the allow-list.
      4. Otherwise return None (unfiltered search).

    Returns (resolved_collection_or_None, source_tag, prefetched_search_result)
    where source_tag is one of: "pin", "planner", "vote", "none".
    """
    from .collections import is_known_collection, sanitize_collection

    if pinned and not pivot and is_known_collection(pinned):
        return pinned, "pin", None

    try:
        from rag.retriever import _retrieve_ranked
        vs = _get_vectorstore()
        top_items, _, _route = _retrieve_ranked(
            query=query,
            vectorstore=vs,
            top_k=top_k,
            collection_filter=None,
            apply_auto_collection_filter=False,
        )
        prefetched_meta = _meta_from_ranked(top_items, collection_filter=None)
        prefetched = _search_result_from_meta(query, prefetched_meta)
    except Exception as exc:
        log.warning("agent.resolve_collection.search_failed", error=str(exc))
        hint = sanitize_collection(planner_hint)
        if hint:
            return hint, "planner", None
        return None, "none", None

    voted = _majority_collection_from_docs(top_items, supermajority_of=5, threshold=4)
    if not voted:
        hint = sanitize_collection(planner_hint)
        if hint:
            return hint, "planner", prefetched
        return None, "none", prefetched

    # Safety: the primary module must also belong to the voted collection.
    primary_mod_coll = None
    for doc, _ in top_items[:5]:
        md = doc.metadata or {}
        if md.get("chunk_type") == "overview":
            primary_mod_coll = md.get("collection")
            break
    if primary_mod_coll is None and top_items:
        primary_mod_coll = (top_items[0][0].metadata or {}).get("collection")

    if primary_mod_coll != voted:
        hint = sanitize_collection(planner_hint)
        if hint:
            return hint, "planner", prefetched
        return None, "none", prefetched

    return voted, "vote", prefetched


# ─────────────────────────────────────────────
#  Tool: search_docs
# ─────────────────────────────────────────────

def _coerce_retrieval_meta_for_prefetch(raw: dict | None) -> dict | None:
    """Accept either a bare retrieval meta dict or a search_docs payload with _retrieval_meta."""
    if not raw or not isinstance(raw, dict):
        return None
    inner = raw.get("_retrieval_meta")
    if isinstance(inner, dict) and inner.get("docs") is not None:
        return inner
    if raw.get("docs") is not None:
        return raw
    return None


def search_docs(
    query: str,
    collection: str | None = None,
    top_k: int = 8,
    _prefetched_meta: dict | None = None,
) -> dict:
    """
    Semantic search over the Ansible documentation index.

    Pass _prefetched_meta to reuse an already-computed retrieval result
    (avoids a second vector search when resolve_collection_with_prefetch
    already ran an unfiltered search).
    """
    from rag.retriever import _retrieve_ranked, get_retrieval_metadata

    meta_in = _coerce_retrieval_meta_for_prefetch(_prefetched_meta)
    if meta_in and meta_in.get("docs"):
        cf = meta_in.get("collection_filter")
        primary_coll = meta_in.get("primary_collection")
        if (
            cf == collection
            or (collection is None and cf is None)
            or (cf is None and collection and primary_coll == collection)
        ):
            log.debug("agent.search_docs.reused_prefetch", collection=collection)
            return _search_result_from_meta(query, meta_in)

    vs = _get_vectorstore()

    if collection:
        # Explicit collection: single-collection Chroma filter (hint / pin).
        top_items, _, _route = _retrieve_ranked(
            query=query,
            vectorstore=vs,
            top_k=top_k,
            collection_filter=collection,
            apply_auto_collection_filter=True,
        )
        meta = _meta_from_ranked(top_items, collection_filter=collection)
    else:
        # Unscoped search: multi-collection by default (see get_retrieval_metadata).
        meta = get_retrieval_metadata(query, vs, top_k=top_k)

    return _search_result_from_meta(query, meta)


def _find_kb_entry(module_name: str) -> dict:
    """Look up a module entry in the KB by its full dotted name."""
    if not module_name:
        return {}
    kb = _get_kb()
    modules = kb.get("modules", {}) or {}
    short = module_name.split(".")[-1]
    slug  = short + "_module"
    entry = modules.get(slug) or {}
    if entry:
        return entry
    for key, candidate in modules.items():
        if key.endswith(f"::{slug}") or candidate.get("module") == module_name:
            return candidate
    return {}


def _required_params_from_kb(module_name: str) -> list[str]:
    entry = _find_kb_entry(module_name)
    if not entry:
        return []
    names = list(entry.get("required_params") or [])
    if names:
        return names
    return [p["name"] for p in (entry.get("parameters") or []) if p.get("required") and p.get("name")]


def _required_params_detailed(module_name: str) -> list[dict]:
    """Return [{'name', 'type', 'description'}] for every required param of the module."""
    entry = _find_kb_entry(module_name)
    if not entry:
        return []
    out = []
    for p in entry.get("parameters") or []:
        if not p.get("required"):
            continue
        out.append({
            "name"       : p.get("name"),
            "type"       : p.get("type", "any"),
            "description": (p.get("description") or "")[:200],
        })
    return out


# ─────────────────────────────────────────────
#  Tool: get_module_info
# ─────────────────────────────────────────────

def get_module_info(module: str) -> dict:
    """
    Return structured reference info about an Ansible module
    (used to power the "Source" chip on assistant messages).
    """
    # Imported lazily to avoid a circular import at package load time.
    from app import build_module_reference
    kb = _get_kb()
    return build_module_reference(module, kb.get("modules", {}))


# ─────────────────────────────────────────────
#  Tool: validate_yaml / validate_playbook_file
# ─────────────────────────────────────────────

def _write_temp_yaml(yaml_content: str) -> str:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".yml", delete=False
    )
    tmp.write(yaml_content)
    tmp.close()
    return tmp.name


def validate_playbook_file(filepath: str) -> dict:
    """Full validator (structure, KB params, secrets, ansible-lint) on a file."""
    from validator import validate_playbook
    kb = _get_kb()
    result = validate_playbook(filepath, kb.get("modules", {}))
    return {
        "is_valid"    : bool(result.is_valid),
        "passed"      : len(result.passed),
        "passed_msgs" : list(result.passed),
        "warnings"    : list(result.warnings),
        "errors"      : list(result.errors),
        "ansible_lint": getattr(result, "ansible_lint", {"status": "not_run", "violations": []}),
        "module"      : getattr(result, "_detected_module", None),
    }


def validate_yaml(yaml_content: str | None = None, filepath: str | None = None) -> dict:
    """
    Validate a playbook, either from raw YAML text or an existing file.
    Returns a JSON-safe summary (is_valid, passed, warnings, errors, ansible_lint).
    """
    created_temp = False
    path = filepath
    if path is None:
        if not yaml_content:
            return {"error": "No YAML provided"}
        path = _write_temp_yaml(yaml_content)
        created_temp = True

    try:
        return validate_playbook_file(path)
    finally:
        if created_temp:
            try:
                os.unlink(path)
            except OSError:
                pass


# ─────────────────────────────────────────────
#  Tool: draft_playbook (one draft/repair pass)
# ─────────────────────────────────────────────

LOW_CONFIDENCE_THRESHOLD = 0.38


def check_retrieval_confidence(retrieval_meta: dict | None) -> tuple[bool, str]:
    """
    True when retrieval identified a module confidently enough to ground
    YAML generation. Returns (confident, reason_when_not).
    """
    meta = retrieval_meta or {}
    primary_module = meta.get("primary_module")
    primary_score = float(meta.get("primary_score") or 0.0)
    if primary_module and primary_score >= LOW_CONFIDENCE_THRESHOLD:
        return True, ""
    candidates = list(meta.get("module_candidates") or [])
    return False, (
        f"Retrieval score {primary_score:.3f} is below threshold {LOW_CONFIDENCE_THRESHOLD}. "
        f"Candidates: {candidates[:3]}. "
        "The requested module may not be indexed — run the scraper to add it."
    )


def _strip_header_comments(raw: str) -> str:
    """Strip header comments + anything before the first `---`."""
    lines = [l for l in raw.splitlines() if not l.startswith("#")]
    start = next((i for i, l in enumerate(lines) if l.strip() == "---"), None)
    if start is not None:
        lines = lines[start:]
    return "\n".join(lines).strip()


def draft_playbook(
    user_request: str,
    retrieval_meta: dict,
    *,
    conversation_facts: dict | None = None,
    feedback: str = "none",
    fix_plan: str = "none",
    existing_path: str | None = None,
) -> dict:
    """
    ONE draft (or repair) pass over `user_request`.

    The file this writes is scratch, not a deliverable: ansible-lint takes
    a path rather than a string, so each pass has to materialise the YAML
    somewhere. Repair calls pass `existing_path` to overwrite the same
    file, so a four-iteration repair loop leaves one file behind, not
    four. The durable copy is archived to object storage once the turn
    settles (see storage.store_playbook).

    Returns {yaml, path, filename, issues}.
    """
    import storage
    from agent.playbook_generator import draft_playbook_from_retrieval

    yaml_content, issues = draft_playbook_from_retrieval(
        user_request,
        retrieval_meta,
        conversation_facts=conversation_facts,
        feedback=feedback,
        fix_plan=fix_plan,
    )
    # The generator's informational header is stripped so that ansible-lint
    # and the UI both see nothing but the playbook.
    yaml_clean = _strip_header_comments(yaml_content)

    filename = (
        os.path.basename(existing_path)
        if existing_path
        else storage.playbook_filename(user_request)
    )
    path = storage.write_working_file(filename, yaml_clean)

    return {
        "yaml"    : yaml_clean,
        "path"    : path,
        "filename": os.path.basename(path),
        "issues"  : issues,
    }


# ─────────────────────────────────────────────
#  Utility: detect yaml pasted into a user message
# ─────────────────────────────────────────────

YAML_FENCE_RE = re.compile(r"```(?:yaml|yml)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_embedded_yaml(message: str) -> str | None:
    """Extract a YAML block from a user message if present."""
    if not message:
        return None
    m = YAML_FENCE_RE.search(message)
    if m:
        return m.group(1).strip()
    stripped = message.strip()
    if stripped.startswith("---") or re.match(r"^-\s+name:", stripped):
        return stripped
    return None
