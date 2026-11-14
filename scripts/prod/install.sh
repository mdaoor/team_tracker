#!/usr/bin/env bash
# Production install on one Linux host with systemd (no containers).
source "$(dirname "$0")/../lib/common.sh"
usage() {
  cat <<EOF
ttctl prod install - production install on one Linux host with systemd (run as root)

SYNOPSIS
  $TT_TTCTL prod install --mode proxy|tls [--prefix DIR] [--with-gateway] [--nginx] [--no-start]

MODES
  proxy   API on 127.0.0.1:8000 behind nginx/Caddy that terminates HTTPS (recommended)
  tls     API serves HTTPS itself on :8443 from PEM files in /etc/tt/tls (+ :8080 redirect); see $GUIDE/06-production.md#tls

OPTIONS
  --prefix DIR     a bundle install's prefix (units then run DIR/current, so upgrades and rollbacks switch one symlink)
  --with-gateway   also the LLM gateway service (privacy_mode local_only when the host is offline)
  --nginx          install deploy/nginx/team-tracker.conf into /etc/nginx/conf.d (edit server_name and certificates)
  --no-start       install and enable, do not start

WHAT IT DOES
  system user "tt"; /etc/tt, /var/lib/team-tracker; dependencies (online: uv sync --no-dev + web build; offline: already
  installed by the bundle); /etc/tt/tt.env with random secrets if missing; systemd units tt-server, tt-worker
  (tt-llm-gateway); migrations; start. Idempotent: run it again after changing /etc/tt/tt.env.

EXAMPLES
  sudo $TT_TTCTL prod install --mode proxy --nginx
  sudo $TT_TTCTL prod install --mode tls
  sudo $TT_TTCTL --dry-run prod install --mode proxy
  sudo /opt/team-tracker/current/scripts/ttctl prod install --mode proxy --prefix /opt/team-tracker

SEE ALSO
  $GUIDE/06-production.md   $GUIDE/07-upgrade-and-rollback.md
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
cd "$ROOT" || exit 1

MODE=proxy; GATEWAY=0; NGINX=0; START=1; PREFIX=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift ;; --with-gateway) GATEWAY=1 ;; --nginx) NGINX=1 ;; --no-start) START=0 ;;
    --prefix) PREFIX="${2:-}"; shift ;;
    *) usage_fail "unknown option '$1' for prod install" ;;
  esac; shift
done
[[ "$MODE" == proxy || "$MODE" == tls ]] || usage_fail "--mode must be proxy or tls"
[[ "$(id -u)" -eq 0 ]] || dry_run || fail TT-E020 "prod install changes /etc and systemd" "you are $(id -un), not root" "sudo $TT_TTCTL prod install --mode $MODE"
have systemctl || fail TT-E502 "systemd is required for prod install" "systemctl is not on PATH" \
  "use Docker images or Helm instead ($GUIDE/06-production.md), or run the processes with $TT_TTCTL start"
APP="$ROOT"; [[ -n "$PREFIX" ]] && APP="$PREFIX/current"
ENVFILE=/etc/tt/tt.env
UNITS=(tt-server tt-worker); [[ $GATEWAY -eq 1 ]] && UNITS+=(tt-llm-gateway)
detail "app directory for the units: $APP"

step "system user and directories" bash -c "id tt >/dev/null 2>&1 || useradd --system --home /var/lib/team-tracker --shell /usr/sbin/nologin tt
  install -d -o tt -g tt -m 750 /var/lib/team-tracker /var/lib/team-tracker/attachments
  install -d -m 755 /etc/tt /etc/tt/tls"

if [[ "${TT_OFFLINE:-0}" == 1 ]]; then
  need_python_env
  [[ -f apps/web/dist/index.html ]] || fail TT-E401 "the prebuilt web console is missing" "apps/web/dist is not in this release" "reinstall the bundle: $(tt_cmd bundle install FILE)"
  ok "dependencies and web console from the bundle"
else
  have uv || fail TT-E100 "uv is needed" "uv is not on PATH or in .tools/bin" "$(tt_cmd prereqs install uv)"
  step "Python dependencies (no dev tools)" uv sync --all-packages --no-dev --frozen
  if [[ -f apps/web/dist/index.html && ! -d node_modules ]] && ! have pnpm; then ok "web console prebuilt"
  else
    have pnpm || fail TT-E104 "pnpm is needed to build the web console" "pnpm is not on PATH or in .tools/bin" "$(tt_cmd prereqs install pnpm)"
    step "pnpm install" pnpm install --frozen-lockfile
    step "build the deck viewer" pnpm --filter @tt/presentation build
    step "build the web console" pnpm --filter @tt/web build
  fi
fi
run chmod -R a+rX "$ROOT"

