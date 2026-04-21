"""
=============================================================
  AnsibleAI Agent — Orchestrator

  Phases:
    1. PLAN              — LLM returns a JSON plan of tool calls
    2. EXECUTE           — we invoke those tools (search_docs / validate_yaml / …)
    3a. CLARIFY DECIDE   — for generate/edit intents, an LLM call decides
                           whether enough information has been supplied;
                           if not it returns the questions to ask
    3b. CLARIFY MESSAGE  — wrap those questions into a friendly reply and
                           STOP (no playbook generation this turn)
    3c. GENERATE         — otherwise produce the playbook via local Ollama
    4. SYNTHESIZE        — compose the final natural-language reply

  The reasoning LLM is configurable (default: local Ollama gemma3:12b).
  The YAML generator stays on local Ollama (qwen2.5-coder) for quality.
=============================================================
"""

from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass, field, asdict
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from .prompts import (
    AGENT_ROLE,
    PLANNING_PROMPT,
    CLARIFY_DECIDER_PROMPT,
    SYNTHESIS_PROMPT,
)
from .tools       import (
    run_tool,
    extract_embedded_yaml,
    resolve_collection_with_prefetch,
)
from .collections import get_collection_allowlist
from .llm         import chat as llm_chat, LLMError, current_config

HISTORY_WINDOW = 10  # most recent messages sent to the reasoning LLM


# ─────────────────────────────────────────────
#  Response dataclass
# ─────────────────────────────────────────────

@dataclass
class AgentResponse:
    text          : str
    playbook      : str | None = None
    filename      : str | None = None
    module        : str | None = None
    validation    : dict | None = None
    module_ref    : dict | None = None
    rag_meta      : dict | None = None
    tool_trace    : list[dict] = field(default_factory=list)
    intent        : str = "chat"
    awaiting_user : bool = False  # True when the agent asked a clarifying question

    def to_message_kwargs(self) -> dict[str, Any]:
        return {
            "role"      : "assistant",
            "content"   : self.text,
            "playbook"  : self.playbook,
            "filename"  : self.filename,
            "module"    : self.module,
            "validation": self.validation,
            "module_ref": self.module_ref,
            "rag_meta"  : self.rag_meta,
            "tool_trace": self.tool_trace,
        }

    def to_api_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────
#  History formatting
# ─────────────────────────────────────────────

def _format_history(history: list[dict]) -> str:
    if not history:
        return "(no previous messages)"
    recent = history[-HISTORY_WINDOW:]
    lines = []
    for m in recent:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role.upper()}: {content[:800]}")
    return "\n".join(lines) if lines else "(no previous messages)"


# ─────────────────────────────────────────────
#  Strict JSON parsing (used by both planner and clarify decider)
# ─────────────────────────────────────────────

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(raw: str, default: dict) -> dict:
    """Robust JSON-object parser that tolerates ```json fences and prose."""
    if not raw:
        return dict(default)
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = _JSON_OBJECT_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return dict(default)


def _parse_plan(raw: str) -> dict:
    return _parse_json_object(raw, default={"intent": "chat", "actions": []})


def _parse_clarify_decision(raw: str) -> dict:
    return _parse_json_object(raw, default={
        "needs_clarification": False,
        "essential_missing"  : [],
        "essential_provided" : [],
        "questions"          : [],
        "starter_values"     : {},
        "rationale"          : "parser fallback",
    })


# ─────────────────────────────────────────────
#  Heuristic intent classifier (fallback only)
# ─────────────────────────────────────────────

GENERATE_HINTS     = ("generate", "create", "make", "write", "deploy", "give me a playbook",
                      "produce", "scaffold", "launch", "provision", "spin up", "start an")
TROUBLESHOOT_HINTS = ("error", "fails", "doesn't work", "not working", "fix",
                      "troubleshoot", "debug", "why is")
