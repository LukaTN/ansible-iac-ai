"""
=============================================================
  AnsibleAI Agent — Entry point (thin wrapper over the graph)

  `handle_message` builds the initial `AgentState`, invokes the
  LangGraph state machine (agent/graph.py), maps the final state
  into an `AgentResponse`, and persists a `Generation` row when a
  playbook was produced.

  The graph owns the whole reasoning loop:
    reason (CoT) → tools → draft → gate → repair … → respond
=============================================================
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from logging_setup import get_logger

from .cancel import (
    GenerationCancelled,
    reset_active_thread,
    set_active_thread,
)
from .cancel import (
    check as check_cancelled,
)
from .graph import get_graph
from .llm import current_config
from .state import build_initial_state

ProgressCallback = Callable[[str, str, str | None], None] | None

log = get_logger(__name__)


# ─────────────────────────────────────────────
#  Response dataclass (public API — unchanged shape)
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
#  Final-state → AgentResponse mapping
# ─────────────────────────────────────────────

def _rag_meta_from_final(state: dict) -> dict | None:
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


def _response_from_state(state: dict) -> AgentResponse:
    response = AgentResponse(
        text=state.get("final_text") or "",
        intent=state.get("intent") or "chat",
        tool_trace=list(state.get("tool_trace") or []),
        awaiting_user=bool(state.get("awaiting_user")),
    )

    has_playbook = bool(state.get("draft_yaml")) and not state.get("low_confidence")
    if has_playbook:
        response.playbook   = state.get("draft_yaml")
        response.filename   = state.get("filename")
        response.validation = state.get("validation")
        response.module_ref = state.get("module_ref")

    response.module = state.get("module") or (state.get("search_summary") or {}).get("primary_module")
    if state.get("low_confidence"):
        response.validation = state.get("validation")

    response.rag_meta = _rag_meta_from_final(state)
    return response


def _persist_generation(state: dict, db_session) -> None:
    """Record the produced playbook in the Generation stats table."""
    if db_session is None or not state.get("draft_yaml") or state.get("low_confidence"):
        return
    try:
        from models import Generation
        validation = state.get("validation") or {}
        entry = Generation(
            request    = state.get("user_message") or "",
            module     = state.get("module") or "unknown",
            filename   = state.get("filename"),
            playbook   = state.get("draft_yaml"),
            is_valid   = bool(state.get("gate_ready")),
            warnings   = len(validation.get("warnings") or []),
            errors     = len(validation.get("errors") or []),
            module_ref = state.get("module_ref"),
        )
        db_session.add(entry)
        db_session.commit()
    except Exception:
        db_session.rollback()


# ─────────────────────────────────────────────
#  Main entry point
# ─────────────────────────────────────────────

def handle_message(
    thread_id: int,
    user_message: str,
    history: list[dict],
    db_session=None,
    on_progress: ProgressCallback = None,
    user_id: int | None = None,
) -> AgentResponse:
    """
    End-to-end handling of one user message via the LangGraph agent.

    `history` is a list of dicts shaped like ChatMessage.to_dict(), ending
    with the current user turn (so we can scan prior user turns too).

    Raises `GenerationCancelled` if the user stops the request mid-flight.
    """
    user_message = (user_message or "").strip()
    if not user_message:
        return AgentResponse(text="I didn't receive any message. What would you like help with?")

    cfg = current_config()
    log.info(
        "agent.llm.config",
        provider=cfg["provider"],
        model=cfg["model"],
    )

    initial_state = build_initial_state(thread_id, user_message, history)
    token = set_active_thread(thread_id)
    try:
        check_cancelled(thread_id)
        graph = get_graph()
        log.info("agent.graph.invoke.start", thread_id=thread_id)

        invoke_config: dict[str, Any] = {
            "configurable": {"on_progress": on_progress},
            "recursion_limit": 60,
        }
        # Nest LangGraph node spans under the current Langfuse agent trace.
        try:
            from observability.tracing import langchain_callback

            lf_handler = langchain_callback()
            if lf_handler is not None:
                invoke_config["callbacks"] = [lf_handler]
                meta: dict[str, Any] = {
                    "langfuse_session_id": str(thread_id),
                    "langfuse_tags": ["ansibleai", "langgraph"],
                }
                if user_id is not None:
                    meta["langfuse_user_id"] = str(user_id)
                invoke_config["metadata"] = meta
        except Exception:  # noqa: BLE001
            pass

        final_state = graph.invoke(initial_state, config=invoke_config)
        log.info(
            "agent.graph.invoke.done",
            thread_id=thread_id,
            gate_ready=final_state.get("gate_ready"),
            iteration=final_state.get("iteration"),
            intent=final_state.get("intent"),
            has_draft=bool(final_state.get("draft_yaml")),
        )
        check_cancelled(thread_id)
    except GenerationCancelled:
        log.info("agent.graph.cancelled", thread_id=thread_id)
        raise
    except Exception:
        log.exception("agent.graph.invoke.failed", thread_id=thread_id)
        raise
    finally:
        reset_active_thread(token)

    _persist_generation(final_state, db_session)
    return _response_from_state(final_state)
