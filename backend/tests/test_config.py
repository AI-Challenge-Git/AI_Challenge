import asyncio
import json
from pathlib import Path

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from app.ai import FakeDualExtractor, get_dual_extractor
from app.api.dependencies import rate_limit_client_identifier
from app.config import Settings, get_settings
from app.main import create_app
from app.schemas import ExtractionResult
from app.services.reports import _extract_with_runtime_limits
from scripts import process_signal_jobs


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


def test_openai_is_the_only_runtime_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_ADAPTER", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.ai_adapter == "openai"
    assert settings.ai_timeout_seconds == 90
    assert settings.ai_max_concurrency == 4
    assert settings.analysis_pending_stale_seconds == 180
    assert settings.report_analyze_limit == 5
    assert settings.report_analyze_window_seconds == 60
    assert settings.signal_worker_max_attempts == 5
    assert settings.signal_worker_poll_seconds == 5
    assert settings.agent_access_token_ttl_minutes == 30
    assert settings.agent_login_failure_limit == 5
    assert settings.agent_login_failure_window_seconds == 300
    assert settings.agent_lookup_limit == 10
    assert settings.agent_lookup_window_seconds == 60
    assert settings.signal_dashboard_limit == 60
    assert settings.signal_dashboard_window_seconds == 60


def test_pending_analysis_lease_must_exceed_provider_timeout() -> None:
    with pytest.raises(ValidationError, match="ANALYSIS_PENDING_STALE_SECONDS"):
        Settings(ai_timeout_seconds=90, analysis_pending_stale_seconds=90)


async def test_signal_worker_forever_keeps_polling_after_failed_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def failed_batch(*, max_jobs: int, report_empty: bool) -> int:
        nonlocal calls
        assert max_jobs == 7
        assert report_empty is False
        calls += 1
        return 1

    async def stop_after_sleep(seconds: float) -> None:
        assert seconds == 5
        raise asyncio.CancelledError

    monkeypatch.setattr(process_signal_jobs, "run", failed_batch)
    monkeypatch.setattr("scripts.process_signal_jobs.asyncio.sleep", stop_after_sleep)
    monkeypatch.setattr(
        process_signal_jobs,
        "get_settings",
        lambda: type("WorkerSettings", (), {"signal_worker_poll_seconds": 5.0})(),
    )

    with pytest.raises(asyncio.CancelledError):
        await process_signal_jobs.run_forever(max_jobs=7)
    assert calls == 1


def test_rate_limit_client_identifier_uses_railway_ip_only_in_production() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-real-ip", b"2001:0db8:0:0:0:0:0:1")],
            "client": ("127.0.0.1", 1234),
        }
    )

    assert rate_limit_client_identifier(request, app_env="development") == "127.0.0.1"
    assert rate_limit_client_identifier(request, app_env="production") == "2001:db8::1"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [([], "missing-railway-client"), ([(b"x-real-ip", b"not-an-ip")], "invalid-railway-client")],
)
def test_rate_limit_client_identifier_fails_closed_for_bad_railway_headers(
    headers: list[tuple[bytes, bytes]],
    expected: str,
) -> None:
    request = Request({"type": "http", "headers": headers, "client": ("10.0.0.1", 1234)})

    assert rate_limit_client_identifier(request, app_env="production") == expected


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


def test_openai_api_key_is_masked() -> None:
    secret = "openai-secret-value"
    configured = Settings(openai_api_key=SecretStr(secret))

    assert secret not in repr(configured)


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "ai_adapter": "openai",
        "openai_api_key": SecretStr("synthetic-openai-key"),
        "signal_embedding_model_revision": "synthetic-revision",
        "cors_origins": ["https://desk.example.com"],
        "attachment_storage_backend": "s3",
        "bucket": "private-bucket",
        "access_key_id": SecretStr("synthetic-access-key"),
        "secret_access_key": SecretStr("synthetic-secret-key"),
        "region": "auto",
        "endpoint": "https://storage.example.com",
        "session_hmac_key": SecretStr("a" * 32),
        "reference_hmac_key": SecretStr("b" * 32),
        "agent_token_hmac_key": SecretStr("c" * 32),
        "rate_limit_hmac_key": SecretStr("d" * 32),
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_production_requires_private_storage_and_exact_https_cors() -> None:
    assert _production_settings().attachment_storage_backend == "s3"

    with pytest.raises(ValidationError, match="private object storage"):
        _production_settings(attachment_storage_backend="local")
    with pytest.raises(ValidationError, match="credentials"):
        _production_settings(secret_access_key=None)
    with pytest.raises(ValidationError, match="exact HTTPS origins"):
        _production_settings(cors_origins=["https://*.example.com"])


def test_production_rejects_fake_ai_missing_key_revision_and_short_hmac_keys() -> None:
    with pytest.raises(ValidationError, match="openai"):
        _production_settings(ai_adapter="fake")
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        _production_settings(openai_api_key=None)
    with pytest.raises(ValidationError, match="SIGNAL_EMBEDDING_MODEL_REVISION"):
        _production_settings(signal_embedding_model_revision=None)
    with pytest.raises(ValidationError, match="distinct production HMAC"):
        _production_settings(session_hmac_key=SecretStr("too-short"))


def test_settings_reject_legacy_fake_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_ADAPTER", "fake")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="openai"):
            Settings()
    finally:
        get_settings.cache_clear()


def test_extractor_dependency_uses_openai_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = FakeDualExtractor()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr("app.ai.OpenAIDualExtractorAdapter", lambda: sentinel)
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


def test_railway_workers_use_the_expected_process_model() -> None:
    backend_root = Path(__file__).parents[1]
    signal = json.loads((backend_root / "railway.signal-worker.json").read_text(encoding="utf-8"))
    retention = json.loads(
        (backend_root / "railway.retention-worker.json").read_text(encoding="utf-8")
    )

    signal_deploy = signal["deploy"]
    assert "--forever" in signal_deploy["startCommand"]
    assert "cronSchedule" not in signal_deploy
    assert signal_deploy["restartPolicyType"] == "ON_FAILURE"
    assert retention["deploy"]["cronSchedule"] == "17 * * * *"
