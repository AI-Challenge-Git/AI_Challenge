import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from app.ai import FakeDualExtractor, get_dual_extractor
from app.config import Settings, get_settings
from app.main import create_app
from app.schemas import ExtractionResult
from app.services.reports import _extract_with_runtime_limits


def test_settings_load_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "environment-secret"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql+asyncpg://user:{secret}@db:5432/test_db",
    )
    monkeypatch.setenv("DATABASE_ECHO", "true")
    monkeypatch.setenv("CORS_ORIGINS", '["https://desk.example.com"]')

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.database_echo is True
    assert settings.cors_origins == ["https://desk.example.com"]
    assert secret not in repr(settings)
    assert settings.database_url.get_secret_value().endswith("@db:5432/test_db")


def test_fake_ai_adapter_is_the_safe_default() -> None:
    settings = Settings()

    assert settings.ai_adapter == "fake"
    assert settings.ai_timeout_seconds == 10
    assert settings.ai_max_concurrency == 4


async def test_backend_service_keeps_timed_out_ai_calls_bounded() -> None:
    class BlockingExtractor(FakeDualExtractor):
        def __init__(self) -> None:
            self.release = asyncio.Event()
            self.first_started = asyncio.Event()
            self.active = 0
            self.started = 0
            self.max_active = 0

        async def extract(self, masked_text: str) -> ExtractionResult:
            self.active += 1
            self.started += 1
            self.max_active = max(self.max_active, self.active)
            self.first_started.set()
            await self.release.wait()
            self.active -= 1
            return await super().extract(masked_text)

    extractor = BlockingExtractor()
    short_timeout = Settings(ai_timeout_seconds=0.01, ai_max_concurrency=1)
    queued_timeout = Settings(ai_timeout_seconds=1, ai_max_concurrency=1)

    with pytest.raises(TimeoutError):
        await _extract_with_runtime_limits(
            extractor,
            "첫 번째 합성 요청이며 결과를 기다리는 중입니다.",
            short_timeout,
        )

    queued = asyncio.create_task(
        _extract_with_runtime_limits(
            extractor,
            "두 번째 합성 요청이며 결과를 기다리는 중입니다.",
            queued_timeout,
        )
    )
    await asyncio.sleep(0.05)
    assert extractor.started == 1
    assert extractor.max_active == 1

    extractor.release.set()
    assert await asyncio.wait_for(queued, timeout=1) is not None
    assert extractor.started == 2
    assert extractor.max_active == 1


def test_nvidia_adapter_requires_a_masked_api_key() -> None:
    secret = "nvidia-secret-value"
    with pytest.raises(ValidationError) as missing:
        Settings(ai_adapter="nvidia", nvidia_api_key=None)
    configured = Settings(ai_adapter="nvidia", nvidia_api_key=SecretStr(secret))

    assert "NVIDIA_API_KEY" in str(missing.value)
    assert secret not in repr(configured)


def test_extractor_dependency_uses_fake_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ADAPTER", "fake")
    get_settings.cache_clear()
    get_dual_extractor.cache_clear()
    try:
        assert isinstance(get_dual_extractor(), FakeDualExtractor)
    finally:
        get_dual_extractor.cache_clear()
        get_settings.cache_clear()


def test_extractor_dependency_switches_to_nvidia_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = FakeDualExtractor()
    monkeypatch.setenv("AI_ADAPTER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "synthetic-test-key")
    monkeypatch.setattr("app.ai.NvidiaDualExtractorAdapter", lambda: sentinel)
    get_settings.cache_clear()
    get_dual_extractor.cache_clear()
    try:
        assert get_dual_extractor() is sentinel
    finally:
        get_dual_extractor.cache_clear()
        get_settings.cache_clear()


async def test_cors_allows_only_configured_origin() -> None:
    origin = "https://desk.example.com"
    application = create_app(Settings(cors_origins=[origin]))
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await client.options(
            "/health/live",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = await client.options(
            "/health/live",
            headers={
                "Origin": "https://attacker.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == origin
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_initial_migration_only_manages_pgvector_extension() -> None:
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0001_enable_vector.py"
    ).read_text(encoding="utf-8")

    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "DROP EXTENSION IF EXISTS vector" in migration
    assert "create_table" not in migration.lower()
    assert "CREATE TABLE" not in migration.upper()
