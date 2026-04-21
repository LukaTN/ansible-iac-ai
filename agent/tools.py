"""
=============================================================
  AnsibleAI Agent — Tools layer

  Thin, callable wrappers around the existing pipeline:
    - search_docs        : semantic search over ChromaDB (RAG retriever)
    - generate_playbook  : RAG retrieval + agent LLM YAML generation
    - validate_yaml      : run the validator on a YAML string or file
    - get_module_info    : structured info about an Ansible module

  The agent's orchestrator calls these by name; the LLM never imports Python.
=============================================================
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from typing import Any


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
#  The planner LLM's free-form `collection` field is unreliable; it often
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


def _meta_from_ranked(top_items: list[tuple], collection_filter: str | None) -> dict:
    """Build retrieval metadata dict from ranked (doc, score) items."""
    docs = [d for d, _ in top_items]
    scores = [s for _, s in top_items]

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

    module_candidates = []
    for d in docs:
        mod = d.metadata.get("module")
        if mod and mod not in module_candidates:
            module_candidates.append(mod)

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

    print(
        f"  [Agent][Debug] search_docs primary_module={primary_module} "
        f"required_params={required_params or []}"
    )
    chunks_dbg = []
    for c in chunks[:5]:
        chunks_dbg.append(
            f"{c.get('module')}[{c.get('chunk_type')}@{c.get('score')}]"
        )
    print(f"  [Agent][Debug] top_chunks={chunks_dbg or ['(none)']}")

    return {
        "query"             : query,
        "primary_module"    : primary_module,
        "primary_collection": meta.get("primary_collection"),
        "score"             : meta.get("primary_score"),
        "source_url"        : meta.get("source_url"),
        "required_params"   : required_params,
        "required_params_detailed": _required_params_detailed(primary_module),
        "module_params"     : _module_params_for_clarifier(primary_module),
        "module_candidates" : meta.get("module_candidates", []),
        "chunks"            : chunks,
        "_retrieval_meta"   : meta,
    }


def resolve_collection(
    query: str,
    planner_hint: str | None,
    pinned: str | None,
    pivot: bool,
    top_k: int = 3,
) -> tuple[str | None, str]:
    resolved, source, _prefetch = resolve_collection_with_prefetch(
        query=query,
        planner_hint=planner_hint,
        pinned=pinned,
        pivot=pivot,
        top_k=top_k,
    )
    return resolved, source


def resolve_collection_with_prefetch(
    query: str,
    planner_hint: str | None,
    pinned: str | None,
    pivot: bool,
    top_k: int = 3,
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
         (This is your "supermajority" choice.)
      3. If there is no safe vote, fall back to planner hint only if
         the hint is in the allow-list.
      4. If neither a safe vote nor a safe hint is available, return
         None (unfiltered search).

    Returns (resolved_collection_or_None, source_tag, prefetched_search_result)
    where source_tag
    is one of: "pin", "planner", "vote", "none".
    """
    from .collections import sanitize_collection, is_known_collection

    if pinned and not pivot and is_known_collection(pinned):
        return pinned, "pin", None

    try:
        from rag.retriever import _retrieve_ranked
        vs = _get_vectorstore()
        top_items, _ = _retrieve_ranked(
            query=query, vectorstore=vs, top_k=top_k, collection_filter=None
        )
        prefetched_meta = _meta_from_ranked(top_items, collection_filter=None)
        prefetched = _search_result_from_meta(query, prefetched_meta)
    except Exception as exc:
        print(f"  [Agent] resolve_collection: unfiltered search failed: {exc}")
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

def search_docs(query: str, collection: str | None = None, top_k: int = 3) -> dict:
    """
    Semantic search over the Ansible documentation index.

    Returns a compact summary suitable for feeding back to the LLM:
      {
        "query"        : str,
        "primary_module": "kubernetes.core.helm",
        "collection"   : "kubernetes.core",
        "score"        : 0.82,
        "required_params": [...],
        "chunks"       : [ {module, type, score, text}, ... ],
        "module_candidates": [...],
        "source_url"   : "...",
      }
    """
    from rag.retriever import get_retrieval_metadata, _retrieve_ranked

    vs = _get_vectorstore()

    if collection:
        top_items, _ = _retrieve_ranked(
            query=query, vectorstore=vs, top_k=top_k, collection_filter=collection
        )
        meta = _meta_from_ranked(top_items, collection_filter=collection)
    else:
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
    # Prefer the pre-computed list if present.
    names = list(entry.get("required_params") or [])
    if names:
        return names
    # Otherwise derive from the parameter list.
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


