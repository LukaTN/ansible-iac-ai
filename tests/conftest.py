"""Pytest bootstrap: project root on sys.path, cwd, and a hermetic config.

Environment defaults are set *before* any project module is imported, so
`config.Settings` (which validates at import time) builds against a
throwaway SQLite database rather than the developer's MySQL instance. That
keeps the suite runnable with no external services.
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

# A file-backed SQLite database rather than ":memory:": Flask-SQLAlchemy
# hands out a new connection per context, and an in-memory database would
# be empty in each one.
_TEST_DB = os.path.join(tempfile.gettempdir(), "ansibleai_test.sqlite3")

_TEST_ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": f"sqlite:///{_TEST_DB}",
    "SECRET_KEY": "test-secret-key-not-used-outside-the-test-suite",
    "REGISTRATION_MODE": "open",
    "REQUIRE_ADMIN_APPROVAL": "false",
    "RATE_LIMIT_ENABLED": "false",
    "LOG_LEVEL": "WARNING",
    "LOG_FORMAT": "console",
    "BOOTSTRAP_ADMIN_EMAIL": "",
    "BOOTSTRAP_ADMIN_PASSWORD": "",
}

# setdefault so a caller can still override, e.g. to run against MySQL.
for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

# Flask fixtures (app, client, make_user, login, admin_client). Registered
# as a plugin rather than imported so the environment above is already in
# place before any project module loads.
pytest_plugins = ["tests.fixtures_app"]
