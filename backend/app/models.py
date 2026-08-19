from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.codes import AnalysisStatus, ReportStatus
from app.db import Base


class PolicySnapshot(Base):
    __tablename__ = "policy_snapshots"
    __table_args__ = (
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="content_sha256_lower_hex",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_checked_on: Mapped[date] = mapped_column(Date, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    reports: Mapped[list["Report"]] = relationship(back_populates="policy_snapshot")


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("session_digest", "client_request_id"),
        CheckConstraint("octet_length(session_digest) = 32", name="session_digest_length"),
        CheckConstraint("char_length(masked_text) > 0", name="masked_text_not_empty"),
        CheckConstraint(
            "request_payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="request_payload_sha256_lower_hex",
        ),
        CheckConstraint(
            "status IN ('ANALYSIS_PENDING', 'AWAITING_CONFIRMATION', "
            "'CONFIRMED', 'ANALYSIS_FAILED')",
            name="status_value",
        ),
        CheckConstraint(
            "((status = 'CONFIRMED' AND confirmed_at IS NOT NULL) OR "
            "(status <> 'CONFIRMED' AND confirmed_at IS NULL))",
            name="confirmed_at_matches_status",
        ),
        Index("ix_reports_policy_snapshot_id", "policy_snapshot_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    client_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    policy_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("policy_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pii_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    masked_text: Mapped[str] = mapped_column(Text, nullable=False)
    request_payload_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=ReportStatus.ANALYSIS_PENDING.value, nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    policy_snapshot: Mapped[PolicySnapshot] = relationship(back_populates="reports")
    analyses: Mapped[list["ReportAnalysis"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    technical_symptom: Mapped["TechnicalSymptom | None"] = relationship(
        back_populates="report", cascade="all, delete-orphan", uselist=False
    )
    consultation_card: Mapped["ConsultationCard | None"] = relationship(
        back_populates="report", cascade="all, delete-orphan", uselist=False
    )


Index("ix_reports_received_at", Report.received_at.desc())
Index("ix_reports_session_received", Report.session_digest, Report.received_at.desc())


class ReportAnalysis(Base):
    __tablename__ = "report_analyses"
    __table_args__ = (
        UniqueConstraint("report_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("status IN ('PENDING', 'SUCCEEDED', 'FAILED')", name="status_value"),
        CheckConstraint(
            "(status <> 'PENDING' OR (technical_candidate IS NULL AND "
            "consultation_candidate IS NULL AND safe_error_code IS NULL AND "
            "completed_at IS NULL))",
            name="pending_payload",
        ),
        CheckConstraint(
            "(status <> 'SUCCEEDED' OR (technical_candidate IS NOT NULL AND "
            "consultation_candidate IS NOT NULL AND safe_error_code IS NULL AND "
            "completed_at IS NOT NULL))",
            name="succeeded_payload",
        ),
        CheckConstraint(
            "(status <> 'FAILED' OR (technical_candidate IS NULL AND "
            "consultation_candidate IS NULL AND safe_error_code IS NOT NULL AND "
            "completed_at IS NOT NULL))",
            name="failed_payload",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        String(16), default=AnalysisStatus.PENDING.value, nullable=False
    )
    technical_candidate: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    consultation_candidate: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    report: Mapped[Report] = relationship(back_populates="analyses")


class TechnicalSymptom(Base):
    __tablename__ = "technical_symptoms"
    __table_args__ = (
        CheckConstraint("channel = 'MABLE'", name="channel_value"),
        CheckConstraint(
            "feature_area = 'DOMESTIC_STOCK_ORDER'",
            name="feature_area_value",
        ),
        CheckConstraint(
            "issue_type ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="issue_type_format",
        ),
        CheckConstraint(
            "symptom IS NULL OR char_length(btrim(symptom)) BETWEEN 1 AND 500",
            name="symptom_length",
        ),
        CheckConstraint(
            "submission_status IN ('CUSTOMER_REPORTED_SUBMITTED', "
            "'CUSTOMER_REPORTED_NOT_SUBMITTED', 'UNKNOWN')",
            name="submission_status_value",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[A-Za-z0-9._-]{1,64}$'",
            name="error_code_format",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("reports.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    taxonomy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_area: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    symptom: Mapped[str | None] = mapped_column(String(500))
    submission_status: Mapped[str] = mapped_column(String(40), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    reported_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    report: Mapped[Report] = relationship(back_populates="technical_symptom")


class ConsultationCard(Base):
    __tablename__ = "consultation_cards"
    __table_args__ = (
        CheckConstraint("action IN ('SELL', 'UNKNOWN')", name="action_value"),
        CheckConstraint(
            "symbol_code IS NULL OR symbol_code ~ '^[0-9]{6}$'",
            name="symbol_code_format",
        ),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "order_type IN ('LIMIT', 'MARKET', 'UNKNOWN')",
            name="order_type_value",
        ),
        CheckConstraint("price_krw IS NULL OR price_krw > 0", name="price_krw_positive"),
        CheckConstraint(
            "order_type <> 'MARKET' OR price_krw IS NULL",
            name="market_order_without_price",
        ),
        CheckConstraint(
            "order_type <> 'LIMIT' OR price_krw IS NOT NULL",
            name="limit_order_requires_price",
        ),
        CheckConstraint(
            "reference_digest IS NULL OR octet_length(reference_digest) = 32",
            name="reference_digest_length",
        ),
        CheckConstraint(
            "confirmation_payload_sha256 IS NULL OR confirmation_payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="confirmation_payload_sha256_lower_hex",
        ),
        CheckConstraint(
            "((reference_digest IS NULL AND expires_at IS NULL AND "
            "confirmation_request_id IS NULL AND confirmation_payload_sha256 IS NULL) OR "
            "(reference_digest IS NOT NULL AND expires_at IS NOT NULL AND "
            "confirmation_request_id IS NOT NULL AND confirmation_payload_sha256 IS NOT NULL))",
            name="reference_metadata_complete",
        ),
        Index("ix_consultation_cards_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("reports.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol_name: Mapped[str | None] = mapped_column(String(80))
    symbol_code: Mapped[str | None] = mapped_column(String(6))
    quantity: Mapped[int | None] = mapped_column(BigInteger)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    price_krw: Mapped[int | None] = mapped_column(BigInteger)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference_digest: Mapped[bytes | None] = mapped_column(LargeBinary(32), unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_request_id: Mapped[UUID | None] = mapped_column(Uuid)
    confirmation_payload_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    report: Mapped[Report] = relationship(back_populates="consultation_card")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("principal_digest", "operation", "client_request_id"),
        CheckConstraint("octet_length(principal_digest) = 32", name="principal_digest_length"),
        CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="payload_sha256_lower_hex",
        ),
        CheckConstraint("response_status BETWEEN 200 AND 599", name="response_status_range"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    principal_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    client_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "resource_fingerprint ~ '^[0-9a-f]{64}$'",
            name="resource_fingerprint_lower_hex",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
