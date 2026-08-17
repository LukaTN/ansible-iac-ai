"""
=============================================================
  AnsibleAI — background generation task

  `POST /api/chat` persists the user's message and returns 202; this is
  what actually runs the agent. Progress and the final result reach the
  browser over Socket.IO (via the Redis message queue), never through
  the HTTP response that started it.

  Every exit path must do three things, or the UI hangs on "thinking"
  forever: clear the run marker, write something to the thread, and emit
  a terminal event. That is why the body is one try/except/finally
  rather than a set of early returns.

  Worker entry point:  celery -A tasks worker
=============================================================
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

# Same bootstrap as celery_app.py: the task body does lazy imports of
# `agent`, `app`, `models`, and those must resolve after fork.
_BACKEND_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_ROOT.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
os.chdir(_REPO_ROOT)

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_ready

from celery_app import celery

log = structlog.get_logger(__name__)


@worker_ready.connect
def _warm_ollama_on_ready(**_kwargs) -> None:
    """
    Prefetch agent weights into Ollama when the worker comes online.

    Without this, the first chat of the day pays a full cold load inside
    the "Understand" step and looks stuck — especially when AGENT_MODEL
    and PLAYBOOK_MODEL are different and must be swapped in VRAM.
    """
    try:
        from agent.llm import warm_up

        result = warm_up()
        log.info("worker.warm_up.done", **{k: result.get(k) for k in ("warmed", "skipped")})
        warmed = result.get("warmed") or []
        if warmed:
            print(f"[worker] warmed Ollama models: {', '.join(warmed)}", flush=True)
    except Exception as exc:  # noqa: BLE001 — never block worker boot
        log.warning("worker.warm_up.failed", error=str(exc))
        print(f"[worker] Ollama warm-up skipped: {exc}", flush=True)


# Assistant text used when a turn ends without a real answer. Persisted so
# the thread never shows a user message with nothing after it.
CANCELLED_TEXT = "Generation stopped. Send a new message whenever you want to try again."
TIMEOUT_TEXT = (
    "This request took too long and was stopped. Try narrowing it down — "
    "a single playbook for one task usually completes well within the limit."
)
FAILED_TEXT = "Generation failed before an answer could be produced. Please try again."
BUDGET_TEXT = (
    "Daily generation budget reached. Try again tomorrow, or ask an administrator "
    "to raise the limit."
)


def _flask_app():
    """
    Import the Flask application on first use.

    Deferred rather than imported at module scope so that `celery -A tasks`
    can load this module — and validate the broker — without constructing
    the web app, and so the API can import the task to enqueue it without
    importing itself.
    """
    from app import app

    return app


def _persist_note(thread, role_text: str, rag_meta: dict, tool_trace: list[dict]):
    """Write a short assistant message for a turn that produced no answer."""
    from models import ChatMessage, db, utcnow

    msg = ChatMessage(
        thread_id=thread.id,
        role="assistant",
        content=role_text,
        rag_meta=rag_meta,
        tool_trace=tool_trace,
    )
    db.session.add(msg)
    thread.updated_at = utcnow()
    db.session.commit()
    return msg


def _archive_playbook(thread_id: int, agent_resp) -> dict[str, str] | None:
    """
    Copy a finished playbook to durable storage and drop the scratch file.

    Best effort by design: the YAML is already on the chat message, which
    is what the UI renders, so a storage outage must not turn a successful
    generation into a visible failure.
    """
    import storage

    if not agent_resp.playbook or not agent_resp.filename:
        return None

    ref = storage.store_playbook(thread_id, agent_resp.filename, agent_resp.playbook)
    storage.discard_working_file(
        os.path.join(storage.working_dir(), os.path.basename(agent_resp.filename))
    )
    return ref.as_dict() if ref else None


def _persist_answer(thread, agent_resp):
    """Turn an AgentResponse into the persisted assistant message."""
    from app import _enrich_agent_rag_meta, _resolve_agent_module_ref
    from models import ChatMessage, db, utcnow

    rag_meta = _enrich_agent_rag_meta(agent_resp)
    module_ref = _resolve_agent_module_ref(agent_resp, rag_meta)

    artifact = _archive_playbook(thread.id, agent_resp)
    if artifact:
        # Kept in rag_meta rather than a new column: it is operational
        # metadata about where the archive landed, not something the
        # conversation model needs to know about.
        rag_meta["artifact"] = artifact

    # Generation stats are persisted inside handle_message when db_session is set.
    msg = ChatMessage(
        thread_id=thread.id,
        role="assistant",
        content=agent_resp.text,
        playbook=agent_resp.playbook,
        filename=agent_resp.filename,
        module=agent_resp.module,
        validation=agent_resp.validation,
        module_ref=module_ref,
        rag_meta=rag_meta,
        tool_trace=agent_resp.tool_trace or [],
    )
    db.session.add(msg)
    thread.updated_at = utcnow()
    db.session.commit()
    return msg


@celery.task(
    bind=True,
    name="ansibleai.generation.run",
    # No automatic retry. A failed generation has already spent its tokens,
    # and replaying it would spend them again for the same likely failure.
    # The user retries by sending the message again, which is an explicit,
    # visible decision.
    max_retries=0,
    ignore_result=False,
)
def run_generation(self, thread_id: int, user_id: int, message: str) -> dict[str, Any]:
    """
    Run one agent turn for an already-persisted user message.

    Returns a small status dict for observability. The client learns the
    outcome from Socket.IO, not from this value.
    """
    from agent import GenerationCancelled, handle_message
    from agent import cancel as cancel_mod
    from app import _thread_history
    from models import ChatThread, db
    from realtime import (
        emit_generation_cancelled,
        emit_generation_complete,
        emit_generation_failed,
        emit_generation_progress,
    )

    app = _flask_app()

    with app.app_context():
        thread = db.session.get(ChatThread, thread_id)
        if thread is None:
            # The thread was deleted between enqueue and pickup. Nothing to
            # answer into, and no client that still cares.
            log.warning("generation.thread_missing", thread_id=thread_id)
            cancel_mod.end(thread_id)
            return {"status": "thread_missing", "thread_id": thread_id}

        log.info(
            "generation.start",
            thread_id=thread_id,
            user_id=user_id,
            task_id=self.request.id,
            message_chars=len(message),
        )

        def _on_progress(step: str, text: str, detail: str | None = None) -> None:
            emit_generation_progress(thread_id, step, text, detail, user_id)

        from auth.budgets import BudgetExceeded, bind_user, check_budget, reset_user, snapshot
        from observability.metrics import record_generation
        from observability.tracing import finish_generation_trace, generation_trace

        record_generation(status="started", started=True)
        gen_t0 = time.perf_counter()
        status = "ok"
        output_preview: str | None = None
        budget_token = bind_user(user_id)

        with generation_trace(
            thread_id=thread_id,
            user_id=user_id,
            message=message,
            task_id=self.request.id,
            extra_metadata=snapshot(user_id),
        ) as lf_root:
            try:
                # A Stop pressed while this job sat in the queue is already
                # recorded; surface it before spending anything on the LLM.
                cancel_mod.check(thread_id)
                check_budget(user_id)

                emit_generation_progress(
                    thread_id,
                    "planning",
                    "Reading your message and conversation history",
                    user_id=user_id,
                )

                agent_resp = handle_message(
                    thread_id=thread_id,
                    user_message=message,
                    history=_thread_history(thread_id),
                    db_session=db.session,
                    on_progress=_on_progress,
                    user_id=user_id,
                )

                if agent_resp.awaiting_user:
                    emit_generation_progress(
                        thread_id,
                        "synthesizing",
                        "Preparing clarifying questions",
                        user_id=user_id,
                    )

                _persist_answer(thread, agent_resp)
                _retitle_if_untouched(thread, message)
                output_preview = (agent_resp.text or "")[:500]

            except GenerationCancelled:
                status = "cancelled"
                db.session.rollback()
                _persist_note(
                    thread,
                    CANCELLED_TEXT,
                    {"cancelled": True, "intent": "chat"},
                    [{"tool": "cancel", "args": {}, "result": {"stopped": True}}],
                )
                emit_generation_cancelled(thread_id, user_id)
                log.info("generation.cancelled", thread_id=thread_id)

            except SoftTimeLimitExceeded:
                status = "timeout"
                db.session.rollback()
                _persist_note(
                    thread,
                    TIMEOUT_TEXT,
                    {"timed_out": True, "intent": "chat"},
                    [{"tool": "timeout", "args": {}, "result": {"soft_limit": True}}],
                )
                emit_generation_failed(thread_id, "Generation timed out.", user_id)
                log.warning("generation.timeout", thread_id=thread_id)

            except BudgetExceeded as exc:
                status = "budget"
                db.session.rollback()
                _persist_note(
                    thread,
                    BUDGET_TEXT,
                    {
                        "budget_exceeded": True,
                        "intent": "chat",
                        "used": exc.used,
                        "limit": exc.limit,
                    },
                    [{"tool": "budget", "args": {}, "result": {"exceeded": True}}],
                )
                emit_generation_failed(thread_id, BUDGET_TEXT, user_id)
                log.info(
                    "generation.budget_exceeded",
                    thread_id=thread_id,
                    user_id=user_id,
                    used=exc.used,
                    limit=exc.limit,
                )

            except Exception as exc:
                status = "failed"
                db.session.rollback()
                log.exception("generation.failed", thread_id=thread_id)
                try:
                    _persist_note(
                        thread,
                        FAILED_TEXT,
                        {"failed": True, "intent": "chat"},
                        [{"tool": "error", "args": {}, "result": {"failed": True}}],
                    )
                except Exception:
                    # The database is likely the thing that broke. Still emit,
                    # so the client stops waiting.
                    db.session.rollback()
                    log.exception("generation.failed.note_unsaved", thread_id=thread_id)
                # Exception text can carry connection strings and file paths, so
                # the detail stays in the log and the client gets a generic line.
                emit_generation_failed(thread_id, FAILED_TEXT, user_id)
                log.info(
                    "generation.failed.reported",
                    thread_id=thread_id,
                    error=type(exc).__name__,
                )

            finally:
                try:
                    finish_generation_trace(
                        lf_root,
                        status=status,
                        output_preview=output_preview,
                        extra_metadata=snapshot(user_id),
                    )
                    record_generation(
                        status=status,
                        duration_s=time.perf_counter() - gen_t0,
                    )
                except Exception:  # noqa: BLE001
                    pass
                try:
                    reset_user(budget_token)
                except Exception:
                    log.debug("generation.budget_unbind_failed", exc_info=True)
                cancel_mod.end(thread_id)
                # Always the last word: the client clears its pending state on
                # this event and refetches the thread, whatever the outcome.
                try:
                    fresh = db.session.get(ChatThread, thread_id)
                    if fresh is not None:
                        emit_generation_complete(thread_id, fresh.to_dict(), user_id)
                except Exception:
                    log.warning(
                        "generation.complete_emit_failed",
                        thread_id=thread_id,
                        exc_info=True,
                    )

        log.info("generation.done", thread_id=thread_id, status=status)
        return {"status": status, "thread_id": thread_id}


def _retitle_if_untouched(thread, message: str) -> None:
    """Name a thread after its first message, leaving user edits alone."""
    from app import _make_thread_title
    from models import db

    if (thread.title or "").strip().lower() in ("", "new chat"):
        thread.title = _make_thread_title(message)
        db.session.commit()


__all__ = ["celery", "run_generation"]
