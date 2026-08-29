"""Implement authenticated agent consultation workflow.

Revision ID: implement_agent_workflow
Revises: implement_data_retention
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "implement_agent_workflow"
down_revision: str | Sequence[str] | None = "implement_data_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.String(length=32), nullable=False),
        sa.Column("agent_label", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            "employee_id ~ '^[A-Z0-9_-]{4,32}$'",
            name=op.f("ck_agent_accounts_employee_id_format"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(agent_label)) BETWEEN 1 AND 80",
            name=op.f("ck_agent_accounts_agent_label_length"),
        ),
        sa.CheckConstraint(
            "role IN ('AGENT', 'OPERATOR')",
            name=op.f("ck_agent_accounts_role_value"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_accounts")),
        sa.UniqueConstraint("employee_id", name=op.f("uq_agent_accounts_employee_id")),
    )

    op.create_table(
        "agent_access_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(token_digest) = 32",
            name=op.f("ck_agent_access_tokens_token_digest_length"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_agent_access_tokens_expiry_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_accounts.id"],
            name=op.f("fk_agent_access_tokens_agent_id_agent_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_access_tokens")),
        sa.UniqueConstraint("token_digest", name=op.f("uq_agent_access_tokens_token_digest")),
    )
    op.create_index(
        "ix_agent_access_tokens_agent_id",
        "agent_access_tokens",
        ["agent_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_access_tokens_expires_at",
        "agent_access_tokens",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "rate_limit_buckets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("principal_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("client_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('AGENT_LOGIN_FAILURE', 'AGENT_CARD_LOOKUP')",
            name=op.f("ck_rate_limit_buckets_scope_value"),
        ),
        sa.CheckConstraint(
            "octet_length(principal_fingerprint) = 32",
            name=op.f("ck_rate_limit_buckets_principal_fingerprint_length"),
        ),
        sa.CheckConstraint(
            "octet_length(client_fingerprint) = 32",
            name=op.f("ck_rate_limit_buckets_client_fingerprint_length"),
        ),
        sa.CheckConstraint(
            "request_count > 0",
            name=op.f("ck_rate_limit_buckets_request_count_positive"),
        ),
        sa.CheckConstraint(
            "expires_at > window_started_at",
            name=op.f("ck_rate_limit_buckets_expiry_after_window_start"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_limit_buckets")),
        sa.UniqueConstraint(
            "scope",
            "principal_fingerprint",
            "client_fingerprint",
            name=op.f("uq_rate_limit_buckets_scope"),
        ),
    )
    op.create_index(
        "ix_rate_limit_buckets_expires_at",
        "rate_limit_buckets",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "agent_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("symbol_name", sa.String(length=80), nullable=True),
        sa.Column("symbol_code", sa.String(length=6), nullable=True),
        sa.Column("quantity", sa.BigInteger(), nullable=True),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("price_krw", sa.BigInteger(), nullable=True),
        sa.Column("submission_status", sa.String(length=40), nullable=False),
        sa.Column("order_history_checked", sa.Boolean(), nullable=False),
        sa.Column("overall_status", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('BUY', 'SELL', 'UNKNOWN')",
            name=op.f("ck_agent_verifications_action_value"),
        ),
        sa.CheckConstraint(
            "symbol_code IS NULL OR symbol_code ~ '^[0-9]{6}$'",
            name=op.f("ck_agent_verifications_symbol_code_format"),
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name=op.f("ck_agent_verifications_quantity_positive"),
        ),
        sa.CheckConstraint(
            "order_type IN ('LIMIT', 'MARKET', 'UNKNOWN')",
            name=op.f("ck_agent_verifications_order_type_value"),
        ),
        sa.CheckConstraint(
            "price_krw IS NULL OR price_krw > 0",
            name=op.f("ck_agent_verifications_price_krw_positive"),
        ),
        sa.CheckConstraint(
            "order_type <> 'MARKET' OR price_krw IS NULL",
            name=op.f("ck_agent_verifications_market_order_without_price"),
        ),
        sa.CheckConstraint(
            "order_type <> 'LIMIT' OR price_krw IS NOT NULL",
            name=op.f("ck_agent_verifications_limit_order_requires_price"),
        ),
        sa.CheckConstraint(
            "submission_status IN ('CUSTOMER_REPORTED_SUBMITTED', "
            "'CUSTOMER_REPORTED_NOT_SUBMITTED', 'UNKNOWN')",
            name=op.f("ck_agent_verifications_submission_status_value"),
        ),
        sa.CheckConstraint(
            "overall_status IN ('MATCHED', 'NEEDS_CONFIRMATION', 'IMPORTANT')",
            name=op.f("ck_agent_verifications_overall_status_value"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_accounts.id"],
            name=op.f("fk_agent_verifications_agent_id_agent_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["consultation_cards.id"],
            name=op.f("fk_agent_verifications_card_id_consultation_cards"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_verifications")),
        sa.UniqueConstraint(
            "agent_id",
            "client_request_id",
            name=op.f("uq_agent_verifications_agent_id"),
        ),
    )
    op.create_index(
        "ix_agent_verifications_card_id",
        "agent_verifications",
        ["card_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_verifications_agent_id",
        "agent_verifications",
        ["agent_id"],
        unique=False,
    )

    op.add_column("audit_logs", sa.Column("actor_id", sa.Uuid(), nullable=True))
    op.add_column(
        "audit_logs",
        sa.Column(
            "outcome",
            sa.String(length=16),
            server_default=sa.text("'SUCCESS'"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        op.f("fk_audit_logs_actor_id_agent_accounts"),
        "audit_logs",
        "agent_accounts",
        ["actor_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_audit_logs_outcome_value"),
        "audit_logs",
        "outcome IN ('SUCCESS', 'FAILURE', 'RATE_LIMITED', 'REPLAY', 'CONFLICT')",
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"], unique=False)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_accounts) "
        "OR EXISTS (SELECT 1 FROM agent_access_tokens) "
        "OR EXISTS (SELECT 1 FROM agent_verifications) "
        "OR EXISTS (SELECT 1 FROM rate_limit_buckets) "
        "OR EXISTS (SELECT 1 FROM audit_logs WHERE action LIKE 'AGENT_%') "
        "THEN RAISE EXCEPTION 'cannot downgrade while agent workflow data exists; "
        "export required audit data and remove agent rows explicitly first'; END IF; END $$"
    )
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_constraint(
        op.f("ck_audit_logs_outcome_value"),
        "audit_logs",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_audit_logs_actor_id_agent_accounts"),
        "audit_logs",
        type_="foreignkey",
    )
    op.drop_column("audit_logs", "outcome")
    op.drop_column("audit_logs", "actor_id")

    op.drop_index("ix_agent_verifications_agent_id", table_name="agent_verifications")
    op.drop_index("ix_agent_verifications_card_id", table_name="agent_verifications")
    op.drop_table("agent_verifications")
    op.drop_index("ix_rate_limit_buckets_expires_at", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
    op.drop_index("ix_agent_access_tokens_expires_at", table_name="agent_access_tokens")
    op.drop_index("ix_agent_access_tokens_agent_id", table_name="agent_access_tokens")
    op.drop_table("agent_access_tokens")
    op.drop_table("agent_accounts")