EXPLAIN_HINTS      = ("what is", "what does", "explain", "how does", "tell me about", "describe")
COMPARE_HINTS      = (" vs ", "versus", "compare", "difference between")
EDIT_HINTS         = ("add", "change", "update", "remove", "modify", "now make", "also ")
_AWS_HINTS         = ("aws", "amazon", "cloudwatch", "ec2", "s3", "iam", "rds", "vpc", "lambda")
_AZURE_HINTS       = ("azure", "resource group", "aks", "arm", "blob")
_K8S_HINTS         = ("kubernetes", "k8s", "helm", "kubectl", "deployment", "namespace", "cluster")
_LOCAL_OBS_HINTS   = (
    "localhost", "127.0.0.1", "endpoint", "http", "request", "latency",
    "status code", "200", "404", "500", "ingest logs", "access log",
    "application log", "prometheus", "grafana", "otel", "open telemetry",
)
_CLOUD_VENDOR_HINTS = ("aws", "amazon", "cloudwatch", "azure", "gcp", "google cloud")
_DEFAULT_DELEGATION_HINTS = (
    "use defaults", "default is fine", "use default", "use any", "you choose",
    "your choice", "whatever works", "as you want", "up to you", "i don't care",
    "any value is fine", "pick one", "choose for me",
)


def _guess_intent(message: str, history: list[dict]) -> str:
    m = (message or "").lower()
    if any(h in m for h in COMPARE_HINTS):     return "compare"
    if any(h in m for h in TROUBLESHOOT_HINTS): return "troubleshoot"
    if any(h in m for h in GENERATE_HINTS):    return "generate"
    if any(h in m for h in EXPLAIN_HINTS):     return "explain"
    has_prev_playbook = any((h.get("role") == "assistant") and h.get("playbook") for h in history)
    if has_prev_playbook and any(m.startswith(h) or (" " + h) in f" {m}" for h in EDIT_HINTS):
        return "edit"
    return "chat"


def _fallback_actions(intent: str, message: str) -> list[dict]:
    if intent in ("generate", "edit", "explain", "troubleshoot", "compare"):
        return [{"tool": "search_docs", "query": message[:160]}]
    return []


def _cap_search_actions(actions: list[dict], max_search: int = 1) -> list[dict]:
    """Keep at most `max_search` search_docs actions while preserving order."""
    out: list[dict] = []
    seen_search = 0
    for a in actions or []:
        if (a.get("tool") or "").strip() == "search_docs":
            if seen_search >= max_search:
                continue
            seen_search += 1
        out.append(a)
    return out


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(t in text for t in terms)


def _force_pivot_from_message(message: str, pinned_collection: str | None) -> bool:
    """
    Safety override for stale pins.
    If the thread is pinned to one ecosystem but the new message clearly
    asks for another (or asks for local endpoint observability), force pivot.
    """
    if not pinned_collection:
        return False

    m = (message or "").lower()
    pinned = pinned_collection.lower()

    # Explicit ecosystem switch words should always pivot.
    if any(w in m for w in ("switch to", "instead of", "rather than", "not aws", "not azure")):
        return True

    if pinned == "amazon.aws":
        if _contains_any(m, _AZURE_HINTS) or _contains_any(m, _K8S_HINTS):
            return True
        # Important for your reported case: local endpoint metrics requests
        # should not stay hard-pinned to AWS unless AWS is explicitly mentioned.
        if _contains_any(m, _LOCAL_OBS_HINTS) and not _contains_any(m, _AWS_HINTS):
            return True

    if pinned == "azure.azcollection":
        if _contains_any(m, _AWS_HINTS) or _contains_any(m, _K8S_HINTS):
            return True

    if pinned == "kubernetes.core":
        if _contains_any(m, _AWS_HINTS) or _contains_any(m, _AZURE_HINTS):
            return True

    return False


def _is_local_observability_request(message: str) -> bool:
    m = (message or "").lower()
    return _contains_any(m, _LOCAL_OBS_HINTS)


def _has_explicit_cloud_vendor(message: str) -> bool:
    m = (message or "").lower()
    return _contains_any(m, _CLOUD_VENDOR_HINTS)


def _user_delegates_missing_values(message: str) -> bool:
    """
    True when the user explicitly lets the assistant pick defaults for
    remaining fields (prevents clarify loops like "use any transformation").
    """
    m = (message or "").lower()
    return _contains_any(m, _DEFAULT_DELEGATION_HINTS)


# ─────────────────────────────────────────────
#  Pinned-collection lookup (from prior turns)
# ─────────────────────────────────────────────

