"""
=============================================================
  AnsibleAI — Keycloak identity (ROPC + optional auth-code)

  Phase 5b: members type email and password on AnsibleAI. The API
  is the confidential client and calls Keycloak's token endpoint
  (`grant_type=password`). The browser does not redirect to Keycloak.

  Authorization-code + PKCE remains as an opt-in escape hatch
  (`OIDC_BROWSER_REDIRECT=true`). Bearer access tokens (API /
  Socket.IO) are verified against the same JWKS.
=============================================================
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from jwt import PyJWKClient

from config import settings
from logging_setup import get_logger
from models import (
    PROVIDER_KEYCLOAK,
    ROLE_ADMIN,
    ROLE_USER,
    User,
    db,
    utcnow,
)

log = get_logger(__name__)

OIDC_STATE_KEY = "_oidc_state"
OIDC_NONCE_KEY = "_oidc_nonce"
OIDC_VERIFIER_KEY = "_oidc_verifier"
OIDC_NEXT_KEY = "_oidc_next"

_ALLOWED_ALGS = ["RS256"]
_HTTP_TIMEOUT = 10.0
_JWKS_CLIENT: PyJWKClient | None = None


class OidcError(Exception):
    """User-facing SSO failure. `code` is a stable machine value."""

    def __init__(self, code: str, message: str = "Sign-in with SSO failed.") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def oidc_available() -> bool:
    return settings.oidc_enabled


def authorization_endpoint() -> str:
    return f"{_public_issuer()}/protocol/openid-connect/auth"


def token_endpoint() -> str:
    return f"{_server_issuer()}/protocol/openid-connect/token"


def jwks_endpoint() -> str:
    return f"{_server_issuer()}/protocol/openid-connect/certs"


def _public_issuer() -> str:
    return settings.oidc_issuer.rstrip("/")


def _server_issuer() -> str:
    """Issuer URL used for back-channel calls (token + JWKS)."""
    internal = settings.oidc_internal_base_url.strip().rstrip("/")
    realm_path = urlparse(settings.oidc_issuer).path.rstrip("/") or "/realms/ansibleai"
    if internal:
        return f"{internal}{realm_path}"
    return _public_issuer()


def _jwks_client() -> PyJWKClient:
    global _JWKS_CLIENT
    if _JWKS_CLIENT is None:
        _JWKS_CLIENT = PyJWKClient(jwks_endpoint(), cache_keys=True, lifespan=300)
    return _JWKS_CLIENT


def reset_jwks_client() -> None:
    """Test helper — drop the cached JWKS client."""
    global _JWKS_CLIENT
    _JWKS_CLIENT = None


def new_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(*, state: str, nonce: str, code_challenge: str) -> str:
    params = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "scope": " ".join(settings.oidc_scope_list),
        "redirect_uri": settings.oidc_redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{authorization_endpoint()}?{urlencode(params)}"


def safe_next_path(raw: str | None) -> str:
    """Only allow same-origin relative paths (open-redirect guard)."""
    candidate = (raw or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    if "://" in candidate or "\\" in candidate:
        return "/"
    return candidate.split("?", 1)[0][:200] or "/"


def exchange_code(code: str, code_verifier: str) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
        "code_verifier": code_verifier,
    }
    try:
        response = httpx.post(
            token_endpoint(),
            data=data,
            timeout=_HTTP_TIMEOUT,
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        log.warning("auth.oidc.token_http_error", error=str(exc))
        raise OidcError("token_exchange") from exc

    if response.status_code >= 400:
        log.warning(
            "auth.oidc.token_rejected",
            status=response.status_code,
        )
        raise OidcError("token_exchange")

    try:
        payload = response.json()
    except ValueError as exc:
        raise OidcError("token_exchange") from exc
    if not isinstance(payload, dict) or not (
        payload.get("id_token") or payload.get("access_token")
    ):
        raise OidcError("token_exchange")
    return payload


def password_grant(username: str, password: str) -> tuple[dict[str, Any] | None, str]:
    """
    Resource Owner Password Credentials against Keycloak.

    Returns (payload, "") on success, or (None, reason) where reason is
    `invalid`, `not_fully_set_up`, or `unavailable`.
    """
    data = {
        "grant_type": "password",
        "username": username,
        "password": password,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
        "scope": " ".join(settings.oidc_scope_list),
    }
    try:
        response = httpx.post(
            token_endpoint(),
            data=data,
            timeout=_HTTP_TIMEOUT,
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        log.warning("auth.oidc.password_http_error", error=str(exc))
        return None, "unavailable"

    if response.status_code < 400:
        try:
            payload = response.json()
        except ValueError:
            return None, "unavailable"
        if isinstance(payload, dict) and (payload.get("id_token") or payload.get("access_token")):
            return payload, ""
        return None, "invalid"

    if response.status_code >= 500:
        return None, "unavailable"

    description = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            description = str(body.get("error_description") or body.get("error") or "")
    except ValueError:
        description = ""
    lowered = description.lower()
    if "not fully set up" in lowered or "required action" in lowered:
        return None, "not_fully_set_up"
    return None, "invalid"


def exchange_password(username: str, password: str) -> tuple[dict[str, Any], bool]:
    """
    Authenticate against Keycloak. Returns (token payload, must_change_password).
    """
    payload, reason = password_grant(username, password)
    if payload is not None:
        return payload, False
    if reason == "unavailable":
        raise OidcError("idp_unavailable", "The identity provider is unreachable.")
    if reason != "not_fully_set_up":
        raise OidcError("invalid_credentials")

    from .keycloak_admin import KeycloakAdminError, suspend_update_password

    try:
        with suspend_update_password(username) as suspended:
            if not suspended:
                raise OidcError("invalid_credentials")
            retry, retry_reason = password_grant(username, password)
            if retry is None:
                if retry_reason == "unavailable":
                    raise OidcError("idp_unavailable", "The identity provider is unreachable.")
                raise OidcError("invalid_credentials")
            return retry, True
    except KeycloakAdminError as exc:
        raise OidcError("idp_admin_unavailable", exc.message) from exc


def claims_from_token_response(payload: dict[str, Any]) -> dict[str, Any]:
    id_token = payload.get("id_token")
    if isinstance(id_token, str) and id_token:
        return decode_id_token(id_token, nonce=None)
    access = payload.get("access_token")
    if isinstance(access, str) and access:
        return decode_access_token(access)
    raise OidcError("invalid_token")


def authenticate_with_password(email: str, password: str) -> tuple[User, bool]:
    """Verify email+password at Keycloak and upsert the application user."""
    tokens, must_change = exchange_password(email, password)
    claims = claims_from_token_response(tokens)
    user, _linked = upsert_user_from_claims(claims)
    return user, must_change


def decode_id_token(token: str, *, nonce: str | None = None) -> dict[str, Any]:
    claims = _decode_jwt(token, audience=settings.oidc_client_id)
    if nonce is None:
        return claims
    token_nonce = claims.get("nonce")
    if not nonce or not isinstance(token_nonce, str) or not secrets.compare_digest(token_nonce, nonce):
        raise OidcError("invalid_nonce", "Sign-in with SSO failed.")
    return claims


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Verify a Keycloak access token for API / Socket.IO Bearer auth.

    Audience is accepted as our client id *or* a matching `azp` (Keycloak
    often puts `aud=account` on access tokens even with an audience mapper).
    """
    claims = _decode_jwt(token, audience=None)
    if not _audience_ok(claims):
        raise OidcError("invalid_audience")
    return claims


