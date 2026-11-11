# 4. Production

Three supported shapes. All of them run the same three processes - **API server**, **worker**, **web console** (static files) -
against **PostgreSQL 16**, plus the optional **LLM gateway**.

| Shape | When | Section |
| --- | --- | --- |
| One Linux server with systemd (+ nginx) | most teams, VM or bare metal | [A](#a-one-linux-server-systemd) |
| Docker images | you run containers but not Kubernetes | [B](#b-docker-images) |
| Kubernetes (Helm) | you have a cluster | [C](#c-kubernetes-helm) |

## Before you start (all shapes)

* **Database:** PostgreSQL 16 with `pg_trgm`, `ltree`, `btree_gist`. Managed Postgres is fine. Use TLS to the database
  (`?sslmode=verify-full` and `TT_DB_SSLROOTCERT`). Create the extensions as a superuser once if the app role may not.
* **Secrets:** `TT_SESSION_SECRET` and `TT_SECRET_KEY`, each at least 32 random bytes and different from each other:
  `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`. The server refuses to start outside dev without them.
* **Sign-in:** OIDC (Entra ID, ADFS, Keycloak...), LDAPS / Active Directory, or Kerberos behind a proxy.
  See [`docs/security/`](../security/README.md). **`TT_DEV_AUTH` must be off**; the server refuses to start with it in `TT_ENV=prod`.
* **HTTPS:** either nginx/Caddy/an ingress terminates TLS (`TT_TRUST_FORWARDED_PROTO=true`), or the API serves HTTPS itself
  from a PEM pair ([TLS](../security/tls.md)).
* All settings: [configuration reference](../ops/config-reference.md) and [Configuration](07-configuration.md).

---

## A. One Linux server (systemd)

### Install
On the server, as root:
```bash
git clone https://github.com/mdaoor/team_tracker /opt/team-tracker && cd /opt/team-tracker
```
```bash
scripts/ttctl prod install --mode proxy --nginx
```
The installer (`scripts/prod/install.sh`):
1. creates the system user `tt`, `/etc/tt`, `/var/lib/team-tracker`;
2. installs Python dependencies without dev tools (`uv sync --no-dev --frozen`), builds the **web console**
   (`apps/web/dist`) and the **deck viewer**;
3. writes **`/etc/tt/tt.env`** (only if it does not exist) with `TT_ENV=prod` and **random secrets**, mode 640;
4. installs and enables the systemd units `tt-server` and `tt-worker` (from `deploy/systemd/`, pointed at this checkout);
5. with `--nginx`, installs `deploy/nginx/team-tracker.conf` into `/etc/nginx/conf.d/`;
6. runs migrations and starts the services.

On the first run it stops after step 3 because `TT_DATABASE_URL` still says `CHANGE_ME`. Edit the file:
```bash
sudo nano /etc/tt/tt.env
```
Set at least `TT_DATABASE_URL` and one sign-in method (`TT_OIDC_*` or `TT_LDAP_*`), then run the installer again:
```bash
sudo scripts/ttctl prod install --mode proxy --nginx
```
Finally edit `server_name` and the certificate paths in `/etc/nginx/conf.d/team-tracker.conf`, then:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

**Modes**

| `--mode` | API listens on | HTTPS by | Certificates |
| --- | --- | --- | --- |
| `proxy` (recommended) | `127.0.0.1:8000` | nginx (config in `deploy/nginx/`) | nginx's `ssl_certificate` |
| `tls` | `:8443` (+ `:8080` redirect) | the API itself (`tt-admin serve`) | `/etc/tt/tls/cert.pem`, `key.pem`; rotate with `scripts/ttctl prod reload` |

In `tls` mode you still need a web server for the built web console (`apps/web/dist`); proxy mode does both in one nginx config.

Add `--with-gateway` to also install the LLM gateway service (`/etc/tt/llm_gateway.yaml`, `/etc/tt/llm-gateway.env` with a
random client token). Then set `TT_LLM_GATEWAY_URL` and `TT_LLM_GATEWAY_TOKEN` in `/etc/tt/tt.env` ([LLM gateway](../llm/gateway.md)).

### Run and stop (production)

| Action | Command |
| --- | --- |
| status + health check | `scripts/ttctl prod status` |
| start / stop / restart | `scripts/ttctl prod start`, `prod stop`, `prod restart` |
| reload TLS certificates (tls mode) | `scripts/ttctl prod reload` |
| logs (journald) | `scripts/ttctl prod logs -f`, `scripts/ttctl prod logs tt-worker` |
| start on boot / not | `scripts/ttctl prod enable`, `prod disable` |
| health JSON | `scripts/ttctl prod health` |

These call `systemctl`/`journalctl` for `tt-server`, `tt-worker` and (if installed) `tt-llm-gateway`.
Plain systemd works too: `sudo systemctl restart tt-server tt-worker`.

### Upgrade
```bash
sudo scripts/ttctl prod upgrade
```
Backs up the database (`/var/backups/tt`), pulls, reinstalls dependencies, rebuilds the web console, migrates, restarts and
checks health. Pin a version with `sudo scripts/ttctl prod upgrade v1.0.0`. It prints the previous commit so you can go back
(`sudo scripts/ttctl prod upgrade <previous-commit>`); migrations are additive, so the old code runs on the new schema.

### Backups
Nightly, from cron (`/etc/cron.d/team-tracker`):
```text
15 2 * * * tt  set -a; . /etc/tt/tt.env; set +a; BACKUP_DIR=/var/backups/tt KEEP_DAYS=14 /opt/team-tracker/deploy/backup/backup.sh
```
Restore drill and point-in-time recovery: [restore runbook](../runbooks/restore.md). Ad hoc: `scripts/ttctl db backup` / `db restore FILE`.

### Monitoring
* `GET /healthz` (alive), `GET /readyz` (database + job queue) - point your load balancer and uptime check at `/readyz`.
* Prometheus: set `TT_METRICS_TOKEN`, scrape `/metrics` with that bearer token; alert rules in `ops/prometheus/alerts.yml`,
  dashboard in `ops/grafana/tt-overview.json`. Runbooks: [`docs/runbooks/`](../runbooks/README.md).

---

## B. Docker images

Build:
```bash
scripts/ttctl build images
```
This produces `team-tracker-server` (API + worker + `tt-admin`) and `team-tracker-llm-gateway`. Tag with `TT_IMAGE_TAG=1.0.0`.

Run (example; use your own secrets store and network):
```bash
docker run --rm --env-file /etc/tt/tt.env team-tracker-server tt-admin migrate
```
```bash
docker run -d --name tt-server --env-file /etc/tt/tt.env -p 127.0.0.1:8000:8000 team-tracker-server uvicorn tt_server.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers
```
```bash
docker run -d --name tt-worker --env-file /etc/tt/tt.env team-tracker-server python -m tt_worker
```
Serve `apps/web/dist` (from `scripts/ttctl build web`) with nginx using `deploy/nginx/team-tracker.conf`.
`docker-compose.yml` in the repository is for development (source mounted, dev sign-in) - do not use it as is in production.

## C. Kubernetes (Helm)

Chart in `deploy/helm/team-tracker`: server (2 replicas), worker, web (nginx + SPA), optional gateway, a migrate Job on
install/upgrade, PVC for attachments, Ingress, optional ServiceMonitor. Full instructions: [Helm](../ops/helm.md).
```bash
helm upgrade --install tt deploy/helm/team-tracker -f my-values.yaml
```

---

## Production checklist
- [ ] `TT_ENV=prod`, `TT_DEV_AUTH` unset/0
- [ ] `TT_SESSION_SECRET` and `TT_SECRET_KEY` random, >= 32 bytes, different, not in git
- [ ] HTTPS everywhere (`TT_FORCE_HTTPS=true`); database over TLS
- [ ] a real sign-in method configured and tested with a non-admin account
- [ ] `/readyz` monitored; `/metrics` behind `TT_METRICS_TOKEN`
- [ ] nightly backup + a restore drill done once
- [ ] raw-input retention set (workspace setting `raw_input_retention_days`; the worker purges nightly, or run `tt-admin purge-raw-input --days N`)
- [ ] firewall: only 443 (and 80 for the redirect) open to users; 5432 and 8000 private
