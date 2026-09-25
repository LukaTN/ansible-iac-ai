"""Scope generation analytics per user

Revision ID: 0004_generation_user_stats
Revises: 0003_pgvector
Create Date: 2026-09-16

Adds ownership to generation rows so analytics can be filtered per user.
Historical rows are backfilled to the bootstrap admin account so older
stats remain visible to the original owner instead of leaking into every
new user's dashboard.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_generation_user_stats"
down_revision: str | None = "0003_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("generations", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index("ix_generations_user_id", "generations", ["user_id"])
    op.create_foreign_key(
        "fk_generations_user_id",
        "generations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Backfill historical generations to the bootstrap admin if available.
    bind = op.get_bind()
    admin_id = bind.execute(
        sa.text("SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1")
    ).scalar()
    if admin_id is not None:
        bind.execute(
            sa.text("UPDATE generations SET user_id = :uid WHERE user_id IS NULL"),
            {"uid": int(admin_id)},
        )


def downgrade() -> None:
    op.drop_constraint("fk_generations_user_id", "generations", type_="foreignkey")
    op.drop_index("ix_generations_user_id", table_name="generations")
    op.drop_column("generations", "user_id")