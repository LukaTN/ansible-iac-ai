"""
=============================================================
  AnsibleAI — Authentication endpoints

    POST /api/auth/register         self-serve signup (policy-gated)
    POST /api/auth/login            email + password
    POST /api/auth/logout           ends the current session
    GET  /api/auth/me               current identity (200 when anonymous)
    POST /api/auth/password/change  rotates password, revokes sessions
    GET  /api/auth/csrf             CSRF token bootstrap for the SPA

  Failure responses are deliberately uniform: a wrong password and an
  unknown email return the same status, body, and (via a dummy hash
  verification) roughly the same latency, so the endpoint cannot be used
  to enumerate accounts.
=============================================================
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf.csrf import generate_csrf

from config import settings
from logging_setup import bind_request_context, get_logger
from models import PROVIDER_LOCAL, ROLE_ADMIN, ROLE_USER, User, db, utcnow

from . import audit
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
    return {"authenticated": True, "user": user.to_dict()}


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

    # Password is correct from here on, so specific messages leak nothing
    # the caller does not already know.

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

    # Opportunistically upgrade hashes when argon2 parameters change.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        log.info("auth.password.rehashed", user_id=user.id)

    _start_session(user)
    db.session.commit()

    bind_request_context(user_id=user.id)
    audit.record(audit.LOGIN_SUCCESS, user=user)
    return jsonify(_user_payload(user))


@bp.post("/logout")
@login_required
def logout() -> Any:
    user = current_user
    audit.record(audit.LOGOUT, user=user)
    logout_user()
    session.clear()
    return jsonify({"authenticated": False, "user": None})


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

    try:
        validate_password(new_password, email=user.email, display_name=user.display_name or "")
    except PasswordPolicyError as exc:
        return jsonify({"error": str(exc), "code": "weak_password"}), 400

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
    # Revokes every other session, including any an attacker may hold.
    user.invalidate_sessions()
    db.session.commit()

    audit.record(audit.PASSWORD_CHANGED, user=user)
    audit.record(audit.SESSION_REVOKED, user=user, reason="password_change")

    # The current session's epoch is now stale, so re-establish it rather
    # than logging the user out of the tab they are using.
    _start_session(user)
    db.session.commit()

    return jsonify(
        {**_user_payload(user), "message": "Password updated. Other sessions were signed out."}
    )


__all__ = ["bp"]
