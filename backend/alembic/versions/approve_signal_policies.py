"""Record evidence-backed signal policy approval.

Revision ID: approve_signal_policies
Revises: harden_runtime_failures
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "approve_signal_policies"
down_revision: str | Sequence[str] | None = "harden_runtime_failures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clustering_policies",
        sa.Column("approval_evidence_sha256", sa.CHAR(length=64), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_clustering_policies_approval_evidence_sha256_lower_hex"),
        "clustering_policies",
        "approval_evidence_sha256 IS NULL OR approval_evidence_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.drop_constraint(
        op.f("ck_clustering_policies_approval_metadata"),
        "clustering_policies",
        type_="check",
    )
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM clustering_policies WHERE status = 'APPROVED') THEN "
        "RAISE EXCEPTION 'approved signal policies require recorded evaluation evidence'; "
        "END IF; END $$"
    )
    op.create_check_constraint(
        op.f("ck_clustering_policies_approval_metadata"),
        "clustering_policies",
        "((status = 'APPROVED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL "
        "AND approval_evidence_sha256 IS NOT NULL) OR status <> 'APPROVED')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_clustering_policies_approval_metadata"),
        "clustering_policies",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_clustering_policies_approval_metadata"),
        "clustering_policies",
        "((status = 'APPROVED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL) "
        "OR status <> 'APPROVED')",
    )
    op.drop_constraint(
        op.f("ck_clustering_policies_approval_evidence_sha256_lower_hex"),
        "clustering_policies",
        type_="check",
    )
    op.drop_column("clustering_policies", "approval_evidence_sha256")
