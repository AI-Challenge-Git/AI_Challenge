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
    """Expose the blocking NVIDIA extractor through the async backend boundary.

    The application service limits the complete adapter call to ``ai_timeout_seconds``.
    Cancelling ``asyncio.to_thread`` cannot stop a provider thread already in progress, so a
    permit remains occupied until that thread really exits. This bounds lingering provider calls
    instead of allowing timed-out requests to create an unlimited number of worker threads.
    """

    schema_version = "dual-extraction.v1"
    taxonomy_version = "issue-type.v1"
    adapter_name = "nvidia-build"
    model_id: str | None = "meta/llama-3.1-8b-instruct"

    def __init__(self, *, max_concurrency: int | None = None) -> None:
        settings = get_settings()
        self._inner = RealDualExtractor()
        self._provider_slots = asyncio.Semaphore(
            max_concurrency if max_concurrency is not None else settings.ai_max_concurrency
        )

    def _provider_call_finished(self, completed: object) -> None:
        self._provider_slots.release()
        if isinstance(completed, asyncio.Task) and not completed.cancelled():
            completed.exception()

    async def extract(self, masked_text: str) -> ExtractionResult:
        await self._provider_slots.acquire()
        try:
            provider_call = asyncio.create_task(
                asyncio.to_thread(self._inner.extract_safe, masked_text)
            )
        except BaseException:
            self._provider_slots.release()
            raise
        provider_call.add_done_callback(self._provider_call_finished)
        outcome = await asyncio.shield(provider_call)

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
