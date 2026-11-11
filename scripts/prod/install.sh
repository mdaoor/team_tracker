#!/usr/bin/env bash
# Production install on one Linux host with systemd (no containers). Run as root from the checkout you want to serve.
#
#   sudo scripts/prod/install.sh --mode proxy     API on 127.0.0.1:8000 behind nginx/Caddy that terminates HTTPS   (recommended)
#   sudo scripts/prod/install.sh --mode tls       API serves HTTPS itself on :8443 from PEM files in /etc/tt/tls (+ :8080 redirect)
#   options:  --with-gateway      also install the LLM gateway service (config /etc/tt/llm_gateway.yaml)
#             --nginx             also install deploy/nginx/team-tracker.conf into /etc/nginx/conf.d (edit server_name!)
#             --no-start          install and enable, but do not start
#
# What it does: system user "tt"; dirs /etc/tt, /var/lib/team-tracker; Python deps without dev tools (.venv);
# builds the web console and deck viewer; writes /etc/tt/tt.env (only if missing, with random secrets);
# installs tt-server / tt-worker (/ tt-llm-gateway) units pointing at this checkout; runs migrations; starts.
# Idempotent: run it again after "git pull" to upgrade (or use scripts/prod/upgrade.sh).
source "$(dirname "$0")/../lib/common.sh"
cd "$ROOT" || exit 1

MODE=proxy; GATEWAY=0; NGINX=0; START=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift ;; --with-gateway) GATEWAY=1 ;; --nginx) NGINX=1 ;; --no-start) START=0 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; *) die "unknown option $1" ;;
  esac; shift
done
[[ "$MODE" == proxy || "$MODE" == tls ]] || die "--mode is proxy or tls"
[[ "$(id -u)" -eq 0 ]] || die "run as root (sudo)"
have systemctl || die "systemd is required (for other hosts see docs/guide/04-production.md: Docker / Helm)"
need uv "Install uv for root: curl -LsSf https://astral.sh/uv/install.sh | sh"
need pnpm "Install Node 22 and run: corepack enable"

ENVFILE=/etc/tt/tt.env
title "1/6 User and directories"
id tt >/dev/null 2>&1 || useradd --system --home /var/lib/team-tracker --shell /usr/sbin/nologin tt
install -d -o tt -g tt -m 750 /var/lib/team-tracker
install -d -m 755 /etc/tt /etc/tt/tls
ok "user tt, /etc/tt, /var/lib/team-tracker"

title "2/6 Dependencies and builds"
uv sync --all-packages --no-dev --frozen
pnpm install --frozen-lockfile
pnpm --filter @tt/presentation build
pnpm --filter @tt/web build
chmod -R a+rX "$ROOT"
ok "Python .venv, web console in apps/web/dist, deck viewer built"

title "3/6 Configuration ($ENVFILE)"
if [[ -f "$ENVFILE" ]]; then
  ok "$ENVFILE exists (left unchanged)"
else
  rnd() { python3 -c 'import secrets;print(secrets.token_urlsafe(48))'; }
  {
    echo "# Team Tracker server configuration - every key: docs/ops/config-reference.md"
    echo "TT_ENV=prod"
    echo "TT_DATABASE_URL=postgresql+psycopg://tt:CHANGE_ME@localhost:5432/tt"
    echo "TT_SESSION_SECRET=$(rnd)"
    echo "TT_SECRET_KEY=$(rnd)"
    echo "TT_LOG_JSON=true"
    echo "TT_ATTACHMENT_DIR=/var/lib/team-tracker/attachments"
    if [[ "$MODE" == proxy ]]; then
      echo "TT_TRUST_FORWARDED_PROTO=true"; echo "TT_FORCE_HTTPS=true"
    else
      echo "TT_TLS_CERTFILE=/etc/tt/tls/cert.pem"; echo "TT_TLS_KEYFILE=/etc/tt/tls/key.pem"; echo "TT_FORCE_HTTPS=true"; echo "TT_HTTPS_PORT=8443"
    fi
    echo "# Sign-in: configure OIDC or LDAPS (see docs/security/). TT_DEV_AUTH must stay off."
    echo "# TT_OIDC_ISSUER=  TT_OIDC_CLIENT_ID=  TT_OIDC_CLIENT_SECRET=  TT_OIDC_REDIRECT_URI=https://tracker.example.com/auth/callback"
  } > "$ENVFILE"
  chown root:tt "$ENVFILE"; chmod 640 "$ENVFILE"
  warn "$ENVFILE written with random secrets - set TT_DATABASE_URL and sign-in, then re-run this script"
  [[ $START -eq 1 ]] && grep -q CHANGE_ME "$ENVFILE" && START=0
