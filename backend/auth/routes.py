"""
=============================================================
  AnsibleAI — Authentication endpoints

    POST /api/auth/register         self-serve signup (policy-gated)
    POST /api/auth/login            email + password (local hash or Keycloak ROPC)
    POST /api/auth/logout           ends the current session
    GET  /api/auth/me               current identity (200 when anonymous)
    GET  /api/auth/config           public auth capabilities (no secrets)
    GET  /api/auth/profile          identity, usage, activity, tracing
    GET  /api/auth/oidc/login       optional Keycloak hosted UI (escape hatch)
    GET  /api/auth/oidc/callback    finish hosted SSO
    POST /api/auth/password/change  rotates password, revokes sessions
    GET  /api/auth/csrf             CSRF token bootstrap for the SPA

  Failure responses are deliberately uniform: a wrong password and an
  unknown email return the same status, body, and (via a dummy hash
  verification) roughly the same latency, so the endpoint cannot be used
  to enumerate accounts.
=============================================================
"""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from flask import Blueprint, current_app, jsonify, redirect, request, session
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf.csrf import generate_csrf

from config import settings
from logging_setup import bind_request_context, get_logger
from models import (
    PROVIDER_KEYCLOAK,
    PROVIDER_LOCAL,
    ROLE_ADMIN,
    ROLE_USER,
    ChatThread,
    User,
    db,
    iso_utc,
    utcnow,
)

from . import audit
from .budgets import snapshot as budget_snapshot
from .keycloak_admin import KeycloakAdminError, change_keycloak_password
from .oidc import (
    OIDC_NEXT_KEY,
    OIDC_NONCE_KEY,
    OIDC_STATE_KEY,
    OIDC_VERIFIER_KEY,
    OidcError,
    authenticate_with_password,
    build_authorization_url,
    decode_id_token,
    exchange_code,
    new_pkce_pair,
    oidc_available,
    safe_next_path,
    upsert_user_from_claims,
)
from .passwords import (
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    validate_password,
    verify_dummy_password,
    verify_password,
)

log = get_logger(__name__)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# Identical for every authentication failure mode.
_GENERIC_LOGIN_ERROR = "Invalid email or password."

_MAX_EMAIL_LENGTH = 255
_MAX_NAME_LENGTH = 120
MUST_CHANGE_KEY = "_must_change_password"


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────


def _json_body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _clean_email(raw: Any) -> str:
    return str(raw or "").strip().lower()[:_MAX_EMAIL_LENGTH]


