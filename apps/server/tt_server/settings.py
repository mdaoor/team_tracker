"""Environment-driven configuration (`TT_*` variables; see `.env.example`)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Features(BaseSettings):
    insights_llm: bool = False
    llm_suggestions: bool = False  # review-queue suggestions + classification pre-fill via the LLM gateway
    jira_sync: bool = False
    governance: bool = (
        False  # policy evaluation: on-event routing from the outbox and the scheduled sweeps (Phase 8)
    )
    habits_nightly: bool = False  # nightly habits + metric snapshots + key-result updates
    slack_bot: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TT_", env_file=".env", extra="ignore", env_nested_delimiter="__"
    )

    env: str = "dev"
    database_url: str = "postgresql+psycopg://tt:tt@localhost:5432/tt"
    dev_auth: bool = Field(default=False, description="Accept the X-Debug-Actor header (dev only).")
    log_level: str = "INFO"
    log_json: bool = True
    otlp_endpoint: str | None = Field(default=None, description="OTLP endpoint; no exporter when unset.")
    features: Features = Field(default_factory=Features)

    # --- web sessions (Phase 4 §4.5)
    session_secret: str = Field(default="dev-insecure-session-secret", description="Signs session cookies.")
    session_ttl_hours: int = 12
    secret_key: str | None = Field(
        default=None, description="Encrypts stored integration credentials (Jira). Required in production."
    )
    jira_inline_timeout_s: float = Field(
        default=4.0,
        description="Bound on the immediate push for an item the reporter opted in (0 = always async).",
    )
    web_origin: str | None = Field(default=None, description="Allowed redirect target after login.")

    # --- OIDC (Entra ID / ADFS / any provider)
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_groups_claim: str = "groups"

    # --- LDAPS to Active Directory (§4.6)
    ldap_url: str | None = Field(default=None, description="ldaps://dc.example.com:636")
    ldap_start_tls: bool = False
    ldap_ca_bundle: str | None = Field(default=None, description="PEM bundle for the domain controllers.")
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None
    ldap_base_dn: str | None = None
    ldap_workspace: str | None = Field(default=None, description="Workspace slug that LDAP users sign in to.")

    # --- LLM gateway (Phase 2b); unset = every LLM-backed feature stays off
    llm_gateway_url: str | None = None
    llm_gateway_token: str | None = None
    llm_gateway_ca_bundle: str | None = None
    llm_service_token: str | None = Field(
        default=None, description="Read-only core token the gateway may use."
    )

    # --- transport (§4.6)
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    force_https: bool = False
    https_port: int | None = Field(default=None, description="Public HTTPS port used in redirects.")
    trust_forwarded_proto: bool = False
    hsts_max_age: int = 31_536_000

    def validate_for_runtime(self) -> None:
        """Fail fast on unsafe production configuration (anything but `env=dev`): a guessable cookie-signing secret is account takeover."""
        if self.env == "dev":
            return
        if self.session_secret == "dev-insecure-session-secret" or len(self.session_secret.encode()) < 32:  # noqa: S105
            raise RuntimeError(
                "TT_SESSION_SECRET must be set to at least 32 random bytes when TT_ENV is not 'dev' (e.g. `openssl rand -hex 32`)"
            )
        if self.dev_auth:
            raise RuntimeError("TT_DEV_AUTH must be off when TT_ENV is not 'dev'")

    @property
    def psycopg_dsn(self) -> str:
        """Plain libpq DSN (for Procrastinate), derived from the SQLAlchemy URL."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
