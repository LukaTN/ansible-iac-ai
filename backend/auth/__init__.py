"""
=============================================================
  AnsibleAI — Authentication and authorization

  Phase 0 authenticates against a local argon2id password. Phase 5b
  authenticates members against Keycloak from the AnsibleAI login page
  (resource-owner password grant). Default remains local so the test
  suite and host `python app.py` need no identity provider.
=============================================================
"""

from __future__ import annotations

from .security import (
    ADMIN_ENDPOINTS,
    PUBLIC_ENDPOINTS,
    admin_required,
    audit_admin_action,
    csrf,
    init_security,
    login_manager,
    registered_endpoints,
)

__all__ = [
    "ADMIN_ENDPOINTS",
    "PUBLIC_ENDPOINTS",
    "admin_required",
    "audit_admin_action",
    "csrf",
    "init_security",
    "login_manager",
    "registered_endpoints",
]


def register_auth(app) -> None:  # type: ignore[no-untyped-def]
    """
    Install every security hook and the auth blueprint onto `app`.

    Order matters: init_security constructs the rate limiter, and the
    per-route limits in auth.routes bind to it at import time. Importing
    the blueprint first would silently drop those limits.
    """
    init_security(app)

    from .routes import bp as auth_bp

    app.register_blueprint(auth_bp)