def _validate_email(email: str) -> str:
    """Normalize and syntactically validate an address, or raise ValueError."""
    from email_validator import EmailNotValidError, validate_email

    try:
        # DNS deliverability checks would add a network round trip to the
        # login path and fail closed on air-gapped installs.
        result = validate_email(email, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError(f"Invalid email address: {exc}") from exc
    return result.normalized.lower()


def _registration_allowed(email: str) -> tuple[bool, str]:
    mode = settings.registration_mode
    if mode == "closed":
        return False, "Self-registration is disabled. Ask an administrator for an account."
    if mode == "domain":
        domain = email.rsplit("@", 1)[-1]
        if domain not in settings.email_domains:
            allowed = ", ".join(settings.email_domains)
            return False, f"Registration is limited to these domains: {allowed}."
    return True, ""


def _start_session(user: User) -> None:
    """
    Establish an authenticated session, rotating the session identifier.

    Rotation matters: without it an attacker who plants a known session ID
    in the victim's browser before login (session fixation) still holds a
    valid session afterwards.
    """
    session.clear()
    try:
        current_app.session_interface.regenerate(session)  # type: ignore[attr-defined]
    except AttributeError:
        # Client-side session fallback; Flask re-signs the cookie anyway.
        log.debug("auth.session.regenerate_unavailable")

    login_user(user, remember=False, fresh=True)
    session.permanent = True
    user.register_successful_login()


def _user_payload(user: User) -> dict:
    data = user.to_dict()
    data["must_change_password"] = bool(session.get(MUST_CHANGE_KEY))
    return {"authenticated": True, "user": data}


def _password_login_permitted(email: str) -> bool:
    """Whether this address may authenticate with a local password."""
    if settings.auth_mode in ("local", "hybrid"):
        return True
    return email in settings.break_glass_emails


def _sso_error_redirect(code: str) -> Any:
    return redirect(f"/?sso={code}")


# ─────────────────────────────────────────────
#  Rate limits
# ─────────────────────────────────────────────


def _limit(spec_name: str) -> Any:
    """
    Attach a Flask-Limiter limit to a view.

    Resolves at import time, which is why `register_auth` runs
    `init_security` (where the limiter is built) before importing this
    module. If the limiter is genuinely disabled the view is returned
    unchanged; if it is merely missing, that is a wiring bug and is
    logged rather than silently ignored.
    """

    def decorator(view: Any) -> Any:
        from . import security

        limiter = getattr(security, "limiter", None)
        if limiter is None:
            if settings.rate_limit_enabled:
                log.error(
                    "auth.rate_limit.not_initialized",
                    view=getattr(view, "__name__", "?"),
                    hint="init_security must run before auth.routes is imported",
                )
            return view
        return limiter.limit(lambda: getattr(settings, spec_name))(view)

    return decorator


# ─────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────


@bp.get("/csrf")
def csrf_token() -> Any:
    """Hand the SPA a CSRF token before it issues its first write."""
    return jsonify({"csrf_token": generate_csrf()})


@bp.get("/config")
def auth_config() -> Any:
    """
    Non-secret auth capabilities for the login screen.

    The SPA uses this to show or hide the password form, registration,
    and (rarely) the Keycloak hosted-UI escape hatch. Nothing here is a
    credential; client id and issuer stay server-side.
    """
    show_hosted = oidc_available() and settings.oidc_browser_redirect
    return jsonify(
        {
            "auth_mode": settings.auth_mode,
            "oidc_enabled": oidc_available(),
            "local_login_enabled": settings.local_login_enabled,
            "registration_enabled": settings.registration_enabled,
            "app_admin_ui": settings.app_admin_ui,
            "oidc_login_url": "/api/auth/oidc/login" if show_hosted else None,
        }
    )


@bp.get("/me")
def me() -> Any:
    """
    Current identity.

    Returns 200 with `authenticated: false` rather than 401 so the SPA can
    determine login state at startup without its 401 handler firing.
    """
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False, "user": None})
    return jsonify(_user_payload(current_user))