def _pinned_collection_from_history(history: list[dict]) -> str | None:
    """
    Walk the history in reverse and return the most recent
    `rag_meta.primary_collection` from an assistant turn. This is the
    "pin" used to keep follow-ups grounded in the same ecosystem.
    """
    for msg in reversed(history or []):
        if msg.get("role") != "assistant":
            continue
        rm = msg.get("rag_meta") or {}
        coll = (rm.get("primary_collection") or "").strip() if isinstance(rm, dict) else ""
        if coll:
            return coll
    return None


# ─────────────────────────────────────────────
#  Cross-turn request merging for the generator
# ─────────────────────────────────────────────

# Matches lines like `key: value` or `key = value` (param-style replies).
_KV_LINE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_\-\.]{1,40})\s*[:=]\s*(.+?)\s*$"
)

# Tokens we consider part of a user's "generate/create" intent sentence.
_GENERATE_VERBS = (
    "create", "generate", "launch", "provision", "deploy", "spin up",
    "build", "make", "set up", "setup", "start", "add",
)


def _looks_like_intent_sentence(text: str) -> bool:
    low = (text or "").lower().strip()
    if not low:
        return False
    if len(low) < 10:
        return False
    # Plain "key: value" replies shouldn't count as intent sentences.
    if "\n" not in low and _KV_LINE_RE.match(low):
        return False
    return any(v in low for v in _GENERATE_VERBS)


def _parse_kv_reply(text: str) -> dict[str, str]:
    """Parse a short user reply that lists `param: value` pairs."""
    out: dict[str, str] = {}
    if not text:
        return out
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-*").strip()
        if not line:
            continue
        m = _KV_LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1).strip().lower()
        val = m.group(2).strip().strip("`\"'")
        if key and val:
            out[key] = val
    return out


def _find_original_intent(history: list[dict], current_message: str) -> str | None:
    """
    Walk back through `history` (oldest → newest) and return the earliest
    user message that looks like a generation intent sentence. Fall back
    to the most recent one if nothing qualifies.
    """
    candidate_latest = None
    for msg in history or []:
        if msg.get("role") != "user":
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if _looks_like_intent_sentence(content):
            return content
        candidate_latest = content
    return candidate_latest


def _build_merged_request(user_message: str, history: list[dict]) -> str:
    """
    Build the single "enriched" request string handed to the generator.
    It combines:
      • the original intent sentence from the thread (if any),
      • every `key: value` parameter the user supplied across turns,
      • the current message.
    This is what fixes the bug where values captured during clarify
    (e.g. `name: rg-monitoring`) were dropped before YAML generation.
    """
    original_intent = _find_original_intent(history, user_message)

    # Collect param replies across the whole thread, newest overrides older.
    params: dict[str, str] = {}
    for msg in history or []:
        if msg.get("role") != "user":
            continue
        params.update(_parse_kv_reply(msg.get("content") or ""))
    params.update(_parse_kv_reply(user_message))

    parts: list[str] = []
    if original_intent and original_intent.strip() != (user_message or "").strip():
        parts.append(f"Original request: {original_intent.strip()}")
    elif original_intent:
        parts.append(original_intent.strip())
    else:
        parts.append((user_message or "").strip())

    if params:
        rendered = "\n".join(f"- {k}: {v}" for k, v in params.items())
        parts.append("Parameters provided across the conversation:\n" + rendered)

    # Always echo the current turn verbatim as well, so short replies
    # like "use defaults" still reach the generator.
    cur = (user_message or "").strip()
    if cur and (not original_intent or cur != original_intent.strip()):
        parts.append(f"Current user message: {cur}")

    return "\n\n".join(p for p in parts if p)


def _collect_conversation_facts(user_message: str, history: list[dict]) -> dict[str, str]:
    """Collect compact key:value facts across turns for strict param checks."""
    facts: dict[str, str] = {}
    for msg in history or []:
        if msg.get("role") != "user":
            continue
        facts.update(_parse_kv_reply(msg.get("content") or ""))
    facts.update(_parse_kv_reply(user_message))
    return facts


# ─────────────────────────────────────────────
#  Tool execution
# ─────────────────────────────────────────────

