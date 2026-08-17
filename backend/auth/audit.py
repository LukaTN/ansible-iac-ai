"""
=============================================================
  AnsibleAI — Audit trail

  Records security-relevant events to the `audit_events` table and
  mirrors them to the structured log, so the trail survives even if
  the database write fails.

  Audit writes must never break the request that triggered them: a
  failure here is logged and swallowed.
=============================================================
"""

from __future__ import annotations

from typing import Any

from flask import g, has_request_context, request

from logging_setup import get_logger
from models import AuditEvent, db

log = get_logger("audit")

# ── Authentication ──
LOGIN_SUCCESS = "auth.login.success"
LOGIN_FAILURE = "auth.login.failure"
LOGIN_BLOCKED = "auth.login.blocked"  # locked, inactive, or rate-limited
LOGOUT = "auth.logout"
REGISTER = "auth.register"
PASSWORD_CHANGED = "auth.password.changed"  # noqa: S105 — event name, not a credential
SESSION_REVOKED = "auth.session.revoked"
OIDC_SUCCESS = "auth.oidc.success"
OIDC_FAILURE = "auth.oidc.failure"
OIDC_LINKED = "auth.oidc.linked"

# ── Authorization ──
ACCESS_DENIED = "authz.denied"

# ── Admin / destructive ──
THREADS_CLEARED = "admin.threads.cleared"
DOCS_RESCRAPE = "admin.docs.rescrape"
DOCS_ROLLBACK = "admin.docs.rollback"
USER_ROLE_CHANGED = "admin.user.role_changed"
USER_ACTIVATED = "admin.user.activated"
USER_DEACTIVATED = "admin.user.deactivated"

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"


def client_ip() -> str | None:
    """
    Best-effort client address.

    X-Forwarded-For is only trusted when a proxy is actually in front of
    the app; Phase 1 puts gunicorn behind ingress-nginx and sets
    ProxyFix, which rewrites request.remote_addr correctly. Reading the
    header directly here would let any client spoof its own IP and evade
    per-IP lockout.
    """
    if not has_request_context():
        return None
    return (request.remote_addr or "")[:45] or None


def user_agent() -> str | None:
    if not has_request_context():
        return None
    return (request.headers.get("User-Agent") or "")[:255] or None


def record(
    event: str,
    *,
    user: Any = None,
    actor_email: str | None = None,
    outcome: str = OUTCOME_SUCCESS,
    commit: bool = True,
    **detail: Any,
) -> None:
    """
    Persist an audit event and emit a matching log line.

    Set `commit=False` when the caller owns the surrounding transaction.
    """
    email = actor_email or getattr(user, "email", None)
    user_id = getattr(user, "id", None)
    request_id = getattr(g, "request_id", None) if has_request_context() else None

    # structlog's bound logger takes the event name positionally, so the
    # audit event must not be passed as an `event` keyword.
    log_fields = {
        "audit_event": event,
        "outcome": outcome,
        "user_id": user_id,
        "actor_email": email,
        **detail,
    }
    if outcome == OUTCOME_FAILURE:
        log.warning("audit.event", **log_fields)
    else:
        log.info("audit.event", **log_fields)

    try:
        db.session.add(
            AuditEvent(
                user_id=user_id,
                actor_email=email,
                event=event,
                outcome=outcome,
                ip=client_ip(),
                user_agent=user_agent(),
                request_id=request_id,
                detail=detail or None,
            )
        )
        if commit:
            db.session.commit()
    except Exception:
        # The audit row is best-effort; the log line above is the fallback.
        log.exception("audit.persist_failed", audit_event=event)
        # Already logged above; a failing rollback must not mask the
        # caller's own error handling.
        try:
            db.session.rollback()
        except Exception:  # noqa: S110
            pass


__all__ = [
    "ACCESS_DENIED",
    "DOCS_RESCRAPE",
    "DOCS_ROLLBACK",
    "LOGIN_BLOCKED",
    "LOGIN_FAILURE",
    "LOGIN_SUCCESS",
    "LOGOUT",
    "OIDC_FAILURE",
    "OIDC_LINKED",
    "OIDC_SUCCESS",
    "OUTCOME_FAILURE",
    "OUTCOME_SUCCESS",
    "PASSWORD_CHANGED",
    "REGISTER",
    "SESSION_REVOKED",
    "THREADS_CLEARED",
    "USER_ACTIVATED",
    "USER_DEACTIVATED",
    "USER_ROLE_CHANGED",
    "client_ip",
    "record",
    "user_agent",
]
