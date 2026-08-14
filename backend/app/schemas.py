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
    OrderAction,
    OrderType,
    ReportStatus,
    SubmissionStatus,
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
    def validate_unknown_value(self) -> "CandidateField[T]":
        if self.status is FieldStatus.UNKNOWN and (
            self.value is not None or self.evidence_quote is not None
        ):
            raise ValueError("UNKNOWN fields cannot contain a value or evidence")
        if self.status is FieldStatus.CONFIRMED_FROM_TEXT and (
            self.value is None or not self.evidence_quote
        ):
            raise ValueError("confirmed fields require a value and evidence")
        return self


class TechnicalCandidate(StrictAiModel):
    issue_type: CandidateField[str]
    symptom: CandidateField[str]
    submission_status: CandidateField[SubmissionStatus]
    error_code: CandidateField[str]
    reported_occurred_at: CandidateField[datetime]


class ConsultationCandidate(StrictAiModel):
    action: CandidateField[OrderAction]
    symbol_name: CandidateField[str]
    symbol_code: CandidateField[str]
    quantity: CandidateField[int]
    order_type: CandidateField[OrderType]
    price_krw: CandidateField[int]
    attempted_at: CandidateField[datetime]


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
    issue_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
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