fi
if [[ "$MODE" == tls ]] && [[ ! -f /etc/tt/tls/cert.pem || ! -f /etc/tt/tls/key.pem ]]; then
  warn "put the PEM pair in /etc/tt/tls/cert.pem and key.pem (owner tt, key mode 600) - docs/security/tls.md"; START=0
fi

title "4/6 systemd units"
unit() {   # unit NAME  - copy deploy/systemd/NAME with this checkout's path
  sed -e "s#/opt/team-tracker#$ROOT#g" "deploy/systemd/$1" > "/etc/systemd/system/$1"
}
unit tt-server.service; unit tt-worker.service
if [[ "$MODE" == proxy ]]; then
  sed -i -e "s#^ExecStart=.*#ExecStart=$ROOT/.venv/bin/uvicorn tt_server.main:create_app --factory --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1#" \
    /etc/systemd/system/tt-server.service
fi
sed -i -e "s#^ReadWritePaths=.*#ReadWritePaths=/var/lib/team-tracker#" /etc/systemd/system/tt-server.service
if [[ $GATEWAY -eq 1 ]]; then
  unit tt-llm-gateway.service
  [[ -f /etc/tt/llm_gateway.yaml ]] || install -m 640 -g tt apps/llm_gateway/llm_gateway.example.yaml /etc/tt/llm_gateway.yaml
  [[ -f /etc/tt/llm-gateway.env ]] || { printf 'TT_LLM_CONFIG=/etc/tt/llm_gateway.yaml\nTT_LLM_GATEWAY_TOKENS=%s\n# ANTHROPIC_API_KEY=\n' \
    "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" > /etc/tt/llm-gateway.env; chown root:tt /etc/tt/llm-gateway.env; chmod 640 /etc/tt/llm-gateway.env; }
fi
systemctl daemon-reload
UNITS=(tt-server tt-worker); [[ $GATEWAY -eq 1 ]] && UNITS+=(tt-llm-gateway)
systemctl enable "${UNITS[@]}" >/dev/null
ok "units installed and enabled"

if [[ $NGINX -eq 1 ]]; then
  title "nginx"
  [[ -d /etc/nginx/conf.d ]] || die "nginx is not installed (/etc/nginx/conf.d missing)"
  sed -e "s#/opt/team-tracker#$ROOT#g" deploy/nginx/team-tracker.conf > /etc/nginx/conf.d/team-tracker.conf
  install -m 644 deploy/nginx/team-tracker.proxy /etc/nginx/conf.d/team-tracker.proxy
  warn "edit server_name and the certificate paths in /etc/nginx/conf.d/team-tracker.conf, then: nginx -t && systemctl reload nginx"
fi

title "5/6 Database migrations"
if [[ $START -eq 1 ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENVFILE"
  set +a
  sudo -u tt --preserve-env "$ROOT/.venv/bin/tt-admin" migrate
else
  warn "skipped - finish the configuration first"
fi

title "6/6 Start"
if [[ $START -eq 1 ]]; then
  systemctl restart "${UNITS[@]}"
  "$ROOT/scripts/prod/service.sh" status
else
  echo "not started. When ready: sudo scripts/prod/install.sh --mode $MODE   (or: sudo scripts/prod/service.sh start)"
fi
