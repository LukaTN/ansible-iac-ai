"""
Authorization tests.

The most valuable test here is `test_every_endpoint_requires_authentication`:
it walks the real URL map, so a route added later without a deliberate
allowlist entry fails the build rather than quietly serving data to anyone.
"""

from __future__ import annotations

import pytest

from auth.security import ADMIN_ENDPOINTS, PUBLIC_ENDPOINTS

PASSWORD = "correct-horse-battery-staple-42"


# ─────────────────────────────────────────────
#  Default-deny coverage
# ─────────────────────────────────────────────

def test_public_and_admin_endpoint_names_all_exist(app):
    """A stale allowlist entry is dead config and hides real intent."""
    registered = {rule.endpoint for rule in app.url_map.iter_rules()}

    assert PUBLIC_ENDPOINTS <= registered, (
        f"allowlisted endpoints that do not exist: {sorted(PUBLIC_ENDPOINTS - registered)}"
    )
    assert ADMIN_ENDPOINTS <= registered, (
        f"admin endpoints that do not exist: {sorted(ADMIN_ENDPOINTS - registered)}"
    )


def _protected_get_rules(app):
    """Every GET rule that should demand a session, with no URL parameters."""
    for rule in app.url_map.iter_rules():
        if rule.endpoint in PUBLIC_ENDPOINTS:
            continue
        if "GET" not in (rule.methods or set()):
            continue
        if rule.arguments:
            continue
        yield rule


def test_every_endpoint_requires_authentication(app, client):
    """
    Anonymous GETs to non-public endpoints must return 401.

    This is the regression guard for the default-deny hook: it derives the
    list from the app itself rather than a hand-maintained fixture.
    """
    checked = []
    for rule in _protected_get_rules(app):
        resp = client.get(rule.rule)
        checked.append(rule.rule)
        assert resp.status_code == 401, (
            f"{rule.rule} ({rule.endpoint}) returned {resp.status_code}, "
            "expected 401 for an anonymous caller"
        )
        assert resp.get_json()["code"] == "unauthenticated"

    # Guard against the loop silently matching nothing.
    assert len(checked) >= 5, f"only checked {checked}"


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/api/chat"),
        ("post", "/api/chat/cancel"),
        ("get", "/api/threads"),
        ("delete", "/api/threads"),
        ("get", "/api/threads/1"),
        ("patch", "/api/threads/1"),
        ("delete", "/api/threads/1"),
        ("get", "/stats"),
        ("get", "/rag/status"),
        ("get", "/docs/status"),
        ("post", "/docs/rescrape"),
        ("post", "/docs/check-updates"),
    ],
)
def test_anonymous_access_is_refused(client, method, path):
    resp = getattr(client, method)(path, json={})
    assert resp.status_code == 401


def test_health_probes_stay_public(client):
    """Kubernetes probes must answer before any user exists."""
    assert client.get("/healthz").status_code == 200
    # readyz may report 503 (no knowledge base in tests) but must not 401.
    assert client.get("/readyz").status_code in (200, 503)
    assert client.get("/metrics").status_code == 200


# ─────────────────────────────────────────────
#  Per-user isolation
# ─────────────────────────────────────────────

def _make_thread(app, user_id: int, title: str = "Owned thread") -> int:
    from models import ChatThread, db

    with app.app_context():
        thread = ChatThread(user_id=user_id, title=title)
        db.session.add(thread)
        db.session.commit()
        return thread.id


def test_thread_list_only_returns_own_threads(app, make_user, login, client):
    alice = make_user("alice@example.com", PASSWORD)
    bob = make_user("bob@example.com", PASSWORD)
    _make_thread(app, alice, "alice thread")
    _make_thread(app, bob, "bob thread")

    login("alice@example.com", PASSWORD)
    titles = [t["title"] for t in client.get("/api/threads").get_json()]

    assert titles == ["alice thread"]


def test_reading_another_users_thread_returns_404_not_403(app, make_user, login, client):
    """
    404, not 403: a 403 would confirm the thread exists, letting someone
    enumerate other users' activity by walking IDs.
    """
    make_user("alice@example.com", PASSWORD)
    bob = make_user("bob@example.com", PASSWORD)
    bob_thread = _make_thread(app, bob)

    login("alice@example.com", PASSWORD)

    assert client.get(f"/api/threads/{bob_thread}").status_code == 404


def test_cannot_modify_or_delete_another_users_thread(app, make_user, login, client):
    make_user("alice@example.com", PASSWORD)
    bob = make_user("bob@example.com", PASSWORD)
    bob_thread = _make_thread(app, bob, "bob original")

    login("alice@example.com", PASSWORD)

    assert client.patch(
        f"/api/threads/{bob_thread}", json={"title": "hijacked"}
    ).status_code == 404
    assert client.delete(f"/api/threads/{bob_thread}").status_code == 404

    # And the thread is genuinely untouched.
    from models import ChatThread, db

    with app.app_context():
        assert db.session.get(ChatThread, bob_thread).title == "bob original"


