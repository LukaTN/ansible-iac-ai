"""Phase 5: OIDC account linking, auth config, JWT bearer, SSO modes."""

from __future__ import annotations

import pytest

from auth.oidc import OidcError, claims_are_admin, safe_next_path, upsert_user_from_claims
from models import PROVIDER_KEYCLOAK, ROLE_ADMIN, ROLE_USER, User, db

GOOD_PASSWORD = "correct-horse-battery-staple-42"


def _claims(**overrides):
    base = {
        "sub": "kc-user-1",
        "email": "sso@example.com",
        "email_verified": True,
        "name": "SSO User",
        "groups": ["ansibleai-users"],
    }
    base.update(overrides)
    return base


def test_auth_config_is_public_and_local_by_default(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "oidc_client_secret", "")
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["auth_mode"] == "local"
    assert body["oidc_enabled"] is False
    assert body["local_login_enabled"] is True
    assert body["oidc_login_url"] is None
    assert body["app_admin_ui"] is True
    assert "client_secret" not in body
    assert "oidc_client_secret" not in body


def test_oidc_login_is_404_when_sso_is_off(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "auth_mode", "local")
    resp = client.get("/api/auth/oidc/login")
    assert resp.status_code == 404
    assert resp.get_json()["code"] == "oidc_disabled"


def test_oidc_callback_is_404_when_sso_is_off(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "auth_mode", "local")
    resp = client.get("/api/auth/oidc/callback?code=x&state=y")
    assert resp.status_code == 404


def test_safe_next_path_rejects_open_redirects():
    assert safe_next_path("/threads") == "/threads"
    assert safe_next_path("https://evil.example/") == "/"
    assert safe_next_path("//evil.example") == "/"
    assert safe_next_path("/\\evil") == "/"
    assert safe_next_path(None) == "/"


def test_upsert_creates_sso_user_without_password(app):
    with app.app_context():
        user, linked = upsert_user_from_claims(_claims())
        db.session.commit()
        assert linked is False
        assert user.provider == PROVIDER_KEYCLOAK
        assert user.external_id == "kc-user-1"
        assert user.password_hash is None
        assert user.role == ROLE_USER
        assert user.email_verified_at is not None


def test_upsert_links_existing_email_and_retires_password(app, make_user):
    make_user("sso@example.com", GOOD_PASSWORD)
    with app.app_context():
        user, linked = upsert_user_from_claims(_claims())
        db.session.commit()
        assert linked is True
        assert user.provider == PROVIDER_KEYCLOAK
        assert user.external_id == "kc-user-1"
        assert user.password_hash is None


def test_upsert_does_not_demote_local_admin(app, make_user):
    make_user("sso@example.com", GOOD_PASSWORD, role="admin")
    with app.app_context():
        user, _ = upsert_user_from_claims(_claims(groups=["ansibleai-users"]))
        db.session.commit()
        assert user.role == ROLE_ADMIN


def test_upsert_promotes_admin_group(app, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "oidc_map_app_admin", True)
    with app.app_context():
        user, _ = upsert_user_from_claims(_claims(groups=["ansibleai-admins"]))
        db.session.commit()
        assert user.role == ROLE_ADMIN


def test_upsert_does_not_promote_when_mapping_disabled(app):
    with app.app_context():
        user, _ = upsert_user_from_claims(_claims(groups=["ansibleai-admins"], sub="kc-no-promo"))
        db.session.commit()
        assert user.role == ROLE_USER


def test_upsert_rejects_unverified_email(app, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "oidc_require_email_verified", True)
    with app.app_context():
        with pytest.raises(OidcError) as exc:
            upsert_user_from_claims(_claims(email_verified=False))
        assert exc.value.code == "email_unverified"


def test_upsert_accepts_unverified_when_policy_allows(app, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "oidc_require_email_verified", False)
    with app.app_context():
        user, linked = upsert_user_from_claims(_claims(email_verified=False, sub="kc-uv-1"))
        db.session.commit()
        assert linked is False
        assert user.email == "sso@example.com"


def test_claims_are_admin_from_realm_role():
    assert claims_are_admin({"realm_access": {"roles": ["ansibleai-admin"]}})
    assert not claims_are_admin({"groups": ["ansibleai-users"]})


def test_oidc_mode_rejects_password_login_except_break_glass(app, client, make_user, monkeypatch):
    from config import settings

    make_user("member@example.com", GOOD_PASSWORD)
    make_user("break@example.com", GOOD_PASSWORD)
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_client_secret", "")
    monkeypatch.setattr(settings, "auth_break_glass_emails", "break@example.com")

    blocked = client.post(
        "/api/auth/login",
        json={"email": "member@example.com", "password": GOOD_PASSWORD},
    )
    assert blocked.status_code == 401
    assert blocked.get_json()["code"] == "invalid_credentials"

    allowed = client.post(
        "/api/auth/login",
        json={"email": "break@example.com", "password": GOOD_PASSWORD},
    )
    assert allowed.status_code == 200


def test_oidc_mode_disables_registration(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "auth_mode", "oidc")
    resp = client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "registration_disabled"


