"""
=============================================================
  AnsibleAI Agent — Cooperative cancellation

  A generation runs in a Celery worker; the Stop button hits an API
  process. Those are different processes, and in Kubernetes different
  pods, so the cancel flag has to live somewhere both can see.

  Two markers per thread:

      run:{thread_id}      set when a turn is accepted, cleared when it
                           settles. Tells the API whether Stop has
                           anything to stop.
      cancel:{thread_id}   set by Stop. Graph nodes call `check()`
                           between steps and raise GenerationCancelled.

  Both carry a TTL so a worker that is killed mid-generation cannot
  leave a thread marked "running" forever.

  Ordering matters. `begin()` clears any stale cancel flag and is called
  by the API *before* the task is enqueued, so a Stop pressed while the
  job is still queued is not lost: the flag survives until the worker
  starts and its first `check()` raises immediately.

  An in-flight HTTP call to the LLM cannot be torn down mid-body, so
  cancellation always takes effect at the next check point.
=============================================================
"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from typing import Protocol

import structlog

log = structlog.get_logger(__name__)


class GenerationCancelled(Exception):
    """Raised when the user (or system) stops an in-flight generation."""

    def __init__(self, thread_id: int | None = None, message: str | None = None):
        self.thread_id = thread_id
        super().__init__(message or "Generation stopped by user.")


# Optional ambient thread id for helpers that don't receive AgentState
# (e.g. llm.chat). Set by handle_message for the duration of a turn.
_active_thread_id: ContextVar[int | None] = ContextVar("agent_active_thread_id", default=None)


def set_active_thread(thread_id: int | None):
    """Return a token that must be reset with `reset_active_thread`."""
    return _active_thread_id.set(thread_id)


def reset_active_thread(token) -> None:
    _active_thread_id.reset(token)


# ─────────────────────────────────────────────
#  Backends
# ─────────────────────────────────────────────

class CancelBackend(Protocol):
    def begin(self, thread_id: int) -> None: ...
    def end(self, thread_id: int) -> None: ...
    def request_cancel(self, thread_id: int) -> bool: ...
    def is_cancelled(self, thread_id: int) -> bool: ...
    def is_running(self, thread_id: int) -> bool: ...


class MemoryCancelBackend:
    """
    Single-process backend for development and tests.

    Correct only while the generation runs in the same process that
    serves the Stop request, which is true under CELERY_TASK_ALWAYS_EAGER
    and false everywhere else. config.py refuses this backend outside
    development for that reason.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[int] = set()
        self._cancelled: set[int] = set()

    def begin(self, thread_id: int) -> None:
        with self._lock:
            self._cancelled.discard(thread_id)
            self._running.add(thread_id)

    def end(self, thread_id: int) -> None:
        with self._lock:
            self._running.discard(thread_id)
            self._cancelled.discard(thread_id)

    def request_cancel(self, thread_id: int) -> bool:
        with self._lock:
            if thread_id not in self._running:
                return False
            self._cancelled.add(thread_id)
            return True

    def is_cancelled(self, thread_id: int) -> bool:
        with self._lock:
            return thread_id in self._cancelled

    def is_running(self, thread_id: int) -> bool:
        with self._lock:
            return thread_id in self._running