def _decode_jwt(token: str, *, audience: str | None) -> dict[str, Any]:
    if not token or token.count(".") != 2:
        raise OidcError("invalid_token")
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        decode_kwargs: dict[str, Any] = {
            "algorithms": _ALLOWED_ALGS,
            "issuer": _public_issuer(),
            "leeway": 30,
            "options": {
                "require": ["exp", "iss", "sub"],
                "verify_aud": audience is not None,
            },
        }
        if audience is not None:
            decode_kwargs["audience"] = audience
        claims = jwt.decode(token, signing_key.key, **decode_kwargs)
    except OidcError:
        raise
    except jwt.PyJWTError as exc:
        log.info("auth.oidc.jwt_invalid", error=type(exc).__name__)
        raise OidcError("invalid_token") from exc
    if not isinstance(claims, dict) or not claims.get("sub"):
        raise OidcError("invalid_token")
    return claims


def _audience_ok(claims: dict[str, Any]) -> bool:
    client_id = settings.oidc_client_id
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud] if aud else []
    if client_id in audiences:
        return True
    azp = claims.get("azp")
    return isinstance(azp, str) and azp == client_id


def claims_are_admin(claims: dict[str, Any]) -> bool:
    admin_group = settings.oidc_admin_group.lstrip("/")
    groups = claims.get("groups") or []
    if isinstance(groups, list):
        normalized = {str(g).lstrip("/") for g in groups}
        if admin_group in normalized:
            return True
    realm_roles = (claims.get("realm_access") or {}).get("roles") or []
    if settings.oidc_admin_role in realm_roles:
        return True
    resource = (claims.get("resource_access") or {}).get(settings.oidc_client_id) or {}
    client_roles = resource.get("roles") or []
    return settings.oidc_admin_role in client_roles


