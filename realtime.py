"""
=============================================================
  AnsibleAI — Socket.IO emission

  Generation progress is produced in two different processes: the API
  serves the socket, but the Celery worker does the work. Both need to
  emit the same events to the same room, so the helpers live here rather
  than in app.py.

  How a worker emit reaches a browser:

      worker  --.emit()-->  Redis pub/sub  -->  API process
                                                 |
                                          connected client

  That hop only exists when SOCKETIO_MESSAGE_QUEUE is set. Without it the
  emitter writes to local connections only, which is correct for
  single-process development and wrong the moment a worker is involved —
  hence the config guard that requires a message queue outside
  development.

  Events are always addressed to a user room, never broadcast. A
  broadcast would hand every connected browser every other user's
  generation progress, playbook filenames included.
=============================================================
"""

from __future__ import annotations

import threading
from typing import Any

import structlog
from flask_socketio import SocketIO

from config import settings

log = structlog.get_logger(__name__)

# The server instance when running inside the API, None in the worker.
_server: SocketIO | None = None
# Write-only client used by processes that do not serve sockets.
_emitter: SocketIO | None = None
_emitter_lock = threading.Lock()


def bind_server(socketio: SocketIO) -> None:
    """
    Register the API's live SocketIO server as the emit target.

    Called once from app.py. Emitting through the server instance keeps
    same-process delivery direct instead of taking a needless round trip
    through Redis.
    """
    global _server
    _server = socketio


def _handle() -> SocketIO | None:
    """The SocketIO object to emit through, or None if emission is impossible."""
    if _server is not None:
        return _server

    queue_url = settings.socketio_message_queue.strip()
    if not queue_url:
        # A worker with no message queue has no route to any client. Say so
        # once per emit rather than failing the generation, which would be
        # a worse outcome than a UI that misses progress updates.
        return None

    global _emitter
    if _emitter is None:
        with _emitter_lock:
            if _emitter is None:
                # No app argument: this instance only writes to the queue.
                # Created lazily so a prefork Celery worker builds its own
                # Redis connection after forking rather than sharing one.
                _emitter = SocketIO(message_queue=queue_url, async_mode="threading")
    return _emitter


def user_room(user_id: int) -> str:
    return f"user:{user_id}"


def emit_to_user(event: str, payload: dict | None, user_id: int | None) -> None:
    """
    Emit to one user's room.

    A missing user_id means we could not attribute the event, in which
    case dropping it is correct: broadcasting would leak another user's
    thread activity.
    """
    if user_id is None:
        # `event` is structlog's positional argument, hence the rename.
        log.warning("socket.emit.dropped", socket_event=event, reason="no_user_id")
        return

    handle = _handle()
    if handle is None:
        log.warning("socket.emit.dropped", socket_event=event, reason="no_message_queue")
        return

    try:
        handle.emit(event, payload, to=user_room(user_id))
    except Exception:
        # A dead Redis connection must not abort a generation that has
        # already cost minutes of GPU time; the client recovers by
        # refetching the thread.
        log.warning("socket.emit.failed", socket_event=event, exc_info=True)


def emit_generation_failed(thread_id: int, error: str, user_id: int | None) -> None:
    """Clear in-flight progress in connected clients when generation aborts."""
    emit_to_user("generation_failed", {"thread_id": thread_id, "error": error}, user_id)


def emit_generation_cancelled(thread_id: int, user_id: int | None) -> None:
    """Notify clients that the user stopped generation for this thread."""
    emit_to_user(
        "generation_cancelled",
        {"thread_id": thread_id, "error": "Generation stopped by user."},
        user_id,
    )
    # Also clear progress maps that only listen for generation_failed.
    emit_generation_failed(thread_id, "Generation stopped by user.", user_id)


def emit_generation_progress(
    thread_id: int,
    step: str,
    message: str,
    detail: str | None = None,
    user_id: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "thread_id": thread_id,
        "step": step,
        "message": message,
    }
    if detail:
        payload["detail"] = detail
    # Mirror to the process console. In the worker this is the only place a
    # long-running generation shows up in the logs at all, since there is no
    # HTTP response to log when it finishes.
    log.info(
        "chat.progress",
        thread_id=thread_id,
        step=step,
        message=message,
        detail=detail,
    )
    emit_to_user("generation_progress", payload, user_id)


def emit_generation_complete(thread_id: int, thread_dict: dict, user_id: int | None) -> None:
    """
    Signal that the thread is settled and worth refetching.

    This is now the only completion signal the client gets: POST /api/chat
    returns 202 before the answer exists, so the response body cannot carry
    the assistant message.
    """
    emit_to_user(
        "generation_complete",
        {"thread_id": thread_id, "thread": thread_dict},
        user_id,
    )


__all__ = [
    "bind_server",
    "emit_generation_cancelled",
    "emit_generation_complete",
    "emit_generation_failed",
    "emit_generation_progress",
    "emit_to_user",
    "user_room",
]
