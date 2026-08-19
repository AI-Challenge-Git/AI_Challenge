"""Add report lifecycle idempotency, reference TTL, and deletion audit.

Revision ID: report_lifecycle_security
Revises: 0002
Create Date: 2026-08-18
"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "report_lifecycle_security"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_VERSION = "kb-trading-failure-guidance-2026-08-18"
POLICY_CONTENT_SHA256 = "b9bdd1159379273bef4cff26c33883f5df23b104c9aa747110885a1852c22dd9"


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("request_payload_sha256", sa.CHAR(length=64), nullable=True),
    )
    op.execute(
        "UPDATE reports SET request_payload_sha256 = "
        "encode(sha256(convert_to(masked_text, 'UTF8')), 'hex') "
        "WHERE request_payload_sha256 IS NULL"
    )
    op.alter_column("reports", "request_payload_sha256", nullable=False)
    op.create_check_constraint(
        op.f("ck_reports_request_payload_sha256_lower_hex"),
        "reports",
        "request_payload_sha256 ~ '^[0-9a-f]{64}$'",
    )

    op.add_column(
        "consultation_cards",
        sa.Column("reference_digest", sa.LargeBinary(length=32), nullable=True),
    )
    op.add_column(
        "consultation_cards",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "consultation_cards",
        sa.Column("confirmation_request_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "consultation_cards",
        sa.Column("confirmation_payload_sha256", sa.CHAR(length=64), nullable=True),
    )
    op.create_unique_constraint(
        op.f("uq_consultation_cards_reference_digest"),
        "consultation_cards",
        ["reference_digest"],
    )
    op.create_index(
        "ix_consultation_cards_expires_at",
        "consultation_cards",
        ["expires_at"],
        unique=False,
    )
    op.create_check_constraint(
        op.f("ck_consultation_cards_reference_digest_length"),
        "consultation_cards",
        "reference_digest IS NULL OR octet_length(reference_digest) = 32",
    )
    op.create_check_constraint(
        op.f("ck_consultation_cards_confirmation_payload_sha256_lower_hex"),
        "consultation_cards",
        "confirmation_payload_sha256 IS NULL OR confirmation_payload_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_consultation_cards_reference_metadata_complete"),
        "consultation_cards",
        "((reference_digest IS NULL AND expires_at IS NULL AND "
        "confirmation_request_id IS NULL AND confirmation_payload_sha256 IS NULL) OR "
        "(reference_digest IS NOT NULL AND expires_at IS NOT NULL AND "
        "confirmation_request_id IS NOT NULL AND confirmation_payload_sha256 IS NOT NULL))",
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("payload_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(principal_digest) = 32",
            name=op.f("ck_idempotency_records_principal_digest_length"),
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_idempotency_records_payload_sha256_lower_hex"),
        ),
        sa.CheckConstraint(
            "response_status BETWEEN 200 AND 599",
            name=op.f("ck_idempotency_records_response_status_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_records")),
        sa.UniqueConstraint(
            "principal_digest",
            "operation",
            "client_request_id",
            name=op.f("uq_idempotency_records_principal_digest"),
        ),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_fingerprint", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "resource_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_audit_logs_resource_fingerprint_lower_hex"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )

    policy_table = sa.table(
        "policy_snapshots",
        sa.column("id", sa.Uuid()),
        sa.column("version", sa.String()),
        sa.column("source_url", sa.Text()),
        sa.column("source_checked_on", sa.Date()),
        sa.column("content", postgresql.JSONB()),
        sa.column("content_sha256", sa.CHAR(64)),
    )
    op.bulk_insert(
        policy_table,
        [
            {
                "id": UUID("0198be5d-4e95-7b65-b831-f34c0f53a8db"),
                "version": POLICY_VERSION,
                "source_url": "https://www.kbsec.com/go.able?linkcd=s060318010004",
                "source_checked_on": date(2026, 8, 18),
                "content": {
                    "notice": "상담 준비카드는 주문 접수·체결 증빙이 아닙니다. "
                    "재주문 전 공식 채널에서 주문 상태를 확인해야 합니다.",
                    "title": "KB증권 전산장애시유의사항",
                },
                "content_sha256": POLICY_CONTENT_SHA256,
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM policy_snapshots WHERE version = :version "
            "AND NOT EXISTS (SELECT 1 FROM reports "
            "WHERE reports.policy_snapshot_id = policy_snapshots.id)"
        ).bindparams(version=POLICY_VERSION)
    )
    op.drop_table("audit_logs")
    op.drop_table("idempotency_records")
    op.drop_constraint(
        op.f("ck_consultation_cards_reference_metadata_complete"),
        "consultation_cards",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_consultation_cards_confirmation_payload_sha256_lower_hex"),
        "consultation_cards",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_consultation_cards_reference_digest_length"),
        "consultation_cards",
        type_="check",
    )
    op.drop_index("ix_consultation_cards_expires_at", table_name="consultation_cards")
    op.drop_constraint(
        op.f("uq_consultation_cards_reference_digest"),
        "consultation_cards",
        type_="unique",
    )
    op.drop_column("consultation_cards", "confirmation_payload_sha256")
    op.drop_column("consultation_cards", "confirmation_request_id")
    op.drop_column("consultation_cards", "expires_at")
    op.drop_column("consultation_cards", "reference_digest")
    op.drop_constraint(
        op.f("ck_reports_request_payload_sha256_lower_hex"),
        "reports",
        type_="check",
    )
    op.drop_column("reports", "request_payload_sha256")
