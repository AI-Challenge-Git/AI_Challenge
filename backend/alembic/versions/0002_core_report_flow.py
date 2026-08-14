"""Create the core report flow tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_checked_on", sa.Date(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_policy_snapshots_content_sha256_lower_hex"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_snapshots")),
        sa.UniqueConstraint("version", name=op.f("uq_policy_snapshots_version")),
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("policy_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("pii_policy_version", sa.String(length=64), nullable=False),
        sa.Column("masked_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(session_digest) = 32",
            name=op.f("ck_reports_session_digest_length"),
        ),
        sa.CheckConstraint(
            "char_length(masked_text) > 0",
            name=op.f("ck_reports_masked_text_not_empty"),
        ),
        sa.CheckConstraint(
            "status IN ('ANALYSIS_PENDING', 'AWAITING_CONFIRMATION', "
            "'CONFIRMED', 'ANALYSIS_FAILED')",
            name=op.f("ck_reports_status_value"),
        ),
        sa.CheckConstraint(
            "((status = 'CONFIRMED' AND confirmed_at IS NOT NULL) OR "
            "(status <> 'CONFIRMED' AND confirmed_at IS NULL))",
            name=op.f("ck_reports_confirmed_at_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["policy_snapshot_id"],
            ["policy_snapshots.id"],
            name=op.f("fk_reports_policy_snapshot_id_policy_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.UniqueConstraint(
            "session_digest",
            "client_request_id",
            name=op.f("uq_reports_session_digest"),
        ),
    )
    op.create_index(
        "ix_reports_policy_snapshot_id",
        "reports",
        ["policy_snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_reports_received_at",
        "reports",
        [sa.text("received_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_reports_session_received",
        "reports",
        ["session_digest", sa.text("received_at DESC")],
        unique=False,
    )

    op.create_table(
        "report_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=64), nullable=False),
        sa.Column("adapter_name", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "technical_candidate",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "consultation_candidate",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("safe_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_report_analyses_version_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_report_analyses_status_value"),
        ),
        sa.CheckConstraint(
            "(status <> 'PENDING' OR (technical_candidate IS NULL AND "
            "consultation_candidate IS NULL AND safe_error_code IS NULL AND "
            "completed_at IS NULL))",
            name=op.f("ck_report_analyses_pending_payload"),
        ),
        sa.CheckConstraint(
            "(status <> 'SUCCEEDED' OR (technical_candidate IS NOT NULL AND "
            "consultation_candidate IS NOT NULL AND safe_error_code IS NULL AND "
            "completed_at IS NOT NULL))",
            name=op.f("ck_report_analyses_succeeded_payload"),
        ),
        sa.CheckConstraint(
            "(status <> 'FAILED' OR (technical_candidate IS NULL AND "
            "consultation_candidate IS NULL AND safe_error_code IS NOT NULL AND "
            "completed_at IS NOT NULL))",
            name=op.f("ck_report_analyses_failed_payload"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_report_analyses_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_analyses")),
        sa.UniqueConstraint(
            "report_id",
            "version",
            name=op.f("uq_report_analyses_report_id"),
        ),
    )

    op.create_table(
        "technical_symptoms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("feature_area", sa.String(length=64), nullable=False),
        sa.Column("issue_type", sa.String(length=64), nullable=False),
        sa.Column("symptom", sa.String(length=500), nullable=True),
        sa.Column("submission_status", sa.String(length=40), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("reported_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "channel = 'MABLE'",
            name=op.f("ck_technical_symptoms_channel_value"),
        ),
        sa.CheckConstraint(
            "feature_area = 'DOMESTIC_STOCK_ORDER'",
            name=op.f("ck_technical_symptoms_feature_area_value"),
        ),
        sa.CheckConstraint(
            "issue_type ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name=op.f("ck_technical_symptoms_issue_type_format"),
        ),
        sa.CheckConstraint(
            "symptom IS NULL OR char_length(btrim(symptom)) BETWEEN 1 AND 500",
            name=op.f("ck_technical_symptoms_symptom_length"),
        ),
        sa.CheckConstraint(
            "submission_status IN ('CUSTOMER_REPORTED_SUBMITTED', "
            "'CUSTOMER_REPORTED_NOT_SUBMITTED', 'UNKNOWN')",
            name=op.f("ck_technical_symptoms_submission_status_value"),
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[A-Za-z0-9._-]{1,64}$'",
            name=op.f("ck_technical_symptoms_error_code_format"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_technical_symptoms_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_technical_symptoms")),
        sa.UniqueConstraint(
            "report_id",
            name=op.f("uq_technical_symptoms_report_id"),
        ),
    )

    op.create_table(
        "consultation_cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("symbol_name", sa.String(length=80), nullable=True),
        sa.Column("symbol_code", sa.String(length=6), nullable=True),
        sa.Column("quantity", sa.BigInteger(), nullable=True),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("price_krw", sa.BigInteger(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('SELL', 'UNKNOWN')",
            name=op.f("ck_consultation_cards_action_value"),
        ),
        sa.CheckConstraint(
            "symbol_code IS NULL OR symbol_code ~ '^[0-9]{6}$'",
            name=op.f("ck_consultation_cards_symbol_code_format"),
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name=op.f("ck_consultation_cards_quantity_positive"),
        ),
        sa.CheckConstraint(
            "order_type IN ('LIMIT', 'MARKET', 'UNKNOWN')",
            name=op.f("ck_consultation_cards_order_type_value"),
        ),
        sa.CheckConstraint(
            "price_krw IS NULL OR price_krw > 0",
            name=op.f("ck_consultation_cards_price_krw_positive"),
        ),
        sa.CheckConstraint(
            "order_type <> 'MARKET' OR price_krw IS NULL",
            name=op.f("ck_consultation_cards_market_order_without_price"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_consultation_cards_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consultation_cards")),
        sa.UniqueConstraint(
            "report_id",
            name=op.f("uq_consultation_cards_report_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("consultation_cards")
    op.drop_table("technical_symptoms")
    op.drop_table("report_analyses")
    op.drop_index("ix_reports_session_received", table_name="reports")
    op.drop_index("ix_reports_received_at", table_name="reports")
    op.drop_index("ix_reports_policy_snapshot_id", table_name="reports")
    op.drop_table("reports")
    op.drop_table("policy_snapshots")
