"""
=============================================================
  Seed or repair the bootstrap administrator account.

  Run once after `alembic upgrade head`:

      python -m scripts.seed_admin

  Reads BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD. Idempotent:
  re-running promotes the account to admin, activates it, and resets the
  password to the configured value, which is also the recovery path for a
  locked-out administrator.

  Credentials deliberately live in configuration rather than in a
  migration, so they never enter version control or migration history.
=============================================================
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))

from config import settings
from logging_setup import configure_logging, get_logger

log = get_logger("seed_admin")


def main() -> int:
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    email = (settings.bootstrap_admin_email or "").strip().lower()
    password = settings.bootstrap_admin_password or ""

    if not email or not password:
        print(
            "BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD must both be set "
            "in .env before seeding an administrator.",
            file=sys.stderr,
        )
        return 2

    # Imported here so a configuration error is reported before the app
    # spends time loading the knowledge base and RAG stack.
    from app import app
    from auth.passwords import PasswordPolicyError, hash_password, validate_password
    from models import PROVIDER_LOCAL, ROLE_ADMIN, User, db, utcnow

    try:
        validate_password(password, email=email)
    except PasswordPolicyError as exc:
        print(f"BOOTSTRAP_ADMIN_PASSWORD rejected: {exc}", file=sys.stderr)
        return 2

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        created = user is None

        if user is None:
            user = User(
                email=email,
                display_name=email.split("@")[0],
                provider=PROVIDER_LOCAL,
            )
            db.session.add(user)

        user.password_hash = hash_password(password)
        user.password_changed_at = utcnow()
        user.role = ROLE_ADMIN
        user.is_active = True
        user.failed_login_count = 0
        user.locked_until = None
        # Any session issued before this reset is now invalid.
        user.invalidate_sessions()

        db.session.commit()

        log.info(
            "seed_admin.done",
            email=email,
            created=created,
            user_id=user.id,
        )

    action = "Created" if created else "Updated"
    print(f"{action} administrator {email}.")
    print("Sign in, then change the password from the account menu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
