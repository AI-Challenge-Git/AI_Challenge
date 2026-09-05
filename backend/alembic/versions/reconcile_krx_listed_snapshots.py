"""Reconcile the common-stock master with daily listed snapshots.

Revision ID: reconcile_krx_listed_snapshots
Revises: approve_signal_policies
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "reconcile_krx_listed_snapshots"
down_revision: str | Sequence[str] | None = "approve_signal_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "symbol_master_versions",
        sa.Column(
            "source_kind",
            sa.String(length=32),
            server_default="KRX_CSV",
            nullable=False,
        ),
    )
    op.add_column(
        "symbol_master_versions",
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "symbol_master_versions",
        sa.Column("baseline_version_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_symbol_master_versions_parent_version_id_symbol_master_versions"),
        "symbol_master_versions",
        "symbol_master_versions",
        ["parent_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_symbol_master_versions_parent_version_id",
        "symbol_master_versions",
        ["parent_version_id"],
    )
    op.create_foreign_key(
        op.f("fk_symbol_master_versions_baseline_version_id_symbol_master_versions"),
        "symbol_master_versions",
        "symbol_master_versions",
        ["baseline_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_symbol_master_versions_baseline_version_id",
        "symbol_master_versions",
        ["baseline_version_id"],
    )
    op.create_check_constraint(
        op.f("ck_symbol_master_versions_source_kind_value"),
        "symbol_master_versions",
        "source_kind IN ('KRX_CSV', 'LISTED_API_RECONCILIATION')",
    )
    op.drop_constraint(
        op.f("ck_symbol_master_versions_source_encoding_value"),
        "symbol_master_versions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_symbol_master_versions_source_encoding_value"),
        "symbol_master_versions",
        "source_encoding IN ('UTF-8-SIG', 'CP949', 'API-JSON')",
    )
    op.create_check_constraint(
        op.f("ck_symbol_master_versions_source_lineage"),
        "symbol_master_versions",
        "((source_kind = 'KRX_CSV' AND parent_version_id IS NULL "
        "AND baseline_version_id IS NULL "
        "AND source_encoding IN ('UTF-8-SIG', 'CP949')) OR "
        "(source_kind = 'LISTED_API_RECONCILIATION' AND parent_version_id IS NOT NULL "
        "AND baseline_version_id IS NOT NULL "
        "AND source_encoding = 'API-JSON'))",
    )

    op.add_column(
        "symbols",
        sa.Column("listed_api_last_seen_on", sa.Date(), nullable=True),
    )
    op.add_column(
        "symbols",
        sa.Column("listed_api_missing_since", sa.Date(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_symbols_listed_api_observation_order"),
        "symbols",
        "listed_api_missing_since IS NULL OR listed_api_last_seen_on IS NULL "
        "OR listed_api_missing_since > listed_api_last_seen_on",
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT 1 FROM symbol_master_versions "
        "WHERE source_kind = 'LISTED_API_RECONCILIATION'"
        ") THEN RAISE EXCEPTION "
        "'cannot downgrade while reconciled KRX Symbol Master versions exist'; "
        "END IF; END $$"
    )
    op.drop_constraint(
        op.f("ck_symbols_listed_api_observation_order"),
        "symbols",
        type_="check",
    )
    op.drop_column("symbols", "listed_api_missing_since")
    op.drop_column("symbols", "listed_api_last_seen_on")

    op.drop_constraint(
        op.f("ck_symbol_master_versions_source_lineage"),
        "symbol_master_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_symbol_master_versions_source_encoding_value"),
        "symbol_master_versions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_symbol_master_versions_source_encoding_value"),
        "symbol_master_versions",
        "source_encoding IN ('UTF-8-SIG', 'CP949')",
    )
    op.drop_constraint(
        op.f("ck_symbol_master_versions_source_kind_value"),
        "symbol_master_versions",
        type_="check",
    )
    op.drop_index(
        "ix_symbol_master_versions_baseline_version_id",
        table_name="symbol_master_versions",
    )
    op.drop_constraint(
        op.f("fk_symbol_master_versions_baseline_version_id_symbol_master_versions"),
        "symbol_master_versions",
        type_="foreignkey",
    )
    op.drop_column("symbol_master_versions", "baseline_version_id")
    op.drop_index(
        "ix_symbol_master_versions_parent_version_id",
        table_name="symbol_master_versions",
    )
    op.drop_constraint(
        op.f("fk_symbol_master_versions_parent_version_id_symbol_master_versions"),
        "symbol_master_versions",
        type_="foreignkey",
    )
    op.drop_column("symbol_master_versions", "parent_version_id")
    op.drop_column("symbol_master_versions", "source_kind")
