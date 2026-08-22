"""
=============================================================
  AnsibleAI Agent — LangGraph state machine

  Single agent, one graph:

      START → reason ──→ tools ──→ reason
                 │
                 ├──→ ask_user → END
                 │
                 ├──→ draft → gate ──→ reason   (repair loop)
                 │              │
                 │              └──→ respond → END
                 └──→ respond → END

  The REASON node thinks (chain-of-thought JSON) and picks the next
  step; deterministic fallbacks keep the graph moving when the LLM
  is unavailable. The DRAFT → GATE cycle repeats until the playbook
  passes the production gate (0 validator errors + ansible-lint
  passed + no placeholders) or the iteration budget is exhausted.
=============================================================
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from logging_setup import get_logger

from . import tools as T
from .cancel import check as check_cancelled
from .collections import get_collection_allowlist
from .llm import LLMError
from .llm import chat as llm_chat
from .prompts import REASON_PROMPT, REPAIR_PROMPT, RESPOND_PROMPT, agent_system_prompt
from .state import (
    VALID_INTENTS,
    AgentState,
    evaluate_gate,
    force_pivot_from_message,
    format_repair_feedback,
    guess_intent,
    has_explicit_cloud_vendor,
    heuristic_intent_is_confident,
    is_local_observability_request,
)

MAX_REASON_STEPS = 12
MAX_TOOL_STEPS = 4

ProgressCallback = Callable[[str, str, str | None], None] | None

log = get_logger(__name__)

_GENERATE_INTENTS = ("generate", "edit")


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _ensure_not_cancelled(state: AgentState) -> None:
    check_cancelled(state.get("thread_id"))


def _progress(config: RunnableConfig, step: str, message: str, detail: str | None = None) -> None:
    cb: ProgressCallback = ((config or {}).get("configurable") or {}).get("on_progress")
    if not cb:
        return
    try:
        cb(step, message, detail)
    except Exception:
        log.debug("agent.progress.callback_failed", step=step, exc_info=True)


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


def _str(val: Any) -> str:
    """Coerce an LLM-parsed value to str (lists get joined with newlines)."""
    if val is None:
        return ""
    if isinstance(val, list):
        return "\n".join(str(item) for item in val)
    return str(val)


def _compact_tool_result(result: dict | Any) -> dict:
    compact = result if isinstance(result, dict) else {"value": str(result)}
    return {k: v for k, v in compact.items() if k != "_retrieval_meta"}


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
        elif tool == "validate_yaml":
            block = (
                f"validate_yaml: is_valid={result.get('is_valid')}, "
                f"warnings={len(result.get('warnings') or [])}, "
                f"errors={len(result.get('errors') or [])}, "
                f"first_errors={(result.get('errors') or [])[:2]}"
            )
        elif tool in ("draft_playbook", "gate"):
            block = f"{tool}: {json.dumps(result, default=str)[:400]}"
        else:
            block = f"{tool}: {json.dumps(result, default=str)[:400]}"
        blocks.append(block)

    text = "\n\n".join(blocks)
    return text[:limit_chars] + ("…" if len(text) > limit_chars else "")


def _rag_meta_from_state(state: AgentState) -> dict | None:
    meta = state.get("retrieval_meta") or {}
    summary = state.get("search_summary") or {}
    if not meta and not summary:
        return None
    return {
        "primary_module"    : meta.get("primary_module") or summary.get("primary_module"),
        "primary_collection": meta.get("primary_collection") or summary.get("primary_collection"),
        "primary_score"     : meta.get("primary_score") or summary.get("score"),
        "chunks"            : len(meta.get("docs", []) or []),
        "source_url"        : meta.get("source_url") or summary.get("source_url"),
    }


# ─────────────────────────────────────────────
#  Node: REASON (chain-of-thought decision)
# ─────────────────────────────────────────────

def reason_node(state: AgentState, config: RunnableConfig) -> dict:
    _ensure_not_cancelled(state)
    steps = int(state.get("reason_steps") or 0) + 1
    updates: dict = {"reason_steps": steps}
    thoughts = list(state.get("thoughts") or [])

    if steps > MAX_REASON_STEPS:
        thoughts.append("Reason-step budget exhausted — responding with what we have.")
        updates.update(thoughts=thoughts, decision={"next": "respond"})
        return updates

    # ── 1. First pass: classify intent + plan via LLM CoT ─────────
    if not state.get("intent"):
        return _first_reason_pass(state, config, updates, thoughts)

    intent = state["intent"]

    # ── 2. Repair pass: gate failed, budget left ──────────────────
    if (
        state.get("draft_yaml")
        and not state.get("gate_ready")
        and state.get("gate_failures")
        and int(state.get("iteration") or 0) < int(state.get("max_iterations") or 1)
    ):
        return _repair_reason_pass(state, config, updates, thoughts)

    # ── 3. Retrieval done, generation not started ─────────────────
    if (
        intent in _GENERATE_INTENTS
        and state.get("search_summary") is not None
        and not state.get("draft_yaml")
    ):
        summary = state.get("search_summary") or {}
        primary_collection = summary.get("primary_collection")

        # Guardrail: ambiguous local-observability requests that retrieval
        # grounded in a cloud collection need a user decision first.
        if (
            is_local_observability_request(state["user_message"])
            and not has_explicit_cloud_vendor(state["user_message"])
            and primary_collection in {"amazon.aws", "azure.azcollection"}
        ):
            thoughts.append(
                "Local observability request landed on a cloud collection without "
                "explicit cloud intent — asking the user to confirm the stack."
            )
            updates.update(
                thoughts=thoughts,
                decision={
                    "next": "ask_user",
                    "questions": [
                        "**Target backend** — Prometheus/Grafana, ELK/OpenSearch, CloudWatch, Azure Monitor, or another stack?",
                        "**Log source** — where should I collect HTTP request logs from (app stdout, file path, reverse proxy like Nginx, or Docker logs)?",
                        "**Metric method** — OpenTelemetry instrumentation, access-log parsing, or an exporter/agent-based setup?",
                        "**Scope** — only status-code + latency metrics, or also request body/path scanning rules?",
                    ],
                },
            )
            return updates

        confident, why_not = T.check_retrieval_confidence(state.get("retrieval_meta"))
        if not confident:
            thoughts.append(f"Retrieval confidence too low to ground YAML: {why_not}")
            updates.update(
                thoughts=thoughts,
                low_confidence=True,
                low_confidence_reason=why_not,
                decision={"next": "respond"},
            )
            return updates

        thoughts.append(
            f"Docs retrieved for `{summary.get('primary_module')}` "
            f"({primary_collection}) — drafting the playbook."
        )
        updates.update(thoughts=thoughts, decision={"next": "draft"})
        return updates

    # ── 4. Everything else: compose the answer ────────────────────
    updates.update(decision={"next": "respond"})
    return updates


# Set AGENT_FAST_PLANNER=0 to always spend the planner round-trip.
_FAST_PLANNER = (os.getenv("AGENT_FAST_PLANNER") or "1").strip().lower() not in (
    "0", "false", "no",
)

# Longest message handed to the retriever verbatim. Past this the planner
# earns its round-trip by distilling the request into a focused query.
_FAST_PLANNER_MAX_CHARS = 300


def _can_skip_planner(state: AgentState, intent_guess: str) -> bool:
    """
    True when heuristics already answer everything the planner LLM would.

    The planner supplies four things, and we skip it only when each has a
    deterministic answer:

      intent   `guess_intent` matched a keyword rather than falling through
               to its "chat" default (which doubles as "no idea"), AND the
               keywords agreed with each other. A request like "a playbook
               that uses the debug module" matches both the generate and the
               troubleshoot families; only the planner can settle those, and
               guessing wrong costs the whole draft.
      pivot    Meaningless without a pinned collection. With a pin we keep
               the call: only the LLM reliably catches a switch that
               `force_pivot_from_message` has no keyword for, such as an
               ansible.builtin question inside a thread pinned to amazon.aws.
      query    The retriever is tuned and benchmarked on natural-language
               task descriptions, so a short message can go to it verbatim.
      ask_user The ambiguity that actually matters (local observability
               grounded in a cloud collection) has its own deterministic
               guardrail in `reason_node`, downstream of here.
    """
    if not _FAST_PLANNER:
        return False
    if state.get("pinned_collection"):
        return False
    if intent_guess == "chat":
        return False
    if not heuristic_intent_is_confident(state["user_message"]):
        return False
    return len(state["user_message"]) <= _FAST_PLANNER_MAX_CHARS


def _first_reason_pass(
    state: AgentState, config: dict, updates: dict, thoughts: list[str],
) -> dict:
    _progress(config, "planning", "Thinking about your request")

    intent_guess = guess_intent(state["user_message"], state.get("history") or [])

    if _can_skip_planner(state, intent_guess):
        thoughts.append(f"Heuristic intent: {intent_guess} (planner LLM skipped).")
        updates.update(intent=intent_guess, pivot=False, thoughts=thoughts)
        log.info("agent.reason.planned", intent=intent_guess, pivot=False, planner="heuristic")
        _progress(config, "planning", f"Planned {intent_guess} workflow", intent_guess)
        updates["decision"] = {
            "next": "tools",
            "search_query": state["user_message"].strip(),
        }
        return updates

    allowlist_str = ", ".join(sorted(get_collection_allowlist())) or "(none indexed)"

    prompt = REASON_PROMPT.format(
        history=state["history_text"],
        message=state["user_message"],
        pinned_collection=state.get("pinned_collection") or "none",
        known_collections=allowlist_str,
        intent_guess=intent_guess,
    )
    decision_raw: dict = {}
    try:
        raw = llm_chat(
            prompt,
            system=agent_system_prompt(),
            temperature=0.1,
            max_tokens=600,
            expect_json=True,
        )
        decision_raw = _parse_json_object(raw, default={})
    except LLMError as e:
        log.warning("agent.reason.llm_error", error=str(e), fallback="heuristics")

    intent = _str(decision_raw.get("intent")).strip().lower()
    if intent not in VALID_INTENTS:
        intent = intent_guess

    thought = _str(decision_raw.get("thought")).strip()
    if thought:
        thoughts.append(thought)
    else:
        thoughts.append(f"Heuristic intent: {intent}.")

    planner_pivot = bool(decision_raw.get("pivot"))
    forced_pivot = force_pivot_from_message(
        state["user_message"], state.get("pinned_collection")
    )
    pivot = planner_pivot or forced_pivot
    if forced_pivot and not planner_pivot:
        log.info("agent.reason.pivot_override", reason="deterministic_safety_heuristic")

    updates.update(intent=intent, pivot=pivot, thoughts=thoughts)
    log.info("agent.reason.planned", intent=intent, pivot=pivot)
    _progress(config, "planning", f"Planned {intent} workflow", intent)

    # LLM explicitly asked the user something it cannot decide itself.
    questions = [q for q in (decision_raw.get("questions") or []) if isinstance(q, str) and q.strip()]
    if decision_raw.get("ask_user") and questions:
        updates["decision"] = {"next": "ask_user", "questions": questions[:4]}
        return updates

    if intent == "chat":
        updates["decision"] = {"next": "respond"}
        return updates

    search_query = _str(decision_raw.get("search_query")).strip() or state["user_message"][:160]
    updates["decision"] = {"next": "tools", "search_query": search_query}
    return updates


def _repair_reason_pass(
    state: AgentState, config: dict, updates: dict, thoughts: list[str],
) -> dict:
    failures = list(state.get("gate_failures") or [])
    iteration = int(state.get("iteration") or 0)
    _progress(
        config, "generating",
        f"Draft failed the production gate — planning fixes (attempt {iteration + 1})",
        f"{len(failures)} issue(s)",
    )

    summary = state.get("search_summary") or {}
    prompt = REPAIR_PROMPT.format(
        message=state["merged_request"][:1200],
        primary_module=summary.get("primary_module") or "unknown",
        primary_collection=summary.get("primary_collection") or "unknown",
        draft_yaml=(state.get("draft_yaml") or "")[:4000],
        failures=format_repair_feedback(failures),
    )

    fix_plan = ""
    needs_search = False
    search_query = ""
    try:
        raw = llm_chat(
            prompt,
            system=agent_system_prompt(),
            temperature=0.1,
            max_tokens=800,
            expect_json=True,
        )
        parsed = _parse_json_object(raw, default={})
        thought = _str(parsed.get("thought")).strip()
        if thought:
            thoughts.append(thought)
        fix_plan = _str(parsed.get("fix_plan")).strip()
        needs_search = bool(parsed.get("needs_different_module"))
        search_query = _str(parsed.get("search_query")).strip()
    except LLMError as e:
        log.warning("agent.repair.llm_error", error=str(e), fallback="raw_gate_failures")

    if not fix_plan:
        fix_plan = format_repair_feedback(failures)
        thoughts.append("Using the raw gate failures as the fix plan.")

    updates.update(
        thoughts=thoughts,
        repair_plan=fix_plan,
        repair_feedback=format_repair_feedback(failures),
    )

    # Allow ONE corrective re-retrieval per turn when the model believes the
    # module itself is wrong (e.g. validator error "unknown module").
    if (
        needs_search
        and search_query
        and int(state.get("tool_calls") or 0) < MAX_TOOL_STEPS
        and not state.get("research_used")
    ):
        updates["research_used"] = True
        updates["decision"] = {"next": "tools", "search_query": search_query}
        return updates

    updates["decision"] = {"next": "draft"}
    return updates


# ─────────────────────────────────────────────
#  Node: TOOLS (search_docs / validate_yaml)
# ─────────────────────────────────────────────

def tools_node(state: AgentState, config: RunnableConfig) -> dict:
    _ensure_not_cancelled(state)
    decision = state.get("decision") or {}
    trace = list(state.get("tool_trace") or [])
    tool_calls = int(state.get("tool_calls") or 0)
    updates: dict = {}

    query = (decision.get("search_query") or state["user_message"][:160]).strip()
    _progress(config, "retrieving", "Searching Ansible documentation", query[:80])

    from observability.tracing import observe

    with observe(
        "retrieve-docs",
        as_type="retriever",
        input={"query": query[:200]},
        metadata={"node": "tools"},
    ) as ret_obs:
        resolved, source, prefetched = T.resolve_collection_with_prefetch(
            query=query,
            planner_hint=None,
            pinned=state.get("pinned_collection"),
            pivot=bool(state.get("pivot")),
            top_k=8,
        )

        if prefetched and source in {"vote", "none"}:
            result = prefetched
            result["query"] = query
        else:
            result = T.search_docs(
                query=query, collection=resolved, top_k=8, _prefetched_meta=prefetched,
            )
        tool_calls += 1
        trace.append({
            "tool": "search_docs",
            "args": {"query": query, "collection": resolved, "source": source},
            "result": _compact_tool_result(result),
        })

        # Fallback: broaden once when generation needs a module and the scoped
        # search did not surface one.
        if (
            state.get("intent") in _GENERATE_INTENTS
            and isinstance(result, dict)
            and not result.get("primary_module")
            and tool_calls < MAX_TOOL_STEPS
        ):
            log.info("agent.retrieval.broadening", reason="no_primary_module")
            _progress(config, "retrieving", "No strong module match yet — broadening doc search")
            fb_query = state["user_message"][:160]
            fb_resolved, fb_source, fb_prefetched = T.resolve_collection_with_prefetch(
                query=fb_query,
                planner_hint=None,
                pinned=None,
                pivot=True,  # ignore the pin for the broadened pass
                top_k=8,
            )
            fallback = (
                fb_prefetched
                if (fb_prefetched and fb_source in {"vote", "none"})
                else T.search_docs(query=fb_query, collection=fb_resolved, top_k=8,
                                   _prefetched_meta=fb_prefetched)
            )
            tool_calls += 1
            trace.append({
                "tool": "search_docs",
                "args": {"query": fb_query, "collection": fb_resolved, "source": fb_source},
                "result": _compact_tool_result(fallback),
            })
            if isinstance(fallback, dict) and fallback.get("primary_module"):
                result = fallback

        summary = _compact_tool_result(result)
        if ret_obs is not None:
            try:
                ret_obs.update(
                    output={
                        "primary_module": summary.get("primary_module"),
                        "collection": resolved,
                        "source": source,
                        "hit_count": len(summary.get("modules") or summary.get("hits") or []),
                    },
                    metadata={
                        "collection": str(resolved or ""),
                        "source": str(source or ""),
                    },
                )
            except Exception:  # noqa: BLE001
                pass

    updates.update(
        search_summary=summary,
        retrieval_meta=(result.get("_retrieval_meta") if isinstance(result, dict) else None),
        collection_debug={"resolved": resolved, "source": source,
                          "pinned": state.get("pinned_collection"),
                          "pivot": bool(state.get("pivot"))},
    )

    if summary.get("primary_module"):
        _progress(
            config, "retrieving", "Found candidate module in docs",
            f"{summary['primary_module']} ({summary.get('primary_collection')})",
        )

    # Troubleshoot turns with pasted YAML: validate it as evidence.
    if state.get("intent") == "troubleshoot" and not any(
        t.get("tool") == "validate_yaml" for t in trace
    ):
        embedded = T.extract_embedded_yaml(state["user_message"])
        if embedded:
            _progress(config, "validating", "Checking the YAML you pasted")
            val = T.validate_yaml(yaml_content=embedded)
            tool_calls += 1
            trace.append({
                "tool": "validate_yaml",
                "args": {"source": "user_message"},
                "result": _compact_tool_result(val),
            })

    updates.update(tool_trace=trace, tool_calls=tool_calls)
    return updates


# ─────────────────────────────────────────────
#  Node: DRAFT (one generation / repair pass)
# ─────────────────────────────────────────────

def draft_node(state: AgentState, config: RunnableConfig) -> dict:
    _ensure_not_cancelled(state)
    iteration = int(state.get("iteration") or 0) + 1
    trace = list(state.get("tool_trace") or [])
    summary = state.get("search_summary") or {}

    max_iter = state.get("max_iterations") or 1
    if iteration == 1:
        label = "Drafting Ansible playbook"
    else:
        label = f"Redrafting with fixes (attempt {iteration}/{max_iter})"
    _progress(config, "generating", label, summary.get("primary_module"))

    try:
        result = T.draft_playbook(
            user_request=state["merged_request"],
            retrieval_meta=state.get("retrieval_meta") or {},
            conversation_facts=state.get("conversation_facts") or {},
            feedback=state.get("repair_feedback") or "none",
            fix_plan=state.get("repair_plan") or "none",
            existing_path=state.get("playbook_path"),
        )
    except Exception:
        log.exception("agent.draft.failed", iteration=iteration)
        raise

    log.info(
        "agent.draft.done",
        iteration=iteration,
        filename=result.get("filename"),
        yaml_chars=len(result.get("yaml") or ""),
        issues=result.get("issues") or [],
    )

    trace.append({
        "tool": "draft_playbook",
        "args": {"iteration": iteration},
        "result": {
            "filename": result.get("filename"),
            "issues": result.get("issues") or [],
            "yaml_chars": len(result.get("yaml") or ""),
        },
    })

    return {
        "iteration": iteration,
        "draft_yaml": result.get("yaml"),
        "playbook_path": result.get("path"),
        "filename": result.get("filename"),
        "draft_issues": list(result.get("issues") or []),
        "tool_trace": trace,
    }


# ─────────────────────────────────────────────
#  Node: GATE (full validation + production-ready check)
# ─────────────────────────────────────────────

def gate_node(state: AgentState, config: RunnableConfig) -> dict:
    _ensure_not_cancelled(state)
    iteration = state.get("iteration") or 1
    max_iter = state.get("max_iterations") or 1
    _progress(
        config, "validating",
        f"Running production gate (attempt {iteration}/{max_iter})",
        "validator + ansible-lint",
    )
    trace = list(state.get("tool_trace") or [])

    from observability.tracing import observe

    with observe(
        "run-production-gate",
        as_type="evaluator",
        input={"iteration": iteration},
        metadata={"node": "gate"},
    ) as gate_obs:
        validation = T.validate_playbook_file(state["playbook_path"])

        lint = validation.get("ansible_lint") or {}
        lint_status = (lint.get("status") or "not_run").lower()
        lint_backend = lint.get("backend") or "none"
        lint_violations = lint.get("violations") or []

        if lint_status == "passed":
            _progress(config, "validating", "ansible-lint passed", f"via {lint_backend}")
        elif lint_status == "violations":
            _progress(
                config, "validating",
                f"ansible-lint found {len(lint_violations)} violation(s)",
                f"via {lint_backend}",
            )
        elif lint_status in ("skipped", "not_run", "not_installed",
                             "wsl_not_configured", "unsupported_platform",
                             "docker_not_available"):
            _progress(config, "validating", f"ansible-lint skipped ({lint_status})", lint_backend)
        else:
            _progress(config, "validating", f"ansible-lint: {lint_status}", lint_backend)

        ready, repairable, environmental = evaluate_gate(
            validation, state.get("draft_issues")
        )

        if gate_obs is not None:
            try:
                gate_obs.update(
                    output={
                        "ready": ready,
                        "repairable_count": len(repairable or []),
                        "environmental": bool(environmental),
                        "ansible_lint": lint_status,
                    },
                    metadata={
                        "ready": str(bool(ready)),
                        "lint_status": lint_status,
                    },
                )
            except Exception:  # noqa: BLE001
                pass

    try:
        from observability.metrics import record_gate_result

        record_gate_result(
            passed=bool(ready),
            environmental=bool(environmental) and not ready,
            iteration=int(iteration),
        )
    except Exception:  # noqa: BLE001
        pass

    n_passed = validation.get("passed") or len(validation.get("passed_msgs") or [])
    n_warnings = len(validation.get("warnings") or [])
    n_errors = len(validation.get("errors") or [])

    trace.append({
        "tool": "gate",
        "args": {"iteration": iteration},
        "result": {
            "ready": ready,
            "repairable_failures": repairable[:10],
            "environmental_failures": environmental,
            "is_valid": validation.get("is_valid"),
            "passed": n_passed,
            "warnings": n_warnings,
            "errors": n_errors,
            "ansible_lint": lint_status,
            "ansible_lint_backend": lint_backend,
            "ansible_lint_violations": len(lint_violations),
        },
    })

    if ready:
        log.info("agent.gate.passed", iteration=iteration, checks_passed=n_passed)
        _progress(
            config, "validating",
            f"Production gate passed (attempt {iteration}/{max_iter})",
            f"{n_passed} checks passed · 0 errors · ansible-lint clean",
        )
    else:
        log.info(
            "agent.gate.failed",
            iteration=iteration,
            repairable=len(repairable),
            environmental=len(environmental),
        )
        _progress(
            config, "validating",
            f"Gate failed — {len(repairable)} issue(s) to fix (attempt {iteration}/{max_iter})",
            f"{n_errors} errors · {n_warnings} warnings · lint: {lint_status}",
        )

    return {
        "validation": validation,
        "gate_ready": ready,
        "gate_failures": repairable,
        "gate_environment": environmental,
        "repair_feedback": format_repair_feedback(repairable),
        "module": validation.get("module")
                  or (state.get("search_summary") or {}).get("primary_module"),
        "tool_trace": trace,
    }


# ─────────────────────────────────────────────
#  Node: ASK_USER
# ─────────────────────────────────────────────

def ask_user_node(state: AgentState, config: RunnableConfig) -> dict:
    _ensure_not_cancelled(state)
    questions = (state.get("decision") or {}).get("questions") or []
    bullets = "\n".join(f"- {q}" for q in questions)
    text = (
        "Before I can generate a correct playbook I need you to confirm a few "
        f"things:\n\n{bullets}\n\nReply with these details and I'll produce the YAML."
    )
    _progress(config, "synthesizing", "Need more details before generating YAML")
    return {"awaiting_user": True, "questions": questions, "final_text": text}


# ─────────────────────────────────────────────
#  Node: RESPOND (final synthesis)
# ─────────────────────────────────────────────

def respond_node(state: AgentState, config: RunnableConfig) -> dict:
    _ensure_not_cancelled(state)
    _progress(config, "synthesizing", "Composing response summary")
    updates: dict = {}

    # Attach the module reference chip for drafted playbooks.
    module = state.get("module") or (state.get("search_summary") or {}).get("primary_module")
    if state.get("draft_yaml") and module:
        try:
            updates["module_ref"] = T.get_module_info(module)
        except Exception:
            updates["module_ref"] = None
    updates["module"] = module

    if state.get("low_confidence"):
        updates["final_text"] = state.get("low_confidence_reason") or (
            "I could not find a confident module match in the documentation index. "
            "Try indexing the collection (scraper), then ask again."
        )
        return updates

    if state.get("draft_yaml"):
        updates["final_text"] = _playbook_summary_text(state)
        return updates

    # Non-generation intents: LLM synthesis from the tool evidence.
    prompt = RESPOND_PROMPT.format(
        history=state["history_text"],
        message=state["user_message"],
        intent=state.get("intent") or "chat",
        generated_flag="no",
        gate_summary="n/a",
        primary_module=module or "n/a",
        tool_results=_summarize_tool_trace(state.get("tool_trace") or []),
    )
    try:
        text = llm_chat(
            prompt, system=agent_system_prompt(), temperature=0.25, max_tokens=900,
        ).strip()
    except LLMError as e:
        log.warning("agent.respond.llm_error", error=str(e))
        text = ""

    updates["final_text"] = text or (
        "I'm not sure how to help with that yet. "
        "Could you rephrase or give me a bit more detail?"
    )
    return updates


def _playbook_summary_text(state: AgentState) -> str:
    module = state.get("module") or "unknown"
    iteration = int(state.get("iteration") or 0)
    max_iter = int(state.get("max_iterations") or 1)
    attempts = f"{iteration}/{max_iter} attempt{'s' if iteration != 1 else ''}"

    validation = state.get("validation") or {}
    lint = validation.get("ansible_lint") or {}
    lint_status = (lint.get("status") or "not_run").lower()
    lint_backend = lint.get("backend") or "none"
    n_passed = validation.get("passed") or len(validation.get("passed_msgs") or [])

    if lint_status == "passed":
        lint_text = f"ansible-lint **passed** (via {lint_backend})"
    elif lint_status == "violations":
        n_v = len(lint.get("violations") or [])
        lint_text = f"ansible-lint found **{n_v} violation(s)** (via {lint_backend})"
    elif lint_status in ("skipped", "not_run", "not_installed",
                         "wsl_not_configured", "unsupported_platform"):
        lint_text = f"ansible-lint was skipped ({lint_status})"
    else:
        lint_text = f"ansible-lint: {lint_status}"

    if state.get("gate_ready"):
        return (
            f"I generated a **production-ready** playbook using `{module}` "
            f"({attempts}). It passed the full production gate: "
            f"{n_passed} checks passed, 0 errors, {lint_text}, "
            f"and no leftover placeholders. "
            f"Review the YAML and let me know if you want adjustments."
        )

    lines = [
        f"I generated a playbook using `{module}` ({attempts}), but it did "
        f"**not** fully pass the production gate. {lint_text}."
    ]
    failures = list(state.get("gate_failures") or [])
    env = list(state.get("gate_environment") or [])
    if failures:
        lines.append("\nRemaining issues:")
        lines.extend(f"- {f}" for f in failures[:8])
    if env:
        lines.append("\nEnvironment limitations (not fixable by regenerating):")
        lines.extend(f"- {e}" for e in env)
    lines.append(
        "\nReview the YAML carefully before using it — or fix the environment "
        "and ask me to regenerate."
    )
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  Routing
# ─────────────────────────────────────────────

def _route_from_reason(state: AgentState) -> str:
    nxt = (state.get("decision") or {}).get("next") or "respond"
    if nxt in ("tools", "draft", "ask_user", "respond"):
        return nxt
    return "respond"


def _route_from_gate(state: AgentState) -> str:
    if state.get("gate_ready"):
        return "respond"
    if int(state.get("iteration") or 0) >= int(state.get("max_iterations") or 1):
        log.warning(
            "agent.gate.budget_exhausted",
            iteration=state.get("iteration"),
            max_iterations=state.get("max_iterations"),
        )
        return "respond"
    if not state.get("gate_failures"):
        # Only environmental failures remain: redrafting cannot fix them.
        return "respond"
    return "reason"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("reason", reason_node)
    g.add_node("tools", tools_node)
    g.add_node("draft", draft_node)
    g.add_node("gate", gate_node)
    g.add_node("ask_user", ask_user_node)
    g.add_node("respond", respond_node)

    g.add_edge(START, "reason")
    g.add_conditional_edges(
        "reason", _route_from_reason,
        {"tools": "tools", "draft": "draft", "ask_user": "ask_user", "respond": "respond"},
    )
    g.add_edge("tools", "reason")
    g.add_edge("draft", "gate")
    g.add_conditional_edges(
        "gate", _route_from_gate,
        {"reason": "reason", "respond": "respond"},
    )
    g.add_edge("ask_user", END)
    g.add_edge("respond", END)
    return g.compile()


_COMPILED_GRAPH = None


def get_graph():
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_graph()
    return _COMPILED_GRAPH
