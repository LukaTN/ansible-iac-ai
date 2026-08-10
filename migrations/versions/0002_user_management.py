"""User management: users, audit_events, and chat thread ownership

Revision ID: 0002_user_management
Revises: 0001_baseline
Create Date: 2026-07-30

Adds the identity model and, critically, makes every chat thread owned by
a user. `chat_threads.user_id` is added nullable, backfilled, and only
then tightened to NOT NULL, so the migration works on a database that
already contains threads.

Existing threads are assigned to a placeholder owner account, created
inactive and without a password. `python -m scripts.seed_admin` then sets
its password and activates it — keeping credentials out of migration
history while ensuring no thread is left orphaned.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC

import sqlalchemy as sa
from alembic import op

revision: str = "0002_user_management"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FALLBACK_OWNER_EMAIL = "admin@ansibleai.local"


def _bootstrap_email() -> str:
    """Owner for pre-existing threads: the configured admin, or a fallback."""
    from config import settings

    return (settings.bootstrap_admin_email or FALLBACK_OWNER_EMAIL).strip().lower()


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        # Null for accounts that authenticate only via an external identity
        # provider, which is the Phase 5 (Keycloak) end state.
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(), nullable=True),
        # Provider linkage is added now, unused, so that wiring up OIDC in
        # Phase 5 needs no second schema migration.
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("session_epoch", sa.Integer(), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_external_id", "users", ["external_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        # Denormalized so the trail survives deletion of the user row.
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("event", sa.String(length=60), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])
    op.create_index("ix_audit_events_event", "audit_events", ["event"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    # ── Thread ownership ──
    # Added nullable so existing rows survive, then backfilled, then made
    # NOT NULL. Adding it NOT NULL directly would fail on any non-empty table.
    op.add_column("chat_threads", sa.Column("user_id", sa.Integer(), nullable=True))

    bind = op.get_bind()
    orphan_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM chat_threads WHERE user_id IS NULL")
    ).scalar_one()

    if orphan_count:
        owner_id = _ensure_placeholder_owner(bind)
        bind.execute(
            sa.text("UPDATE chat_threads SET user_id = :uid WHERE user_id IS NULL"),
            {"uid": owner_id},
        )

    op.alter_column(
        "chat_threads",
        "user_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_index("ix_chat_threads_user_id", "chat_threads", ["user_id"])
    op.create_foreign_key(
        "fk_chat_threads_user_id",
        "chat_threads",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def _ensure_placeholder_owner(bind: sa.engine.Connection) -> int:
    """
    Create (or reuse) the account that inherits pre-existing threads.

    Created inactive with no password so it cannot be logged into until
    `scripts.seed_admin` deliberately provisions credentials.
    """
    from datetime import datetime

    email = _bootstrap_email()

    existing = bind.execute(
        sa.text("SELECT id FROM users WHERE email = :email"), {"email": email}
    ).scalar()
    if existing:
        return int(existing)

    now = datetime.now(UTC).replace(tzinfo=None)
    bind.execute(
        sa.text(
            """
            INSERT INTO users (
                email, display_name, password_hash, role, is_active,
                provider, failed_login_count, session_epoch,
                created_at, updated_at
            ) VALUES (
                :email, :display_name, NULL, 'admin', :is_active,
                'local', 0, 1, :now, :now
            )
            """
        ),
        {
            "email": email,
            "display_name": email.split("@")[0],
            "is_active": False,
            "now": now,
        },
    )
    return int(
        bind.execute(
            sa.text("SELECT id FROM users WHERE email = :email"), {"email": email}
        ).scalar_one()
    )


def downgrade() -> None:
    op.drop_constraint("fk_chat_threads_user_id", "chat_threads", type_="foreignkey")
    op.drop_index("ix_chat_threads_user_id", table_name="chat_threads")
    op.drop_column("chat_threads", "user_id")

    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_event", table_name="audit_events")
    op.drop_index("ix_audit_events_user_id", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_users_external_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
