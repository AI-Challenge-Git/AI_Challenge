from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


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
    ai_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    session_hmac_key: SecretStr = SecretStr("development-session-hmac-key-change-me")
    reference_hmac_key: SecretStr = SecretStr("development-reference-hmac-key-change-me")

    @model_validator(mode="after")
    def require_production_secrets(self) -> "Settings":
        if self.app_env == "production" and (
            self.session_hmac_key.get_secret_value().startswith("development-")
            or self.reference_hmac_key.get_secret_value().startswith("development-")
        ):
            raise ValueError("production HMAC keys must be configured")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
