"""Implement 72-hour retention and durable object deletion.

Revision ID: implement_data_retention
Revises: support_buy_order_actions
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "implement_data_retention"
down_revision: str | Sequence[str] | None = "support_buy_order_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("purge_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE reports SET purge_at = received_at + INTERVAL '72 hours'")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM reports WHERE purge_at IS NULL) "
        "THEN RAISE EXCEPTION 'reports.purge_at backfill is incomplete'; END IF; END $$"
    )
    op.alter_column("reports", "purge_at", nullable=False)
    op.create_index("ix_reports_purge_at", "reports", ["purge_at"], unique=False)

    op.add_column(
        "idempotency_records", sa.Column("safe_failure_code", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "idempotency_records",
        sa.Column("processing_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "idempotency_records", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "idempotency_records", sa.Column("purge_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE idempotency_records SET processing_status = 'COMPLETED', "
        "completed_at = created_at, purge_at = created_at + INTERVAL '72 hours'"
    )
    op.alter_column("idempotency_records", "processing_status", nullable=False)
    op.alter_column("idempotency_records", "completed_at", nullable=False)
    op.alter_column("idempotency_records", "purge_at", nullable=False)
    op.create_check_constraint(
        op.f("ck_idempotency_records_processing_status_value"),
        "idempotency_records",
        "processing_status = 'COMPLETED'",
    )
    op.create_check_constraint(
        op.f("ck_idempotency_records_safe_failure_code_value"),
        "idempotency_records",
        "safe_failure_code IS NULL OR safe_failure_code IN "
        "('TIMEOUT', 'INVALID_SCHEMA', 'PROVIDER_UNAVAILABLE')",
    )
    op.create_index(
        "ix_idempotency_records_purge_at", "idempotency_records", ["purge_at"], unique=False
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], unique=False)

    op.create_table(
        "object_deletion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=43), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("safe_error_code", sa.String(length=64), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "object_key ~ '^[A-Za-z0-9_-]{43}$'",
            name=op.f("ck_object_deletion_jobs_object_key_format"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'RETRY_PENDING', 'COMPLETED')",
            name=op.f("ck_object_deletion_jobs_status_value"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_object_deletion_jobs_attempt_count_nonnegative")
        ),
        sa.CheckConstraint(
            "safe_error_code IS NULL OR safe_error_code = 'STORAGE_UNAVAILABLE'",
            name=op.f("ck_object_deletion_jobs_safe_error_code_value"),
        ),
        sa.CheckConstraint(
            "((status = 'PENDING' AND attempt_count = 0 AND safe_error_code IS NULL "
            "AND next_attempt_at IS NOT NULL AND completed_at IS NULL AND purge_at IS NULL) OR "
            "(status = 'PROCESSING' AND attempt_count > 0 AND safe_error_code IS NULL "
            "AND next_attempt_at IS NOT NULL AND completed_at IS NULL AND purge_at IS NULL) OR "
            "(status = 'RETRY_PENDING' AND attempt_count > 0 AND safe_error_code IS NOT NULL "
            "AND next_attempt_at IS NOT NULL AND completed_at IS NULL AND purge_at IS NULL) OR "
            "(status = 'COMPLETED' AND attempt_count > 0 AND safe_error_code IS NULL "
            "AND next_attempt_at IS NULL AND completed_at IS NOT NULL AND purge_at IS NOT NULL))",
            name=op.f("ck_object_deletion_jobs_state_metadata"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_deletion_jobs")),
        sa.UniqueConstraint("object_key", name=op.f("uq_object_deletion_jobs_object_key")),
    )
    op.create_index(
        "ix_object_deletion_jobs_ready",
        "object_deletion_jobs",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_object_deletion_jobs_purge_at",
        "object_deletion_jobs",
        ["purge_at"],
        unique=False,
    )
    op.execute(
        "INSERT INTO object_deletion_jobs "
        "(id, object_key, status, attempt_count, next_attempt_at) "
        "SELECT gen_random_uuid(), attachments.object_key, 'PENDING', 0, now() "
        "FROM attachments JOIN reports ON reports.id = attachments.report_id "
        "WHERE reports.status = 'ANALYSIS_FAILED' "
        "ON CONFLICT (object_key) DO NOTHING"
    )
    op.execute(
        "INSERT INTO idempotency_records "
        "(id, principal_digest, operation, client_request_id, payload_sha256, response_status, "
        "safe_failure_code, processing_status, created_at, completed_at, purge_at) "
        "SELECT gen_random_uuid(), reports.session_digest, 'ANALYZE_REPORT', "
        "reports.client_request_id, reports.request_payload_sha256, 200, "
        "CASE WHEN failed.safe_error_code IN "
        "('TIMEOUT', 'INVALID_SCHEMA', 'PROVIDER_UNAVAILABLE') "
        "THEN failed.safe_error_code ELSE 'PROVIDER_UNAVAILABLE' END, "
        "'COMPLETED', COALESCE(failed.completed_at, reports.updated_at), "
        "COALESCE(failed.completed_at, reports.updated_at), "
        "COALESCE(failed.completed_at, reports.updated_at) + INTERVAL '72 hours' "
        "FROM reports JOIN LATERAL "
        "(SELECT safe_error_code, completed_at FROM report_analyses "
        "WHERE report_analyses.report_id = reports.id AND status = 'FAILED' "
        "ORDER BY version DESC LIMIT 1) AS failed ON TRUE "
        "WHERE reports.status = 'ANALYSIS_FAILED' "
        "ON CONFLICT (principal_digest, operation, client_request_id) DO NOTHING"
    )
    op.execute("DELETE FROM reports WHERE status = 'ANALYSIS_FAILED'")
    op.execute(
        "INSERT INTO object_deletion_jobs "
        "(id, object_key, status, attempt_count, next_attempt_at) "
        "SELECT gen_random_uuid(), attachment_object_key, 'PENDING', 0, now() "
        "FROM idempotency_records WHERE attachment_object_key IS NOT NULL "
        "ON CONFLICT (object_key) DO NOTHING"
    )
    op.drop_constraint(
        op.f("ck_idempotency_records_attachment_object_key_format"),
        "idempotency_records",
        type_="check",
    )
    op.drop_column("idempotency_records", "attachment_object_key")


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM object_deletion_jobs "
        "WHERE status <> 'COMPLETED') THEN RAISE EXCEPTION "
        "'cannot downgrade while object deletion jobs are pending; run the purge CLI until "
        "retry_waiting is zero'; END IF; END $$"
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
    op.drop_index("ix_object_deletion_jobs_purge_at", table_name="object_deletion_jobs")
    op.drop_index("ix_object_deletion_jobs_ready", table_name="object_deletion_jobs")
    op.drop_table("object_deletion_jobs")

    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_idempotency_records_purge_at", table_name="idempotency_records")
    op.drop_constraint(
        op.f("ck_idempotency_records_safe_failure_code_value"),
        "idempotency_records",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_idempotency_records_processing_status_value"),
        "idempotency_records",
        type_="check",
    )
    op.drop_column("idempotency_records", "purge_at")
    op.drop_column("idempotency_records", "completed_at")
    op.drop_column("idempotency_records", "processing_status")
    op.drop_column("idempotency_records", "safe_failure_code")

    op.drop_index("ix_reports_purge_at", table_name="reports")
    op.drop_column("reports", "purge_at")
