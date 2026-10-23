"""Environment-driven configuration (`TT_*` variables; see `.env.example`)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Features(BaseSettings):
    insights_llm: bool = False
    jira_sync: bool = False
    habits_nightly: bool = False
    slack_bot: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TT_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+psycopg://tt:tt@localhost:5432/tt"
    dev_auth: bool = Field(default=False, description="Accept the X-Debug-Actor header (dev only).")
    log_level: str = "INFO"
    log_json: bool = True
    otlp_endpoint: str | None = Field(default=None, description="OTLP endpoint; no exporter when unset.")
    features: Features = Field(default_factory=Features)

    @property
    def psycopg_dsn(self) -> str:
        """Plain libpq DSN (for Procrastinate), derived from the SQLAlchemy URL."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
