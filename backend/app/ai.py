import asyncio
from functools import lru_cache
from typing import Protocol

from app.codes import FieldStatus
from app.config import get_settings
from app.real_extractor_v5 import ExtractFailureReason, RealDualExtractor
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


class NvidiaDualExtractorAdapter:
    """
    DualExtractor Protocol에 RealDualExtractor(NVIDIA Build, 8B)를 연결하는 어댑터.

    - Protocol이 요구하는 클래스 속성(schema_version 등)을 노출한다.
    - extract_safe()의 ExtractOutcome(반환값 기반 성공/실패 표현)을
      analyze_report()가 기대하는 "성공 시 반환값, 실패 시 예외" 방식으로 변환한다.
    - extract_safe()는 동기(blocking) 함수이므로 asyncio.to_thread로 감싸서
      FastAPI 이벤트 루프를 막지 않게 한다.

    주의: settings.ai_timeout_seconds의 기본 90초는 adapter 전체 호출 예산이다.
    NVIDIA 내부 provider 호출과 correction retry도 이 전체 예산 안에 끝나야 한다.
    """

    schema_version = "dual-extraction.v1"
    taxonomy_version = "issue-type.v1"
    adapter_name = "nvidia-build"
    model_id: str | None = "openai/gpt-oss-20b"

    def __init__(self) -> None:
        self._inner = RealDualExtractor()

    async def extract(self, masked_text: str) -> ExtractionResult:
        outcome = await asyncio.to_thread(self._inner.extract_safe, masked_text)

        if outcome.result is None:
            if outcome.failure_reason == ExtractFailureReason.TIMEOUT:
                raise TimeoutError(outcome.detail)
            if outcome.failure_reason in (
                ExtractFailureReason.INVALID_JSON,
                ExtractFailureReason.INVALID_SCHEMA,
            ):
                raise ValueError(outcome.detail)
            raise RuntimeError(outcome.detail)  # PROVIDER_UNAVAILABLE 등

        return outcome.result


@lru_cache
def get_dual_extractor() -> DualExtractor:
    if get_settings().ai_adapter == "fake":
        return FakeDualExtractor()
    return NvidiaDualExtractorAdapter()


def validate_evidence_quotes(result: ExtractionResult, masked_text: str) -> None:
    for section in (result.technical, result.consultation):
        for field_name in type(section).model_fields:
            quote = getattr(section, field_name).evidence_quote
            if quote is not None and quote not in masked_text:
                raise ValueError("AI evidence must be a substring of masked_text")