def test_cannot_post_into_another_users_thread(app, make_user, login, client):
    make_user("alice@example.com", PASSWORD)
    bob = make_user("bob@example.com", PASSWORD)
    bob_thread = _make_thread(app, bob)

    login("alice@example.com", PASSWORD)

    resp = client.post(
        "/api/chat", json={"thread_id": bob_thread, "message": "inject"}
    )
    assert resp.status_code == 404

    from models import ChatMessage, db

    with app.app_context():
        assert (
            db.session.query(ChatMessage).filter_by(thread_id=bob_thread).count() == 0
        )


def test_cannot_cancel_another_users_generation(app, make_user, login, client):
    make_user("alice@example.com", PASSWORD)
    bob = make_user("bob@example.com", PASSWORD)
    bob_thread = _make_thread(app, bob)

    login("alice@example.com", PASSWORD)

    assert client.post(
        "/api/chat/cancel", json={"thread_id": bob_thread}
    ).status_code == 404


def test_clearing_threads_only_affects_the_caller(app, make_user, admin_client):
    """`DELETE /api/threads` must not wipe the table for everyone."""
    from models import ChatThread, User

    with app.app_context():
        admin_id = User.query.filter_by(email="admin@example.com").one().id

    bob = make_user("bob@example.com", PASSWORD)
    _make_thread(app, admin_id, "admin thread")
    _make_thread(app, bob, "bob thread")

    assert admin_client.delete("/api/threads").status_code == 200

    with app.app_context():
        remaining = [t.title for t in ChatThread.query.all()]
    assert remaining == ["bob thread"]


# ─────────────────────────────────────────────
#  Admin gating
# ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/docs/rescrape"),
        ("post", "/docs/check-updates"),
        ("post", "/docs/rollback/restore"),
    ],
)
def test_regular_user_cannot_reach_admin_endpoints(make_user, login, client, method, path):
    make_user("plain@example.com", PASSWORD)
    login("plain@example.com", PASSWORD)

    resp = getattr(client, method)(path, json={})
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "forbidden"


def test_clearing_own_threads_is_not_admin_only(app, make_user, login, client):
    """Destructive but self-scoped, so a member may do it."""
    user_id = make_user("plain@example.com", PASSWORD)
    _make_thread(app, user_id, "mine")
    login("plain@example.com", PASSWORD)

    assert client.delete("/api/threads").status_code == 200


def test_denied_access_is_audited(app, make_user, login, client):
    from models import AuditEvent

    make_user("plain@example.com", PASSWORD)
    login("plain@example.com", PASSWORD)
    client.post("/docs/rescrape", json={})

    with app.app_context():
        events = [e.event for e in AuditEvent.query.all()]
    assert "authz.denied" in events


# ─────────────────────────────────────────────
#  CSRF
# ─────────────────────────────────────────────

def test_state_changing_request_without_csrf_token_is_rejected(app, make_user):
    """
    With cookie authentication, CSRF protection is what stops another
    origin from issuing writes using the victim's session.
    """
    make_user("csrf@example.com", PASSWORD)

    app.config["WTF_CSRF_ENABLED"] = True
    try:
        c = app.test_client()
        # Logging in is itself CSRF-protected, so fetch a token first.
        token = c.get("/api/auth/csrf").get_json()["csrf_token"]
        assert c.post(
            "/api/auth/login",
            json={"email": "csrf@example.com", "password": PASSWORD},
            headers={"X-CSRFToken": token},
        ).status_code == 200

        # An authenticated write with no token must fail.
        blocked = c.post("/api/threads/1", json={"title": "x"})
        assert blocked.status_code in (403, 405)

        no_token = c.delete("/api/threads")
        assert no_token.status_code == 403
        assert no_token.get_json()["code"] == "csrf"
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_csrf_endpoint_issues_a_token(client):
    body = client.get("/api/auth/csrf").get_json()
    assert body["csrf_token"]


def test_csrf_cookie_is_readable_by_the_spa(client):
    """The token cookie must not be HttpOnly, or the SPA cannot echo it back."""
    resp = client.get("/api/auth/me")

    csrf_cookies = [
        c for c in resp.headers.getlist("Set-Cookie") if c.startswith("csrf_token=")
    ]
    assert csrf_cookies, f"no csrf_token cookie in {resp.headers.getlist('Set-Cookie')}"
    assert "HttpOnly" not in csrf_cookies[0]
    assert "SameSite=Lax" in csrf_cookies[0]
