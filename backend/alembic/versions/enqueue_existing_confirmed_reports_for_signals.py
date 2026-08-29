"""Queue existing confirmed reports for later signal processing.

Revision ID: enqueue_existing_signal_reports
Revises: add_incident_signal_workflow
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "enqueue_existing_signal_reports"
down_revision: str | Sequence[str] | None = "add_incident_signal_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO signal_processing_jobs (
            id,
            report_id,
            technical_symptom_id,
            policy_id,
            status,
            safe_error_code,
            attempt_count,
            next_attempt_at,
            created_at,
            updated_at,
            completed_at
        )
        SELECT
            md5('signal-processing-job:' || reports.id::text)::uuid,
            reports.id,
            technical_symptoms.id,
            NULL,
            'PENDING',
            NULL,
            0,
            COALESCE(technical_symptoms.confirmed_at, reports.confirmed_at, reports.received_at),
            COALESCE(technical_symptoms.confirmed_at, reports.confirmed_at, reports.received_at),
            COALESCE(technical_symptoms.confirmed_at, reports.confirmed_at, reports.received_at),
            NULL
        FROM reports
        JOIN technical_symptoms ON technical_symptoms.report_id = reports.id
        WHERE reports.status = 'CONFIRMED'
        ON CONFLICT (report_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM signal_processing_jobs) "
        "THEN RAISE EXCEPTION 'cannot downgrade while signal processing jobs exist; "
        "process, purge, or explicitly remove them first'; END IF; END $$"
    )
