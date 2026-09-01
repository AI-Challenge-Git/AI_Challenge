from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Protocol, cast

from app.config import Settings, get_settings
from app.schemas import SignalEmbeddingRequest, SignalEmbeddingResult

EXPECTED_INPUT_FORMAT = "passage"


@dataclass(frozen=True, slots=True)
class RawEmbedding:
    model_id: str
    dimension: int
    normalization: str
    distance_metric: str
    vector: list[float]


@dataclass(frozen=True, slots=True)
class SignalEmbeddingContract:
    model_id: str
    model_revision: str
    dimension: int
    normalization: str
    input_format: str
    distance_metric: str


EmbeddingCall = Callable[[str], RawEmbedding]


class AiEmbeddingModule(Protocol):
    EMBEDDING_MODEL: str
    EMBEDDING_DIMENSION: int
    NORMALIZATION: str
    DISTANCE_METRIC: str

    def get_symptom_embedding(self, symptom_text: str) -> list[float]: ...


def load_signal_embedding_contract(
    settings: Settings | None = None,
) -> SignalEmbeddingContract:
    configured = settings or get_settings()
    revision = configured.signal_embedding_model_revision
    if revision is None or not revision.strip():
        raise RuntimeError("SIGNAL_EMBEDDING_MODEL_REVISION is required")
    ai_embedding = cast(AiEmbeddingModule, importlib.import_module("app.embedding"))
    return SignalEmbeddingContract(
        model_id=ai_embedding.EMBEDDING_MODEL,
        model_revision=revision.strip(),
        dimension=ai_embedding.EMBEDDING_DIMENSION,
        normalization=ai_embedding.NORMALIZATION.upper(),
        input_format=EXPECTED_INPUT_FORMAT,
        distance_metric=ai_embedding.DISTANCE_METRIC.upper(),
    )


def embedding_contract_mismatches(
    contract: SignalEmbeddingContract,
    *,
    model_id: str,
    model_revision: str,
    dimension: int,
    normalization: str,
    input_format: str,
    distance_metric: str,
) -> tuple[str, ...]:
    supplied = {
        "model_id": model_id,
        "model_revision": model_revision,
        "dimension": dimension,
        "normalization": normalization,
        "input_format": input_format,
        "distance_metric": distance_metric,
    }
    return tuple(field for field, actual in supplied.items() if actual != getattr(contract, field))


def _call_ai_embedding(technical_symptom: str) -> RawEmbedding:
    # Lazy import keeps API startup independent from the AI-owned provider module and key.
    ai_embedding = cast(AiEmbeddingModule, importlib.import_module("app.embedding"))

    return RawEmbedding(
        model_id=ai_embedding.EMBEDDING_MODEL,
        dimension=ai_embedding.EMBEDDING_DIMENSION,
        normalization=ai_embedding.NORMALIZATION,
        distance_metric=ai_embedding.DISTANCE_METRIC,
        vector=ai_embedding.get_symptom_embedding(technical_symptom),
    )


def _release_provider_slot(
    slots: asyncio.Semaphore,
    completed: asyncio.Future[RawEmbedding],
) -> None:
    slots.release()
    if not completed.cancelled():
        completed.exception()


class OpenAiSignalEmbeddingAdapter:
    """Typed, bounded async boundary around the AI-owned synchronous embedder."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        embedding_call: EmbeddingCall = _call_ai_embedding,
    ) -> None:
        self._settings = settings or get_settings()
        self._embedding_call = embedding_call
        self._slots = asyncio.Semaphore(self._settings.ai_max_concurrency)

        key = self._settings.openai_api_key
        if key is None or not key.get_secret_value().strip():
            raise RuntimeError("OPENAI_API_KEY is required for signal embedding")
        revision = self._settings.signal_embedding_model_revision
        if revision is None or not revision.strip():
            raise RuntimeError("SIGNAL_EMBEDDING_MODEL_REVISION is required")
        self._model_revision = revision.strip()

    async def embed(self, request: SignalEmbeddingRequest) -> SignalEmbeddingResult:
        if request.input_format != EXPECTED_INPUT_FORMAT:
            raise ValueError("unsupported signal embedding input format")

        async with asyncio.timeout(self._settings.ai_timeout_seconds):
            await self._slots.acquire()
            try:
                provider_call = asyncio.create_task(
                    asyncio.to_thread(self._embedding_call, request.technical_symptom)
                )
            except BaseException:
                self._slots.release()
                raise
            provider_call.add_done_callback(partial(_release_provider_slot, self._slots))
            raw = await asyncio.shield(provider_call)

        return SignalEmbeddingResult.model_validate(
            {
                "model_id": raw.model_id,
                "model_revision": self._model_revision,
                "dimension": raw.dimension,
                "normalization": raw.normalization.upper(),
                "input_format": EXPECTED_INPUT_FORMAT,
                "distance_metric": raw.distance_metric.upper(),
                "vector": raw.vector,
            }
        )