def _run_actions(
    actions: list[dict],
    user_message: str,
    db_session,
    pinned_collection: str | None = None,
    pivot: bool = False,
) -> tuple[list[dict], dict | None, dict | None, dict]:
    """
    Execute planner-chosen actions. Returns:
      - tool_trace
      - playbook_result (always None here; we generate separately in the
        dedicated phase after the coverage check)
      - best_rag_meta : richest retrieval_meta we saw (for downstream use)
      - collection_debug : dict describing how the collection filter was
        chosen for each search_docs call (for the diagnostic log)
    """
    trace: list[dict] = []
    best_rag_meta: dict | None = None
    # Per-turn cache to avoid duplicate retriever calls for identical
    # (query, collection, top_k) requests.
    search_cache: dict[tuple[str, str | None, int], dict] = {}
    collection_debug: dict = {
        "planner" : None,
        "pinned"  : pinned_collection,
        "pivot"   : pivot,
        "resolved": None,
        "source"  : "none",
    }

    for action in (actions or [])[:3]:
        tool = (action.get("tool") or "").strip()
        if not tool:
            continue
        # The orchestrator — not the planner — decides when to call
        # generate_playbook, so we drop any such actions here.
        if tool == "generate_playbook":
            continue

        args = {k: v for k, v in action.items() if k != "tool"}

        if tool == "validate_yaml" and not args.get("yaml") and not args.get("yaml_content"):
            embedded = extract_embedded_yaml(user_message)
            if embedded:
                args["yaml"] = embedded

        # For search_docs, let RAG decide the collection instead of
        # trusting the planner's free-form field.
        if tool == "search_docs":
            raw_hint = args.get("collection")
            query = args.get("query") or user_message[:160]
            top_k = int(args.get("top_k", 3))
            resolved, source, prefetched = resolve_collection_with_prefetch(
                query        = query,
                planner_hint = raw_hint,
                pinned       = pinned_collection,
                pivot        = pivot,
                top_k        = top_k,
            )
            if collection_debug["planner"] is None and raw_hint:
                collection_debug["planner"] = raw_hint
            # Only overwrite the "resolved" snapshot with the first
            # search call so the diagnostic line reflects the primary
            # retrieval used by the clarify decider / generator.
            if collection_debug["resolved"] is None:
                collection_debug["resolved"] = resolved
                collection_debug["source"]   = source
            args["collection"] = resolved
            args["top_k"] = top_k

            # Reuse unfiltered retrieval performed during collection
            # resolution when possible (source=vote or source=none).
            if prefetched and source in {"vote", "none"}:
                result = prefetched
                # Keep trace accurate to the final resolved collection.
                result["query"] = query
                cache_key = (query, resolved, top_k)
                search_cache[cache_key] = result
            else:
                cache_key = (query, resolved, top_k)
                if cache_key in search_cache:
                    result = search_cache[cache_key]
                else:
                    result = run_tool(tool, args)
                    if isinstance(result, dict):
                        search_cache[cache_key] = result
        else:
            result = run_tool(tool, args)

        if tool == "search_docs":
            rm = result.get("_retrieval_meta") if isinstance(result, dict) else None
            if rm and (best_rag_meta is None or len(rm.get("docs", [])) > len(best_rag_meta.get("docs", []))):
                best_rag_meta = rm

        compact = result if isinstance(result, dict) else {"value": str(result)}
        compact = {k: v for k, v in compact.items() if k != "_retrieval_meta"}
        trace.append({"tool": tool, "args": args, "result": compact})

    return trace, None, best_rag_meta, collection_debug


def _do_generate_playbook(
    user_message: str,
    retrieval_meta: dict | None,
    conversation_facts: dict | None,
    db_session,
) -> dict | None:
    """Invoke the generate_playbook tool. Returns the tool result dict or None on failure."""
    result = run_tool(
        "generate_playbook",
        {
            "user_request": user_message,
            "retrieval_meta": retrieval_meta,
            "conversation_facts": conversation_facts or {},
        },
        db_session=db_session,
    )
    if isinstance(result, dict) and (result.get("playbook") or result.get("needs_clarification")):
        return result
    return None


# ─────────────────────────────────────────────
#  Tool-trace summary for prompts
# ─────────────────────────────────────────────

