"""
=============================================================
  AnsibleAI — Application security wiring

  Installs, in one place:
    - server-side sessions (Flask-Session)
    - Flask-Login with epoch-aware session identifiers
    - CSRF protection for cookie-authenticated writes
    - security response headers (Talisman)
    - rate limiting (Flask-Limiter)
    - a DEFAULT-DENY authentication hook

  The default-deny hook is the important part. Decorating routes
  individually means one forgotten decorator is a data leak; here every
  endpoint requires a session unless it is named in PUBLIC_ENDPOINTS.
=============================================================
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import Flask, jsonify, request
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from config import settings
from logging_setup import get_logger
from models import ROLE_ADMIN, User, db

from . import audit

log = get_logger(__name__)

login_manager = LoginManager()
csrf = CSRFProtect()

# Populated by init_security so route modules can attach limits.
limiter: Any = None

# Endpoints reachable without a session. Everything else is denied by
# default. Keep this list short and justify every entry.
PUBLIC_ENDPOINTS: set[str] = {
    # SPA shell and its assets: the login screen has to render before
    # anyone can authenticate. No application data is served here.
    "index",
    "vite_assets",
    "static",
    # CORS preflight carries no credentials and must not 401.
    "_cors_preflight",
    # Kubernetes probes; these must answer before a user exists.
    "healthz",
    "readyz",
    # Prometheus scrape target (no auth; network-restricted in prod).
    "metrics",
    # Authentication surface itself.
    "auth.login",
    "auth.register",
    "auth.csrf_token",
    # Returns {"authenticated": false} instead of 401 so the SPA can
    # bootstrap without tripping its own 401 redirect.
    "auth.me",
}

# Endpoints that mutate shared state (the scraped knowledge base every
# user generates against) and are therefore restricted to administrators.
#
# Note what is deliberately absent: `api_threads_clear`. It is destructive
# but scoped to the caller's own threads, so it is a normal user action.
ADMIN_ENDPOINTS: set[str] = {
    "api_docs_rescrape",
    "api_docs_check_updates",
    "api_docs_rollback_restore",
}


# ─────────────────────────────────────────────
#  Flask-Login
# ─────────────────────────────────────────────


@login_manager.user_loader
def _load_user(session_id: str) -> User | None:
    """
    Resolve the session identifier written by `User.get_id()`.

    The identifier is "<user_id>:<session_epoch>". A stale epoch means
    the password changed (or sessions were force-revoked) after this
    session was issued, so the session is rejected. This is what makes
    logout-everywhere work regardless of session backend.
    """
    raw_id, _, raw_epoch = (session_id or "").partition(":")
    if not raw_id.isdigit():
        return None

    user = db.session.get(User, int(raw_id))
    if user is None or not user.is_active:
        return None

    # Sessions issued before the epoch mechanism existed carry no epoch.
    if not raw_epoch.isdigit() or int(raw_epoch) != user.session_epoch:
        log.info("auth.session.stale_epoch", user_id=user.id)
        return None

    return user


@login_manager.unauthorized_handler
def _unauthorized() -> Any:
    """JSON 401 — this API has no server-rendered login page to redirect to."""
    return (
        jsonify({"error": "Authentication required", "code": "unauthenticated"}),
        401,
    )


# ─────────────────────────────────────────────
#  Decorators
# ─────────────────────────────────────────────


def admin_required(view: Callable) -> Callable:
    """Require an authenticated administrator."""

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_authenticated:
            return _unauthorized()
        if not current_user.is_admin:
            audit.record(
                audit.ACCESS_DENIED,
                user=current_user,
                outcome=audit.OUTCOME_FAILURE,
                endpoint=request.endpoint,
                reason="admin_required",
            )
            return (
                jsonify({"error": "Administrator access required", "code": "forbidden"}),
                403,
            )
        return view(*args, **kwargs)

    return wrapper


# ─────────────────────────────────────────────
#  Initialization
# ─────────────────────────────────────────────


def init_security(app: Flask) -> None:
    """Attach every security extension and hook to `app`."""
    _init_sessions(app)
    _init_login(app)
    _init_csrf(app)
    _init_headers(app)
    _init_rate_limiting(app)
    _install_default_deny(app)


def _init_sessions(app: Flask) -> None:
    from flask_session import Session

    if settings.session_backend == "sqlalchemy":
        # Reuse the app database so local development needs no extra
        # service. Production switches SESSION_BACKEND to redis.
        app.config["SESSION_SQLALCHEMY"] = db
        app.config["SESSION_SQLALCHEMY_TABLE"] = "sessions"
    elif settings.session_backend == "redis":
        import redis

        app.config["SESSION_REDIS"] = redis.Redis.from_url(settings.redis_url)

    Session(app)


def _init_login(app: Flask) -> None:
    # "strong" ties the session to a client fingerprint, so a stolen
    # cookie replayed from elsewhere is rejected rather than accepted.
    login_manager.session_protection = "strong"
    login_manager.init_app(app)


def _init_csrf(app: Flask) -> None:
    """
    Enable CSRF protection for cookie-authenticated state changes.

    The SPA reads the (non-HttpOnly) `csrf_token` cookie and echoes it in
    the `X-CSRFToken` header. Flask-WTF validates that against the signed
    value in the session, so a cross-site form post cannot forge it.
    """
    app.config.setdefault("WTF_CSRF_HEADERS", ["X-CSRFToken", "X-CSRF-Token"])
    csrf.init_app(app)

    @app.after_request
    def _refresh_csrf_cookie(response: Any) -> Any:
        # Always refresh after a response. Login/register call session.clear()
        # which rotates the CSRF secret; if we only set the cookie on GETs,
        # the SPA keeps sending the pre-login token and the next write fails.
        if request.method == "OPTIONS":
            return response
        try:
            response.set_cookie(
                "csrf_token",
                generate_csrf(),
                secure=settings.session_cookie_secure,
                httponly=False,  # the SPA must read it
                samesite="Lax",
                max_age=int(settings.session_lifetime_minutes) * 60,
            )
        except Exception:
            log.exception("auth.csrf.cookie_failed")
        return response

    @app.errorhandler(400)
    def _csrf_error(error: Any) -> Any:
        description = getattr(error, "description", "") or ""
        if "CSRF" in str(description):
            return (
                jsonify(
                    {
                        "error": (
                            "Your session security token expired. "
                            "Refresh the page and try again."
                        ),
                        "code": "csrf",
                    }
                ),
                403,
            )
        return (
            jsonify(
                {
                    "error": str(description) or "Bad request",
                    "code": "bad_request",
                }
            ),
            400,
        )


def _init_headers(app: Flask) -> None:
    from flask_talisman import Talisman

    # Vite emits hashed asset files, so scripts need no inline allowance.
    # Styles still do: the app injects inline style attributes for chart
    # sizing and progress rings.
    connect_src = ["'self'"]
    if settings.is_development:
        # Vite dev server + its HMR websocket.
        connect_src += ["ws:", "wss:", "http://localhost:5173", "http://127.0.0.1:5173"]
    else:
        connect_src += ["wss:"]

    csp = {
        "default-src": "'self'",
        "script-src": "'self'",
        "style-src": ["'self'", "'unsafe-inline'"],
        "img-src": ["'self'", "data:", "blob:"],
        "font-src": ["'self'", "data:"],
        "connect-src": connect_src,
        "frame-ancestors": "'none'",
        "base-uri": "'self'",
        "form-action": "'self'",
        "object-src": "'none'",
    }

    Talisman(
        app,
        force_https=settings.force_https,
        strict_transport_security=settings.force_https,
        strict_transport_security_max_age=31_536_000,
        session_cookie_secure=settings.session_cookie_secure,
        session_cookie_http_only=True,
        content_security_policy=csp,
        # The SPA is served from the same origin as the API.
        content_security_policy_nonce_in=None,
        referrer_policy="strict-origin-when-cross-origin",
        frame_options="DENY",
        x_content_type_options=True,
        permissions_policy={
            "geolocation": "()",
            "microphone": "()",
            "camera": "()",
        },
    )


def _init_rate_limiting(app: Flask) -> None:
    global limiter

    if not settings.rate_limit_enabled:
        log.warning("auth.rate_limit.disabled")
        return

    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    storage_uri = settings.redis_url if settings.rate_limit_backend == "redis" else "memory://"
    if storage_uri == "memory://":
        # In-memory counters are per-process, so they stop being a real
        # control once the API runs more than one replica (Phase 2).
        log.warning("auth.rate_limit.in_memory_storage")

    limiter = Limiter(
        get_remote_address,
        app=app,
        storage_uri=storage_uri,
        strategy="fixed-window",
        headers_enabled=True,
    )

    @app.errorhandler(429)
    def _too_many(error: Any) -> Any:
        return (
            jsonify(
                {
                    "error": "Too many requests. Please slow down and try again shortly.",
                    "code": "rate_limited",
                    "detail": str(getattr(error, "description", "")),
                }
            ),
            429,
        )


def _install_default_deny(app: Flask) -> None:
    """
    Require an authenticated session for every endpoint outside
    PUBLIC_ENDPOINTS, and an admin role for ADMIN_ENDPOINTS.
    """

    @app.before_request
    def _require_authentication() -> Any:
        endpoint = request.endpoint

        # No matched route: let Flask produce its own 404.
        if endpoint is None:
            return None
        if request.method == "OPTIONS":
            return None
        if endpoint in PUBLIC_ENDPOINTS:
            return None

        if not current_user.is_authenticated:
            return _unauthorized()

        if endpoint in ADMIN_ENDPOINTS and not current_user.is_admin:
            audit.record(
                audit.ACCESS_DENIED,
                user=current_user,
                outcome=audit.OUTCOME_FAILURE,
                endpoint=endpoint,
                reason="admin_endpoint",
            )
            return (
                jsonify({"error": "Administrator access required", "code": "forbidden"}),
                403,
            )

        return None


def registered_endpoints(app: Flask) -> list[str]:
    """Every endpoint name, for the coverage test that guards default-deny."""
    return sorted({rule.endpoint for rule in app.url_map.iter_rules()})


def audit_admin_action(event: str, **detail: Any) -> None:
    """Convenience wrapper used by destructive admin routes."""
    audit.record(event, user=current_user if current_user.is_authenticated else None, **detail)


__all__ = [
    "ADMIN_ENDPOINTS",
    "PUBLIC_ENDPOINTS",
    "ROLE_ADMIN",
    "admin_required",
    "audit_admin_action",
    "csrf",
    "init_security",
    "limiter",
    "login_manager",
    "registered_endpoints",
]
