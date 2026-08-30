"""Index approved 1024-dimension signal embeddings with HNSW cosine search.

Revision ID: index_1024_signal_embeddings
Revises: enqueue_existing_signal_reports
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "index_1024_signal_embeddings"
down_revision: str | Sequence[str] | None = "enqueue_existing_signal_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_technical_embeddings_1024_hnsw_cosine",
        "technical_embeddings",
        [sa.literal_column("(embedding::vector(1024))").label("embedding_1024")],
        postgresql_using="hnsw",
        postgresql_ops={"embedding_1024": "vector_cosine_ops"},
        postgresql_where=sa.text(
            "embedding_dimension = 1024 AND normalization = 'L2' AND distance_metric = 'COSINE'"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_technical_embeddings_1024_hnsw_cosine",
        table_name="technical_embeddings",
        postgresql_using="hnsw",
    )
