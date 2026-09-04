import math
import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    SecretStr,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from app.codes import (
    AgentRole,
    BaselineStatus,
    ClusteringLinkageMethod,
    ClusteringPolicyStatus,
    ClusterRepresentativeMethod,
    FieldStatus,
    IssueType,
    OrderAction,
    OrderType,
    ReportStatus,
    SignalClosureReason,
    SignalProcessingStatus,
    SignalStatus,
    SubmissionStatus,
    VerificationStatus,
)
from app.security import normalize_placeholders
from app.signal_lock import SignalLockDecision
from app.signal_relevance import SignalRelevanceStatus
from app.signal_verification import AgentSignalDecision

# FE-07 / AI-05: 원문에 날짜 없이 시각만 있으면(예: "09:03") CONFIRMED_FROM_TEXT로
# 확정할 수 없다. 날짜+시각+UTC offset이 모두 있는 완전한 형식일 때만 허용한다.
# 이 값은 masked_text(고객 원문)에서만 근거를 취하며, 이미지 첨부 등 텍스트 외
# 입력에서 유추한 시각은 여기 해당하지 않는다 (이미지는 저장만 하고 AI가
# 판단하지 않음 — 판단은 상담사 몫).
_FULL_DATETIME_WITH_OFFSET = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
# 한국어 표기("2026년 8월 15일") 외에, ISO형("2026-08-15" 또는 "26-09-28")·
# 점형("2026.08.15")·슬래시형("2026/08/15" 또는 "26/08/15") 날짜 표기도 근거로
# 인정한다. 대시형은 원래 계좌번호 정규식(숫자-숫자-숫자)과 겹칠까봐 4자리
# 연도만 허용했으나, 이 패턴은 evidence_quote 검증에만 쓰이고 evidence_quote는
# assert_no_unmasked_pii/validate_no_restored_pii 어디에서도 PII 스캔 대상이
# 아니므로(날짜 필드는 애초에 스캔에서 제외됨, app/ai.py의
# _DATETIME_FIELD_NAMES) 실제로 막던 위험이 없었다. 2자리 연도도 허용한다.
_KOREAN_FULL_DATE_PATTERN = re.compile(
    r"(?:\d{2}|\d{4})\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일"
    r"|(?:\d{2}|\d{4})\s*-\s*\d{1,2}\s*-\s*\d{1,2}"
    r"|(?:\d{2}|\d{4})\s*[./]\s*\d{1,2}\s*[./]\s*\d{1,2}"
)
# "23시 33분" 같은 한국어 표기 외에, "23:33" 같은 콜론 표기 시각도 근거로
# 인정한다. 시(0-23)·분(0-59) 범위를 벗어나는 값(예: "23:89")은 애초에
# 존재하지 않는 시각이므로 일부러 매칭하지 않는다.
_KOREAN_TIME_PATTERN = re.compile(
    r"(?:(?:오전|오후)\s*)?\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?"
    r"|(?:[01]?\d|2[0-3])\s*:\s*[0-5]\d"
)


def _evidence_contains_explicit_date_and_time(evidence: str | None) -> bool:
    """근거 문자열에 연·월·일과 시각이 모두 원문 그대로 명시되어 있는지 확인."""
    if not evidence:
        return False
    return bool(
        _KOREAN_FULL_DATE_PATTERN.search(evidence) and _KOREAN_TIME_PATTERN.search(evidence)
    )


class ApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )


class StrictAiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class CandidateField[T](StrictAiModel):
    value: T | None
    status: FieldStatus
    evidence_quote: str | None

    @field_validator("value", "evidence_quote", mode="before")
    @classmethod
    def normalize_placeholder_aliases(cls, value: object) -> object:
        # StrEnum is also a str instance.  Enum values have already been
        # converted by the adapter and must retain their runtime type for the
        # strict CandidateField schema.
        return normalize_placeholders(value) if type(value) is str else value

    @model_validator(mode="after")
    def validate_field_state(self) -> "CandidateField[T]":
        # AI-05: 근거 없는 값은 AI가 임의로 확정하지 않는다.
        # status별 value/evidence_quote 허용 규칙:
        #   CONFIRMED_FROM_TEXT   -> value 필수, evidence_quote 필수
        #   NEEDS_CONFIRMATION    -> value=null 필수, evidence_quote는 선택
        #                            (원문에 단서는 있으나 확정할 수 없는 경우,
        #                             근거만 남겨 상담원/운영자가 참고할 수 있게 한다)
        #   UNKNOWN, OUT_OF_SCOPE -> value=null 필수, evidence_quote=null 필수
        if self.status is FieldStatus.CONFIRMED_FROM_TEXT:
            if self.value is None or not self.evidence_quote:
                raise ValueError("CONFIRMED_FROM_TEXT fields require a value and evidence")
        elif self.status is FieldStatus.NEEDS_CONFIRMATION:
            if self.value is not None:
                raise ValueError("NEEDS_CONFIRMATION fields cannot contain a confirmed value")
        else:  # UNKNOWN, OUT_OF_SCOPE
            if self.value is not None or self.evidence_quote is not None:
                raise ValueError(f"{self.status.value} fields cannot contain a value or evidence")
        return self


class TechnicalCandidate(StrictAiModel):
    issue_type: CandidateField[IssueType]
    symptom: CandidateField[str]
    submission_status: CandidateField[SubmissionStatus]
    error_code: CandidateField[str]
    # FE-07: 날짜 없는 시각("09:03")을 표현해야 하므로 datetime이 아니라 str로 둔다.
    # 완전한 날짜+시각+offset이 있을 때만 CONFIRMED_FROM_TEXT를 허용한다 (아래 validator).
    reported_occurred_at: CandidateField[str]

    @model_validator(mode="after")
    def enforce_occurred_at_confirmation_rule(self) -> "TechnicalCandidate":
        field = self.reported_occurred_at
        if field.status is FieldStatus.CONFIRMED_FROM_TEXT:
            value_is_full_datetime = (
                field.value is not None
                and _FULL_DATETIME_WITH_OFFSET.fullmatch(field.value) is not None
            )
            evidence_has_explicit_date_and_time = _evidence_contains_explicit_date_and_time(
                field.evidence_quote
            )
            if not value_is_full_datetime or not evidence_has_explicit_date_and_time:
                raise ValueError(
                    "reported_occurred_at can be CONFIRMED_FROM_TEXT only when "
                    "value is a full ISO 8601 datetime with UTC offset and evidence_quote "
                    "explicitly contains year, month, day, and time from the source text. "
                    "Partial dates or date-less times must use NEEDS_CONFIRMATION."
                )
        return self


class ConsultationCandidate(StrictAiModel):
    action: CandidateField[OrderAction]
    symbol_name: CandidateField[str]
    symbol_code: CandidateField[str]
    quantity: CandidateField[int]
    order_type: CandidateField[OrderType]
    price_krw: CandidateField[int]
    attempted_at: CandidateField[str]

    @model_validator(mode="after")
    def enforce_attempted_at_confirmation_rule(self) -> "ConsultationCandidate":
        field = self.attempted_at
        if field.status is FieldStatus.CONFIRMED_FROM_TEXT:
            value_is_full_datetime = (
                field.value is not None
                and _FULL_DATETIME_WITH_OFFSET.fullmatch(field.value) is not None
            )
            evidence_has_explicit_date_and_time = _evidence_contains_explicit_date_and_time(
                field.evidence_quote
            )
            if not value_is_full_datetime or not evidence_has_explicit_date_and_time:
                raise ValueError(
                    "attempted_at can be CONFIRMED_FROM_TEXT only when value is a full "
                    "ISO 8601 datetime with UTC offset and evidence_quote explicitly contains "
                    "year, month, day, and time from the source text. Partial dates or "
                    "date-less times must use NEEDS_CONFIRMATION."
                )
        return self


