import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.schemas import SignalEmbeddingRequest
from app.services.signal_embeddings import (
    OpenAiSignalEmbeddingAdapter,
    RawEmbedding,
    SignalEmbeddingContract,
    embedding_contract_mismatches,
    load_signal_embedding_contract,
)
from scripts.register_signal_policy import parse_args
from scripts.register_signal_policy import run as register_policy


def _settings(*, timeout: float = 1, concurrency: int = 1) -> Settings:
    return Settings(
        openai_api_key=SecretStr("synthetic-test-key"),
        signal_embedding_model_revision="test-revision",
        ai_timeout_seconds=timeout,
        ai_max_concurrency=concurrency,
    )


def _request(*, input_format: str = "passage") -> SignalEmbeddingRequest:
    return SignalEmbeddingRequest(
        schema_version="signal-embedding-request.v1",
        input_format=input_format,
        technical_symptom="synthetic masked symptom",
    )


async def test_adapter_returns_canonical_typed_metadata() -> None:
    adapter = OpenAiSignalEmbeddingAdapter(
        _settings(),
        embedding_call=lambda _: RawEmbedding(
            model_id="text-embedding-3-small",
            dimension=3,
            normalization="l2",
            distance_metric="cosine",
            vector=[1.0, 0.0, 0.0],
        ),
    )

    result = await adapter.embed(_request())

    assert result.model_id == "text-embedding-3-small"
    assert result.model_revision == "test-revision"
    assert result.normalization == "L2"
    assert result.distance_metric == "COSINE"
    assert result.input_format == "passage"


async def test_adapter_rejects_an_input_format_the_ai_module_does_not_use() -> None:
    called = False

    def embedding_call(_: str) -> RawEmbedding:
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    adapter = OpenAiSignalEmbeddingAdapter(_settings(), embedding_call=embedding_call)

    with pytest.raises(ValueError, match="input format"):
        await adapter.embed(_request(input_format="query"))
    assert called is False


async def test_adapter_rejects_unapproved_vector_metadata() -> None:
    adapter = OpenAiSignalEmbeddingAdapter(
        _settings(),
        embedding_call=lambda _: RawEmbedding(
            model_id="text-embedding-3-small",
            dimension=3,
            normalization="unknown",
            distance_metric="cosine",
            vector=[1.0, 0.0, 0.0],
        ),
    )

    with pytest.raises(ValueError, match="normalization"):
        await adapter.embed(_request())


@pytest.mark.parametrize(
    ("key", "revision", "expected"),
    [
        (None, "revision", "OPENAI_API_KEY"),
        (SecretStr("test-key"), None, "SIGNAL_EMBEDDING_MODEL_REVISION"),
    ],
)
def test_adapter_requires_secrets_and_model_revision(
    key: SecretStr | None,
    revision: str | None,
    expected: str,
) -> None:
    settings = Settings(
        ai_adapter="fake",
        openai_api_key=key,
        signal_embedding_model_revision=revision,
    )

    with pytest.raises(RuntimeError, match=expected):
        OpenAiSignalEmbeddingAdapter(settings)


async def test_timed_out_provider_threads_remain_bounded() -> None:
    release = threading.Event()
    started = 0
    started_lock = threading.Lock()

    def blocking_call(_: str) -> RawEmbedding:
        nonlocal started
        with started_lock:
            started += 1
        release.wait(timeout=2)
        return RawEmbedding(
            model_id="text-embedding-3-small",
            dimension=3,
            normalization="l2",
            distance_metric="cosine",
            vector=[1.0, 0.0, 0.0],
        )

    adapter = OpenAiSignalEmbeddingAdapter(
        _settings(timeout=0.05, concurrency=1),
        embedding_call=blocking_call,
    )

    with pytest.raises(TimeoutError):
        await adapter.embed(_request())
    with pytest.raises(TimeoutError):
        await adapter.embed(_request())
    assert started == 1

    release.set()
    await asyncio.sleep(0.05)


def test_policy_cli_defaults_to_evaluated_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "register_signal_policy",
            "--policy-version",
            "test-policy",
            "--model-id",
            "text-embedding-3-small",
            "--model-revision",
            "test-revision",
            "--dimension",
            "1024",
            "--normalization",
            "L2",
            "--input-format",
            "passage",
            "--taxonomy-version",
            "issue-type.v1",
        ],
    )

    assert parse_args().similarity_threshold == 0.58


def test_runtime_contract_uses_configured_revision_and_provider_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.signal_embeddings.importlib.import_module",
        lambda _: SimpleNamespace(
            EMBEDDING_MODEL="text-embedding-3-small",
            EMBEDDING_DIMENSION=1024,
            NORMALIZATION="l2",
            DISTANCE_METRIC="cosine",
        ),
    )

    contract = load_signal_embedding_contract(_settings())

    assert contract.model_revision == "test-revision"
    assert contract.dimension == 1024
    assert contract.normalization == "L2"
    assert contract.distance_metric == "COSINE"
    assert embedding_contract_mismatches(
        contract,
        model_id=contract.model_id,
        model_revision="wrong-revision",
        dimension=contract.dimension,
        normalization=contract.normalization,
        input_format=contract.input_format,
        distance_metric=contract.distance_metric,
    ) == ("model_revision",)


async def test_policy_activation_rejects_runtime_revision_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "register_signal_policy",
            "--policy-version",
            "test-policy",
            "--model-id",
            "text-embedding-3-small",
            "--model-revision",
            "wrong-revision",
            "--dimension",
            "1024",
            "--normalization",
            "L2",
            "--input-format",
            "passage",
            "--taxonomy-version",
            "issue-type.v1",
            "--activate",
        ],
    )
    monkeypatch.setattr(
        "scripts.register_signal_policy.load_signal_embedding_contract",
        lambda: SignalEmbeddingContract(
            model_id="text-embedding-3-small",
            model_revision="configured-revision",
            dimension=1024,
            normalization="L2",
            input_format="passage",
            distance_metric="COSINE",
        ),
    )

    with pytest.raises(ValueError, match="model_revision"):
        await register_policy(parse_args())
