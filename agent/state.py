"""
=============================================================
  AnsibleAI Agent — Graph state + production-ready gate

  The LangGraph agent carries a single `AgentState` dict through
  every node (reason → tools → draft → gate → respond). This module
  defines that state, the conversation/context helpers used to
  build it, and the hard "production-ready" gate that decides
  whether a drafted playbook may be released.
=============================================================
"""

from __future__ import annotations

import os
import re
from typing import TypedDict

# ─────────────────────────────────────────────
#  State
# ─────────────────────────────────────────────

def max_iterations_default() -> int:
    """Total draft attempts (1 initial + N repairs). Env-overridable."""
    raw = (os.getenv("AGENT_MAX_ITERATIONS") or "").strip()
    if raw.isdigit() and int(raw) >= 1:
        return int(raw)
    return 4


class AgentState(TypedDict, total=False):
    # ── inputs ──────────────────────────────
    thread_id: int
    user_message: str
    merged_request: str          # original intent + cross-turn params
    history: list[dict]
    history_text: str
    conversation_facts: dict
    pinned_collection: str | None

    # ── reasoning (CoT) ─────────────────────
    intent: str
    pivot: bool
    thoughts: list[str]          # chain-of-thought scratchpad
    decision: dict               # last reason-node decision
    reason_steps: int
    tool_calls: int
    research_used: bool          # one corrective re-retrieval per turn

    # ── retrieval ───────────────────────────
    retrieval_meta: dict | None  # raw meta (docs, scores, ...)
    search_summary: dict | None  # public search_docs payload
    collection_debug: dict

    # ── draft / repair loop ─────────────────
    draft_yaml: str | None
    playbook_path: str | None
    draft_issues: list[str]      # cheap generation heuristics
    validation: dict | None      # full validator + ansible-lint payload
    gate_ready: bool
    gate_failures: list[str]     # repairable via redraft
    gate_environment: list[str]  # NOT repairable (e.g. lint backend missing)
    repair_feedback: str         # structured feedback for the next draft
    repair_plan: str             # CoT plan for fixing the failures
    iteration: int               # draft attempts consumed
    max_iterations: int
    low_confidence: bool
    low_confidence_reason: str

    # ── clarification ───────────────────────
    awaiting_user: bool
    questions: list[str]

    # ── output ──────────────────────────────
    final_text: str
    module: str | None
    module_ref: dict | None
    filename: str | None
    tool_trace: list[dict]


# ─────────────────────────────────────────────
#  Production-ready gate
# ─────────────────────────────────────────────
#
#  A playbook is released as "production-ready" ONLY when:
#    1. the full validator reports zero errors,
#    2. ansible-lint actually ran and passed (skipped ≠ passed),
#    3. no unfilled placeholder tokens remain (quoted "{{ var_* }}"
#       Jinja variables are legitimate Ansible and stay allowed),
#    4. no cheap generation heuristics fired (fake modules, RST
#       markup, constraint mismatches, literal secrets, ...).
#
#  Failures are split into two buckets:
#    - repairable   : fixable by redrafting the YAML (loop on these)
#    - environmental: system-level (lint backend missing/timeout) —
#      redrafting cannot fix them, so they never consume iterations.

_LINT_ENV_STATUSES = {
    "skipped", "timeout", "unsupported_platform",
    "wsl_not_configured", "docker_not_available", "not_installed",
    "failed_to_run", "error", "not_run",
}

_PLACEHOLDER_WARNING_MARKER = "placeholder"


