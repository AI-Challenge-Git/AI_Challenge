from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from app.ai import FakeDualExtractor, get_dual_extractor
from app.config import Settings, get_settings
from app.main import create_app


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
    assert Settings().ai_adapter == "fake"


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
