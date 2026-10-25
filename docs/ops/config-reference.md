# Configuration reference

Generated from `tt_server/settings.py` by `tools/gen_config_reference.py` - do not edit by hand (CI fails when it drifts).
Variables are read from the environment (prefix `TT_`) or a `.env` file. Rows marked **secret** must come from a secret store, never from git.

| Variable | Type | Default | Notes |
| --- | --- | --- | --- |
| `TT_ENV` | `str` | `dev` |  |
| `TT_DATABASE_URL` | `str` | `postgresql+psycopg://tt:tt@localhost:5432/tt` |  |
| `TT_DEV_AUTH` | `bool` | `False` | Accept the X-Debug-Actor header (dev only). |
| `TT_LOG_LEVEL` | `str` | `INFO` |  |
| `TT_LOG_JSON` | `bool` | `True` |  |
| `TT_OTLP_ENDPOINT` | `str \| None` | unset | OTLP endpoint; no exporter when unset. |
| `TT_SESSION_SECRET` | `str` | `dev-insecure-session-secret` | Signs session cookies. **secret** |
| `TT_SESSION_TTL_HOURS` | `int` | `12` |  |
| `TT_SECRET_KEY` | `str \| None` | unset | Encrypts stored integration credentials (Jira). Required in production. **secret** |
| `TT_JIRA_INLINE_TIMEOUT_S` | `float` | `4.0` | Bound on the immediate push for an item the reporter opted in (0 = always async). |
| `TT_WEB_ORIGIN` | `str \| None` | unset | Allowed redirect target after login. |
| `TT_ATTACHMENT_DIR` | `str` | `./var/attachments` | Root directory for uploaded files. |
| `TT_ATTACHMENT_BACKEND` | `str` | `local` | local \| s3 (any S3-compatible store, pre-signed URLs). |
| `TT_S3_BUCKET` | `str` | `` |  |
| `TT_S3_REGION` | `str` | `us-east-1` |  |
| `TT_S3_ENDPOINT` | `str \| None` | unset | Set for MinIO/Ceph (path-style); unset = AWS virtual-hosted. |
| `TT_S3_ACCESS_KEY` | `str` | `` | **secret** |
| `TT_S3_SECRET_KEY` | `str` | `` | **secret** |
| `TT_S3_PREFIX` | `str` | `` |  |
| `TT_ATTACHMENT_MAX_BYTES` | `int` | `26214400` | Largest accepted upload. |
| `TT_OIDC_ISSUER` | `str \| None` | unset |  |
| `TT_OIDC_CLIENT_ID` | `str \| None` | unset |  |
| `TT_OIDC_CLIENT_SECRET` | `str \| None` | unset | **secret** |
| `TT_OIDC_REDIRECT_URI` | `str \| None` | unset |  |
| `TT_OIDC_GROUPS_CLAIM` | `str` | `groups` |  |
| `TT_LDAP_URL` | `str \| None` | unset | ldaps://dc.example.com:636 |
| `TT_LDAP_START_TLS` | `bool` | `False` |  |
| `TT_LDAP_CA_BUNDLE` | `str \| None` | unset | PEM bundle for the domain controllers. |
| `TT_LDAP_BIND_DN` | `str \| None` | unset |  |
| `TT_LDAP_BIND_PASSWORD` | `str \| None` | unset | **secret** |
| `TT_LDAP_BASE_DN` | `str \| None` | unset |  |
| `TT_LDAP_WORKSPACE` | `str \| None` | unset | Workspace slug that LDAP users sign in to. |
| `TT_LLM_GATEWAY_URL` | `str \| None` | unset |  |
| `TT_LLM_GATEWAY_TOKEN` | `str \| None` | unset | **secret** |
| `TT_LLM_GATEWAY_CA_BUNDLE` | `str \| None` | unset |  |
| `TT_LLM_SERVICE_TOKEN` | `str \| None` | unset | Read-only core token the gateway may use. **secret** |
| `TT_TRUSTED_PRINCIPAL_HEADER` | `str \| None` | unset | Header carrying the authenticated principal (e.g. X-Remote-User). Off when unset. |
| `TT_TRUSTED_GROUPS_HEADER` | `str \| None` | unset | Optional header with comma-separated group names. |
| `TT_TRUSTED_PROXIES` | `str` | `` | Comma-separated IPs/CIDRs allowed to set the principal header. Empty = SSO header ignored. |
| `TT_PRINCIPAL_EMAIL_DOMAIN` | `str \| None` | unset | Appended to a bare principal (`jdoe`) or used to replace the Kerberos realm in `jdoe@REALM`. |
| `TT_DB_SSLMODE` | `str \| None` | unset | disable\|allow\|prefer\|require\|verify-ca\|verify-full |
| `TT_DB_SSLROOTCERT` | `str \| None` | unset |  |
| `TT_DB_SSLCERT` | `str \| None` | unset |  |
| `TT_DB_SSLKEY` | `str \| None` | unset | **secret** |
| `TT_METRICS_TOKEN` | `str \| None` | unset | Bearer token for GET /metrics. Unset = /metrics is dev-only. **secret** |
| `TT_RATE_CAPTURE_PER_MIN` | `int` | `600` | POST /capture/batches per credential. |
| `TT_RATE_AUTH_PER_MIN` | `int` | `60` | POST /auth/* per client address. |
| `TT_CERT_RELOAD_INTERVAL_S` | `float` | `30.0` |  |
| `TT_CERT_EXPIRY_WARN_DAYS` | `int` | `14` |  |
| `TT_TLS_CERTFILE` | `str \| None` | unset |  |
| `TT_TLS_KEYFILE` | `str \| None` | unset | **secret** |
| `TT_FORCE_HTTPS` | `bool` | `False` |  |
| `TT_HTTPS_PORT` | `int \| None` | unset | Public HTTPS port used in redirects. |
| `TT_TRUST_FORWARDED_PROTO` | `bool` | `False` |  |
| `TT_HSTS_MAX_AGE` | `int` | `31536000` |  |

## Feature flags (`TT_FEATURES__<NAME>`)

| Variable | Default |
| --- | --- |
| `TT_FEATURES__INSIGHTS_LLM` | `False` |
| `TT_FEATURES__LLM_SUGGESTIONS` | `False` |
| `TT_FEATURES__JIRA_SYNC` | `False` |
| `TT_FEATURES__GOVERNANCE` | `False` |
| `TT_FEATURES__HABITS_NIGHTLY` | `False` |
| `TT_FEATURES__SLACK_BOT` | `False` |

## Environment matrix

| Concern | dev | staging / prod |
| --- | --- | --- |
| `TT_ENV` | `dev` | `prod` (startup validates secrets) |
| `TT_DEV_AUTH` | allowed | refused at startup |
| `TT_SESSION_SECRET` | any | >= 32 random bytes, required |
| `TT_SECRET_KEY` | optional | required (encrypts stored integration credentials) |
| `TT_DB_SSLMODE` | unset | `verify-full` with `TT_DB_SSLROOTCERT` |
| `TT_METRICS_TOKEN` | `/metrics` open | required to enable `/metrics` |
| TLS | plain HTTP | PEM pair (`TT_TLS_*`) or terminate at the proxy with `TT_TRUST_FORWARDED_PROTO` |