def evaluate_gate(
    validation: dict | None,
    draft_issues: list[str] | None = None,
) -> tuple[bool, list[str], list[str]]:
    """
    Evaluate the production-ready gate.

    Returns (ready, repairable_failures, environmental_failures).
    """
    repairable: list[str] = []
    environmental: list[str] = []

    if not validation:
        return False, ["Validation did not run"], []

    for err in validation.get("errors") or []:
        repairable.append(f"validator error: {err}")

    lint = validation.get("ansible_lint") or {}
    status = (lint.get("status") or "not_run").lower()
    if status == "violations":
        for v in (lint.get("violations") or [])[:20]:
            repairable.append(f"ansible-lint: {v}")
        if not lint.get("violations"):
            repairable.append("ansible-lint reported violations")
    elif status in _LINT_ENV_STATUSES:
        environmental.append(
            f"ansible-lint could not run (status: {status}) — configure "
            "ANSIBLE_LINT_MODE (native/wsl/docker) to enable the lint gate"
        )

    # Placeholder warnings are promoted to gate failures: a playbook that
    # still contains YOUR-*, <token>, TODO/CHANGEME or bare var_* strings
    # is not production-ready. Quoted "{{ var_* }}" Jinja is fine and is
    # NOT flagged by the validator.
    for warn in validation.get("warnings") or []:
        if _PLACEHOLDER_WARNING_MARKER in warn.lower():
            repairable.append(f"placeholder: {warn}")

    for issue in draft_issues or []:
        repairable.append(f"generation check: {issue}")

    # De-duplicate while preserving order.
    repairable = list(dict.fromkeys(repairable))
    environmental = list(dict.fromkeys(environmental))

    ready = not repairable and not environmental
    return ready, repairable, environmental


def format_repair_feedback(
    repairable: list[str],
    max_items: int = 15,
) -> str:
    """Render gate failures as numbered feedback for the next draft."""
    if not repairable:
        return "none"
    lines = [f"{i}. {f}" for i, f in enumerate(repairable[:max_items], start=1)]
    if len(repairable) > max_items:
        lines.append(f"... and {len(repairable) - max_items} more issue(s)")
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  History / conversation helpers
# ─────────────────────────────────────────────

HISTORY_WINDOW = 10  # most recent messages sent to the reasoning LLM


def format_history(history: list[dict]) -> str:
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


def pinned_collection_from_history(history: list[dict]) -> str | None:
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


# Matches lines like `key: value` or `key = value` (param-style replies).
_KV_LINE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_\-\.]{1,40})\s*[:=]\s*(.+?)\s*$"
)

_GENERATE_VERBS = (
    "create", "generate", "launch", "provision", "deploy", "spin up",
    "build", "make", "set up", "setup", "start", "add",
)


def _looks_like_intent_sentence(text: str) -> bool:
    low = (text or "").lower().strip()
    if not low or len(low) < 10:
        return False
    if "\n" not in low and _KV_LINE_RE.match(low):
        return False
    return any(v in low for v in _GENERATE_VERBS)


def parse_kv_reply(text: str) -> dict[str, str]:
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
    """Earliest user message that looks like a generation intent sentence."""
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


def build_merged_request(user_message: str, history: list[dict]) -> str:
    """
    Combine the original intent sentence, every `key: value` parameter the
    user supplied across turns, and the current message into one request
    string for the draft node.
    """
    original_intent = _find_original_intent(history, user_message)

    params: dict[str, str] = {}
    for msg in history or []:
        if msg.get("role") != "user":
            continue
        params.update(parse_kv_reply(msg.get("content") or ""))
    params.update(parse_kv_reply(user_message))

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

    cur = (user_message or "").strip()
    if cur and (not original_intent or cur != original_intent.strip()):
        parts.append(f"Current user message: {cur}")

    return "\n\n".join(p for p in parts if p)


def collect_conversation_facts(user_message: str, history: list[dict]) -> dict[str, str]:
    """Collect compact key:value facts across turns for strict param checks."""
    facts: dict[str, str] = {}
    for msg in history or []:
        if msg.get("role") != "user":
            continue
        facts.update(parse_kv_reply(msg.get("content") or ""))
    facts.update(parse_kv_reply(user_message))
    return facts


# ─────────────────────────────────────────────
#  Heuristic intent classifier (LLM fallback)
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

VALID_INTENTS = {"generate", "explain", "troubleshoot", "compare", "edit", "chat"}

# An explicit request for an artefact ("write a playbook that ...") outranks
# every keyword family. Without this, a request naming a module whose name is
# also a symptom word — "a playbook that uses the debug module" — matches
# TROUBLESHOOT_HINTS first and the graph never drafts anything.
_EXPLICIT_GENERATE_RE = re.compile(
    r"\b(write|create|generate|produce|give me|make|build|scaffold|provision)\b"
    r"[^.?!]{0,60}?\b(playbook|role|task file|tasks file)\b",
    re.IGNORECASE,
)

