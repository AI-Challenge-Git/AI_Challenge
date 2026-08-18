from functools import lru_cache
from typing import Protocol

from app.codes import FieldStatus
from app.schemas import (
    CandidateField,
    ConsultationCandidate,
    ExtractionResult,
    TechnicalCandidate,
)


class DualExtractor(Protocol):
    schema_version: str
    taxonomy_version: str
    adapter_name: str
    model_id: str | None

    async def extract(self, masked_text: str) -> ExtractionResult: ...


def _unknown_candidate[T]() -> CandidateField[T]:
    return CandidateField(value=None, status=FieldStatus.UNKNOWN, evidence_quote=None)


class FakeDualExtractor:
    """Deterministic contract fixture used until the AI-owned schema is approved."""

    schema_version = "dual-extraction.fake.v1"
    taxonomy_version = "issue-taxonomy.pending"
    adapter_name = "fake"
    model_id: str | None = None

    async def extract(self, masked_text: str) -> ExtractionResult:
        if not masked_text:
            raise ValueError("masked_text cannot be empty")

        return ExtractionResult(
            schema_version=self.schema_version,
            taxonomy_version=self.taxonomy_version,
            adapter_name=self.adapter_name,
            model_id=self.model_id,
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


@lru_cache
def get_dual_extractor() -> DualExtractor:
    return FakeDualExtractor()


def validate_evidence_quotes(result: ExtractionResult, masked_text: str) -> None:
    for section in (result.technical, result.consultation):
        for field_name in type(section).model_fields:
            quote = getattr(section, field_name).evidence_quote
            if quote is not None and quote not in masked_text:
                raise ValueError("AI evidence must be a substring of masked_text")
