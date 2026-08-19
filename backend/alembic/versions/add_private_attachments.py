"""Add private screenshot attachment metadata.

Revision ID: add_private_attachments
Revises: enforce_limit_order_price
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "add_private_attachments"
down_revision: str | Sequence[str] | None = "enforce_limit_order_price"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=43), nullable=False),
        sa.Column("content_type", sa.String(length=16), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "byte_size BETWEEN 1 AND 5242880",
            name=op.f("ck_attachments_byte_size_range"),
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_attachments_content_sha256_lower_hex"),
        ),
        sa.CheckConstraint(
            "content_type IN ('image/png', 'image/jpeg', 'image/webp')",
            name=op.f("ck_attachments_content_type_value"),
        ),
        sa.CheckConstraint(
            "width BETWEEN 1 AND 4096 AND height BETWEEN 1 AND 4096 AND width * height <= 16000000",
            name=op.f("ck_attachments_dimensions_range"),
        ),
        sa.CheckConstraint(
            "object_key ~ '^[A-Za-z0-9_-]{43}$'",
            name=op.f("ck_attachments_object_key_format"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_attachments_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attachments")),
        sa.UniqueConstraint("object_key", name=op.f("uq_attachments_object_key")),
        sa.UniqueConstraint("report_id", name=op.f("uq_attachments_report_id")),
    )
    op.add_column(
        "idempotency_records",
        sa.Column("attachment_object_key", sa.String(length=43), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_idempotency_records_attachment_object_key_format"),
        "idempotency_records",
        "attachment_object_key IS NULL OR attachment_object_key ~ '^[A-Za-z0-9_-]{43}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_idempotency_records_attachment_object_key_format"),
        "idempotency_records",
        type_="check",
    )
    op.drop_column("idempotency_records", "attachment_object_key")
    op.drop_table("attachments")
