"""
=============================================================
  AnsibleAI — Keycloak Admin API (BFF-only)

  Used to finish a temporary-password login and to rotate a member
  password without sending the browser to Keycloak. The SPA never
  sees these credentials.

  Token preference:
    1. Client-credentials on the confidential app client (service account).
    2. Resource-owner grant on `admin-cli` in the master realm using
       KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD.
=============================================================
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

import httpx

from config import settings
from logging_setup import get_logger

log = get_logger(__name__)

_HTTP_TIMEOUT = 10.0
UPDATE_PASSWORD = "UPDATE_PASSWORD"  # noqa: S105 — Keycloak required-action name


class KeycloakAdminError(Exception):
    """Admin API is missing, unauthorized, or the user cannot be resolved."""

    def __init__(self, code: str, message: str = "Identity administration is unavailable.") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _internal_origin() -> str:
    internal = settings.oidc_internal_base_url.strip().rstrip("/")
    if internal:
        return internal
    parsed = urlparse(settings.oidc_issuer)
    return f"{parsed.scheme}://{parsed.netloc}"


def realm_name() -> str:
    path = urlparse(settings.oidc_issuer).path.rstrip("/")
    if "/realms/" in path:
        return path.rsplit("/realms/", 1)[-1] or "ansibleai"
    return "ansibleai"


def admin_api_base() -> str:
    return f"{_internal_origin()}/admin/realms/{realm_name()}"


def _token_endpoint(realm: str) -> str:
    return f"{_internal_origin()}/realms/{realm}/protocol/openid-connect/token"


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any] | None:
    try:
        response = httpx.post(
            url,
            data=data,
            timeout=_HTTP_TIMEOUT,
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        log.warning("auth.keycloak_admin.token_http_error", error=str(exc))
        return None
    if response.status_code >= 400:
        log.warning("auth.keycloak_admin.token_rejected", status=response.status_code)
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or not payload.get("access_token"):
        return None
    return payload


def admin_access_token() -> str:
    """Bearer token for the realm Admin API, or raise KeycloakAdminError."""
    client_id = settings.oidc_client_id.strip()
    client_secret = settings.oidc_client_secret.strip()
    if client_id and client_secret:
        payload = _post_form(
            _token_endpoint(realm_name()),
            {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if payload:
            return str(payload["access_token"])

    username = settings.keycloak_admin.strip()
    password = settings.keycloak_admin_password
    if username and password:
        payload = _post_form(
            _token_endpoint("master"),
            {
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": username,
                "password": password,
            },
        )
        if payload:
            return str(payload["access_token"])

    raise KeycloakAdminError("idp_admin_unavailable")


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {admin_access_token()}",
        "Accept": "application/json",
    }


def find_user_by_username(username: str) -> dict[str, Any] | None:
    """Resolve a Keycloak user by email or username."""
    needle = (username or "").strip()
    if not needle:
        return None
    try:
        headers = _auth_headers()
        for params in (
            {"email": needle, "exact": "true"},
            {"username": needle, "exact": "true"},
        ):
            response = httpx.get(
                f"{admin_api_base()}/users",
                params=params,
                timeout=_HTTP_TIMEOUT,
                headers=headers,
            )
            if response.status_code >= 400:
                log.warning(
                    "auth.keycloak_admin.user_lookup_failed",
                    status=response.status_code,
                )
                continue
            rows = response.json()
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                return rows[0]
    except (httpx.HTTPError, KeycloakAdminError, ValueError) as exc:
        log.warning("auth.keycloak_admin.user_lookup_error", error=str(exc))
        if isinstance(exc, KeycloakAdminError):
            raise
    return None


def set_required_actions(user_id: str, actions: list[str]) -> None:
    try:
        response = httpx.put(
            f"{admin_api_base()}/users/{user_id}",
            json={"requiredActions": actions},
            timeout=_HTTP_TIMEOUT,
            headers={**_auth_headers(), "Content-Type": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise KeycloakAdminError("idp_admin_unavailable") from exc
    if response.status_code >= 400:
        log.warning("auth.keycloak_admin.set_actions_failed", status=response.status_code)
        raise KeycloakAdminError("idp_admin_unavailable")


def set_permanent_password(user_id: str, password: str) -> None:
    try:
        response = httpx.put(
            f"{admin_api_base()}/users/{user_id}/reset-password",
            json={"type": "password", "value": password, "temporary": False},
            timeout=_HTTP_TIMEOUT,
            headers={**_auth_headers(), "Content-Type": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise KeycloakAdminError("idp_admin_unavailable") from exc
    if response.status_code >= 400:
        log.warning("auth.keycloak_admin.reset_password_failed", status=response.status_code)
        raise KeycloakAdminError("idp_admin_unavailable")
    user = httpx.get(
        f"{admin_api_base()}/users/{user_id}",
        timeout=_HTTP_TIMEOUT,
        headers=_auth_headers(),
    )
    if user.status_code < 400:
        try:
            body = user.json()
        except ValueError:
            body = {}
        actions = [
            a
            for a in (body.get("requiredActions") or [])
            if a != UPDATE_PASSWORD
        ]
        set_required_actions(user_id, actions)


@contextmanager
def suspend_update_password(username: str) -> Iterator[bool]:
    """
    Briefly drop UPDATE_PASSWORD so a password grant can succeed.

    Yields True when the action was present and cleared. Restores the
    original required-action list unless the caller marks the login as
    successful via `keep_update_password_on_success`.
    """
    user = find_user_by_username(username)
    if user is None or not user.get("id"):
        yield False
        return
    user_id = str(user["id"])
    original = [str(a) for a in (user.get("requiredActions") or [])]
    if UPDATE_PASSWORD not in original:
        yield False
        return
    remaining = [a for a in original if a != UPDATE_PASSWORD]
    set_required_actions(user_id, remaining)
    state = {"restore": True, "success": False}
    try:
        yield True
        state["success"] = True
    finally:
        if state["success"]:
            # Keep the temp-password flag until the member changes it in-app.
            set_required_actions(user_id, [*remaining, UPDATE_PASSWORD])
        else:
            set_required_actions(user_id, original)


def change_keycloak_password(username: str, new_password: str) -> None:
    user = find_user_by_username(username)
    if user is None or not user.get("id"):
        raise KeycloakAdminError("account_not_found", "Account not found.")
    set_permanent_password(str(user["id"]), new_password)


__all__ = [
    "UPDATE_PASSWORD",
    "KeycloakAdminError",
    "admin_access_token",
    "change_keycloak_password",
    "find_user_by_username",
    "set_permanent_password",
    "set_required_actions",
    "suspend_update_password",
]