def _summarize_tool_trace(trace: list[dict], limit_chars: int = 2800) -> str:
    if not trace:
        return "(no tools used)"

    blocks = []
    for entry in trace:
        tool   = entry.get("tool")
        result = entry.get("result", {}) or {}

        if tool == "search_docs":
            bits = []
            if result.get("primary_module"):
                bits.append(f"primary module = {result['primary_module']} (score {result.get('score')})")
            req = result.get("required_params") or []
            if req:
                bits.append("required params: " + ", ".join(req))
            chunk_lines = []
            for c in (result.get("chunks") or [])[:4]:
                chunk_lines.append(
                    f"  - [{c.get('module')} / {c.get('chunk_type')} / score {c.get('score')}] "
                    f"{(c.get('text') or '')[:220].strip()}"
                )
            block = f"search_docs(query={result.get('query')!r}):\n" + "\n".join(bits + chunk_lines)
        elif tool == "get_module_info":
            if result.get("found"):
                req = ", ".join(p["name"] for p in (result.get("required_params") or [])) or "none"
                block = (
                    f"get_module_info({result.get('module')}): description="
                    f"{(result.get('description') or '')[:200]} | required: {req}"
                )
            else:
                block = "get_module_info: module not in KB"
        elif tool == "validate_yaml":
            block = (
                f"validate_yaml: is_valid={result.get('is_valid')}, "
                f"warnings={len(result.get('warnings') or [])}, "
                f"errors={len(result.get('errors') or [])}, "
                f"first_errors={(result.get('errors') or [])[:2]}"
            )
        elif tool == "generate_playbook":
            val = result.get("validation") or {}
            block = (
                f"generate_playbook: module={result.get('module')}, "
                f"is_valid={val.get('is_valid')}, "
                f"warnings={len(val.get('warnings') or [])}, "
                f"errors={len(val.get('errors') or [])}"
            )
        else:
            block = f"{tool}: {json.dumps(result, default=str)[:400]}"

        blocks.append(block)

    text = "\n\n".join(blocks)
    return text[:limit_chars] + ("…" if len(text) > limit_chars else "")


def _validation_summary(validation: dict | None) -> str:
    if not validation:
        return "n/a"
    if validation.get("is_valid"):
        warns = len(validation.get("warnings") or [])
        return f"passed ({warns} warning{'s' if warns != 1 else ''})"
    return f"failed ({len(validation.get('errors') or [])} error(s))"


# ─────────────────────────────────────────────
#  Clarify-decider helpers
# ─────────────────────────────────────────────

