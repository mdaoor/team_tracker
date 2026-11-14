# 6. Production (systemd, TLS, Active Directory)

One Linux host with systemd: the API (`tt-server`), the worker (`tt-worker`), optionally the LLM gateway
(`tt-llm-gateway`), PostgreSQL 16 (same host or another), and nginx for HTTPS and the web console. Kubernetes:
[Helm](../ops/helm.md). Containers without Kubernetes: `scripts/ttctl build images` and the notes at the end.

## Install

Connected host (from a git checkout):
```bash
sudo scripts/ttctl prod install --mode proxy --nginx
```
Air-gapped host (from a bundle, see [Install air-gapped](03-install-airgapped.md)):
```bash
sudo ./install.sh --with-db --mode prod
```
What `prod install` does: system user `tt`; `/etc/tt`, `/var/lib/team-tracker`; dependencies (online: `uv sync --no-dev`
and the web build; offline: already in the release); `/etc/tt/tt.env` with **random secrets** if it does not exist;
systemd units pointing at the install (bundle installs: `PREFIX/current`, so upgrades switch one symlink); migrations;
start. The first run stops before starting because `TT_DATABASE_URL` says `CHANGE_ME`:
```bash
sudo vi /etc/tt/tt.env
```
Set `TT_DATABASE_URL` and the sign-in (below), then run `prod install` again.

| `--mode` | API listens on | HTTPS by |
| --- | --- | --- |
| `proxy` (recommended) | 127.0.0.1:8000 | nginx with `deploy/nginx/team-tracker.conf` (`--nginx` installs it; edit `server_name` and certificates) |
| `tls` | :8443 (+ :8080 redirect) | the API itself, from PEM files (see [TLS](#tls)) |

`--with-gateway` adds the LLM gateway unit (`/etc/tt/llm_gateway.yaml`, a random client token in `/etc/tt/llm-gateway.env`;
offline hosts get `privacy_mode: local_only`). Set `TT_LLM_GATEWAY_URL` and `TT_LLM_GATEWAY_TOKEN` in `/etc/tt/tt.env`.

## Operate
| Action | Command |
| --- | --- |
| status + health | `scripts/ttctl prod status` |
| start / stop / restart | `sudo scripts/ttctl prod start` · `stop` · `restart` |
| logs | `scripts/ttctl prod logs -f` · `prod logs tt-worker` |
| reload certificates (tls mode) | `sudo scripts/ttctl prod reload` |
| boot behaviour | `sudo scripts/ttctl prod enable` · `disable` |
| upgrade / roll back | [Upgrade and rollback](07-upgrade-and-rollback.md) |

## TLS
**Proxy mode:** nginx terminates TLS with your certificate (`ssl_certificate` = full chain, `ssl_certificate_key`), the
API trusts `X-Forwarded-Proto` (`TT_TRUST_FORWARDED_PROTO=true`, written by the installer) and redirects to HTTPS
(`TT_FORCE_HTTPS=true`).

**TLS mode:** the API reads a PEM pair:
```bash
sudo install -o tt -g tt -m 644 fullchain.pem /etc/tt/tls/cert.pem
```
```bash
sudo install -o tt -g tt -m 600 privkey.pem /etc/tt/tls/key.pem
```
```bash
sudo scripts/ttctl prod install --mode tls
```
Rotate by replacing the two files and `sudo scripts/ttctl prod reload` (or wait 30 s; the server re-reads changed files).
`tt-admin cert-info /etc/tt/tls/cert.pem` shows subject, SANs and expiry; a warning is logged 14 days before expiry.
Clients behind an internal CA: `TT_CA_BUNDLE` / `tt login --ca-bundle`. Details: [TLS](../security/tls.md).

## Active Directory
Sign-in against AD over LDAPS (NT accounts and groups). In `/etc/tt/tt.env`:
```text
TT_LDAP_URL=ldaps://dc1.corp.example.com:636
TT_LDAP_CA_BUNDLE=/etc/tt/ca.pem
TT_LDAP_BIND_DN=CN=svc-tt,OU=Service,DC=corp,DC=example,DC=com
TT_LDAP_BIND_PASSWORD=...
TT_LDAP_BASE_DN=DC=corp,DC=example,DC=com
TT_LDAP_WORKSPACE=acme
```
Map AD groups to roles in the web console (Setup → Identity). Kerberos single sign-on runs behind a proxy that sets a
trusted header (`TT_TRUSTED_PRINCIPAL_HEADER`, `TT_TRUSTED_PROXIES`). OIDC (Entra ID, ADFS, Keycloak) uses `TT_OIDC_*`.
Details: [Active Directory](../security/active-directory.md), [access control](../security/access-control.md).
`TT_DEV_AUTH` must stay off: the server refuses to start with it when `TT_ENV=prod`.

## Backups
```text
# /etc/cron.d/team-tracker
15 2 * * * tt  set -a; . /etc/tt/tt.env; set +a; BACKUP_DIR=/var/backups/tt KEEP_DAYS=14 /opt/team-tracker/current/deploy/backup/backup.sh
```
`prod upgrade` takes a backup first. Restore drill: [restore runbook](../runbooks/restore.md); ad hoc:
`scripts/ttctl db backup` / `db restore FILE`.

## Monitoring
`/readyz` for the load balancer and uptime checks; Prometheus at `/metrics` with `TT_METRICS_TOKEN`; alert rules in
`ops/prometheus/alerts.yml`, dashboard `ops/grafana/tt-overview.json`; [runbooks](../runbooks/README.md).

## Containers without Kubernetes
`scripts/ttctl build images` builds `team-tracker-server` (API, worker, `tt-admin`) and `team-tracker-llm-gateway`;
air-gapped hosts get them with `bundle create --with-docker-images`. Run `tt-admin migrate` once, then the API
(`uvicorn tt_server.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers`) and the worker
(`python -m tt_worker`) with `--env-file /etc/tt/tt.env`; serve `apps/web/dist` with nginx. `docker-compose.yml` is for development.

## Checklist
- [ ] `TT_ENV=prod`, `TT_DEV_AUTH` off; `TT_SESSION_SECRET` and `TT_SECRET_KEY` random, 32+ bytes, different
- [ ] HTTPS everywhere; database over TLS (`TT_DB_SSLMODE=verify-full`)
- [ ] real sign-in tested with a non-admin account
- [ ] `/readyz` monitored, `/metrics` behind its token
- [ ] nightly backup and one restore drill done
- [ ] firewall: 443 (and 80 for the redirect) open to users; 5432 and 8000 private
