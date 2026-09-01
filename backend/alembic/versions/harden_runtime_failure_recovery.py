"""Add bounded signal failures and public analysis rate-limit scope.

Revision ID: harden_runtime_failures
Revises: remove_ineligible_signal_jobs
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "harden_runtime_failures"
down_revision: str | Sequence[str] | None = "remove_ineligible_signal_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_signal_processing_jobs_status_value"),
        "signal_processing_jobs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_signal_processing_jobs_safe_error_code_value"),
        "signal_processing_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_signal_processing_jobs_status_value"),
        "signal_processing_jobs",
        "status IN ('PENDING', 'PROCESSING', 'FAILED', 'DEAD_LETTER', 'COMPLETED')",
    )
    op.create_check_constraint(
        op.f("ck_signal_processing_jobs_safe_error_code_value"),
        "signal_processing_jobs",
        "safe_error_code IS NULL OR safe_error_code IN "
        "('EMBEDDING_UNAVAILABLE', 'INVALID_EMBEDDING', 'POLICY_MISMATCH', "
        "'EMBEDDING_INPUT_UNAVAILABLE', 'RETRY_EXHAUSTED')",
    )

    op.drop_constraint(
        op.f("ck_rate_limit_buckets_scope_value"),
        "rate_limit_buckets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_rate_limit_buckets_scope_value"),
        "rate_limit_buckets",
        "scope IN ('AGENT_LOGIN_FAILURE', 'AGENT_CARD_LOOKUP', 'SIGNAL_DASHBOARD', "
        "'REPORT_ANALYZE')",
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM rate_limit_buckets WHERE scope = 'REPORT_ANALYZE'"))
    op.execute(
        sa.text(
            "UPDATE signal_processing_jobs "
            "SET status = 'FAILED', safe_error_code = 'EMBEDDING_UNAVAILABLE' "
            "WHERE status = 'DEAD_LETTER' OR safe_error_code = 'RETRY_EXHAUSTED'"
        )
    )
    op.drop_constraint(
        op.f("ck_rate_limit_buckets_scope_value"),
        "rate_limit_buckets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_rate_limit_buckets_scope_value"),
        "rate_limit_buckets",
        "scope IN ('AGENT_LOGIN_FAILURE', 'AGENT_CARD_LOOKUP', 'SIGNAL_DASHBOARD')",
    )

    op.drop_constraint(
        op.f("ck_signal_processing_jobs_safe_error_code_value"),
        "signal_processing_jobs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_signal_processing_jobs_status_value"),
        "signal_processing_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_signal_processing_jobs_safe_error_code_value"),
        "signal_processing_jobs",
        "safe_error_code IS NULL OR safe_error_code IN "
        "('EMBEDDING_UNAVAILABLE', 'INVALID_EMBEDDING', 'POLICY_MISMATCH', "
        "'EMBEDDING_INPUT_UNAVAILABLE')",
    )
    op.create_check_constraint(
        op.f("ck_signal_processing_jobs_status_value"),
        "signal_processing_jobs",
        "status IN ('PENDING', 'PROCESSING', 'FAILED', 'COMPLETED')",
    )