def _format_module_params_for_prompt(params: list[dict], limit: int = 30) -> str:
    """Render the curated module parameter list as a compact bullet list."""
    if not params:
        return "(no parameter info available)"
    lines = []
    for p in params[:limit]:
        req = "REQUIRED" if p.get("required") else "optional"
        desc = (p.get("description") or "").strip().replace("\n", " ")
        if desc:
            desc = " — " + desc[:120]
        lines.append(f"- `{p.get('name')}` ({p.get('type', 'any')}, {req}){desc}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  Main entry point
# ─────────────────────────────────────────────

def handle_message(
    thread_id: int,
    user_message: str,
    history: list[dict],
    db_session=None,
) -> AgentResponse:
    """
    End-to-end handling of one user message.

    `history` is a list of dicts shaped like ChatMessage.to_dict(), ending
    with the current user turn (so we can scan prior user turns too).
    """
    user_message = (user_message or "").strip()
    if not user_message:
        return AgentResponse(text="I didn't receive any message. What would you like help with?")

    history_text = _format_history(history)
    cfg = current_config()
    fb = cfg.get("fallbacks") or []
    fb_str = (" (fallbacks: " + ", ".join(fb) + ")") if fb else ""
    print(f"  [Agent] LLM: {cfg['provider']} | {cfg['model']}{fb_str}")

    # Pinned collection from the last assistant turn (if any). This is
    # what keeps follow-ups grounded in the same ecosystem across turns.
    pinned_collection = _pinned_collection_from_history(history)
    allowlist_str = ", ".join(sorted(get_collection_allowlist())) or "(none indexed)"

    # ─── PHASE 1 — PLAN ───────────────────────────
    planner_input = PLANNING_PROMPT.format(
        history             = history_text,
        message             = user_message,
        pinned_collection   = pinned_collection or "none",
        known_collections   = allowlist_str,
    )
    try:
        plan_raw = llm_chat(
            planner_input,
            system      = AGENT_ROLE,
            temperature = 0.1,
            max_tokens  = 500,
            expect_json = True,
        )
    except LLMError as e:
        print(f"  [Agent] Planner LLM error: {e}")
        plan_raw = ""

    plan   = _parse_plan(plan_raw)
    intent = (plan.get("intent") or "").strip().lower()
    if intent not in {"generate", "explain", "troubleshoot", "compare", "edit", "chat"}:
        intent = _guess_intent(user_message, history)

    planner_pivot = bool(plan.get("pivot"))
    forced_pivot  = _force_pivot_from_message(user_message, pinned_collection)
    pivot = planner_pivot or forced_pivot
    if forced_pivot and not planner_pivot:
        print("  [Agent] Pivot override: forced by deterministic safety heuristic.")

    actions = plan.get("actions") or []
    if intent == "troubleshoot":
        embedded = extract_embedded_yaml(user_message)
        if embedded and not any(a.get("tool") == "validate_yaml" for a in actions):
            actions.append({"tool": "validate_yaml", "yaml": embedded})
    if not actions and intent != "chat":
        actions = _fallback_actions(intent, user_message)
    # Latency optimization: generation/editing only needs one document
    # search to identify module + params in most cases.
    if intent in ("generate", "edit"):
        actions = _cap_search_actions(actions, max_search=1)

    print(
        f"  [Agent] Intent: {intent} | Pivot: {pivot} "
        f"(planner={planner_pivot}, forced={forced_pivot}) | "
        f"Actions: {[a.get('tool') for a in actions]}"
    )

    # ─── PHASE 2 — EXECUTE ─────────────────────────
    tool_trace, _unused, best_rag_meta, collection_debug = _run_actions(
        actions, user_message,
        db_session        = db_session,
        pinned_collection = pinned_collection,
        pivot             = pivot,
    )

    def _best_search() -> dict:
        """Pick the search_docs result that actually identified a module."""
        best = None
        for entry in tool_trace:
            if entry.get("tool") != "search_docs":
                continue
            res = entry.get("result") or {}
            if res.get("primary_module"):
                best = res
        if best is None:
            for entry in tool_trace:
                if entry.get("tool") == "search_docs":
                    best = entry.get("result") or {}
        return best or {}

    latest_search = _best_search()

    # If we're going to generate but the planner's search didn't surface
    # a primary module (e.g. wrong collection filter, low-confidence
    # retrieval), do one extra search ourselves so the clarify decider
    # has real `module_params` to reason about. The fallback also goes
    # through resolve_collection so it respects pin/pivot.
    if intent in ("generate", "edit") and not latest_search.get("primary_module"):
        print("  [Agent] No primary module from planner search; running fallback search.")
        fb_query = user_message[:160]
        fb_resolved, fb_source, fb_prefetched = resolve_collection_with_prefetch(
            query        = fb_query,
            planner_hint = None,
            pinned       = pinned_collection,
            pivot        = pivot,
        )
        fb_args = {"query": fb_query, "collection": fb_resolved, "top_k": 3}
        fallback = (
            fb_prefetched
            if (fb_prefetched and fb_source in {"vote", "none"})
            else run_tool("search_docs", fb_args)
        )
        rm = fallback.get("_retrieval_meta") if isinstance(fallback, dict) else None
        if rm and (best_rag_meta is None or len(rm.get("docs", [])) > len(best_rag_meta.get("docs", []))):
            best_rag_meta = rm
        compact = {k: v for k, v in (fallback or {}).items() if k != "_retrieval_meta"}
        tool_trace.append({"tool": "search_docs", "args": fb_args, "result": compact})
        if isinstance(fallback, dict) and fallback.get("primary_module"):
            latest_search = fallback
        if collection_debug["resolved"] is None:
            collection_debug["resolved"] = fb_resolved
            collection_debug["source"]   = fb_source

    primary_module     = latest_search.get("primary_module")
    primary_collection = latest_search.get("primary_collection")
    module_params      = latest_search.get("module_params") or []

    # ─── Collection resolution diagnostic ─────────
    print(
        "  [Agent] Collection: planner={p} | pinned={pi} | pivot={pv} | "
        "resolved={r} | source={s} | primary_module_coll={mc}".format(
            p  = collection_debug.get("planner") or "-",
            pi = collection_debug.get("pinned") or "-",
            pv = collection_debug.get("pivot"),
            r  = collection_debug.get("resolved") or "-",
            s  = collection_debug.get("source"),
            mc = primary_collection or "-",
        )
    )

    # Local-observability ambiguity guard disabled: always proceed to generation.

    # ─── PHASE 3a — CLARIFY DECIDER (disabled) ──
    # In no-clarify mode we always proceed to generation and let the
    # generator auto-fill unspecified parameters.
    playbook_result: dict | None = None
    clarified = False

    if False and intent in ("generate", "edit") and primary_module and module_params:
        decider_prompt = CLARIFY_DECIDER_PROMPT.format(
            primary_module     = primary_module,
            primary_collection = primary_collection or "unknown",
            module_params      = _format_module_params_for_prompt(module_params),
            history            = history_text,
            message            = user_message,
        )
        try:
            decider_raw = llm_chat(
                decider_prompt,
                system      = AGENT_ROLE,
                temperature = 0.0,
                max_tokens  = 600,
                expect_json = True,
            )
        except LLMError as e:
            print(f"  [Agent] Clarify decider LLM error: {e}")
            decider_raw = ""

        decision = _parse_clarify_decision(decider_raw)
        questions = decision.get("questions") or []
        needs_clarify = bool(decision.get("needs_clarification")) and bool(questions)

        # Deterministic escape hatch for clarify loops: when the user
        # explicitly delegates remaining values to defaults ("use any",
        # "you choose", ...), do not keep asking the same question.
        if needs_clarify and _user_delegates_missing_values(user_message):
            missing = list(decision.get("essential_missing") or [])
            provided = list(decision.get("essential_provided") or [])
            for p in missing:
                if p not in provided:
                    provided.append(p)
            decision["essential_provided"] = provided
            decision["essential_missing"] = []
            decision["questions"] = []
            questions = []
            needs_clarify = False
            print(
                "  [Agent] Clarify override: user delegated missing values; "
                "proceeding with sensible defaults."
            )

        print(
            f"  [Agent] Clarify decider: needs_clarification={needs_clarify}, "
            f"missing={decision.get('essential_missing')}, "
            f"provided={decision.get('essential_provided')}"
        )

        if needs_clarify:
            # Render the clarifying message DETERMINISTICALLY.
            # Empirically, asking the LLM to "list these N questions
            # verbatim" leads to duplicates / paraphrasing / dropped
            # items. Since the decider already produced clean questions,
            # we just format them ourselves. This also saves one full
            # LLM round-trip (~10s of latency).
            seen = set()
            unique_questions = []
            for q in questions:
                qtxt = (q.get("question") or "").strip()
                if not qtxt or qtxt in seen:
                    continue
                seen.add(qtxt)
                unique_questions.append(q)

            bullets = "\n".join(
                f"- **{q.get('param') or '?'}** — {q.get('question')}"
                for q in unique_questions
            )
            starter = decision.get("starter_values") or {}
            captured_line = ""
            if starter:
                captured = ", ".join(f"`{k}`=`{v}`" for k, v in starter.items())
                captured_line = f"\n\nI've already captured: {captured}."

            text = (
                f"I'll use **`{primary_module}`** for this. Before I can generate "
                f"the playbook I need a few more details:\n\n"
                f"{bullets}{captured_line}\n\n"
                f"Reply with these and I'll produce the YAML."
            )
            clarified = True
            tool_trace.append({
                "tool"  : "clarify_decider",
                "args"  : {"primary_module": primary_module},
                "result": {
                    "needs_clarification": True,
                    "essential_missing"  : decision.get("essential_missing"),
                    "essential_provided" : decision.get("essential_provided"),
                    "questions"          : questions,
                },
            })
            return AgentResponse(
                text          = text,
                intent        = intent,
                module        = primary_module,
                tool_trace    = tool_trace,
                awaiting_user = True,
                rag_meta      = {
                    "primary_module"    : primary_module,
                    "primary_collection": primary_collection,
                    "primary_score"     : (latest_search or {}).get("score"),
                    "chunks"            : len((best_rag_meta or {}).get("docs", []) or []),
                    "source_url"        : (latest_search or {}).get("source_url"),
                },
            )

    # ─── PHASE 3c — GENERATE (local Ollama qwen2.5-coder) ──
    if intent in ("generate", "edit") and not clarified:
        # Merge the original intent and every `key: value` the user gave
        # across turns into a single rich request. Without this, a reply
        # like `name: rg-monitoring` captured by the clarifier would be
        # invisible to the YAML generator and the playbook would use
        # whatever name the LLM invented.
        merged_request = _build_merged_request(user_message, history)
        if merged_request != user_message:
            print(
                "  [Agent] Merged cross-turn request passed to generator "
                f"(chars: {len(merged_request)})"
            )
        playbook_result = _do_generate_playbook(
            user_message   = merged_request,
            retrieval_meta = best_rag_meta,
            conversation_facts = _collect_conversation_facts(user_message, history),
            db_session     = db_session,
        )
        if playbook_result is not None:
            tool_trace.append({
                "tool"  : "generate_playbook",
                "args"  : {"user_request": merged_request[:400]},
                "result": {
                    "module"    : playbook_result.get("module"),
                    "filename"  : playbook_result.get("filename"),
                    "validation": playbook_result.get("validation"),
                },
            })

    # ─── PHASE 4 — SYNTHESIZE ──────────────────────
    if playbook_result:
        primary_module = playbook_result.get("module") or primary_module
        validation     = playbook_result.get("validation")
    else:
        validation = None

    # Fast path: when YAML was already generated, skip the synthesis LLM
    # call and return a deterministic summary to shave latency.
    if playbook_result:
        final_text = (
            f"I generated a playbook using `{primary_module}`. "
            f"Validation: {_validation_summary(validation)}. "
            f"Review the YAML and let me know if you want adjustments "
            f"(thresholds, dimensions, alarm actions, naming, or tags)."
        )
    else:
        synthesis_prompt = SYNTHESIS_PROMPT.format(
            history            = history_text,
            message            = user_message,
            intent             = intent,
            generated_flag     = "yes" if playbook_result else "no",
            validation_summary = _validation_summary(validation),
            primary_module     = primary_module or "n/a",
            tool_results       = _summarize_tool_trace(tool_trace),
        )

        try:
            final_text = llm_chat(
                synthesis_prompt,
                system      = AGENT_ROLE,
                temperature = 0.25,
                max_tokens  = 900,
            ).strip()
        except LLMError as e:
            print(f"  [Agent] Synthesis LLM error: {e}")
            final_text = ""

    if not final_text:
        if playbook_result:
            final_text = (
                f"Here is a playbook for your request using `{primary_module}`. "
                f"Validation: {_validation_summary(validation)}."
            )
        else:
            final_text = (
                "I'm not sure how to help with that yet. "
                "Could you rephrase or give me a bit more detail?"
            )

    response = AgentResponse(
        text       = final_text,
        intent     = intent,
        tool_trace = tool_trace,
    )

    if playbook_result:
        response.playbook   = playbook_result.get("playbook")
        response.filename   = playbook_result.get("filename")
        response.module     = playbook_result.get("module")
        response.validation = playbook_result.get("validation")
        response.module_ref = playbook_result.get("module_ref")
        pb_rag_meta         = playbook_result.get("rag_meta") or {}
        # Ensure primary_collection is present so the next turn can
        # read it as the pin for follow-ups.
        if not pb_rag_meta.get("primary_collection") and primary_collection:
            pb_rag_meta = {**pb_rag_meta, "primary_collection": primary_collection}
        response.rag_meta = pb_rag_meta
    elif best_rag_meta and primary_module:
        response.module   = primary_module
        response.rag_meta = {
            "primary_module"    : best_rag_meta.get("primary_module"),
            "primary_collection": best_rag_meta.get("primary_collection"),
            "primary_score"     : best_rag_meta.get("primary_score"),
            "chunks"            : len(best_rag_meta.get("docs", []) or []),
            "source_url"        : best_rag_meta.get("source_url"),
        }

    return response