@bp.post("/register")
@_limit("rate_limit_register")
def register() -> Any:
    body = _json_body()
    email_raw = _clean_email(body.get("email"))
    password = body.get("password") or ""
    display_name = str(body.get("display_name") or "").strip()[:_MAX_NAME_LENGTH]

    if not email_raw or not password:
        return (
            jsonify(
                {
                    "error": "Email and password are required.",
                    "code": "missing_fields",
                }
            ),
            400,
        )

    if settings.auth_mode in ("hybrid", "oidc"):
        audit.record(
            audit.REGISTER,
            actor_email=email_raw,
            outcome=audit.OUTCOME_FAILURE,
            reason="invite_only",
        )
        return (
            jsonify(
                {
                    "error": "Self-registration is disabled. Ask an administrator for an account.",
                    "code": "registration_disabled",
                }
            ),
            403,
        )

    try:
        email = _validate_email(email_raw)
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "invalid_email"}), 400

    allowed, reason = _registration_allowed(email)
    if not allowed:
        audit.record(
            audit.REGISTER,
            actor_email=email,
            outcome=audit.OUTCOME_FAILURE,
            reason="registration_not_allowed",
        )
        return jsonify({"error": reason, "code": "registration_disabled"}), 403

    try:
        validate_password(password, email=email, display_name=display_name)
    except PasswordPolicyError as exc:
        return jsonify({"error": str(exc), "code": "weak_password"}), 400

    existing = User.query.filter_by(email=email).first()
    if existing is not None:
        # Do not confirm that the address is taken; that turns registration
        # into an account-enumeration oracle. The legitimate owner can use
        # password reset.
        audit.record(
            audit.REGISTER,
            actor_email=email,
            outcome=audit.OUTCOME_FAILURE,
            reason="duplicate_email",
        )
        return jsonify(_registration_accepted_payload()), 202

    # First account ever created becomes the administrator, so a fresh
    # install is manageable without a separate seeding step.
    is_first_user = db.session.query(User.id).first() is None
    requires_approval = settings.require_admin_approval and not is_first_user

    user = User(
        email=email,
        display_name=display_name or email.split("@")[0],
        password_hash=hash_password(password),
        role=ROLE_ADMIN if is_first_user else ROLE_USER,
        is_active=not requires_approval,
        provider=PROVIDER_LOCAL,
        password_changed_at=utcnow(),
    )
    db.session.add(user)
    db.session.commit()

    audit.record(
        audit.REGISTER,
        user=user,
        role=user.role,
        pending_approval=requires_approval,
    )

    if requires_approval:
        return jsonify(_registration_accepted_payload()), 202

    _start_session(user)
    db.session.commit()
    bind_request_context(user_id=user.id)
    audit.record(audit.LOGIN_SUCCESS, user=user, via="registration")
    return jsonify(_user_payload(user)), 201


def _registration_accepted_payload() -> dict:
    return {
        "authenticated": False,
        "user": None,
        "pending_approval": True,
        "message": (
            "Registration received. An administrator must activate the account "
            "before it can be used."
        ),
    }


@bp.post("/login")
@_limit("rate_limit_login")
def login() -> Any:
    body = _json_body()
    email = _clean_email(body.get("email"))
    password = body.get("password") or ""

    if not email or not password:
        return (
            jsonify(
                {
                    "error": "Email and password are required.",
                    "code": "missing_fields",
                }
            ),
            400,
        )

    is_break_glass = email in settings.break_glass_emails
    if oidc_available() and not is_break_glass:
        return _keycloak_password_login(email, password)

    if not _password_login_permitted(email):
        verify_dummy_password(password)
        audit.record(
            audit.LOGIN_FAILURE,
            actor_email=email,
            outcome=audit.OUTCOME_FAILURE,
            reason="oidc_required",
        )
        return jsonify({"error": _GENERIC_LOGIN_ERROR, "code": "invalid_credentials"}), 401

    return _local_password_login(email, password)


def _keycloak_password_login(email: str, password: str) -> Any:
    try:
        user, must_change = authenticate_with_password(email, password)
    except OidcError as exc:
        if exc.code == "idp_unavailable":
            audit.record(
                audit.LOGIN_FAILURE,
                actor_email=email,
                outcome=audit.OUTCOME_FAILURE,
                reason="idp_unavailable",
            )
            return (
                jsonify(
                    {
                        "error": "Sign-in is temporarily unavailable. Try again shortly.",
                        "code": "idp_unavailable",
                    }
                ),
                503,
            )
        if exc.code == "idp_admin_unavailable":
            audit.record(
                audit.LOGIN_FAILURE,
                actor_email=email,
                outcome=audit.OUTCOME_FAILURE,
                reason="idp_admin_unavailable",
            )
            return (
                jsonify(
                    {
                        "error": (
                            "This account still has a temporary password, but the "
                            "identity service cannot finish setup. Ask an administrator."
                        ),
                        "code": "idp_admin_unavailable",
                    }
                ),
                503,
            )
        if exc.code == "email_unverified":
            audit.record(
                audit.LOGIN_BLOCKED,
                actor_email=email,
                outcome=audit.OUTCOME_FAILURE,
                reason="email_unverified",
            )
            return (
                jsonify(
                    {
                        "error": "Verify your email with your administrator, then sign in again.",
                        "code": "email_unverified",
                    }
                ),
                403,
            )
        audit.record(
            audit.LOGIN_FAILURE,
            actor_email=email,
            outcome=audit.OUTCOME_FAILURE,
            reason=exc.code,
        )
        return jsonify({"error": _GENERIC_LOGIN_ERROR, "code": "invalid_credentials"}), 401

    blocked = _reject_if_unusable(user)
    if blocked is not None:
        return blocked

    _start_session(user)
    session[MUST_CHANGE_KEY] = bool(must_change)
    session.modified = True
    db.session.commit()

    bind_request_context(user_id=user.id)
    audit.record(audit.LOGIN_SUCCESS, user=user, via="keycloak")
    return jsonify(_user_payload(user))


