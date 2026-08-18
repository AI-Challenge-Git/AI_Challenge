import re
from datetime import datetime
from uuid import UUID

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.codes import (
    FieldStatus,
    IssueType,
    OrderAction,
    OrderType,
    ReportStatus,
    SubmissionStatus,
)

# FE-07 / AI-05: 원문에 날짜 없이 시각만 있으면(예: "09:03") CONFIRMED_FROM_TEXT로
# 확정할 수 없다. 날짜+시각+UTC offset이 모두 있는 완전한 형식일 때만 허용한다.
# 이 값은 masked_text(고객 원문)에서만 근거를 취하며, 이미지 첨부 등 텍스트 외
# 입력에서 유추한 시각은 여기 해당하지 않는다 (이미지는 저장만 하고 AI가
# 판단하지 않음 — 판단은 상담사 몫).
_FULL_DATETIME_WITH_OFFSET = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
_KOREAN_FULL_DATE_PATTERN = re.compile(
    r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일"
)
_KOREAN_TIME_PATTERN = re.compile(
    r"(?:(?:오전|오후)\s*)?\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?"
)


def _evidence_contains_explicit_date_and_time(evidence: str | None) -> bool:
    """근거 문자열에 연·월·일과 시각이 모두 원문 그대로 명시되어 있는지 확인."""
    if not evidence:
        return False
    return bool(
        _KOREAN_FULL_DATE_PATTERN.search(evidence)
        and _KOREAN_TIME_PATTERN.search(evidence)
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
                raise ValueError(
                    "CONFIRMED_FROM_TEXT fields require a value and evidence"
                )
        elif self.status is FieldStatus.NEEDS_CONFIRMATION:
            if self.value is not None:
                raise ValueError(
                    "NEEDS_CONFIRMATION fields cannot contain a confirmed value"
                )
        else:  # UNKNOWN, OUT_OF_SCOPE
            if self.value is not None or self.evidence_quote is not None:
                raise ValueError(
                    f"{self.status.value} fields cannot contain a value or evidence"
                )
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
            evidence_has_explicit_date_and_time = (
                _evidence_contains_explicit_date_and_time(field.evidence_quote)
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
            evidence_has_explicit_date_and_time = (
                _evidence_contains_explicit_date_and_time(field.evidence_quote)
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


class TechnicalConfirmation(ApiModel):
    issue_type: IssueType
    symptom: str | None = Field(default=None, min_length=1, max_length=500)
    submission_status: SubmissionStatus
    error_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")
    reported_occurred_at: datetime | None

    @field_validator("reported_occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("reported_occurred_at must include a UTC offset")
        return value


class ConsultationConfirmation(ApiModel):
    action: OrderAction
    symbol_name: str | None = Field(default=None, min_length=1, max_length=80)
    symbol_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    quantity: StrictInt | None = Field(default=None, gt=0)
    order_type: OrderType
    price_krw: StrictInt | None = Field(default=None, gt=0)
    attempted_at: datetime | None

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
        return self


class ReportConfirmationRequest(ApiModel):
    analysis_version: StrictInt = Field(ge=1)
    technical_symptom: TechnicalConfirmation
    consultation: ConsultationConfirmation


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
