# HTTPS from a PEM certificate

Team Tracker serves HTTPS directly from a PEM certificate and key on Linux; clients trust an internal CA through a PEM bundle.

## Server

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

## Clients

Every client (`tt`, desktop, Slack bot, LLM gateway) builds its SSL context from the system roots **plus** `TT_CA_BUNDLE`
(`tt_client.tls.verify_context`). `tt login` calls `/healthz` over the verified channel before saving anything:

```text
$ tt login --url https://tracker.corp.example.com --workspace acme --handle @sara --token tt_...
login failed: TLS verification failed for tracker.corp.example.com: [SSL: CERTIFICATE_VERIFY_FAILED] ... set TT_CA_BUNDLE
$ TT_CA_BUNDLE=/etc/tt/ca.pem tt login ...      # or --ca-bundle /etc/tt/ca.pem; the path is stored in the config
saved ~/.config/tt/config.toml (mode 600)
```

There is deliberately no flag that skips verification. Plain `http://` is accepted only for `localhost` / `127.0.0.1`.

## Distributing the CA

Give users `ca.pem` (the CA certificate only, never a key) through your usual channel -- configuration management, a shared
drive, or the installer -- and set `TT_CA_BUNDLE` (or `[server] ca_bundle` in `~/.config/tt/config.toml`). Public CAs need nothing.

## Rotation

Replace `cert.pem` / `key.pem` and restart the process (rolling restart behind a load balancer). Keep the key readable only by the
service user. Certificates are not reloaded on SIGHUP yet.

## Not yet

* Certificate **hot reload** on SIGHUP and an **expiry alert** (Phase 12 observability).
* **Postgres `sslmode=verify-full`** and TLS between server, gateway and worker: set `?sslmode=verify-full&sslrootcert=/etc/tt/ca.pem`
  on `TT_DATABASE_URL` yourself; nothing here enforces or tests it.
* Tested with a throw-away CA and real uvicorn TLS (`tests/integration/test_tls.py`): untrusted chain and wrong CA refused,
  trusted CA accepted, HSTS present, HTTP redirected. Not tested against a real internal CA or browsers.