_INTENT_HINT_FAMILIES = (
    ("compare", COMPARE_HINTS),
    ("troubleshoot", TROUBLESHOOT_HINTS),
    ("generate", GENERATE_HINTS),
    ("explain", EXPLAIN_HINTS),
)


def wants_new_artifact(message: str) -> bool:
    """True when the message explicitly asks for a playbook to be produced."""
    return bool(_EXPLICIT_GENERATE_RE.search(message or ""))


def heuristic_intent_is_confident(message: str) -> bool:
    """
    True when `guess_intent` is reading an unambiguous signal.

    An explicit artefact request is decisive on its own. Otherwise the
    message must match exactly one keyword family — matching several means
    the keywords disagree and only the planner LLM can settle it.
    """
    if wants_new_artifact(message):
        return True
    m = (message or "").lower()
    matched = sum(
        1 for _, hints in _INTENT_HINT_FAMILIES if any(h in m for h in hints)
    )
    return matched == 1


def guess_intent(message: str, history: list[dict]) -> str:
    m = (message or "").lower()
    if wants_new_artifact(message):
        return "generate"
    if any(h in m for h in COMPARE_HINTS):
        return "compare"
    if any(h in m for h in TROUBLESHOOT_HINTS):
        return "troubleshoot"
    if any(h in m for h in GENERATE_HINTS):
        return "generate"
    if any(h in m for h in EXPLAIN_HINTS):
        return "explain"
    has_prev_playbook = any((h.get("role") == "assistant") and h.get("playbook") for h in history)
    if has_prev_playbook and any(m.startswith(h) or (" " + h) in f" {m}" for h in EDIT_HINTS):
        return "edit"
    return "chat"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(t in text for t in terms)


def force_pivot_from_message(message: str, pinned_collection: str | None) -> bool:
    """
    Safety override for stale pins: if the thread is pinned to one ecosystem
    but the new message clearly asks for another, force pivot.
    """
    if not pinned_collection:
        return False

    m = (message or "").lower()
    pinned = pinned_collection.lower()

    if any(w in m for w in ("switch to", "instead of", "rather than", "not aws", "not azure")):
        return True

    if pinned == "amazon.aws":
        if _contains_any(m, _AZURE_HINTS) or _contains_any(m, _K8S_HINTS):
            return True
        if _contains_any(m, _LOCAL_OBS_HINTS) and not _contains_any(m, _AWS_HINTS):
            return True

    if pinned == "azure.azcollection":
        if _contains_any(m, _AWS_HINTS) or _contains_any(m, _K8S_HINTS):
            return True

    if pinned == "kubernetes.core":
        if _contains_any(m, _AWS_HINTS) or _contains_any(m, _AZURE_HINTS):
            return True

    return False


def is_local_observability_request(message: str) -> bool:
    return _contains_any((message or "").lower(), _LOCAL_OBS_HINTS)


def has_explicit_cloud_vendor(message: str) -> bool:
    return _contains_any((message or "").lower(), _CLOUD_VENDOR_HINTS)


# ─────────────────────────────────────────────
#  Initial state builder
# ─────────────────────────────────────────────

def build_initial_state(
    thread_id: int,
    user_message: str,
    history: list[dict],
) -> AgentState:
    return AgentState(
        thread_id=thread_id,
        user_message=user_message,
        merged_request=build_merged_request(user_message, history),
        history=list(history or []),
        history_text=format_history(history),
        conversation_facts=collect_conversation_facts(user_message, history),
        pinned_collection=pinned_collection_from_history(history),
        intent="",
        pivot=False,
        thoughts=[],
        decision={},
        reason_steps=0,
        tool_calls=0,
        research_used=False,
        retrieval_meta=None,
        search_summary=None,
        collection_debug={},
        draft_yaml=None,
        playbook_path=None,
        draft_issues=[],
        validation=None,
        gate_ready=False,
        gate_failures=[],
        gate_environment=[],
        repair_feedback="none",
        repair_plan="",
        iteration=0,
        max_iterations=max_iterations_default(),
        low_confidence=False,
        low_confidence_reason="",
        awaiting_user=False,
        questions=[],
        final_text="",
        module=None,
        module_ref=None,
        filename=None,
        tool_trace=[],
    )