class ExtractionResult(StrictAiModel):
    schema_version: str
    taxonomy_version: str
    adapter_name: str
    model_id: str | None
    technical: TechnicalCandidate
    consultation: ConsultationCandidate


class ReportCreateRequest(ApiModel):
    client_request_id: UUID4
    text: str

    @field_validator("text", mode="before")
    @classmethod
    def normalize_placeholder_aliases(cls, value: object) -> object:
        return normalize_placeholders(value) if isinstance(value, str) else value


class TechnicalConfirmation(ApiModel):
    issue_type: IssueType
    symptom: str | None = Field(default=None, min_length=1, max_length=500)
    submission_status: SubmissionStatus
    error_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")
    reported_occurred_at: datetime | None

    @field_validator("symptom", "error_code", mode="before")
    @classmethod
    def normalize_placeholder_aliases(cls, value: object) -> object:
        return normalize_placeholders(value) if isinstance(value, str) else value

    @field_validator("reported_occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("reported_occurred_at must include a UTC offset")
        return value


class ConsultationConfirmation(ApiModel):
    action: OrderAction
    symbol_name: str | None = Field(default=None, min_length=1, max_length=80)
    symbol_code: str | None = Field(default=None, pattern=r"^[0-9A-Z]{6}$")
    quantity: StrictInt | None = Field(default=None, gt=0)
    order_type: OrderType
    price_krw: StrictInt | None = Field(default=None, gt=0)
    attempted_at: datetime | None

    @field_validator("symbol_name", "symbol_code", mode="before")
    @classmethod
    def normalize_placeholder_aliases(cls, value: object) -> object:
        return normalize_placeholders(value) if isinstance(value, str) else value

    @field_validator("attempted_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("attempted_at must include a UTC offset")
        return value

    @model_validator(mode="after")
    def validate_market_price(self) -> "ConsultationConfirmation":
        if self.order_type is OrderType.MARKET and self.price_krw is not None:
            raise ValueError("market orders cannot contain price_krw")
        if self.order_type is OrderType.LIMIT and self.price_krw is None:
            raise ValueError("limit orders require price_krw")
        return self


class ReportConfirmationRequest(ApiModel):
    analysis_id: UUID
    analysis_version: StrictInt = Field(ge=1)
    attachment_id: UUID | None
    masked_text: str = Field(min_length=1, max_length=500)
    technical: TechnicalConfirmation
    consultation: ConsultationConfirmation
    client_request_id: UUID4

    @field_validator("masked_text", mode="before")
    @classmethod
    def normalize_placeholder_aliases(cls, value: object) -> object:
        return normalize_placeholders(value) if isinstance(value, str) else value


class AnalysisResponse(ApiModel):
    schema_version: str
    taxonomy_version: str
    technical: TechnicalCandidate
    consultation: ConsultationCandidate


class SafeError(ApiModel):
    code: str


class ReportResponse(ApiModel):
    id: UUID
    status: ReportStatus
    analysis_version: int
    masked_text: str
    analysis: AnalysisResponse | None
    error: SafeError | None
    received_at: datetime


class ProblemDetails(ApiModel):
    type: str
    title: str
    status: int
    detail: str | None = None
    code: str | None = None
    request_id: str | None = None
    errors: list[dict[str, str]] | None = None


class AttachmentResponse(ApiModel):
    id: UUID
    url: str


class ReportAnalysisPendingResponse(ApiModel):
    analysis_id: UUID
    analysis_version: int
    status: Literal["pending"]


class ReportAnalysisConfirmationResponse(ApiModel):
    analysis_id: UUID
    analysis_version: int
    status: Literal["confirmation"]
    attachment: AttachmentResponse | None
    masked_text: str
    masked_items: list[str]
    technical: TechnicalCandidate
    consultation: ConsultationCandidate


class ReportAnalysisFailedResponse(ApiModel):
    analysis_id: UUID
    analysis_version: int
    status: Literal["failed"]
    error: SafeError


class ReportAnalysisCompleteResponse(ApiModel):
    analysis_id: UUID
    analysis_version: int
    status: Literal["complete"]


class ReportAnalysisResponse(
    RootModel[
        Annotated[
            ReportAnalysisPendingResponse
            | ReportAnalysisConfirmationResponse
            | ReportAnalysisFailedResponse
            | ReportAnalysisCompleteResponse,
            Field(discriminator="status"),
        ]
    ]
):
    pass


class ConsultationCardIssued(ApiModel):
    reference_number: str = Field(pattern=r"^KBSOS-[A-Z2-7]{26}$")
    expires_at: datetime


class ReportConfirmedResponse(ApiModel):
    consultation_card: ConsultationCardIssued


class DiscardReportRequest(ApiModel):
    analysis_id: UUID
    client_request_id: UUID4


class DeleteConsultationCardRequest(ApiModel):
    reference_number: str = Field(pattern=r"^KBSOS-[A-Z2-7]{26}$")
    client_request_id: UUID4


class AgentLoginRequest(ApiModel):
    employee_id: str = Field(min_length=4, max_length=32, pattern=r"^[A-Z0-9_-]{4,32}$")
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("employee_id", mode="before")
    @classmethod
    def normalize_employee_id(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return unicodedata.normalize("NFC", value.strip()).upper()


class AgentLoginResponse(ApiModel):
    access_token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    token_type: Literal["bearer"]
    expires_at: datetime
    agent_label: str
    role: AgentRole


class ConsultationCardListItem(ApiModel):
    card_id: UUID
    received_at: datetime
    issued_at: datetime
    expires_at: datetime
    expired: bool
    can_open: bool
    consultation_status: Literal["OPEN", "VERIFIED"]
    technical_symptom: str | None
    verification_status: VerificationStatus | None


class ConsultationCardListResponse(ApiModel):
    items: list[ConsultationCardListItem]
    limit: int
    offset: int


class ConsultationCardSelector(ApiModel):
    reference_number: str | None = Field(
        default=None,
        pattern=r"^KBSOS-[A-Z2-7]{26}$",
    )
    card_id: UUID | None = None

    @field_validator("reference_number", mode="before")
    @classmethod
    def normalize_reference_number(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_exactly_one_selector(self) -> "ConsultationCardSelector":
        if (self.reference_number is None) == (self.card_id is None):
            raise ValueError("exactly one of reference_number or card_id is required")
        return self


class AgentTechnicalDetail(ApiModel):
    issue_type: IssueType
    symptom: str | None
    submission_status: SubmissionStatus
    error_code: str | None
    reported_occurred_at: datetime | None


class ConsultationCardLookupRequest(ConsultationCardSelector):
    pass


class RelatedSignal(ApiModel):
    signal_id: UUID
    status: SignalStatus
    reported_symptom_type: IssueType
    reporting_unique_sessions: int = Field(ge=1)
    last_report_at: datetime
    official_incident: Literal[False]
    relevance_status: SignalRelevanceStatus | None = None
    confirmation_questions: list[str] = Field(default_factory=list)
    locked_related: bool | None = None


class ConsultationCardDetail(ApiModel):
    card_id: UUID
    created_at: datetime
    expires_at: datetime
    technical: AgentTechnicalDetail
    consultation: ConsultationConfirmation
    verification_status: VerificationStatus | None
    safety_notice: str
    has_attachment: bool
    attachment_url: str | None
    related_signals: list[RelatedSignal]
    related_signal_state: Literal["ACTIVE", "CANDIDATE", "NONE"]


VerificationFieldName = Literal[
    "action",
    "symbol_name",
    "symbol_code",
    "quantity",
    "order_type",
    "price_krw",
    "submission_status",
]


class AgentVerificationRequest(ConsultationCardSelector):
    action: OrderAction
    symbol_name: str | None = Field(default=None, min_length=1, max_length=80)
    symbol_code: str | None = Field(default=None, pattern=r"^[0-9A-Z]{6}$")
    quantity: StrictInt | None = Field(default=None, gt=0)
    order_type: OrderType
    price_krw: StrictInt | None = Field(default=None, gt=0)
    submission_status: SubmissionStatus
    order_history_checked: StrictBool
    client_request_id: UUID4

    @field_validator("symbol_name", "symbol_code", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = normalize_placeholders(unicodedata.normalize("NFC", value.strip()))
        return normalized or None

    @model_validator(mode="after")
    def validate_order_price(self) -> "AgentVerificationRequest":
        if self.order_type is OrderType.MARKET and self.price_krw is not None:
            raise ValueError("market orders cannot contain price_krw")
        if self.order_type is OrderType.LIMIT and self.price_krw is None:
            raise ValueError("limit orders require price_krw")
        return self


class VerificationFieldResult(ApiModel):
    field: VerificationFieldName
    status: VerificationStatus
    customer_value: str | int | None
    agent_value: str | int | None


class AgentVerificationResponse(ApiModel):
    verification_id: UUID
    status: VerificationStatus
    fields: list[VerificationFieldResult]
    mismatch_fields: list[VerificationFieldName]
    saved_at: datetime


class AgentSignalVerificationRequest(ConsultationCardSelector):
    signal_id: UUID
    decision: AgentSignalDecision
    client_request_id: UUID4


class AgentSignalVerificationResponse(ApiModel):
    signal_id: UUID
    relevance_status: SignalRelevanceStatus
    agent_decision: AgentSignalDecision
    verification_status: VerificationStatus
    final_related: bool | None
    lock_decision: SignalLockDecision
    saved_at: datetime


class SignalEmbeddingRequest(StrictAiModel):
    schema_version: Literal["signal-embedding-request.v1"]
    input_format: str
    technical_symptom: str


class SignalEmbeddingResult(StrictAiModel):
    model_id: str
    model_revision: str
    dimension: StrictInt = Field(gt=0)
    normalization: Literal["L2", "NONE"]
    input_format: str
    distance_metric: Literal["COSINE"]
    vector: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_vector(self) -> "SignalEmbeddingResult":
        if len(self.vector) != self.dimension:
            raise ValueError("vector length must equal dimension")
        if not all(math.isfinite(value) for value in self.vector):
            raise ValueError("vector values must be finite")
        if not any(value != 0 for value in self.vector):
            raise ValueError("vector must not be all zeros")
        return self


class SignalDashboardItem(ApiModel):
    signal_id: UUID
    status: Literal[SignalStatus.SIGNAL_DETECTED, SignalStatus.UNDER_REVIEW]
    channel: str
    feature_area: str
    reported_symptom_type: IssueType
    reporting_unique_sessions: int = Field(ge=1)
    raw_report_count: int = Field(ge=1)
    review_priority: bool
    first_report_at: datetime
    last_report_at: datetime
    affected_features: list[str]
    policy_version: str
    policy_status: str
    baseline_status: BaselineStatus
    baseline_ratio: float | None = Field(default=None, ge=0)
    official_incident: Literal[False]
    official_notice_url: str | None

    @model_validator(mode="after")
    def validate_baseline(self) -> "SignalDashboardItem":
        if (self.baseline_status is BaselineStatus.AVAILABLE) != (self.baseline_ratio is not None):
            raise ValueError("baseline ratio is required only when baseline is available")
        return self


class SignalHourlyVolume(ApiModel):
    bucket_start: datetime
    raw_report_count: int = Field(ge=1)
    reporting_unique_sessions: int = Field(ge=1)


class SignalPolicySnapshot(ApiModel):
    policy_version: str
    status: ClusteringPolicyStatus
    window_seconds: int = Field(gt=0)
    min_unique_sessions: int = Field(gt=0)
    review_priority_threshold: int = Field(gt=0)
    similarity_threshold: float = Field(gt=0, le=1)
    linkage_method: ClusteringLinkageMethod
    representative_method: ClusterRepresentativeMethod
    structured_rules_version: str
    taxonomy_version: str
    baseline_policy_version: str | None


class OperatorApproveSignalPolicyRequest(ApiModel):
    policy_version: str = Field(min_length=1, max_length=64)
    evaluation_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    client_request_id: UUID4


class OperatorSignalPolicyApprovalResponse(ApiModel):
    policy_version: str
    status: ClusteringPolicyStatus
    approved_by: UUID
    approved_at: datetime
    evaluation_artifact_sha256: str


class OperationalMetricsResponse(ApiModel):
    observed_at: datetime
    signal_jobs_ready: int = Field(ge=0)
    signal_jobs_repeated_failures: int = Field(ge=0)
    signal_jobs_dead_letter: int = Field(ge=0)
    provider_failures_last_15m: int = Field(ge=0)
    object_deletion_jobs_ready: int = Field(ge=0)
    object_deletion_jobs_retrying: int = Field(ge=0)


class SignalDashboardResponse(ApiModel):
    updated_at: datetime
    items: list[SignalDashboardItem]
    hourly_volume: list[SignalHourlyVolume]
    applied_policy: SignalPolicySnapshot | None
    baseline_status: BaselineStatus
    baseline_ratio: float | None = Field(default=None, ge=0)
    limit: int
    offset: int

    @model_validator(mode="after")
    def validate_baseline(self) -> "SignalDashboardResponse":
        if (self.baseline_status is BaselineStatus.AVAILABLE) != (self.baseline_ratio is not None):
            raise ValueError("baseline ratio is required only when baseline is available")
        return self


class OperatorSignalListItem(ApiModel):
    signal_id: UUID
    status: SignalStatus
    closure_reason: SignalClosureReason | None
    channel: str
    feature_area: str
    reported_symptom_type: IssueType
    representative_symptom_text: str | None
    reporting_unique_sessions: int = Field(ge=0)
    raw_report_count: int = Field(ge=0)
    review_priority: bool
    first_report_at: datetime
    last_report_at: datetime
    window_expires_at: datetime
    public_visible: bool
    policy_version: str
    policy_status: ClusteringPolicyStatus
    official_notice_url: str | None
    closed_at: datetime | None


class OperatorSignalListResponse(ApiModel):
    updated_at: datetime
    items: list[OperatorSignalListItem]
    limit: int
    offset: int


class SignalProcessingResult(ApiModel):
    job_id: UUID
    status: SignalProcessingStatus
    signal_id: UUID | None
    safe_error_code: str | None


class OperatorSignalSelector(ApiModel):
    signal_id: UUID
    client_request_id: UUID4


class OperatorAcknowledgeSignalRequest(OperatorSignalSelector):
    reason: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")


class OperatorCloseSignalRequest(OperatorSignalSelector):
    closure_reason: SignalClosureReason


class OperatorMergeSignalsRequest(ApiModel):
    source_signal_id: UUID
    target_signal_id: UUID
    reason: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    client_request_id: UUID4

    @model_validator(mode="after")
    def reject_same_signal(self) -> "OperatorMergeSignalsRequest":
        if self.source_signal_id == self.target_signal_id:
            raise ValueError("source and target signals must differ")
        return self


class OperatorSplitSignalRequest(OperatorSignalSelector):
    report_ids: list[UUID] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")

    @field_validator("report_ids")
    @classmethod
    def reject_duplicate_report_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("report_ids must be unique")
        return value


class OperatorOfficialNoticeRequest(OperatorSignalSelector):
    official_notice_url: str = Field(
        min_length=8,
        max_length=2048,
        pattern=r"^https://[^\s]+$",
    )


class OperatorSignalMutationResponse(ApiModel):
    signal_id: UUID
    status: SignalStatus
    closure_reason: SignalClosureReason | None
    reporting_unique_sessions: int = Field(ge=0)
    raw_report_count: int = Field(ge=0)
    official_notice_url: str | None
    changed_at: datetime
