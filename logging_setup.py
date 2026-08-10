"""
=============================================================
  AnsibleAI — Structured logging

  structlog emits JSON in staging/production so a log shipper
  (Loki in Phase 6) can index fields directly, and human-readable
  coloured output in development.

  Every log line inside a request carries `request_id`, and
  `user_id` once authentication has run, via contextvars — so the
  agent modules get request correlation without threading a logger
  through every call.

  Usage:
      from logging_setup import get_logger
      log = get_logger(__name__)
      log.info("playbook.generated", module="kubernetes.core.k8s", errors=0)
=============================================================
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import Any

import structlog

_configured = False

# Chatty third-party loggers. Werkzeug's per-request access log is
# redundant once we log requests ourselves.
_NOISY_LOGGERS = {
    "werkzeug": logging.WARNING,
    "engineio": logging.WARNING,
    "engineio.server": logging.WARNING,
    "socketio": logging.WARNING,
    "socketio.server": logging.WARNING,
    "urllib3": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "pgvector": logging.WARNING,
    "asyncio": logging.WARNING,
}


def configure_logging(
    level: str = "INFO",
    fmt: str = "console",
    *,
    force: bool = False,
) -> None:
    """
    Configure structlog and the stdlib root logger.

    Idempotent: repeated calls are ignored unless `force` is set, so
    importing this from both app.py and a worker entrypoint is safe.
    """
    global _configured
    if _configured and not force:
        return

    numeric_level = getattr(logging, level.strip().upper(), logging.INFO)

    shared_processors: list[Any] = [
        # Pulls request_id / user_id bound for the current context.
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if fmt == "json":
        renderer: Any = structlog.processors.JSONRenderer()
        # Full traceback as a structured field rather than raw text.
        shared_processors.append(structlog.processors.dict_tracebacks)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
        shared_processors.append(structlog.processors.format_exc_info)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (Flask, SQLAlchemy, libraries) through the same
    # renderer so output is uniform and machine-parseable.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(numeric_level)

    for name, lvl in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(max(lvl, numeric_level))

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Safe to call at import time."""
    return structlog.stdlib.get_logger(name)


# ─────────────────────────────────────────────
#  Request context
# ─────────────────────────────────────────────


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def bind_request_context(**fields: Any) -> None:
    """Bind fields onto every log line emitted for the current request."""
    structlog.contextvars.bind_contextvars(**{k: v for k, v in fields.items() if v is not None})


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


def install_flask_logging(app: Any) -> None:
    """
    Add request/response logging and trace-ID propagation to a Flask app.

    Honours an inbound `X-Request-ID` so a reverse proxy or upstream
    service can correlate; generates one otherwise. The ID is echoed back
    on the response, which is what makes a user-reported error traceable.
    """
    from flask import g, request

    log = get_logger("http")

    @app.before_request
    def _bind_context() -> None:  # pragma: no cover - exercised via requests
        clear_request_context()
        incoming = request.headers.get("X-Request-ID", "").strip()
        request_id = incoming[:64] if incoming else new_request_id()
        g.request_id = request_id
        bind_request_context(
            request_id=request_id,
            method=request.method,
            path=request.path,
        )

    @app.after_request
    def _log_response(response: Any) -> Any:  # pragma: no cover
        request_id = getattr(g, "request_id", None)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        # Static asset noise adds nothing; skip unless it failed.
        is_asset = request.path.startswith("/assets/") or request.path == "/favicon.ico"
        if not (is_asset and response.status_code < 400):
            level = (
                log.warning
                if 400 <= response.status_code < 500
                else log.error
                if response.status_code >= 500
                else log.info
            )
            level("http.request", status=response.status_code)
        return response

    @app.teardown_request
    def _clear(_exc: BaseException | None = None) -> None:  # pragma: no cover
        clear_request_context()


__all__ = [
    "bind_request_context",
    "clear_request_context",
    "configure_logging",
    "get_logger",
    "install_flask_logging",
    "new_request_id",
]
