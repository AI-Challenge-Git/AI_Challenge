"""Remove signal jobs that have no valid embedding input.

Revision ID: remove_ineligible_signal_jobs
Revises: add_signal_relevance_locks
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "remove_ineligible_signal_jobs"
down_revision: str | Sequence[str] | None = "add_signal_relevance_locks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM signal_processing_jobs AS jobs
        USING technical_symptoms AS symptoms
        WHERE jobs.technical_symptom_id = symptoms.id
          AND jobs.status <> 'COMPLETED'
          AND (
            symptoms.symptom IS NULL
            OR symptoms.issue_type IN ('UNKNOWN', 'UNRELATED_OR_AMBIGUOUS')
          )
        """
    )


def downgrade() -> None:
    pass
