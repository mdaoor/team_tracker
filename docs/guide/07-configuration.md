# 7. Configuration

The server, worker and scripts read **`TT_*` variables** from the environment or from a **`.env`** file in the repository root
(production: `/etc/tt/tt.env`, loaded by systemd). Start from [`.env.example`](../../.env.example). Every server variable with
its type and default is generated into the [configuration reference](../ops/config-reference.md).

## The ones you will touch

| Variable | Dev value | Production | Why |
| --- | --- | --- | --- |
| `TT_ENV` | `dev` | `prod` | outside `dev` the server validates secrets and refuses unsafe settings |
| `TT_DATABASE_URL` | `postgresql+psycopg://tt:tt@localhost:5432/tt` | your database, with `?sslmode=verify-full` | keep the `+psycopg` driver part |
| `TT_DEV_AUTH` | `1` | unset | the **Development sign-in** button / `X-Debug-Actor` header. Never outside a laptop |
| `TT_SESSION_SECRET` | anything | 32+ random bytes | signs the web session cookie |
| `TT_SECRET_KEY` | optional | 32+ random bytes, different from the session secret | encrypts stored integration credentials (Jira) |
| `TT_OIDC_ISSUER`, `TT_OIDC_CLIENT_ID`, `TT_OIDC_CLIENT_SECRET`, `TT_OIDC_REDIRECT_URI` | - | for OIDC sign-in | [security](../security/README.md) |
| `TT_LDAP_URL`, `TT_LDAP_BIND_DN`, `TT_LDAP_BIND_PASSWORD`, `TT_LDAP_BASE_DN`, `TT_LDAP_CA_BUNDLE`, `TT_LDAP_WORKSPACE` | - | for Active Directory | [active directory](../security/active-directory.md) |
| `TT_TLS_CERTFILE`, `TT_TLS_KEYFILE`, `TT_FORCE_HTTPS` | - | API serves HTTPS itself | [TLS](../security/tls.md) |
| `TT_TRUST_FORWARDED_PROTO` | - | `true` behind nginx / an ingress | trust `X-Forwarded-Proto` |
| `TT_ATTACHMENT_DIR` | `./var/attachments` | `/var/lib/team-tracker/attachments` or S3 (`TT_ATTACHMENT_BACKEND=s3`) | uploads |
| `TT_LLM_GATEWAY_URL`, `TT_LLM_GATEWAY_TOKEN` | - | when a gateway runs | LLM features on the server |
| `TT_METRICS_TOKEN` | - | random | enables `/metrics` |
| `TT_LOG_LEVEL`, `TT_LOG_JSON` | `INFO`, `true` | same | logging |
| `TT_FEATURES__<NAME>` | `false` | per need | feature flags: `INSIGHTS_LLM`, `LLM_SUGGESTIONS`, `JIRA_SYNC`, `GOVERNANCE`, `HABITS_NIGHTLY`, `SLACK_BOT` |

## Script-only settings
Used by `scripts/` (not by the server): `TT_HOST`, `TT_PORT`, `TT_WEB_PORT`, `TT_GATEWAY_PORT`, `TT_GATEWAY_MODE`, `TT_RELOAD`,
`TT_START_TIMEOUT`, `TT_STOP_TIMEOUT`, `TT_DB_MODE`, `TT_DB_ADMIN_URL`, `TT_RUN_DIR`, `TT_IMAGE_TAG`, `TT_TIME_ZONE`.
See [Run and stop](03-run-and-stop.md#settings-that-change-how-things-run). `TT_API_URL` is where the web dev server proxies
API calls (default `http://127.0.0.1:$TT_PORT`).

## Client settings
The `tt` CLI keeps its own settings in `~/.config/tt/config.toml` (written by `tt login`). `TT_CA_BUNDLE` points clients at
an internal CA. LLM gateway settings live in its YAML (`apps/llm_gateway/llm_gateway.example.yaml`) plus `TT_LLM_*` variables.

## Workspace settings
Settings that belong to a workspace (sharing, retention, daily-log time, hours per day, ...) are set in the web console
(**Setup**) or the API, not in `.env`: see the [admin guide](../guides/admin.md).
