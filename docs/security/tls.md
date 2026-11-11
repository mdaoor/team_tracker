# TLS, Kerberos SSO and certificate rotation

Decisions: ADR-0022, ADR-0036.

## HTTPS from a PEM certificate

Team Tracker serves HTTPS directly from a PEM certificate and key on Linux; clients trust an internal CA through a PEM bundle.

### Server

```bash
# cert.pem = leaf certificate followed by any intermediates (full chain); key.pem = unencrypted private key, mode 0600
tt-admin serve --certfile /etc/tt/tls/cert.pem --keyfile /etc/tt/tls/key.pem --port 443 --redirect-port 80
```

`TT_TLS_CERTFILE` / `TT_TLS_KEYFILE` are the defaults for the two flags. `--redirect-port` starts a second listener that answers
every plain-HTTP request with `308 https://<host>:<port><path>`.

With `TT_FORCE_HTTPS=true` the app itself also redirects any request that did not arrive over HTTPS (`/healthz` and `/readyz` are
exempt so probes keep working) and every HTTPS response carries
`Strict-Transport-Security: max-age=31536000; includeSubDomains` (`TT_HSTS_MAX_AGE`). Behind a TLS-terminating proxy set
`TT_TRUST_FORWARDED_PROTO=true` so `X-Forwarded-Proto` is honoured; leave it off otherwise, because the header is client-controlled.
Set `TT_HTTPS_PORT` when the public port differs from the bind port.

Docker: `docker compose --profile tls up` runs `server-tls` with `./deploy/tls/{cert,key}.pem` mounted read-only.
Caddy or nginx in front of the plain listener works too: terminate TLS with the same PEM pair and pass `X-Forwarded-Proto`.

### Clients

Every client (`tt`, desktop, Slack bot, LLM gateway) builds its SSL context from the system roots **plus** `TT_CA_BUNDLE`
(`tt_client.tls.verify_context`). `tt login` calls `/healthz` over the verified channel before saving anything:

```text
$ tt login --url https://tracker.corp.example.com --workspace acme --handle @sara --token tt_...
login failed: TLS verification failed for tracker.corp.example.com: [SSL: CERTIFICATE_VERIFY_FAILED] ... set TT_CA_BUNDLE
$ TT_CA_BUNDLE=/etc/tt/ca.pem tt login ...      # or --ca-bundle /etc/tt/ca.pem; the path is stored in the config
saved ~/.config/tt/config.toml (mode 600)
```

There is deliberately no flag that skips verification. Plain `http://` is accepted only for `localhost` / `127.0.0.1`.

### Distributing the CA

Give users `ca.pem` (the CA certificate only, never a key) through your usual channel -- configuration management, a shared
drive, or the installer -- and set `TT_CA_BUNDLE` (or `[server] ca_bundle` in `~/.config/tt/config.toml`). Public CAs need nothing.

## Rotating the certificate without a restart
The server swaps the chain on the live TLS context:

* `kill -HUP <pid>` (systemd: `systemctl reload tt-server`), and
* automatically every `TT_CERT_RELOAD_INTERVAL_S` (default 30 s) when either file's mtime/size changes.

New handshakes use the new certificate; open connections finish on the old one. A replacement is first validated on a scratch context, so a
half-copied pair, an unreadable file or a key that does not match is **logged (`tls.reload_failed`) and ignored**: the old certificate keeps
serving and the next change retries. (Writing cert then key in two steps is therefore safe.)

Expiry: `tls.expiring` is logged every check once fewer than `TT_CERT_EXPIRY_WARN_DAYS` (14) remain, and
`tt-admin cert-info /etc/tt/tls/cert.pem` prints the days left and exits 1 under the threshold, for cron/monitoring.

## Postgres over TLS (`verify-full`)
| Variable | Meaning |
| --- | --- |
| `TT_DB_SSLMODE` | `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full` (use `verify-full` in production) |
| `TT_DB_SSLROOTCERT` | PEM of the CA that signed the database server's certificate (required for `verify-ca`/`verify-full`) |
| `TT_DB_SSLCERT`, `TT_DB_SSLKEY` | client certificate and key, if the server requires them (set together) |

They apply to the API engine **and** the worker/Procrastinate connection. Startup fails fast on a missing CA file, an unknown mode or a
half-configured client certificate. With `verify-full` the name in `TT_DATABASE_URL` must be in the server certificate's SAN list.
Tested against a real TLS-enabled cluster (`tests/integration/test_db_tls.py`): right CA connects over TLS; another CA is rejected; a
host-name mismatch is rejected by `verify-full` and accepted by `verify-ca`.

Service-to-service TLS: the LLM gateway uses `TT_LLM_GATEWAY_CA_BUNDLE`; LDAPS uses `TT_LDAP_CA_BUNDLE`; Jira uses the system trust store
(add private CAs to `SSL_CERT_FILE`).

## Kerberos / SPNEGO single sign-on (reverse-proxy model)
The proxy in front of Team Tracker authenticates the browser (`Negotiate`) and forwards the principal. Examples: IIS + ARR, nginx with
`spnego-http-auth-nginx-module`, Apache `mod_auth_gssapi`, Traefik/oauth2-proxy with a Kerberos middleware.

| Variable | Meaning |
| --- | --- |
| `TT_TRUSTED_PRINCIPAL_HEADER` | e.g. `X-Remote-User`. Unset = feature off |
| `TT_TRUSTED_PROXIES` | comma-separated IPs/CIDRs of the proxies. **Required** with the header; startup fails without it |
| `TT_PRINCIPAL_EMAIL_DOMAIN` | completes `jdoe` / `CORP\jdoe` to `jdoe@domain`; replaces an upper-case Kerberos realm in `jdoe@CORP.LOCAL` |
| `TT_TRUSTED_GROUPS_HEADER` | optional header of comma-separated groups, fed to the IdP group mapping (provider `kerberos`) |

Rules the server enforces:
1. The header is honoured **only when the TCP peer is in `TT_TRUSTED_PROXIES`**. From anyone else it is ignored, so a client cannot forge it.
   Make the proxy overwrite (not append) the header, and make the app reachable only through the proxy.
2. API calls with a trusted principal authenticate an **existing** member by e-mail; an unknown principal is a 401 and nothing is created.
3. `GET /auth/sso?workspace=<slug>&next=/path` turns the principal into a normal session cookie using the same provisioning policy as OIDC/LDAP
   (existing member, mapped group, or allowed e-mail domain; otherwise an access request and 403). `next` may only be a same-site path.
4. A bearer token wins over the header (an invalid one is a 401, never a silent fallback to the header).

The alternative, in-process GSSAPI with a keytab on the application host, is **not** built; it is the open question in ADR-0036. Choose it
only if a proxy in front of the application is impossible.

## Checklist for an Active Directory site
LDAPS 636 reachable from the app; CA PEM in `TT_LDAP_CA_BUNDLE`; service account with read access to users/groups; SPN registered for the
proxy host (`HTTP/tt.corp.example`); keytab on the **proxy**, not on the app; `TT_TRUSTED_PROXIES` set to the proxy's address.

Tested with a throw-away CA and real uvicorn TLS (`tests/integration/test_tls.py`): untrusted chain and wrong CA refused, trusted CA accepted, HSTS present, HTTP redirected. Not tested against a real internal CA or browsers.
