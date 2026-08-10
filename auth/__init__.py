"""
=============================================================
  AnsibleAI — Authentication and authorization

  Phase 0 authenticates against a local argon2id password. The
  User model, session layer, and authorization hooks are kept
  independent of that choice so Phase 5 can add a Keycloak OIDC
  provider by filling `User.provider` / `User.external_id`, without
  touching the models or the route guards.
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
