from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_ATTACHMENT_STORAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "attachments"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "MTS SOS Desk API"
    app_env: Literal["development", "test", "production"] = "development"
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://mts_sos:mts_sos@localhost:5432/mts_sos"
    )
    database_echo: bool = False
    cors_origins: list[str] = []
    active_policy_version: str = "kb-trading-failure-guidance-2026-08-18"
    pii_policy_version: str = "pii-mask.v1"
    ai_adapter: Literal["openai"] = "openai"
    ai_timeout_seconds: float = Field(default=90.0, gt=0, le=120)
    ai_max_concurrency: int = Field(default=4, ge=1, le=32)
    analysis_pending_stale_seconds: int = Field(default=180, ge=30, le=3600)
    report_analyze_limit: int = Field(default=5, ge=1, le=1000)
    report_analyze_window_seconds: int = Field(default=60, ge=1, le=3600)
    signal_worker_max_attempts: int = Field(default=5, ge=1, le=100)
    openai_api_key: SecretStr | None = None
    signal_embedding_model_revision: str | None = None
    krx_listed_info_api_url: str = (
        "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"
    )
    krx_listed_info_api_key: SecretStr | None = None
    attachment_storage_backend: Literal["local", "s3"] = "local"
    attachment_storage_dir: Path = DEFAULT_ATTACHMENT_STORAGE_DIR
    attachment_signed_url_ttl_seconds: int = Field(default=300, ge=60, le=900)
    bucket: str | None = None
    access_key_id: SecretStr | None = None
    secret_access_key: SecretStr | None = None
    region: str | None = None
    endpoint: str | None = None
    s3_addressing_style: Literal["virtual", "path"] = "virtual"
    session_hmac_key: SecretStr = SecretStr("development-session-hmac-key-change-me")
    reference_hmac_key: SecretStr = SecretStr("development-reference-hmac-key-change-me")
    agent_token_hmac_key: SecretStr = SecretStr("development-agent-token-hmac-key-change-me")
    rate_limit_hmac_key: SecretStr = SecretStr("development-rate-limit-hmac-key-change-me")
    agent_access_token_ttl_minutes: int = Field(default=30, ge=5, le=120)
    agent_login_failure_limit: int = Field(default=5, ge=1, le=100)
    agent_login_failure_window_seconds: int = Field(default=300, ge=1, le=3600)
    agent_login_failure_delay_ms: int = Field(default=300, ge=0, le=5000)
    agent_lookup_limit: int = Field(default=10, ge=1, le=1000)
    agent_lookup_window_seconds: int = Field(default=60, ge=1, le=3600)
    agent_lookup_failure_delay_ms: int = Field(default=250, ge=0, le=5000)
    signal_dashboard_limit: int = Field(default=60, ge=1, le=1000)
    signal_dashboard_window_seconds: int = Field(default=60, ge=1, le=3600)

    @model_validator(mode="after")
    def require_production_secrets(self) -> "Settings":
        if self.analysis_pending_stale_seconds <= self.ai_timeout_seconds:
            raise ValueError("ANALYSIS_PENDING_STALE_SECONDS must exceed AI_TIMEOUT_SECONDS")
        if self.app_env == "production" and (
            self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip()
        ):
            raise ValueError("production requires OPENAI_API_KEY")
        if self.app_env == "production" and (
            self.signal_embedding_model_revision is None
            or not self.signal_embedding_model_revision.strip()
        ):
            raise ValueError("production requires SIGNAL_EMBEDDING_MODEL_REVISION")
        hmac_keys = (
            self.session_hmac_key.get_secret_value(),
            self.reference_hmac_key.get_secret_value(),
            self.agent_token_hmac_key.get_secret_value(),
            self.rate_limit_hmac_key.get_secret_value(),
        )
        if self.app_env == "production" and (
            any(key.startswith("development-") for key in hmac_keys)
            or any(len(key) < 32 for key in hmac_keys)
            or len(set(hmac_keys)) != len(hmac_keys)
        ):
            raise ValueError("distinct production HMAC keys must be configured")
        if self.attachment_storage_backend == "s3" and not all(
            (
                self.bucket,
                self.access_key_id,
                self.secret_access_key,
                self.region,
                self.endpoint,
            )
        ):
            raise ValueError("S3-compatible private object storage credentials are required")
        if self.app_env == "production" and self.attachment_storage_backend != "s3":
            raise ValueError("production requires a private object storage backend")
        if self.app_env == "production":
            if not self.cors_origins:
                raise ValueError("production requires an explicit CORS origin allowlist")
            for origin in self.cors_origins:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme != "https"
                    or not parsed.netloc
                    or parsed.path not in ("", "/")
                    or parsed.query
                    or parsed.fragment
                    or "*" in origin
                ):
                    raise ValueError("production CORS origins must be exact HTTPS origins")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
