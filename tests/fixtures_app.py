"""
Shared Flask fixtures.

Exposed to tests through `tests/conftest.py`'s plugin registration so
`app`, `client`, `make_user`, and `login` are available without imports.

The application object is a module-level singleton, so the schema is
rebuilt between tests rather than the app being recreated. That keeps
tests independent without an application-factory refactor.
"""

from __future__ import annotations

import pytest

STRONG_PASSWORD = "correct-horse-battery-staple-42"


@pytest.fixture(scope="session")
def app():
    """The application, pointed at the throwaway SQLite database."""
    from app import app as flask_app

    # CSRF is exercised by a dedicated test that re-enables it; leaving it
    # on everywhere would mean every test had to fetch a token first.
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return flask_app


@pytest.fixture(autouse=True)
def _clean_schema(app):
    """Drop and recreate every table around each test.

    Postgres-only tables (document_chunks, index_meta) are excluded because
    SQLite cannot handle JSONB or pgvector types.
    """
    from models import db

    SKIP_TABLES = {"document_chunks", "index_meta"}

    with app.app_context():
        # Only drop/create tables that SQLite can handle
        tables_to_manage = [
            t for t in db.metadata.sorted_tables
            if t.name not in SKIP_TABLES
        ]
        db.metadata.drop_all(db.engine, tables=tables_to_manage)
        db.metadata.create_all(db.engine, tables=tables_to_manage)
    yield
    with app.app_context():
        db.session.remove()
        db.metadata.drop_all(db.engine, tables=tables_to_manage)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_user(app):
    """Create a user directly, bypassing the registration endpoint."""

    def _make(
        email: str,
        password: str | None = STRONG_PASSWORD,
        *,
        role: str = "user",
        is_active: bool = True,
    ):
        from auth.passwords import hash_password
        from models import PROVIDER_LOCAL, User, db, utcnow

        with app.app_context():
            user = User(
                email=email.lower(),
                display_name=email.split("@")[0],
                password_hash=hash_password(password) if password else None,
                role=role,
                is_active=is_active,
                provider=PROVIDER_LOCAL,
                password_changed_at=utcnow(),
            )
            db.session.add(user)
            db.session.commit()
            return user.id

    return _make


@pytest.fixture
def login(client):
    """Authenticate `client`, asserting the login actually succeeded."""

    def _login(email: str, password: str = STRONG_PASSWORD):
        resp = client.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200, f"login failed: {resp.get_json()}"
        return resp.get_json()

    return _login


@pytest.fixture
def admin_client(app, make_user):
    """A client signed in as an administrator."""
    make_user("admin@example.com", STRONG_PASSWORD, role="admin")
    c = app.test_client()
    resp = c.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": STRONG_PASSWORD},
    )
    assert resp.status_code == 200, resp.get_json()
    return c
