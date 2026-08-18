from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
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
    issue_type: CandidateField[IssueType]
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
