"""
Authentication tests: password policy, hashing, login flow, lockout,
and session revocation.
"""

from __future__ import annotations

import pytest

from auth.passwords import (
    PasswordPolicyError,
    hash_password,
    validate_password,
    verify_password,
)

GOOD_PASSWORD = "correct-horse-battery-staple-42"
OTHER_PASSWORD = "another-perfectly-fine-passphrase-7"


# ─────────────────────────────────────────────
#  Hashing
# ─────────────────────────────────────────────

def test_hash_is_argon2id_and_salted():
    first = hash_password(GOOD_PASSWORD)
    second = hash_password(GOOD_PASSWORD)

    assert first.startswith("$argon2id$")
    # Distinct salts must produce distinct hashes for the same password.
    assert first != second
    assert verify_password(GOOD_PASSWORD, first)
    assert verify_password(GOOD_PASSWORD, second)


def test_wrong_password_is_rejected():
    stored = hash_password(GOOD_PASSWORD)
    assert not verify_password(OTHER_PASSWORD, stored)
    assert not verify_password("", stored)


def test_verify_against_missing_hash_is_false_not_error():
    """External-identity accounts have no local hash and must not crash."""
    assert verify_password(GOOD_PASSWORD, None) is False
    assert verify_password(GOOD_PASSWORD, "") is False


def test_unicode_equivalent_passwords_verify():
    """NFKC normalization: canonically equivalent input must still match."""
    composed = "caf\u00e9-passphrase-longer"       # café with U+00E9
    decomposed = "cafe\u0301-passphrase-longer"    # e + combining acute
    assert verify_password(decomposed, hash_password(composed))


def test_long_password_is_not_silently_truncated():
    """The bcrypt 72-byte truncation footgun must not exist here."""
    base = "x" * 100
    stored = hash_password(base + "-tail-A")
    assert not verify_password(base + "-tail-B", stored)


# ─────────────────────────────────────────────
#  Policy
# ─────────────────────────────────────────────

def test_policy_accepts_a_long_passphrase():
    validate_password(GOOD_PASSWORD, email="user@example.com")


@pytest.mark.parametrize(
    "password, reason",
    [
        ("short", "below the minimum length"),
        ("password1234", "known breach list"),
        ("Passw0rd1234", "breach list after stripping digits"),
        ("aaaaaaaaaaaaaa", "too few distinct characters"),
        ("abcabcabcabcabcabc", "repeated sequence"),
    ],
)
def test_policy_rejects_weak_passwords(password, reason):
    with pytest.raises(PasswordPolicyError):
        validate_password(password, email="user@example.com")


def test_policy_rejects_password_derived_from_the_account():
    with pytest.raises(PasswordPolicyError):
        validate_password("ahmedbenali-secret", email="ahmedbenali@example.com")

    with pytest.raises(PasswordPolicyError):
        validate_password("my-corporation-login", email="x@y.com", display_name="Corporation")


def test_policy_rejects_oversized_input():
    with pytest.raises(PasswordPolicyError):
        validate_password("a" * 5000, email="user@example.com")


# ─────────────────────────────────────────────
#  Login flow
# ─────────────────────────────────────────────