# Heuristic priority order for parameters when truncating large modules
# down to a manageable list for the clarify decider. Names that match any
# of these patterns are considered "essential-ish" and surfaced first.
_ESSENTIAL_NAME_HINTS = (
    "name", "id", "image", "ami", "instance_type", "type", "size",
    "region", "zone", "namespace", "host", "hosts", "url", "endpoint",
    "path", "src", "dest", "state", "kind", "api_version",
    "cluster", "vpc", "subnet", "key_name", "security_group",
    "user", "username", "password", "owner", "group", "mode",
    "command", "cmd", "shell", "script", "repo", "repository",
    "version", "branch", "tag", "release", "chart", "package",
    "port", "protocol", "rule", "tags",
)


def _is_essential_hint(name: str) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(h in n for h in _ESSENTIAL_NAME_HINTS)


def _module_params_for_clarifier(
    module_name: str, *, max_params: int = 30,
) -> list[dict]:
    """
    Return a curated, prompt-safe list of parameters for the clarify
    decider. Strategy:
      1. Always include any param flagged required=True in the docs.
      2. Then add params whose name looks essential (image_id, region,
         host, namespace, ...) until we have `max_params` items.
      3. Finally, fill remaining slots with the rest in original order.
    Each entry is {name, type, required, description}, descriptions are
    truncated to keep the prompt small.
    """
    entry = _find_kb_entry(module_name)
    if not entry:
        return []
    raw_params = entry.get("parameters") or []
    if not raw_params:
        return []

    def shape(p: dict) -> dict:
        return {
            "name"       : p.get("name") or "",
            "type"       : p.get("type", "any"),
            "required"   : bool(p.get("required")),
            "description": (p.get("description") or "").strip()[:140],
        }

    by_name = {p.get("name"): shape(p) for p in raw_params if p.get("name")}
    ordered: list[dict] = []
    seen = set()

    for name, p in by_name.items():
        if p["required"]:
            ordered.append(p); seen.add(name)

    for name, p in by_name.items():
        if name in seen:
            continue
        if _is_essential_hint(name):
            ordered.append(p); seen.add(name)
            if len(ordered) >= max_params:
                break

    if len(ordered) < max_params:
        for name, p in by_name.items():
            if name in seen:
                continue
            ordered.append(p); seen.add(name)
            if len(ordered) >= max_params:
                break

    return ordered[:max_params]


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
#  Tool: validate_yaml
# ─────────────────────────────────────────────

def _write_temp_yaml(yaml_content: str) -> str:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".yml", delete=False
    )
    tmp.write(yaml_content)
    tmp.close()
    return tmp.name


def validate_yaml(yaml_content: str | None = None, filepath: str | None = None) -> dict:
    """
    Validate a playbook, either from raw YAML text or an existing file.
    Returns a JSON-safe summary (is_valid, passed, warnings, errors, ansible_lint).
    """
    from validator import validate_playbook
    kb = _get_kb()

    created_temp = False
    path = filepath
    if path is None:
        if not yaml_content:
            return {"error": "No YAML provided"}
        path = _write_temp_yaml(yaml_content)
        created_temp = True

    try:
        result = validate_playbook(path, kb.get("modules", {}))
    finally:
        if created_temp:
            try:
                os.unlink(path)
            except OSError:
                pass

    return {
        "is_valid"    : bool(result.is_valid),
        "passed"      : len(result.passed),
        "passed_msgs" : list(result.passed),
        "warnings"    : list(result.warnings),
        "errors"      : list(result.errors),
        "ansible_lint": getattr(result, "ansible_lint", {"status": "not_run", "violations": []}),
        "module"      : getattr(result, "_detected_module", None),
    }


# ─────────────────────────────────────────────
#  Tool: generate_playbook
# ─────────────────────────────────────────────

