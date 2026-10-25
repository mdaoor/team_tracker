"""Environment-driven configuration (`TT_*` variables; see `.env.example`)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

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
    seed_reference_on_start: bool = Field(
        default=True, description="Load shipped templates/habit definitions at start (idempotent)."
    )
    worker_concurrency: int = Field(default=4, ge=1, le=64, description="Jobs a worker runs at once.")
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

    # --- attachments (StorageAdapter: local disk in v1; S3 is a Phase-12 adapter behind the same interface)
    attachment_dir: str = Field(default="./var/attachments", description="Root directory for uploaded files.")
    attachment_backend: str = Field(
        default="local", description="local | s3 (any S3-compatible store, pre-signed URLs)."
    )
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint: str | None = Field(
        default=None, description="Set for MinIO/Ceph (path-style); unset = AWS virtual-hosted."
    )
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_prefix: str = ""
    attachment_max_bytes: int = Field(default=25 * 1024 * 1024, description="Largest accepted upload.")

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

    # --- Kerberos / SPNEGO SSO behind a reverse proxy (ADR-0036): the proxy validates the ticket and forwards the principal
    trusted_principal_header: str | None = Field(
        default=None,
        description="Header carrying the authenticated principal (e.g. X-Remote-User). Off when unset.",
    )
    trusted_groups_header: str | None = Field(
        default=None, description="Optional header with comma-separated group names."
    )
    trusted_proxies: str = Field(
        default="",
        description="Comma-separated IPs/CIDRs allowed to set the principal header. Empty = SSO header ignored.",
    )
    principal_email_domain: str | None = Field(
        default=None,
        description="Appended to a bare principal (`jdoe`) or used to replace the Kerberos realm in `jdoe@REALM`.",
    )

    # --- database TLS (ADR-0036)
    db_sslmode: str | None = Field(
        default=None, description="disable|allow|prefer|require|verify-ca|verify-full"
    )
    db_sslrootcert: str | None = None
    db_sslcert: str | None = None
    db_sslkey: str | None = None

    # --- observability
    metrics_token: str | None = Field(
        default=None, description="Bearer token for GET /metrics. Unset = /metrics is dev-only."
    )

    # --- rate limits (requests per minute; 0 = off). In-process, per replica.
    rate_capture_per_min: int = Field(default=600, description="POST /capture/batches per credential.")
    rate_auth_per_min: int = Field(default=60, description="POST /auth/* per client address.")

    # --- certificate hot-reload (ADR-0036)
    cert_reload_interval_s: float = 30.0
    cert_expiry_warn_days: int = 14

    # --- transport (§4.6)
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    force_https: bool = False
    https_port: int | None = Field(default=None, description="Public HTTPS port used in redirects.")
    trust_forwarded_proto: bool = False
    hsts_max_age: int = 31_536_000

    def db_connect_args(self) -> dict[str, str]:
        """libpq TLS parameters from the `TT_DB_SSL*` settings (empty when none are set)."""
        pairs = {
            "sslmode": self.db_sslmode, "sslrootcert": self.db_sslrootcert, "sslcert": self.db_sslcert, "sslkey": self.db_sslkey,
        }  # fmt: skip
        return {k: v for k, v in pairs.items() if v}

    def validate_db_tls(self) -> None:
        mode = self.db_sslmode
        if mode and mode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise RuntimeError(f"TT_DB_SSLMODE={mode!r} is not a libpq sslmode")
        if mode in {"verify-ca", "verify-full"}:
            if not self.db_sslrootcert:
                raise RuntimeError(
                    f"TT_DB_SSLMODE={mode} needs TT_DB_SSLROOTCERT (the CA that signed the server certificate)"
                )
            for label, path in (
                ("TT_DB_SSLROOTCERT", self.db_sslrootcert),
                ("TT_DB_SSLCERT", self.db_sslcert),
                ("TT_DB_SSLKEY", self.db_sslkey),
            ):
                if path and not Path(path).is_file():
                    raise RuntimeError(f"{label}={path}: no such file")
        if bool(self.db_sslcert) != bool(self.db_sslkey):
            raise RuntimeError(
                "TT_DB_SSLCERT and TT_DB_SSLKEY must be set together (client-certificate authentication)"
            )

    def validate_for_runtime(self) -> None:
        """Fail fast on unsafe production configuration (anything but `env=dev`): a guessable cookie-signing secret is account takeover."""
        self.validate_db_tls()
        if self.trusted_principal_header and not self.trusted_proxies.strip():
            raise RuntimeError(
                "TT_TRUSTED_PRINCIPAL_HEADER needs TT_TRUSTED_PROXIES: an unauthenticated header must never be trusted"
            )
        if self.env == "dev":
            return
        if self.session_secret == "dev-insecure-session-secret" or len(self.session_secret.encode()) < 32:  # noqa: S105
            raise RuntimeError(
                "TT_SESSION_SECRET must be set to at least 32 random bytes when TT_ENV is not 'dev' (e.g. `openssl rand -hex 32`)"
            )
        if self.dev_auth:
            raise RuntimeError("TT_DEV_AUTH must be off when TT_ENV is not 'dev'")
        if not self.secret_key or len(self.secret_key.encode()) < 32:
            # Jira credentials are encrypted with a key derived from this; it must not be the cookie-signing secret (ADR-0046)
            raise RuntimeError(
                "TT_SECRET_KEY must be set to at least 32 random bytes when TT_ENV is not 'dev' (it encrypts stored "
                "integration credentials and is separate from TT_SESSION_SECRET)"
            )
        if self.secret_key == self.session_secret:
            raise RuntimeError("TT_SECRET_KEY must differ from TT_SESSION_SECRET")

    @property
    def psycopg_dsn(self) -> str:
        """Plain libpq DSN (for Procrastinate), derived from the SQLAlchemy URL."""
        dsn = self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        if args := self.db_connect_args():
            dsn += ("&" if "?" in dsn else "?") + urlencode(args)
        return dsn


@lru_cache
def get_settings() -> Settings:
    return Settings()