if [[ -f "$ENVFILE" ]]; then ok "$ENVFILE exists (left unchanged)"
elif ! dry_run; then
  rnd() { "$ROOT/.venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(48))'; }
  {
    echo "# Team Tracker server configuration - every key: $GUIDE/09-configuration.md"
    echo "TT_ENV=prod"
    echo "TT_DATABASE_URL=postgresql+psycopg://tt:CHANGE_ME@localhost:5432/tt"
    echo "TT_SESSION_SECRET=$(rnd)"
    echo "TT_SECRET_KEY=$(rnd)"
    echo "TT_LOG_JSON=true"
    echo "TT_ATTACHMENT_DIR=/var/lib/team-tracker/attachments"
    if [[ "$MODE" == proxy ]]; then echo "TT_TRUST_FORWARDED_PROTO=true"; echo "TT_FORCE_HTTPS=true"
    else echo "TT_TLS_CERTFILE=/etc/tt/tls/cert.pem"; echo "TT_TLS_KEYFILE=/etc/tt/tls/key.pem"; echo "TT_FORCE_HTTPS=true"; echo "TT_HTTPS_PORT=8443"; fi
    [[ "${TT_OFFLINE:-0}" == 1 ]] && { echo "TT_OFFLINE=1"; echo "TT_CHROMIUM=${TT_CHROMIUM:-}"; }
    echo "# Sign-in: OIDC (TT_OIDC_*) or Active Directory (TT_LDAP_*) - $GUIDE/06-production.md#active-directory. TT_DEV_AUTH stays off."
  } > "$ENVFILE"
  chown root:tt "$ENVFILE"; chmod 640 "$ENVFILE"
  warn "$ENVFILE written with random secrets - set TT_DATABASE_URL and sign-in, then run this again"
fi
if grep -q CHANGE_ME "$ENVFILE" 2>/dev/null; then START=0; warn "TT_DATABASE_URL in $ENVFILE still says CHANGE_ME - not starting"; fi
if [[ "$MODE" == tls ]] && [[ ! -f /etc/tt/tls/cert.pem || ! -f /etc/tt/tls/key.pem ]] && ! dry_run; then
  fail TT-E503 "--mode tls needs /etc/tt/tls/cert.pem and key.pem" "one or both files are missing" \
    "copy the PEM pair there (chown tt, key mode 600), or use --mode proxy behind nginx ($GUIDE/06-production.md#tls)"
fi

unit() {   # copy deploy/systemd/NAME pointing at APP
  run bash -c "sed -e 's#/opt/team-tracker#$APP#g' '$ROOT/deploy/systemd/$1' > '/etc/systemd/system/$1'"
}
unit tt-server.service; unit tt-worker.service
if [[ "$MODE" == proxy ]]; then
  run sed -i -e "s#^ExecStart=.*#ExecStart=$APP/.venv/bin/uvicorn tt_server.main:create_app --factory --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1#" /etc/systemd/system/tt-server.service
fi
if [[ $GATEWAY -eq 1 ]]; then
  unit tt-llm-gateway.service
  [[ -f /etc/tt/llm_gateway.yaml ]] || run install -m 640 -g tt apps/llm_gateway/llm_gateway.example.yaml /etc/tt/llm_gateway.yaml
  if [[ ! -f /etc/tt/llm-gateway.env ]] && ! dry_run; then
    { echo "TT_LLM_CONFIG=/etc/tt/llm_gateway.yaml"; echo "TT_LLM_GATEWAY_TOKENS=$("$ROOT/.venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(32))')"
      [[ "${TT_OFFLINE:-0}" == 1 ]] && echo "TT_LLM_PRIVACY_MODE=local_only"; echo "# ANTHROPIC_API_KEY= (only for privacy_mode hybrid/cloud)"; } > /etc/tt/llm-gateway.env
    chown root:tt /etc/tt/llm-gateway.env; chmod 640 /etc/tt/llm-gateway.env
  fi
fi
run systemctl daemon-reload
run systemctl enable "${UNITS[@]}"
ok "units installed and enabled: ${UNITS[*]}"
if [[ $NGINX -eq 1 ]]; then
  [[ -d /etc/nginx/conf.d ]] || fail TT-E100 "--nginx, but nginx is not installed" "/etc/nginx/conf.d is missing" "$(sudo_prefix)dnf -y install nginx   (or apt-get install -y nginx), then run this again"
  run bash -c "sed -e 's#/opt/team-tracker#$APP#g' '$ROOT/deploy/nginx/team-tracker.conf' > /etc/nginx/conf.d/team-tracker.conf"
  run install -m 644 "$ROOT/deploy/nginx/team-tracker.proxy" /etc/nginx/conf.d/team-tracker.proxy
  warn "edit server_name and the certificate paths in /etc/nginx/conf.d/team-tracker.conf, then: nginx -t && systemctl reload nginx"
fi
if [[ $START -eq 1 ]]; then
  step "migrations" bash -c "set -a; . '$ENVFILE'; set +a; runuser -u tt -- '$APP/.venv/bin/tt-admin' migrate"
  run systemctl restart "${UNITS[@]}"
  dry_run || "$ROOT/scripts/prod/service.sh" status
else
  say "Not started. When the configuration is complete: sudo $TT_TTCTL prod install --mode $MODE${PREFIX:+ --prefix $PREFIX}"
fi
