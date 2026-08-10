"""
=============================================================
  AnsibleAI — scrape log streaming

  Knowledge-base scrapes emit progress lines that the admin UI tails
  over SSE. The producer and the reader are not the same process once
  there is more than one replica: the browser's EventSource can land on
  any pod, while the scrape runs on exactly one.

  A dict of `queue.Queue` cannot cross that boundary, so the Redis
  backend uses a stream per session:

      XADD  ansibleai:doclog:{session_id}  * line "..."
      XREAD BLOCK ... STREAMS ansibleai:doclog:{session_id} <last-id>

  A stream rather than pub/sub on purpose: pub/sub drops anything
  published before the subscriber connected, and the UI routinely
  attaches a second or two after the scrape starts. Reading a stream
  from id 0 replays what was already written, so a late reader still
  sees the whole run.

  Entries are capped and expired so a long scrape cannot grow without
  bound and finished sessions do not linger in memory forever.
=============================================================
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Protocol

import structlog

from config import settings

log = structlog.get_logger(__name__)

# Written by producers when a run finishes; the browser closes the
# EventSource on seeing it.
STREAM_END = "STREAM_END"

# How long `tail` waits before yielding an idle tick, so the SSE layer can
# send a comment frame and keep proxies from closing the connection.
_IDLE_TICK_SECONDS = 20


class LogStreamBackend(Protocol):
    def create(self, session_id: int) -> None: ...
    def publish(self, session_id: int, line: str) -> None: ...
    def tail(self, session_id: int) -> Iterator[str | None]: ...


class MemoryLogStream:
    """
    Single-process backend for development and tests.

    Loses history: a reader that attaches after a line was published never
    sees it, and a reader on another process sees nothing at all. config.py
    refuses this backend outside development.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[int, queue.Queue[str]] = {}

    def _queue_for(self, session_id: int) -> queue.Queue[str]:
        with self._lock:
            q = self._queues.get(session_id)
            if q is None:
                q = queue.Queue()
                self._queues[session_id] = q
            return q

    def create(self, session_id: int) -> None:
        self._queue_for(session_id)

    def publish(self, session_id: int, line: str) -> None:
        self._queue_for(session_id).put(line)

    def tail(self, session_id: int) -> Iterator[str | None]:
        q = self._queue_for(session_id)
        while True:
            try:
                yield q.get(timeout=_IDLE_TICK_SECONDS)
            except queue.Empty:
                yield None


class RedisLogStream:
    """Cross-process backend built on Redis streams."""

    def __init__(self, url: str, max_entries: int, ttl_seconds: int) -> None:
        import redis

        self._redis = redis.Redis.from_url(url, decode_responses=True)
        self._max_entries = max_entries
        self._ttl = ttl_seconds

    @staticmethod
    def _key(session_id: int) -> str:
        return f"ansibleai:doclog:{session_id}"

    def create(self, session_id: int) -> None:
        # Streams are created by the first XADD; nothing to do up front.
        pass

    def publish(self, session_id: int, line: str) -> None:
        key = self._key(session_id)
        pipe = self._redis.pipeline()
        # approximate=True lets Redis trim on node boundaries, which is far
        # cheaper than an exact cap and close enough for a log buffer.
        pipe.xadd(key, {"line": line}, maxlen=self._max_entries, approximate=True)
        pipe.expire(key, self._ttl)
        pipe.execute()

    def tail(self, session_id: int) -> Iterator[str | None]:
        key = self._key(session_id)
        # "0" rather than "$": replay everything already written, so a
        # browser that attaches mid-scrape still gets the earlier lines.
        last_id = "0"
        while True:
            entries = self._redis.xread(
                {key: last_id}, block=_IDLE_TICK_SECONDS * 1000, count=200
            )
            if not entries:
                yield None
                continue
            for _stream, records in entries:
                for record_id, fields in records:
                    last_id = record_id
                    line = fields.get("line")
                    if line is not None:
                        yield line


_backend: LogStreamBackend | None = None
_backend_lock = threading.Lock()


def get_backend() -> LogStreamBackend:
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                if settings.log_stream_backend == "redis":
                    _backend = RedisLogStream(
                        settings.redis_url,
                        settings.log_stream_max_entries,
                        settings.log_stream_ttl_seconds,
                    )
                    log.info("logstream.backend", backend="redis")
                else:
                    _backend = MemoryLogStream()
                    log.info("logstream.backend", backend="memory")
    return _backend


def reset_backend(backend: LogStreamBackend | None = None) -> None:
    """Swap the backend. Tests use this; nothing else should."""
    global _backend
    with _backend_lock:
        _backend = backend


def create(session_id: int) -> None:
    """Prepare a stream so a reader can attach before the producer starts."""
    get_backend().create(int(session_id))


def publish(session_id: int, message: str) -> None:
    """Append one timestamped line. Never raises into the producer."""
    stamp = datetime.now(UTC).strftime("%H:%M:%S")
    try:
        get_backend().publish(int(session_id), f"[{stamp}] {message}")
    except Exception:
        # A broken log stream must not abort the scrape it is describing.
        log.warning("logstream.publish.failed", session_id=session_id, exc_info=True)


def tail(session_id: int) -> Iterator[str | None]:
    """
    Yield log lines, or None as an idle tick.

    The caller turns a None into an SSE comment so the connection is not
    reaped by an idle proxy timeout.
    """
    return get_backend().tail(int(session_id))


__all__ = [
    "STREAM_END",
    "LogStreamBackend",
    "MemoryLogStream",
    "RedisLogStream",
    "create",
    "get_backend",
    "publish",
    "reset_backend",
    "tail",
]