def _local_password_login(email: str, password: str) -> Any:
    user = User.query.filter_by(email=email).first()

    if user is None:
        # Burn comparable CPU so timing does not reveal that the address
        # is unknown.
        verify_dummy_password(password)
        audit.record(
            audit.LOGIN_FAILURE,
            actor_email=email,
            outcome=audit.OUTCOME_FAILURE,
            reason="unknown_email",
        )
        return jsonify({"error": _GENERIC_LOGIN_ERROR, "code": "invalid_credentials"}), 401

    if not verify_password(password, user.password_hash):
        locked = user.register_failed_login(
            settings.account_lockout_threshold, settings.account_lockout_minutes
        )
        db.session.commit()
        audit.record(
            audit.LOGIN_FAILURE,
            user=user,
            outcome=audit.OUTCOME_FAILURE,
            reason="bad_password",
            triggered_lockout=locked,
        )
        return jsonify({"error": _GENERIC_LOGIN_ERROR, "code": "invalid_credentials"}), 401

    blocked = _reject_if_unusable(user)
    if blocked is not None:
        return blocked

    # Opportunistically upgrade hashes when argon2 parameters change.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        log.info("auth.password.rehashed", user_id=user.id)

    _start_session(user)
    db.session.commit()

    bind_request_context(user_id=user.id)
    audit.record(audit.LOGIN_SUCCESS, user=user)
    return jsonify(_user_payload(user))


def _reject_if_unusable(user: User) -> Any | None:
    """Return a Flask response when the account must not start a session."""
    if user.is_locked:
        audit.record(
            audit.LOGIN_BLOCKED,
            user=user,
            outcome=audit.OUTCOME_FAILURE,
            reason="locked",
        )
        return (
            jsonify(
                {
                    "error": (
                        "Too many failed attempts. This account is temporarily "
                        f"locked; try again in {settings.account_lockout_minutes} minutes."
                    ),
                    "code": "account_locked",
                }
            ),
            423,
        )

    if not user.is_active:
        audit.record(
            audit.LOGIN_BLOCKED,
            user=user,
            outcome=audit.OUTCOME_FAILURE,
            reason="inactive",
        )
        return (
            jsonify(
                {
                    "error": "This account is not active yet. Ask an administrator to enable it.",
                    "code": "account_inactive",
                }
            ),
            403,
        )
    return None


@bp.post("/logout")
@login_required
def logout() -> Any:
    user = current_user
    audit.record(audit.LOGOUT, user=user)
    logout_user()
    session.clear()
    return jsonify({"authenticated": False, "user": None})


@bp.get("/profile")
@login_required
def profile() -> Any:
    """Identity, today's token spend, and conversation activity."""
    user = current_user
    thread_count = ChatThread.query.filter_by(user_id=user.id).count()
    last_thread = (
        ChatThread.query.filter_by(user_id=user.id)
        .order_by(ChatThread.updated_at.desc())
        .first()
    )
    usage = budget_snapshot(int(user.id))
    return jsonify(
        {
            "user": _user_payload(user)["user"],
            "usage": {
                "token_budget_limit": usage["token_budget_limit"],
                "token_budget_used": usage["token_budget_used"],
                "token_budget_remaining": usage["token_budget_remaining"],
                "unlimited": int(usage["token_budget_limit"]) <= 0,
            },
            "activity": {
                "thread_count": thread_count,
                "last_activity_at": iso_utc(last_thread.updated_at) if last_thread else None,
            },
        }
    )