class RedisCancelBackend:
    """Cross-process backend. Every operation is a single round trip."""

    def __init__(self, url: str, ttl_seconds: int) -> None:
        import redis

        self._redis = redis.Redis.from_url(url, decode_responses=True)
        self._ttl = ttl_seconds

    @staticmethod
    def _run_key(thread_id: int) -> str:
        return f"ansibleai:gen:run:{thread_id}"

    @staticmethod
    def _cancel_key(thread_id: int) -> str:
        return f"ansibleai:gen:cancel:{thread_id}"

    def begin(self, thread_id: int) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(self._cancel_key(thread_id))
        pipe.set(self._run_key(thread_id), "1", ex=self._ttl)
        pipe.execute()

    def end(self, thread_id: int) -> None:
        self._redis.delete(self._run_key(thread_id), self._cancel_key(thread_id))

    def request_cancel(self, thread_id: int) -> bool:
        # Set the flag whether or not a worker has picked the job up yet:
        # a job cancelled while queued must still abort on its first check.
        # The return value reports only whether the turn is live, which is
        # what the UI uses to decide if Stop did anything.
        pipe = self._redis.pipeline()
        pipe.exists(self._run_key(thread_id))
        pipe.set(self._cancel_key(thread_id), "1", ex=self._ttl)
        running, _ = pipe.execute()
        return bool(running)

    def is_cancelled(self, thread_id: int) -> bool:
        return bool(self._redis.exists(self._cancel_key(thread_id)))

    def is_running(self, thread_id: int) -> bool:
        return bool(self._redis.exists(self._run_key(thread_id)))


_backend: CancelBackend | None = None
_backend_lock = threading.Lock()


def get_backend() -> CancelBackend:
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                from config import settings

                if settings.cancel_backend == "redis":
                    _backend = RedisCancelBackend(
                        settings.redis_url, settings.cancel_ttl_seconds
                    )
                    log.info("cancel.backend", backend="redis")
                else:
                    _backend = MemoryCancelBackend()
                    log.info("cancel.backend", backend="memory")
    return _backend


def reset_backend(backend: CancelBackend | None = None) -> None:
    """Swap the backend. Tests use this; nothing else should."""
    global _backend
    with _backend_lock:
        _backend = backend


# ─────────────────────────────────────────────
#  Public API (unchanged signatures)
# ─────────────────────────────────────────────

def begin(thread_id: int) -> None:
    """
    Mark a turn as live and clear any stale cancel flag.

    Called by the API before enqueueing, not by the worker: the window
    between accepting the message and a worker picking it up is exactly
    when an impatient user presses Stop.
    """
    get_backend().begin(int(thread_id))


def end(thread_id: int | None) -> None:
    """Drop both markers when the turn settles, however it settled."""
    if thread_id is None:
        return
    try:
        get_backend().end(int(thread_id))
    except Exception:
        # Losing the cleanup is survivable: both keys carry a TTL.
        log.warning("cancel.end.failed", thread_id=thread_id, exc_info=True)


def request_cancel(thread_id: int) -> bool:
    """
    Signal cancellation for `thread_id`.

    Returns True if a turn was live for that thread, which the UI uses to
    distinguish "stopping" from "there was nothing to stop".
    """
    return get_backend().request_cancel(int(thread_id))


def is_running(thread_id: int) -> bool:
    """Whether a turn is queued or executing. Backs the polling fallback."""
    return get_backend().is_running(int(thread_id))


def is_cancelled(thread_id: int | None = None) -> bool:
    tid = int(thread_id) if thread_id is not None else _active_thread_id.get()
    if tid is None:
        return False
    try:
        return get_backend().is_cancelled(tid)
    except Exception:
        # If Redis is unreachable, continuing is the safer failure: the
        # alternative is aborting a generation nobody asked to stop.
        log.warning("cancel.check.failed", thread_id=tid, exc_info=True)
        return False


def check(thread_id: int | None = None) -> None:
    """Raise GenerationCancelled if this thread was asked to stop."""
    tid = int(thread_id) if thread_id is not None else _active_thread_id.get()
    if tid is None:
        return
    if is_cancelled(tid):
        raise GenerationCancelled(tid)


__all__ = [
    "CancelBackend",
    "GenerationCancelled",
    "MemoryCancelBackend",
    "RedisCancelBackend",
    "begin",
    "check",
    "end",
    "get_backend",
    "is_cancelled",
    "is_running",
    "request_cancel",
    "reset_active_thread",
    "reset_backend",
    "set_active_thread",
]