def _clean_yaml_from_path(path: str) -> str:
    """Strip header comments + anything before the first `---`."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    lines = [l for l in raw.splitlines() if not l.startswith("#")]
    start = next((i for i, l in enumerate(lines) if l.strip() == "---"), None)
    if start is not None:
        lines = lines[start:]
    return "\n".join(lines).strip()


def generate_playbook(
    user_request: str,
    retrieval_meta: dict | None = None,
    conversation_facts: dict | None = None,
    db_session=None,
) -> dict:
    """
    Produce a full Ansible playbook for `user_request`.

    - Uses the provided RAG `retrieval_meta` if given (from an earlier
      `search_docs` call), otherwise runs a fresh retrieval.
    - Generates YAML with the agent LLM and retrieved docs as context
      (`agent.playbook_generator`), not the legacy RAG LangChain generator.
    - Validates the generated YAML.
    - Builds a module reference.
    - Persists a `Generation` row when `db_session` is provided, so the
      Stats dashboard stays in sync with every playbook the agent produces.
    """
    from agent.playbook_generator import generate_playbook_from_retrieval
    from rag.retriever import get_retrieval_metadata
    from rag.generator import _extract_constraints

    if not retrieval_meta or not retrieval_meta.get("docs"):
        retrieval_meta = get_retrieval_metadata(user_request, _get_vectorstore())

    conversation_facts = dict(conversation_facts or {})
    merged_context = user_request
    if conversation_facts:
        facts_blob = "\n".join(f"- {k}: {v}" for k, v in conversation_facts.items() if v is not None and str(v).strip())
        if facts_blob:
            merged_context = f"{user_request}\n\nConversation facts:\n{facts_blob}"
    # No-clarify mode: never block generation for missing parameters.
    # The playbook generator is instructed to auto-fill unspecified params.
    _ = _extract_constraints(merged_context)

    output_path, _yaml = generate_playbook_from_retrieval(
        merged_context,
        retrieval_meta,
        conversation_facts=conversation_facts,
        missing_required_params=[],
    )
    playbook_clean = _clean_yaml_from_path(output_path)

    # Rewrite the file with the cleaned YAML (matches behavior of old /generate route).
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(playbook_clean)

    validation = validate_yaml(filepath=output_path)
    detected   = validation.get("module") or retrieval_meta.get("primary_module") or "unknown"
    module_ref = get_module_info(detected)

    filename = os.path.basename(output_path)

    gen_id = None
    if db_session is not None:
        try:
            from models import Generation
            entry = Generation(
                request    = user_request,
                module     = detected,
                filename   = filename,
                playbook   = playbook_clean,
                is_valid   = validation["is_valid"],
                warnings   = len(validation["warnings"]),
                errors     = len(validation["errors"]),
                module_ref = module_ref,
            )
            db_session.add(entry)
            db_session.commit()
            gen_id = entry.id
        except Exception:
            db_session.rollback()

    return {
        "playbook"    : playbook_clean,
        "filename"    : filename,
        "module"      : detected,
        "module_ref"  : module_ref,
        "validation"  : validation,
        "generation_id": gen_id,
        "rag_meta"    : {
            "primary_module"    : retrieval_meta.get("primary_module"),
            "primary_collection": retrieval_meta.get("primary_collection"),
            "primary_score"     : retrieval_meta.get("primary_score"),
            "chunks"            : len(retrieval_meta.get("docs", [])),
            "source_url"        : retrieval_meta.get("source_url"),
        },
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
    # Inline: starts with `---` or list of plays.
    stripped = message.strip()
    if stripped.startswith("---") or re.match(r"^-\s+name:", stripped):
        return stripped
    return None


# ─────────────────────────────────────────────
#  Tool dispatch
# ─────────────────────────────────────────────

TOOL_REGISTRY = {
    "search_docs"      : search_docs,
    "get_module_info"  : get_module_info,
    "validate_yaml"    : validate_yaml,
    "generate_playbook": generate_playbook,
}


def run_tool(name: str, args: dict[str, Any] | None = None, **extra) -> dict:
    """
    Safe dispatcher used by the orchestrator.
    Unknown tools return an error payload instead of raising.
    """
    args = dict(args or {})
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}

    try:
        if name == "generate_playbook":
            return fn(
                user_request = args.get("user_request") or args.get("request") or extra.get("user_request", ""),
                retrieval_meta = args.get("retrieval_meta") or extra.get("retrieval_meta"),
                conversation_facts = args.get("conversation_facts") or extra.get("conversation_facts"),
                db_session   = extra.get("db_session"),
            )
        if name == "validate_yaml":
            return fn(
                yaml_content = args.get("yaml") or args.get("yaml_content"),
                filepath     = args.get("filepath"),
            )
        if name == "get_module_info":
            return fn(module=args.get("module") or args.get("name") or "")
        if name == "search_docs":
            return fn(
                query      = args.get("query", ""),
                collection = args.get("collection"),
                top_k      = int(args.get("top_k", 3)),
            )
        return fn(**args)
    except Exception as e:
        return {"error": f"{name} failed: {e}"}