@bp.post("/password/change")
@login_required
def change_password() -> Any:
    body = _json_body()
    current_password = body.get("current_password") or ""
    new_password = body.get("new_password") or ""

    if not current_password or not new_password:
        return (
            jsonify(
                {
                    "error": "Current and new password are required.",
                    "code": "missing_fields",
                }
            ),
            400,
        )

    user = db.session.get(User, current_user.id)
    if user is None:
        return jsonify({"error": "Account not found.", "code": "account_not_found"}), 404

    if new_password == current_password:
        return (
            jsonify(
                {
                    "error": "New password must differ from the current one.",
                    "code": "password_reuse",
                }
            ),
            400,
        )

    try:
        validate_password(new_password, email=user.email, display_name=user.display_name or "")
    except PasswordPolicyError as exc:
        return jsonify({"error": str(exc), "code": "weak_password"}), 400

    if user.provider == PROVIDER_KEYCLOAK and oidc_available():
        return _change_keycloak_password(user, current_password, new_password)

    if not user.password_hash:
        return (
            jsonify(
                {
                    "error": "This account signs in with SSO and has no local password.",
                    "code": "no_local_password",
                }
            ),
            400,
        )

    if not verify_password(current_password, user.password_hash):
        audit.record(
            audit.PASSWORD_CHANGED,
            user=user,
            outcome=audit.OUTCOME_FAILURE,
            reason="wrong_current_password",
        )
        return (
            jsonify(
                {
                    "error": "Current password is incorrect.",
                    "code": "wrong_current_password",
                }
            ),
            403,
        )

    if verify_password(new_password, user.password_hash):
        return (
            jsonify(
                {
                    "error": "New password must differ from the current one.",
                    "code": "password_reuse",
                }
            ),
            400,
        )

    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    user.invalidate_sessions()
    db.session.commit()

    audit.record(audit.PASSWORD_CHANGED, user=user)
    audit.record(audit.SESSION_REVOKED, user=user, reason="password_change")

    _start_session(user)
    session.pop(MUST_CHANGE_KEY, None)
    db.session.commit()

    return jsonify(
        {**_user_payload(user), "message": "Password updated. Other sessions were signed out."}
    )


def _change_keycloak_password(user: User, current_password: str, new_password: str) -> Any:
    try:
        authenticate_with_password(user.email, current_password)
    except OidcError as exc:
        if exc.code == "invalid_credentials":
            audit.record(
                audit.PASSWORD_CHANGED,
                user=user,
                outcome=audit.OUTCOME_FAILURE,
                reason="wrong_current_password",
            )
            return (
                jsonify(
                    {
                        "error": "Current password is incorrect.",
                        "code": "wrong_current_password",
                    }
                ),
                403,
            )
        code = exc.code if exc.code in ("idp_unavailable", "idp_admin_unavailable") else "idp_unavailable"
        audit.record(
            audit.PASSWORD_CHANGED,
            user=user,
            outcome=audit.OUTCOME_FAILURE,
            reason=exc.code,
        )
        return (
            jsonify(
                {
                    "error": "The identity provider could not update the password. Try again shortly.",
                    "code": code,
                }
            ),
            503,
        )

    try:
        change_keycloak_password(user.email, new_password)
    except KeycloakAdminError as exc:
        audit.record(
            audit.PASSWORD_CHANGED,
            user=user,
            outcome=audit.OUTCOME_FAILURE,
            reason=exc.code,
        )
        return (
            jsonify(
                {
                    "error": "The identity provider could not update the password. Try again shortly.",
                    "code": "idp_admin_unavailable",
                }
            ),
            503,
        )

    user.password_changed_at = utcnow()
    user.invalidate_sessions()
    db.session.commit()

    audit.record(audit.PASSWORD_CHANGED, user=user, via="keycloak")
    audit.record(audit.SESSION_REVOKED, user=user, reason="password_change")

    _start_session(user)
    session.pop(MUST_CHANGE_KEY, None)
    db.session.commit()

    return jsonify(
        {**_user_payload(user), "message": "Password updated. Other sessions were signed out."}
    )


