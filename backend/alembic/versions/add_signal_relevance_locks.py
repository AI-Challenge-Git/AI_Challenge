"""Persist agent-confirmed signal relevance locks.

Revision ID: add_signal_relevance_locks
Revises: add_average_medoid_policy
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "add_signal_relevance_locks"
down_revision: str | Sequence[str] | None = "add_average_medoid_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_signal_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("relevance_status", sa.String(length=24), nullable=False),
        sa.Column("agent_decision", sa.String(length=24), nullable=False),
        sa.Column("verification_status", sa.String(length=24), nullable=False),
        sa.Column("final_related", sa.Boolean(), nullable=True),
        sa.Column("lock_decision", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "agent_decision IN ('RELATED', 'NOT_RELATED', 'UNCONFIRMED')",
            name=op.f("ck_agent_signal_verifications_agent_decision_value"),
        ),
        sa.CheckConstraint(
            "lock_decision IN ('ALLOW', 'BLOCK', 'IDEMPOTENT_REPLAY', 'CONFLICT')",
            name=op.f("ck_agent_signal_verifications_lock_decision_value"),
        ),
        sa.CheckConstraint(
            "relevance_status IN ('RELATED', 'NEEDS_CONFIRMATION', 'NOT_RELATED')",
            name=op.f("ck_agent_signal_verifications_relevance_status_value"),
        ),
        sa.CheckConstraint(
            "verification_status IN ('MATCHED', 'NEEDS_CONFIRMATION', 'IMPORTANT')",
            name=op.f("ck_agent_signal_verifications_verification_status_value"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_accounts.id"],
            name=op.f("fk_agent_signal_verifications_agent_id_agent_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_agent_signal_verifications_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signal_clusters.id"],
            name=op.f("fk_agent_signal_verifications_signal_id_signal_clusters"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_signal_verifications")),
        sa.UniqueConstraint(
            "agent_id",
            "client_request_id",
            name=op.f("uq_agent_signal_verifications_agent_id"),
        ),
    )
    op.create_index(
        op.f("ix_agent_signal_verifications_agent_id"),
        "agent_signal_verifications",
        ["agent_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_signal_verifications_report_signal",
        "agent_signal_verifications",
        ["report_id", "signal_id"],
        unique=False,
    )
    op.create_table(
        "signal_relevance_locks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("verification_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=False),
        sa.Column("final_related", sa.Boolean(), nullable=False),
        sa.Column("relevance_policy_version", sa.String(length=64), nullable=False),
        sa.Column("verification_policy_version", sa.String(length=64), nullable=False),
        sa.Column("lock_policy_version", sa.String(length=64), nullable=False),
        sa.Column("locked_by", sa.Uuid(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["verification_id"],
            ["agent_signal_verifications.id"],
            name=op.f("fk_signal_relevance_locks_verification_id_agent_signal_verifications"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["locked_by"],
            ["agent_accounts.id"],
            name=op.f("fk_signal_relevance_locks_locked_by_agent_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_signal_relevance_locks_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signal_clusters.id"],
            name=op.f("fk_signal_relevance_locks_signal_id_signal_clusters"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_relevance_locks")),
        sa.UniqueConstraint(
            "report_id",
            "signal_id",
            name=op.f("uq_signal_relevance_locks_report_id"),
        ),
        sa.UniqueConstraint(
            "verification_id",
            name=op.f("uq_signal_relevance_locks_verification_id"),
        ),
    )
    op.create_index(
        op.f("ix_signal_relevance_locks_locked_by"),
        "signal_relevance_locks",
        ["locked_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_signal_relevance_locks_signal_id"),
        "signal_relevance_locks",
        ["signal_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_signal_relevance_locks_signal_id"),
        table_name="signal_relevance_locks",
    )
    op.drop_index(
        op.f("ix_signal_relevance_locks_locked_by"),
        table_name="signal_relevance_locks",
    )
    op.drop_table("signal_relevance_locks")
    op.drop_index(
        "ix_agent_signal_verifications_report_signal",
        table_name="agent_signal_verifications",
    )
    op.drop_index(
        op.f("ix_agent_signal_verifications_agent_id"),
        table_name="agent_signal_verifications",
    )
    op.drop_table("agent_signal_verifications")
