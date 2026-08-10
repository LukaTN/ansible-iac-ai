"""Baseline: pre-existing schema before Alembic was adopted

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-30

This captures the schema that `db.create_all()` used to produce, so that
Alembic has a known starting point.

Table creation is guarded by an inspection check. That is unusual for a
migration, and deliberate: existing installations already have these
tables (they were created implicitly at startup), while a fresh database
has none. Guarding lets a single `alembic upgrade head` work correctly in
both cases instead of requiring operators to know when to run
`alembic stamp` first — a step that silently corrupts history when
forgotten. Only this baseline revision is guarded; every later migration
is a normal, unconditional one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    if "generations" not in existing:
        op.create_table(
            "generations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("request", sa.Text(), nullable=False),
            sa.Column("module", sa.String(length=120), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=True),
            sa.Column("playbook", sa.Text(), nullable=True),
            sa.Column("is_valid", sa.Boolean(), nullable=True),
            sa.Column("warnings", sa.Integer(), nullable=True),
            sa.Column("errors", sa.Integer(), nullable=True),
            sa.Column("module_ref", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "scrape_sessions" not in existing:
        op.create_table(
            "scrape_sessions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("triggered_at", sa.DateTime(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("triggered_by", sa.String(length=120), nullable=True),
            sa.Column("kb_version", sa.String(length=255), nullable=True),
            sa.Column("modules_updated", sa.JSON(), nullable=True),
            sa.Column("modules_failed", sa.JSON(), nullable=True),
            sa.Column("summary", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "module_versions" not in existing:
        op.create_table(
            "module_versions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("scrape_session_id", sa.Integer(), nullable=True),
            sa.Column("module_slug", sa.String(length=255), nullable=False),
            sa.Column("scraped_at", sa.DateTime(), nullable=False),
            sa.Column("param_count", sa.Integer(), nullable=False),
            sa.Column("example_count", sa.Integer(), nullable=False),
            sa.Column("required_count", sa.Integer(), nullable=False),
            sa.Column("health_score", sa.Integer(), nullable=False),
            sa.Column("content_hash", sa.String(length=80), nullable=False),
            sa.Column("diff_summary", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_module_versions_scrape_session_id",
            "module_versions",
            ["scrape_session_id"],
        )
        op.create_index("ix_module_versions_module_slug", "module_versions", ["module_slug"])

    # chat_threads.user_id is added by 0002; this is the original shape.
    if "chat_threads" not in existing:
        op.create_table(
            "chat_threads",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "chat_messages" not in existing:
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("thread_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("playbook", sa.Text(), nullable=True),
            sa.Column("filename", sa.String(length=255), nullable=True),
            sa.Column("module", sa.String(length=120), nullable=True),
            sa.Column("validation", sa.JSON(), nullable=True),
            sa.Column("module_ref", sa.JSON(), nullable=True),
            sa.Column("rag_meta", sa.JSON(), nullable=True),
            sa.Column("tool_trace", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_messages_thread_id", "chat_messages", ["thread_id"])


def downgrade() -> None:
    # Dropping the baseline would destroy all application data. Recreating
    # an empty database is the intended path instead.
    raise RuntimeError(
        "Refusing to downgrade past the baseline: this would drop every "
        "application table. Drop and recreate the database instead."
    )