def test_login_succeeds_and_returns_the_user(client, make_user):
    make_user("member@example.com", GOOD_PASSWORD)

    resp = client.post(
        "/api/auth/login",
        json={"email": "member@example.com", "password": GOOD_PASSWORD},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "member@example.com"
    # The hash must never cross the wire.
    assert "password_hash" not in body["user"]


def test_login_is_case_insensitive_on_email(client, make_user):
    make_user("mixed@example.com", GOOD_PASSWORD)
    resp = client.post(
        "/api/auth/login",
        json={"email": "MiXeD@Example.COM", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 200


def test_unknown_email_and_wrong_password_are_indistinguishable(client, make_user):
    """Identical responses, so login cannot be used to enumerate accounts."""
    make_user("real@example.com", GOOD_PASSWORD)

    unknown = client.post(
        "/api/auth/login",
        json={"email": "ghost@example.com", "password": GOOD_PASSWORD},
    )
    wrong = client.post(
        "/api/auth/login",
        json={"email": "real@example.com", "password": OTHER_PASSWORD},
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.get_json() == wrong.get_json()


def test_inactive_account_cannot_log_in(client, make_user):
    make_user("pending@example.com", GOOD_PASSWORD, is_active=False)

    resp = client.post(
        "/api/auth/login",
        json={"email": "pending@example.com", "password": GOOD_PASSWORD},
    )

    assert resp.status_code == 403
    assert resp.get_json()["code"] == "account_inactive"


def test_repeated_failures_lock_the_account(app, client, make_user):
    from config import settings
    from models import User

    make_user("target@example.com", GOOD_PASSWORD)

    for _ in range(settings.account_lockout_threshold):
        client.post(
            "/api/auth/login",
            json={"email": "target@example.com", "password": "wrong-password-here"},
        )

    with app.app_context():
        assert User.query.filter_by(email="target@example.com").one().is_locked

    # The correct password is now refused too, which is the point.
    resp = client.post(
        "/api/auth/login",
        json={"email": "target@example.com", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 423
    assert resp.get_json()["code"] == "account_locked"


def test_successful_login_clears_the_failure_counter(app, client, make_user):
    from models import User

    make_user("resilient@example.com", GOOD_PASSWORD)

    client.post(
        "/api/auth/login",
        json={"email": "resilient@example.com", "password": "wrong-password-here"},
    )
    client.post(
        "/api/auth/login",
        json={"email": "resilient@example.com", "password": GOOD_PASSWORD},
    )

    with app.app_context():
        user = User.query.filter_by(email="resilient@example.com").one()
        assert user.failed_login_count == 0
        assert user.last_login_at is not None


# ─────────────────────────────────────────────
#  Session lifecycle
# ─────────────────────────────────────────────

def test_me_reports_anonymous_without_a_session(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.get_json() == {"authenticated": False, "user": None}


def test_logout_ends_the_session(client, make_user, login):
    make_user("bye@example.com", GOOD_PASSWORD)
    login("bye@example.com", GOOD_PASSWORD)

    assert client.get("/api/auth/me").get_json()["authenticated"] is True

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").get_json()["authenticated"] is False


def test_password_change_revokes_other_sessions(app, client, make_user, login):
    """
    A second client holding a valid session must lose it when the password
    changes — that is the whole point of the session epoch.
    """
    make_user("rotate@example.com", GOOD_PASSWORD)
    login("rotate@example.com", GOOD_PASSWORD)

    other = app.test_client()
    other.post(
        "/api/auth/login",
        json={"email": "rotate@example.com", "password": GOOD_PASSWORD},
    )
    assert other.get("/api/auth/me").get_json()["authenticated"] is True

    resp = client.post(
        "/api/auth/password/change",
        json={"current_password": GOOD_PASSWORD, "new_password": OTHER_PASSWORD},
    )
    assert resp.status_code == 200

    # The other session is now invalid...
    assert other.get("/api/auth/me").get_json()["authenticated"] is False
    # ...while the tab that performed the change stays signed in.
    assert client.get("/api/auth/me").get_json()["authenticated"] is True

    # And the new password is the one that works.
    fresh = app.test_client()
    assert fresh.post(
        "/api/auth/login",
        json={"email": "rotate@example.com", "password": OTHER_PASSWORD},
    ).status_code == 200
    assert fresh.post(
        "/api/auth/login",
        json={"email": "rotate@example.com", "password": GOOD_PASSWORD},
    ).status_code == 401


def test_password_change_requires_the_current_password(client, make_user, login):
    make_user("careful@example.com", GOOD_PASSWORD)
    login("careful@example.com", GOOD_PASSWORD)

    resp = client.post(
        "/api/auth/password/change",
        json={"current_password": "not-the-current-one", "new_password": OTHER_PASSWORD},
    )
    assert resp.status_code == 403


def test_password_change_enforces_the_policy(client, make_user, login):
    make_user("weak@example.com", GOOD_PASSWORD)
    login("weak@example.com", GOOD_PASSWORD)

    resp = client.post(
        "/api/auth/password/change",
        json={"current_password": GOOD_PASSWORD, "new_password": "password123"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "weak_password"


# ─────────────────────────────────────────────
#  Registration
# ─────────────────────────────────────────────

def test_registration_creates_a_usable_account(client):
    resp = client.post(
        "/api/auth/register",
        json={"email": "newcomer@example.com", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 201
    assert resp.get_json()["user"]["email"] == "newcomer@example.com"
    assert client.get("/api/auth/me").get_json()["authenticated"] is True


def test_first_account_becomes_administrator(client):
    resp = client.post(
        "/api/auth/register",
        json={"email": "founder@example.com", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 201
    assert resp.get_json()["user"]["role"] == "admin"


def test_second_account_is_a_regular_user(client, make_user):
    make_user("existing@example.com", GOOD_PASSWORD)
    resp = client.post(
        "/api/auth/register",
        json={"email": "second@example.com", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 201
    assert resp.get_json()["user"]["role"] == "user"


def test_duplicate_registration_does_not_reveal_the_account(client, make_user):
    """A taken address must not be distinguishable from a new one."""
    make_user("taken@example.com", GOOD_PASSWORD)

    resp = client.post(
        "/api/auth/register",
        json={"email": "taken@example.com", "password": OTHER_PASSWORD},
    )

    assert resp.status_code == 202
    assert resp.get_json()["authenticated"] is False
    # The original password must still be the valid one.
    assert client.post(
        "/api/auth/login",
        json={"email": "taken@example.com", "password": GOOD_PASSWORD},
    ).status_code == 200


def test_registration_rejects_a_weak_password(client):
    resp = client.post(
        "/api/auth/register",
        json={"email": "sloppy@example.com", "password": "qwerty"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "weak_password"


def test_registration_rejects_an_invalid_email(client):
    resp = client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 400


def test_domain_restricted_registration(monkeypatch, client):
    from config import settings

    monkeypatch.setattr(settings, "registration_mode", "domain")
    monkeypatch.setattr(settings, "allowed_email_domains", "corp.example")

    blocked = client.post(
        "/api/auth/register",
        json={"email": "outsider@gmail.com", "password": GOOD_PASSWORD},
    )
    assert blocked.status_code == 403
    assert blocked.get_json()["code"] == "registration_disabled"

    allowed = client.post(
        "/api/auth/register",
        json={"email": "insider@corp.example", "password": GOOD_PASSWORD},
    )
    assert allowed.status_code == 201


def test_closed_registration_blocks_signup(monkeypatch, client):
    from config import settings

    monkeypatch.setattr(settings, "registration_mode", "closed")

    resp = client.post(
        "/api/auth/register",
        json={"email": "nope@example.com", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 403


def test_pending_approval_account_cannot_log_in(monkeypatch, client, make_user):
    """With approval required, a new signup is created inactive."""
    from config import settings

    # An account must already exist, otherwise the signup is treated as the
    # first-ever user and auto-promoted.
    make_user("first@example.com", GOOD_PASSWORD)
    monkeypatch.setattr(settings, "require_admin_approval", True)

    resp = client.post(
        "/api/auth/register",
        json={"email": "waiting@example.com", "password": GOOD_PASSWORD},
    )
    assert resp.status_code == 202
    assert resp.get_json()["pending_approval"] is True

    login = client.post(
        "/api/auth/login",
        json={"email": "waiting@example.com", "password": GOOD_PASSWORD},
    )
    assert login.status_code == 403
