"""Version clustering linkage and representative methods.

Revision ID: add_average_medoid_policy
Revises: signal_dashboard_rate_limit
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "add_average_medoid_policy"
down_revision: str | Sequence[str] | None = "signal_dashboard_rate_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clustering_policies",
        sa.Column(
            "linkage_method",
            sa.String(length=16),
            nullable=False,
            server_default="SINGLE_MAX",
        ),
    )
    op.add_column(
        "clustering_policies",
        sa.Column(
            "representative_method",
            sa.String(length=16),
            nullable=False,
            server_default="NONE",
        ),
    )
    op.create_check_constraint(
        op.f("ck_clustering_policies_linkage_method_value"),
        "clustering_policies",
        "linkage_method IN ('SINGLE_MAX', 'AVERAGE')",
    )
    op.create_check_constraint(
        op.f("ck_clustering_policies_representative_method_value"),
        "clustering_policies",
        "representative_method IN ('NONE', 'MEDOID')",
    )
    op.alter_column("clustering_policies", "linkage_method", server_default=None)
    op.alter_column("clustering_policies", "representative_method", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_clustering_policies_representative_method_value"),
        "clustering_policies",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_clustering_policies_linkage_method_value"),
        "clustering_policies",
        type_="check",
    )
    op.drop_column("clustering_policies", "representative_method")
    op.drop_column("clustering_policies", "linkage_method")
