"""
=============================================================
  Gunicorn configuration for the AnsibleAI API container.

  Loaded by docker/entrypoint.sh via `gunicorn -c`. Every value is
  overridable through the environment so a deployment can be tuned
  without rebuilding the image.

  Not used for local development: `python app.py` still runs the
  Werkzeug server, which app.py refuses to do outside APP_ENV=development.
=============================================================
"""

from __future__ import annotations

import os
from typing import Any


def _int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


# ── Socket ───────────────────────────────────────────────────────
# 0.0.0.0, not the 127.0.0.1 the dev server binds: the port has to be
# reachable from outside the container's network namespace.
bind = f"0.0.0.0:{_int('PORT', 5000)}"
backlog = _int("GUNICORN_BACKLOG", 2048)


# ── Workers ──────────────────────────────────────────────────────
# One by default, but no longer *pinned* to one.
#
# Phase 2 removed the three things that made a second worker unsafe:
# emits now travel over a Redis message queue, cancellation lives in
# Redis, and SSE log tailing reads a Redis stream. What remains is
# Socket.IO's own requirement — a session's polling requests must reach
# the process that accepted the connection — and gunicorn cannot pin
# them. Raise this only behind an ingress doing sticky sessions, which is
# what the Kubernetes manifests in Phase 6 configure.
workers = _int("GUNICORN_WORKERS", 1)

# Plain "gevent" — deliberately not the GeventWebSocketWorker that older
# Flask-SocketIO documentation recommends. That worker comes from
# gevent-websocket, unmaintained since 2017, which current versions of
# python-engineio no longer use and which breaks the connection right
# after the 101 handshake. WebSocket support now comes from
# simple-websocket, which the plain gevent worker picks up automatically.
#
# GUNICORN_WORKER_CLASS=gthread with SOCKETIO_ASYNC_MODE=threading is the
# fallback for hosts where gevent's greenlets cause trouble; it costs the
# native upgrade and drops Socket.IO to HTTP long-polling.
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "gevent")
worker_connections = _int("GUNICORN_WORKER_CONNECTIONS", 1000)
threads = _int("GUNICORN_THREADS", 1)

# gevent monkey-patches the standard library inside the worker. Preloading
# would import SQLAlchemy, psycopg2 and redis in the master first, leaving
# them holding unpatched sockets that then block the whole event loop.
preload_app = False


# ── Timeouts ─────────────────────────────────────────────────────
# A single POST /api/chat occupies its request for as long as the model
# takes to draft plus every repair-loop iteration — minutes, not seconds.
# Gunicorn's 30s default would kill generation mid-flight and the user
# would see a dropped connection. AGENT_REQUEST_TIMEOUT (default 300s) is
# the per-LLM-call bound; this has to sit above the worst-case total.
timeout = _int("GUNICORN_TIMEOUT", 600)

# Let in-flight generations finish on redeploy instead of being severed.
graceful_timeout = _int("GUNICORN_GRACEFUL_TIMEOUT", 90)
keepalive = _int("GUNICORN_KEEPALIVE", 5)

# Off by default: recycling a worker mid-generation loses the request and
# the money already spent on tokens.
max_requests = _int("GUNICORN_MAX_REQUESTS", 0)
max_requests_jitter = _int("GUNICORN_MAX_REQUESTS_JITTER", 0)


# ── Logging ──────────────────────────────────────────────────────
# logging_setup.install_flask_logging already emits one structured
# `http.request` line per response, with request_id and user_id bound.
# Gunicorn's access log would duplicate that in a different format, so it
# stays off; errors still go to stderr for the container runtime to pick up.
accesslog = None
errorlog = "-"
loglevel = (os.getenv("GUNICORN_LOG_LEVEL") or "info").strip().lower()


def on_starting(server: Any) -> None:
    """Warn when the worker count and Socket.IO's assumptions disagree.

    Gunicorn calls this in the master before any worker forks; `server` is
    an ``Arbiter``, typed loosely here to avoid importing gunicorn at
    config-parse time.
    """
    if workers > 1 and not (os.getenv("SOCKETIO_MESSAGE_QUEUE") or "").strip():
        server.log.warning(
            "GUNICORN_WORKERS=%s with no SOCKETIO_MESSAGE_QUEUE: a client's "
            "polling requests will reach workers that have never heard of "
            "its session, and worker-emitted progress will reach nobody. "
            "Set SOCKETIO_MESSAGE_QUEUE to the Redis URL, or keep this at 1.",
            workers,
        )
