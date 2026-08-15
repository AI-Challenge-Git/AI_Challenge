from typing import Protocol

from app.codes import FieldStatus, IssueType
from app.schemas import (
    CandidateField,
    ConsultationCandidate,
    ExtractionResult,
    TechnicalCandidate,
)

# AI-11: 마스킹 placeholder는 실제 값으로 복원·추론하지 않는다.
_PLACEHOLDER_TOKENS = frozenset({"[PHONE]", "[ACCOUNT]", "[EMAIL]"})


class DualExtractor(Protocol):
    def extract(self, masked_text: str) -> ExtractionResult: ...


def _unknown_candidate[T]() -> CandidateField[T]:
    return CandidateField(value=None, status=FieldStatus.UNKNOWN, evidence_quote=None)


class FakeDualExtractor:
    """Deterministic contract fixture used until the AI-owned schema is approved."""

    def extract(self, masked_text: str) -> ExtractionResult:
        if not masked_text:
            raise ValueError("masked_text cannot be empty")

        return ExtractionResult(
            schema_version="dual-extraction.v1",
            taxonomy_version="issue-type.v1",
            adapter_name="fake",
            model_id=None,
            technical=TechnicalCandidate(
                issue_type=_unknown_candidate(),
                symptom=_unknown_candidate(),
                submission_status=_unknown_candidate(),
                error_code=_unknown_candidate(),
                reported_occurred_at=_unknown_candidate(),
            ),
            consultation=ConsultationCandidate(
                action=_unknown_candidate(),
                symbol_name=_unknown_candidate(),
                symbol_code=_unknown_candidate(),
                quantity=_unknown_candidate(),
                order_type=_unknown_candidate(),
                price_krw=_unknown_candidate(),
                attempted_at=_unknown_candidate(),
            ),
        )


def validate_evidence_quotes(result: ExtractionResult, masked_text: str) -> None:
    """AI-03: evidence_quote는 masked_text의 실제 substring이어야 한다."""
    for section in (result.technical, result.consultation):
        for field_name in type(section).model_fields:
            quote = getattr(section, field_name).evidence_quote
            if quote is not None and quote not in masked_text:
                raise ValueError(
                    f"{field_name}: AI evidence must be a substring of masked_text"
                )


def validate_placeholder_integrity(result: ExtractionResult) -> None:
    """
    AI-11: evidence_quote가 마스킹 placeholder([PHONE] 등)를 가리키는 경우,
    value는 그 placeholder를 실제 값으로 추론·복원한 결과여서는 안 된다.
    """
    for section in (result.technical, result.consultation):
        for field_name in type(section).model_fields:
            field = getattr(section, field_name)
            if field.evidence_quote in _PLACEHOLDER_TOKENS:
                if field.value not in _PLACEHOLDER_TOKENS:
                    raise ValueError(
                        f"{field_name}: placeholder evidence cannot resolve to "
                        "an inferred concrete value"
                    )


def validate_extraction_result(result: ExtractionResult, masked_text: str) -> None:
    """
    분석 결과 전체를 검증한다. 하나라도 위반하면 응답 전체를 거부한다
    (개별 필드가 아니라 ExtractionResult 단위로 실패 처리).
    """
    validate_evidence_quotes(result, masked_text)
    validate_placeholder_integrity(result)
