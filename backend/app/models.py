from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
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

from app.codes import (
    AgentRole,
    AnalysisStatus,
    AuditOutcome,
    IdempotencyStatus,
    ObjectDeletionStatus,
    RateLimitScope,
    ReportStatus,
    VerificationStatus,
)
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


class SymbolMasterVersion(Base):
    __tablename__ = "symbol_master_versions"
    __table_args__ = (
        CheckConstraint("char_length(btrim(version)) > 0", name="version_not_empty"),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="source_sha256_lower_hex",
        ),
        CheckConstraint(
            "source_encoding IN ('UTF-8-SIG', 'CP949')",
            name="source_encoding_value",
        ),
        CheckConstraint("row_count > 0", name="row_count_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    source_sha256: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)
    source_encoding: Mapped[str] = mapped_column(String(16), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    symbols: Mapped[list["Symbol"]] = relationship(
        back_populates="master_version", cascade="all, delete-orphan"
    )


Index(
    "uq_symbol_master_versions_active",
    SymbolMasterVersion.is_active,
    unique=True,
    postgresql_where=SymbolMasterVersion.is_active.is_(True),
)


class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        UniqueConstraint("master_version_id", "code"),
        CheckConstraint("code ~ '^[0-9A-Z]{6}$'", name="code_format"),
        CheckConstraint("char_length(btrim(name_ko)) > 0", name="name_ko_not_empty"),
        CheckConstraint("market IN ('KOSPI', 'KOSDAQ')", name="market_value"),
        CheckConstraint(
            "source_market IN ('KOSPI', 'KOSDAQ', 'KOSDAQ GLOBAL')",
            name="source_market_value",
        ),
        CheckConstraint("stock_type = '보통주'", name="stock_type_common"),
        Index("ix_symbols_master_version_id", "master_version_id"),
        Index("ix_symbols_code", "code"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    master_version_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("symbol_master_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(80), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    source_market: Mapped[str] = mapped_column(String(20), nullable=False)
    stock_type: Mapped[str] = mapped_column(String(16), nullable=False)

    master_version: Mapped[SymbolMasterVersion] = relationship(back_populates="symbols")


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
    purge_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC) + timedelta(hours=72),
        nullable=False,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    policy_snapshot: Mapped[PolicySnapshot] = relationship(back_populates="reports")
    analyses: Mapped[list["ReportAnalysis"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    attachment: Mapped["Attachment | None"] = relationship(
        back_populates="report", cascade="all, delete-orphan", uselist=False
    )
    technical_symptom: Mapped["TechnicalSymptom | None"] = relationship(
        back_populates="report", cascade="all, delete-orphan", uselist=False
    )
    consultation_card: Mapped["ConsultationCard | None"] = relationship(
        back_populates="report", cascade="all, delete-orphan", uselist=False
    )


Index("ix_reports_received_at", Report.received_at.desc())
Index("ix_reports_session_received", Report.session_digest, Report.received_at.desc())
Index("ix_reports_purge_at", Report.purge_at)


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


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint(
            "object_key ~ '^[A-Za-z0-9_-]{43}$'",
            name="object_key_format",
        ),
        CheckConstraint(
            "content_type IN ('image/png', 'image/jpeg', 'image/webp')",
            name="content_type_value",
        ),
        CheckConstraint(
            "byte_size BETWEEN 1 AND 5242880",
            name="byte_size_range",
        ),
        CheckConstraint(
            "width BETWEEN 1 AND 4096 AND height BETWEEN 1 AND 4096 AND width * height <= 16000000",
            name="dimensions_range",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="content_sha256_lower_hex",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("reports.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(String(43), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(16), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    report: Mapped[Report] = relationship(back_populates="attachment")


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
        CheckConstraint("action IN ('BUY', 'SELL', 'UNKNOWN')", name="action_value"),
        CheckConstraint(
            "symbol_code IS NULL OR symbol_code ~ '^[0-9A-Z]{6}$'",
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
        Index("ix_consultation_cards_symbol_master_version_id", "symbol_master_version_id"),
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
    symbol_master_version_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("symbol_master_versions.id", ondelete="RESTRICT"),
    )
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
    verifications: Mapped[list["AgentVerification"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )


class AgentAccount(Base):
    __tablename__ = "agent_accounts"
    __table_args__ = (
        CheckConstraint(
            "employee_id ~ '^[A-Z0-9_-]{4,32}$'",
            name="employee_id_format",
        ),
        CheckConstraint(
            "char_length(btrim(agent_label)) BETWEEN 1 AND 80",
            name="agent_label_length",
        ),
        CheckConstraint(
            "role IN ('AGENT', 'OPERATOR')",
            name="role_value",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    employee_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    agent_label: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=AgentRole.AGENT.value, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    access_tokens: Mapped[list["AgentAccessToken"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    verifications: Mapped[list["AgentVerification"]] = relationship(back_populates="agent")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="actor")


class AgentAccessToken(Base):
    __tablename__ = "agent_access_tokens"
    __table_args__ = (
        CheckConstraint("octet_length(token_digest) = 32", name="token_digest_length"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_agent_access_tokens_agent_id", "agent_id"),
        Index("ix_agent_access_tokens_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    agent_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_digest: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    agent: Mapped[AgentAccount] = relationship(back_populates="access_tokens")


class AgentVerification(Base):
    __tablename__ = "agent_verifications"
    __table_args__ = (
        UniqueConstraint("agent_id", "client_request_id"),
        CheckConstraint("action IN ('BUY', 'SELL', 'UNKNOWN')", name="action_value"),
        CheckConstraint(
            "symbol_code IS NULL OR symbol_code ~ '^[0-9A-Z]{6}$'",
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
            "submission_status IN ('CUSTOMER_REPORTED_SUBMITTED', "
            "'CUSTOMER_REPORTED_NOT_SUBMITTED', 'UNKNOWN')",
            name="submission_status_value",
        ),
        CheckConstraint(
            "overall_status IN ('MATCHED', 'NEEDS_CONFIRMATION', 'IMPORTANT')",
            name="overall_status_value",
        ),
        Index("ix_agent_verifications_card_id", "card_id"),
        Index("ix_agent_verifications_agent_id", "agent_id"),
        Index("ix_agent_verifications_symbol_master_version_id", "symbol_master_version_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    card_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("consultation_cards.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    client_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol_name: Mapped[str | None] = mapped_column(String(80))
    symbol_code: Mapped[str | None] = mapped_column(String(6))
    symbol_master_version_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("symbol_master_versions.id", ondelete="RESTRICT"),
    )
    quantity: Mapped[int | None] = mapped_column(BigInteger)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    price_krw: Mapped[int | None] = mapped_column(BigInteger)
    submission_status: Mapped[str] = mapped_column(String(40), nullable=False)
    order_history_checked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    overall_status: Mapped[str] = mapped_column(
        String(24), default=VerificationStatus.MATCHED.value, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    card: Mapped[ConsultationCard] = relationship(back_populates="verifications")
    agent: Mapped[AgentAccount] = relationship(back_populates="verifications")


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        UniqueConstraint("scope", "principal_fingerprint", "client_fingerprint"),
        CheckConstraint(
            "scope IN ('AGENT_LOGIN_FAILURE', 'AGENT_CARD_LOOKUP')",
            name="scope_value",
        ),
        CheckConstraint(
            "octet_length(principal_fingerprint) = 32",
            name="principal_fingerprint_length",
        ),
        CheckConstraint(
            "octet_length(client_fingerprint) = 32",
            name="client_fingerprint_length",
        ),
        CheckConstraint("request_count > 0", name="request_count_positive"),
        CheckConstraint("expires_at > window_started_at", name="expiry_after_window_start"),
        Index("ix_rate_limit_buckets_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    scope: Mapped[str] = mapped_column(
        String(32), default=RateLimitScope.AGENT_LOGIN_FAILURE.value, nullable=False
    )
    principal_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    client_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


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
        CheckConstraint("processing_status = 'COMPLETED'", name="processing_status_value"),
        CheckConstraint(
            "safe_failure_code IS NULL OR safe_failure_code IN "
            "('TIMEOUT', 'INVALID_SCHEMA', 'PROVIDER_UNAVAILABLE')",
            name="safe_failure_code_value",
        ),
        Index("ix_idempotency_records_purge_at", "purge_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    principal_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    client_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    safe_failure_code: Mapped[str | None] = mapped_column(String(64))
    processing_status: Mapped[str] = mapped_column(
        String(16), default=IdempotencyStatus.COMPLETED.value, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    purge_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC) + timedelta(hours=72),
        nullable=False,
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "resource_fingerprint ~ '^[0-9a-f]{64}$'",
            name="resource_fingerprint_lower_hex",
        ),
        CheckConstraint(
            "outcome IN ('SUCCESS', 'FAILURE', 'RATE_LIMITED', 'REPLAY', 'CONFLICT')",
            name="outcome_value",
        ),
        Index("ix_audit_logs_actor_id", "actor_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("agent_accounts.id", ondelete="RESTRICT"),
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(
        String(16), default=AuditOutcome.SUCCESS.value, server_default="SUCCESS", nullable=False
    )
    resource_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor: Mapped[AgentAccount | None] = relationship(back_populates="audit_logs")


class ObjectDeletionJob(Base):
    __tablename__ = "object_deletion_jobs"
    __table_args__ = (
        CheckConstraint(
            "object_key ~ '^[A-Za-z0-9_-]{43}$'",
            name="object_key_format",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'RETRY_PENDING', 'COMPLETED')",
            name="status_value",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "safe_error_code IS NULL OR safe_error_code = 'STORAGE_UNAVAILABLE'",
            name="safe_error_code_value",
        ),
        CheckConstraint(
            "((status = 'PENDING' AND attempt_count = 0 AND safe_error_code IS NULL "
            "AND next_attempt_at IS NOT NULL AND completed_at IS NULL AND purge_at IS NULL) OR "
            "(status = 'PROCESSING' AND attempt_count > 0 AND safe_error_code IS NULL "
            "AND next_attempt_at IS NOT NULL AND completed_at IS NULL AND purge_at IS NULL) OR "
            "(status = 'RETRY_PENDING' AND attempt_count > 0 AND safe_error_code IS NOT NULL "
            "AND next_attempt_at IS NOT NULL AND completed_at IS NULL AND purge_at IS NULL) OR "
            "(status = 'COMPLETED' AND attempt_count > 0 AND safe_error_code IS NULL "
            "AND next_attempt_at IS NULL AND completed_at IS NOT NULL AND purge_at IS NOT NULL))",
            name="state_metadata",
        ),
        Index("ix_object_deletion_jobs_ready", "status", "next_attempt_at"),
        Index("ix_object_deletion_jobs_purge_at", "purge_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    object_key: Mapped[str] = mapped_column(String(43), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=ObjectDeletionStatus.PENDING.value, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