@bp.get("/oidc/login")
@_limit("rate_limit_login")
def oidc_login() -> Any:
    if not oidc_available() or not settings.oidc_browser_redirect:
        return jsonify({"error": "SSO is not enabled.", "code": "oidc_disabled"}), 404

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = new_pkce_pair()
    session[OIDC_STATE_KEY] = state
    session[OIDC_NONCE_KEY] = nonce
    session[OIDC_VERIFIER_KEY] = verifier
    session[OIDC_NEXT_KEY] = safe_next_path(request.args.get("next"))
    session.modified = True
    return redirect(build_authorization_url(state=state, nonce=nonce, code_challenge=challenge))


@bp.get("/oidc/callback")
def oidc_callback() -> Any:
    if not oidc_available():
        return jsonify({"error": "SSO is not enabled.", "code": "oidc_disabled"}), 404

    if request.args.get("error"):
        audit.record(
            audit.OIDC_FAILURE,
            outcome=audit.OUTCOME_FAILURE,
            reason=str(request.args.get("error"))[:80],
        )
        return _sso_error_redirect("denied")

    code = (request.args.get("code") or "").strip()
    state = (request.args.get("state") or "").strip()
    expected_state = session.pop(OIDC_STATE_KEY, None)
    nonce = session.pop(OIDC_NONCE_KEY, None)
    verifier = session.pop(OIDC_VERIFIER_KEY, None)
    next_path = safe_next_path(session.pop(OIDC_NEXT_KEY, "/"))

    if not code or not expected_state or not nonce or not verifier:
        audit.record(audit.OIDC_FAILURE, outcome=audit.OUTCOME_FAILURE, reason="missing_state")
        return _sso_error_redirect("failed")

    if not hmac.compare_digest(str(expected_state), state):
        audit.record(audit.OIDC_FAILURE, outcome=audit.OUTCOME_FAILURE, reason="state_mismatch")
        return _sso_error_redirect("failed")

    try:
        tokens = exchange_code(code, str(verifier))
        claims = decode_id_token(str(tokens["id_token"]), nonce=str(nonce))
        user, linked = upsert_user_from_claims(claims)
    except OidcError as exc:
        audit.record(
            audit.OIDC_FAILURE,
            outcome=audit.OUTCOME_FAILURE,
            reason=exc.code,
        )
        if exc.code == "email_unverified":
            return _sso_error_redirect("unverified")
        return _sso_error_redirect("failed")

    if user.is_locked:
        audit.record(
            audit.LOGIN_BLOCKED,
            user=user,
            outcome=audit.OUTCOME_FAILURE,
            reason="locked",
            via="oidc",
        )
        return _sso_error_redirect("locked")

    if not user.is_active:
        audit.record(
            audit.LOGIN_BLOCKED,
            user=user,
            outcome=audit.OUTCOME_FAILURE,
            reason="inactive",
            via="oidc",
        )
        return _sso_error_redirect("inactive")

    _start_session(user)
    db.session.commit()
    bind_request_context(user_id=user.id)
    if linked:
        audit.record(audit.OIDC_LINKED, user=user, provider="keycloak")
    audit.record(audit.OIDC_SUCCESS, user=user, via="oidc")
    audit.record(audit.LOGIN_SUCCESS, user=user, via="oidc")
    return redirect(next_path)


__all__ = ["bp"]
