"""Add the versioned KRX Symbol Master and validation references.

Revision ID: add_krx_symbol_master
Revises: implement_agent_workflow
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "add_krx_symbol_master"
down_revision: str | Sequence[str] | None = "implement_agent_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_consultation_cards_symbol_code_format"),
        "consultation_cards",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_consultation_cards_symbol_code_format"),
        "consultation_cards",
        "symbol_code IS NULL OR symbol_code ~ '^[0-9A-Z]{6}$'",
    )
    op.drop_constraint(
        op.f("ck_agent_verifications_symbol_code_format"),
        "agent_verifications",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_verifications_symbol_code_format"),
        "agent_verifications",
        "symbol_code IS NULL OR symbol_code ~ '^[0-9A-Z]{6}$'",
    )

    op.create_table(
        "symbol_master_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_as_of", sa.Date(), nullable=False),
        sa.Column("source_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("source_encoding", sa.String(length=16), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(version)) > 0",
            name=op.f("ck_symbol_master_versions_version_not_empty"),
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_symbol_master_versions_source_sha256_lower_hex"),
        ),
        sa.CheckConstraint(
            "source_encoding IN ('UTF-8-SIG', 'CP949')",
            name=op.f("ck_symbol_master_versions_source_encoding_value"),
        ),
        sa.CheckConstraint(
            "row_count > 0",
            name=op.f("ck_symbol_master_versions_row_count_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_symbol_master_versions")),
        sa.UniqueConstraint("source_sha256", name=op.f("uq_symbol_master_versions_source_sha256")),
        sa.UniqueConstraint("version", name=op.f("uq_symbol_master_versions_version")),
    )
    op.create_index(
        "uq_symbol_master_versions_active",
        "symbol_master_versions",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "symbols",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("master_version_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=6), nullable=False),
        sa.Column("name_ko", sa.String(length=80), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("source_market", sa.String(length=20), nullable=False),
        sa.Column("stock_type", sa.String(length=16), nullable=False),
        sa.CheckConstraint("code ~ '^[0-9A-Z]{6}$'", name=op.f("ck_symbols_code_format")),
        sa.CheckConstraint(
            "char_length(btrim(name_ko)) > 0",
            name=op.f("ck_symbols_name_ko_not_empty"),
        ),
        sa.CheckConstraint(
            "market IN ('KOSPI', 'KOSDAQ')",
            name=op.f("ck_symbols_market_value"),
        ),
        sa.CheckConstraint(
            "source_market IN ('KOSPI', 'KOSDAQ', 'KOSDAQ GLOBAL')",
            name=op.f("ck_symbols_source_market_value"),
        ),
        sa.CheckConstraint(
            "stock_type = '보통주'",
            name=op.f("ck_symbols_stock_type_common"),
        ),
        sa.ForeignKeyConstraint(
            ["master_version_id"],
            ["symbol_master_versions.id"],
            name=op.f("fk_symbols_master_version_id_symbol_master_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_symbols")),
        sa.UniqueConstraint(
            "master_version_id",
            "code",
            name=op.f("uq_symbols_master_version_id"),
        ),
    )
    op.create_index("ix_symbols_master_version_id", "symbols", ["master_version_id"])
    op.create_index("ix_symbols_code", "symbols", ["code"])

    op.add_column(
        "consultation_cards",
        sa.Column("symbol_master_version_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_consultation_cards_symbol_master_version_id_symbol_master_versions"),
        "consultation_cards",
        "symbol_master_versions",
        ["symbol_master_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_consultation_cards_symbol_master_version_id",
        "consultation_cards",
        ["symbol_master_version_id"],
    )

    op.add_column(
        "agent_verifications",
        sa.Column("symbol_master_version_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_agent_verifications_symbol_master_version_id_symbol_master_versions"),
        "agent_verifications",
        "symbol_master_versions",
        ["symbol_master_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_agent_verifications_symbol_master_version_id",
        "agent_verifications",
        ["symbol_master_version_id"],
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM symbol_master_versions) "
        "THEN RAISE EXCEPTION 'cannot downgrade while KRX Symbol Master data exists; "
        "remove unreferenced imported versions explicitly first'; END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT 1 FROM consultation_cards "
        "WHERE symbol_code IS NOT NULL AND symbol_code !~ '^[0-9]{6}$' "
        "UNION ALL SELECT 1 FROM agent_verifications "
        "WHERE symbol_code IS NOT NULL AND symbol_code !~ '^[0-9]{6}$'"
        ") THEN RAISE EXCEPTION 'cannot downgrade while alphanumeric symbol codes exist'; "
        "END IF; END $$"
    )
    op.drop_index(
        "ix_agent_verifications_symbol_master_version_id",
        table_name="agent_verifications",
    )
    op.drop_constraint(
        op.f("fk_agent_verifications_symbol_master_version_id_symbol_master_versions"),
        "agent_verifications",
        type_="foreignkey",
    )
    op.drop_column("agent_verifications", "symbol_master_version_id")
    op.drop_index(
        "ix_consultation_cards_symbol_master_version_id",
        table_name="consultation_cards",
    )
    op.drop_constraint(
        op.f("fk_consultation_cards_symbol_master_version_id_symbol_master_versions"),
        "consultation_cards",
        type_="foreignkey",
    )
    op.drop_column("consultation_cards", "symbol_master_version_id")
    op.drop_index("ix_symbols_code", table_name="symbols")
    op.drop_index("ix_symbols_master_version_id", table_name="symbols")
    op.drop_table("symbols")
    op.drop_index("uq_symbol_master_versions_active", table_name="symbol_master_versions")
    op.drop_table("symbol_master_versions")
    op.drop_constraint(
        op.f("ck_agent_verifications_symbol_code_format"),
        "agent_verifications",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_verifications_symbol_code_format"),
        "agent_verifications",
        "symbol_code IS NULL OR symbol_code ~ '^[0-9]{6}$'",
    )
    op.drop_constraint(
        op.f("ck_consultation_cards_symbol_code_format"),
        "consultation_cards",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_consultation_cards_symbol_code_format"),
        "consultation_cards",
        "symbol_code IS NULL OR symbol_code ~ '^[0-9]{6}$'",
    )
