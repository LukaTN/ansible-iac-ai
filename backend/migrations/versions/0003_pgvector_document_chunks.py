"""Phase 3: pgvector extension and document_chunks table

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-09

Adds the pgvector extension and the document_chunks table that replaces
ChromaDB. Also adds index_meta for tracking schema versions.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector


revision = "0003_pgvector"
down_revision = "0002_user_management"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doc_id", sa.String(255), unique=True, nullable=False),
        sa.Column("collection_name", sa.String(128), nullable=False, server_default="ansible_docs"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    )

    op.create_index("ix_chunks_doc_id", "document_chunks", ["doc_id"], unique=True)
    op.create_index("ix_chunks_collection", "document_chunks", ["collection_name"])

    op.execute(f"""
        CREATE INDEX ix_chunks_embedding_hnsw
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 128)
    """)

    op.execute("""
        CREATE INDEX ix_chunks_metadata_gin
        ON document_chunks
        USING gin (metadata jsonb_path_ops)
    """)

    op.create_table(
        "index_meta",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), unique=True, nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("index_meta")
    op.drop_table("document_chunks")
    op.execute("DROP EXTENSION IF EXISTS vector")