def test_password_change_rejected_without_local_hash(app, client, make_user):
    uid = make_user("linked@example.com", GOOD_PASSWORD)
    client.post(
        "/api/auth/login",
        json={"email": "linked@example.com", "password": GOOD_PASSWORD},
    )
    with app.app_context():
        user = db.session.get(User, uid)
        user.password_hash = None
        user.provider = PROVIDER_KEYCLOAK
        db.session.commit()

    resp = client.post(
        "/api/auth/password/change",
        json={"current_password": GOOD_PASSWORD, "new_password": GOOD_PASSWORD + "-x"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "no_local_password"


def test_bearer_token_authenticates_api(app, client, make_user, monkeypatch):
    uid = make_user("api@example.com", GOOD_PASSWORD)
    with app.app_context():
        user = db.session.get(User, uid)
        user.provider = PROVIDER_KEYCLOAK
        user.external_id = "kc-api-1"
        db.session.commit()

    def _fake_user(token: str):
        if token != "good-token":
            return None
        return db.session.get(User, uid)

    monkeypatch.setattr("auth.oidc.user_from_access_token", _fake_user)

    denied = client.get("/api/threads")
    assert denied.status_code == 401

    ok = client.get("/api/threads", headers={"Authorization": "Bearer good-token"})
    assert ok.status_code == 200
    assert ok.get_json() == []


def test_hybrid_config_hides_hosted_ui_and_registration(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "auth_mode", "hybrid")
    monkeypatch.setattr(settings, "oidc_browser_redirect", False)
    resp = client.get("/api/auth/config")
    body = resp.get_json()
    assert body["registration_enabled"] is False
    assert body["app_admin_ui"] is False
    assert body["oidc_login_url"] is None
    assert body["local_login_enabled"] is True


def test_hybrid_disables_registration(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "auth_mode", "hybrid")
    resp = client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "registration_disabled"


def test_hosted_oidc_login_stays_off_without_browser_redirect(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "auth_mode", "hybrid")
    monkeypatch.setattr("auth.routes.oidc_available", lambda: True)
    monkeypatch.setattr(settings, "oidc_browser_redirect", False)
    resp = client.get("/api/auth/oidc/login")
    assert resp.status_code == 404
    assert resp.get_json()["code"] == "oidc_disabled"


def test_hybrid_login_uses_keycloak_password_grant(app, client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "auth_mode", "hybrid")
    monkeypatch.setattr("auth.routes.oidc_available", lambda: True)

    created = {}

    def fake_auth(email: str, password: str):
        from models import db

        assert email == "member@example.com"
        assert password == GOOD_PASSWORD
        user = User(
            email=email,
            display_name="Member",
            password_hash=None,
            role=ROLE_USER,
            is_active=True,
            provider=PROVIDER_KEYCLOAK,
            external_id="kc-ropc-1",
        )
        db.session.add(user)
        db.session.flush()
        created["id"] = user.id
        return user, False

    monkeypatch.setattr("auth.routes.authenticate_with_password", fake_auth)
    resp = client.post(
        "/api/auth/login",
        json={"email": "member@example.com", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "member@example.com"
    assert body["user"]["provider"] == PROVIDER_KEYCLOAK
    assert body["user"]["must_change_password"] is False
    assert body["user"]["can_change_password"] is True


def test_temporary_password_blocks_workspace_until_changed(app, client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "auth_mode", "hybrid")
    monkeypatch.setattr("auth.routes.oidc_available", lambda: True)

    def fake_auth(email: str, password: str):
        from models import db

        user = User(
            email=email,
            display_name="Temp",
            password_hash=None,
            role=ROLE_USER,
            is_active=True,
            provider=PROVIDER_KEYCLOAK,
            external_id="kc-temp-1",
        )
        db.session.add(user)
        db.session.flush()
        return user, True

    monkeypatch.setattr("auth.routes.authenticate_with_password", fake_auth)
    login = client.post(
        "/api/auth/login",
        json={"email": "temp@example.com", "password": GOOD_PASSWORD},
    )
    assert login.status_code == 200
    assert login.get_json()["user"]["must_change_password"] is True

    blocked = client.get("/api/threads")
    assert blocked.status_code == 403
    assert blocked.get_json()["code"] == "password_change_required"

    profile = client.get("/api/auth/profile")
    assert profile.status_code == 200
    assert profile.get_json()["user"]["must_change_password"] is True
    assert "usage" in profile.get_json()
    assert "activity" in profile.get_json()
    assert "observability" not in profile.get_json()


def test_password_grant_detects_not_fully_set_up(monkeypatch):
    from auth.oidc import password_grant

    class _Resp:
        status_code = 400

        def json(self):
            return {
                "error": "invalid_grant",
                "error_description": "Account is not fully set up",
            }

    monkeypatch.setattr("auth.oidc.httpx.post", lambda *args, **kwargs: _Resp())
    payload, reason = password_grant("user@example.com", "secret")
    assert payload is None
    assert reason == "not_fully_set_up"