def upsert_user_from_claims(claims: dict[str, Any]) -> tuple[User, bool]:
    """
    Resolve or create the application user for a verified Keycloak identity.

    Returns (user, linked) where `linked` is True when an existing local
    row was associated with this `sub` on this call.
    """
    sub = str(claims.get("sub") or "")
    if not sub:
        raise OidcError("invalid_token")

    existing = User.query.filter_by(provider=PROVIDER_KEYCLOAK, external_id=sub).first()
    if existing is not None:
        _apply_admin_promotion(existing, claims)
        _refresh_profile(existing, claims)
        return existing, False

    email = _verified_email(claims)
    by_email = User.query.filter_by(email=email).first()
    if by_email is not None:
        _link_account(by_email, sub)
        _apply_admin_promotion(by_email, claims)
        _refresh_profile(by_email, claims)
        return by_email, True

    user = User(
        email=email,
        display_name=_display_name(claims, email),
        password_hash=None,
        role=ROLE_ADMIN if _should_promote(claims) else ROLE_USER,
        is_active=True,
        provider=PROVIDER_KEYCLOAK,
        external_id=sub,
        email_verified_at=utcnow(),
    )
    db.session.add(user)
    db.session.flush()
    return user, False


def user_from_access_token(token: str) -> User | None:
    """Map a Bearer token to an active application user, or None."""
    try:
        claims = decode_access_token(token)
        user, _linked = upsert_user_from_claims(claims)
    except OidcError:
        return None
    if not user.is_active or user.is_locked:
        return None
    return user


def _verified_email(claims: dict[str, Any]) -> str:
    raw = str(claims.get("email") or "").strip().lower()
    verified = claims.get("email_verified")
    is_verified = verified is True or str(verified).lower() == "true"
    if not is_verified and settings.oidc_require_email_verified:
        raise OidcError(
            "email_unverified",
            "Your identity provider email is not verified yet.",
        )
    if not raw or "@" not in raw or len(raw) > 255:
        raise OidcError("email_missing", "SSO did not provide a verified email address.")
    if not is_verified:
        log.warning("auth.oidc.email_unverified_accepted", email=raw)
    return raw


def _link_account(user: User, sub: str) -> None:
    user.provider = PROVIDER_KEYCLOAK
    user.external_id = sub
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    if (
        settings.oidc_retire_local_password
        and user.email.lower() not in settings.break_glass_emails
    ):
        user.password_hash = None


def _should_promote(claims: dict[str, Any]) -> bool:
    return settings.oidc_map_app_admin and claims_are_admin(claims)


def _apply_admin_promotion(user: User, claims: dict[str, Any]) -> None:
    """Promote from Keycloak groups when enabled; never auto-demote."""
    if _should_promote(claims) and user.role != ROLE_ADMIN:
        user.role = ROLE_ADMIN
        log.info("auth.oidc.role_promoted", user_id=user.id)


def _refresh_profile(user: User, claims: dict[str, Any]) -> None:
    name = _display_name(claims, user.email)
    if name and not user.display_name:
        user.display_name = name


def _display_name(claims: dict[str, Any], email: str) -> str:
    name = str(claims.get("name") or claims.get("preferred_username") or "").strip()
    if name:
        return name[:120]
    local = email.split("@", 1)[0]
    return local[:120]


__all__ = [
    "OIDC_NEXT_KEY",
    "OIDC_NONCE_KEY",
    "OIDC_STATE_KEY",
    "OIDC_VERIFIER_KEY",
    "OidcError",
    "authenticate_with_password",
    "authorization_endpoint",
    "build_authorization_url",
    "claims_are_admin",
    "claims_from_token_response",
    "decode_access_token",
    "decode_id_token",
    "exchange_code",
    "exchange_password",
    "jwks_endpoint",
    "new_pkce_pair",
    "oidc_available",
    "password_grant",
    "reset_jwks_client",
    "safe_next_path",
    "token_endpoint",
    "upsert_user_from_claims",
    "user_from_access_token",
]
