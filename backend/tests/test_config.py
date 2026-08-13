from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
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
