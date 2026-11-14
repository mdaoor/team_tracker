#!/usr/bin/env bash
# One-time (and repeatable) setup on a machine without Docker.
source "$(dirname "$0")/lib/common.sh"
source "$ROOT/scripts/lib/prereqs.sh"

usage() {
  cat <<EOF
ttctl setup - prerequisites, dependencies, configuration, builds (and the database)

SYNOPSIS
  $TT_TTCTL setup [--with-db [--db native|docker|external] [--no-seed]] [--install-missing] [--browsers] [--prod]

STEPS
  1 prerequisites   checks the tools; offers to install missing user-space tools into .tools/ (no root)
  2 configuration   writes .env from .env.example if missing (never overwrites)
  3 Python          uv sync (offline: from the bundle's wheelhouse)
  4 web             pnpm install + builds the deck viewer (skipped when the bundle ships prebuilt apps)
  5 database        with --with-db: start, create, check the SERVER version and extensions, migrate, seed

OPTIONS
  --with-db           also prepare the database
  --db MODE           native (PostgreSQL $TT_PG_MAJOR on this host, default) | docker (compose "db") | external (TT_DATABASE_URL)
  --no-seed           with --with-db: migrate but do not load the demo workspace
  --install-missing   install missing user-space tools without asking (system packages still ask, or need --yes)
  --browsers          also install Chromium for PDF/PNG export
  --prod              production flavour: no dev dependencies, random secrets in .env, web console built, no seed

  Without a terminal, missing tools are an error (exit 3) unless --install-missing or --yes is given.

EXAMPLES
  $TT_TTCTL setup --with-db
  $TT_TTCTL setup --with-db --install-missing --yes
  $TT_TTCTL setup --with-db --db external
  $TT_TTCTL -v setup --dry-run

SEE ALSO
  $GUIDE/02-install-online.md   $GUIDE/03-install-airgapped.md
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"

WITH_DB=0; SEED=1; BROWSERS=0; PROD=0; INSTALL=0; DBMODE="${TT_DB_MODE:-native}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-db) WITH_DB=1 ;; --no-seed) SEED=0 ;; --browsers) BROWSERS=1 ;; --prod) PROD=1; SEED=0 ;;
    --install-missing) INSTALL=1 ;;
    --db) [[ $# -ge 2 ]] || usage_fail "--db needs native, docker or external"; DBMODE="$2"; shift ;;
    --db=*) DBMODE="${1#*=}" ;;
    *) usage_fail "unknown option '$1' for setup" ;;
  esac; shift
done
[[ "$DBMODE" == local ]] && DBMODE=native
case "$DBMODE" in native|docker|external) ;; *) usage_fail "--db must be native, docker or external, not '$DBMODE'" ;; esac
cd "$ROOT" || exit 1
PREBUILT=0; [[ -f apps/web/dist/index.html && -f apps/presentation/dist/presentation.html ]] && PREBUILT=1

# ---------- 1. prerequisites -------------------------------------------------------------------------------------------
title "1/5 Prerequisites"
need=(uv python curl); [[ $PREBUILT -eq 0 ]] && need+=(node pnpm)
missing=(); system_missing=()
for t in "${need[@]}"; do
  tool_probe "$t"
  case "$P_STATUS" in
    ok) ok "$t ${P_VERSION}" ;;
    *) if [[ "$(tool_kind "$t")" == user ]]; then missing+=("$t"); warn "$t ${P_VERSION:+$P_VERSION }is ${P_STATUS} (need $P_NEED)"
       else system_missing+=("$t"); warn "$t is ${P_STATUS} (need $P_NEED)"; fi ;;
  esac
done
[[ $PREBUILT -eq 1 ]] && ok "node/pnpm not needed: the web console and deck viewer are prebuilt"
# psql: say what the client version means, so nobody chases a non-problem
tool_probe psql
case "$P_STATUS" in
  ok) ok "psql $P_VERSION" ;;
  old) say "${C_DIM}note${C_OFF} psql $P_VERSION found. That is fine for setup and --with-db: the version that matters is the SERVER's, checked below with SHOW server_version_num. Only '$TT_TTCTL db backup|restore|psql' need a $TT_PG_MAJOR client; they use .tools/pgsql, /usr/pgsql-$TT_PG_MAJOR or the postgres:$TT_PG_MAJOR container instead." ;;
  *) detail "psql not installed (only db backup/restore/psql need it)" ;;
esac

if [[ ${#system_missing[@]} -gt 0 ]]; then
  fail TT-E100 "${system_missing[*]} missing" "these are system packages and need root" \
    "$(tt_cmd prereqs install "${system_missing[@]}" --system)" "install them from the OS installation media, then: $(tt_cmd setup)"
fi
if [[ ${#missing[@]} -gt 0 ]]; then
  list="${missing[*]}"
  if [[ "${TT_OFFLINE:-0}" == 1 ]]; then
    fail TT-E401 "$list missing on an offline host" "offline mode (TT_OFFLINE=1) cannot download them" \
      "install them from the bundle: $(tt_cmd prereqs install "${missing[@]}" --from-bundle DIR)" \
      "install them from the bundle: $(tt_cmd prereqs install "${missing[@]}" --from-bundle DIR)"
  fi
  why_needed() {
    local t out=(); for t in "$@"; do case "$t" in
      uv|python) out+=("$t installs and runs the Python server") ;; node|pnpm) out+=("$t installs and builds the web console") ;;
      curl) out+=("curl is used for downloads and health checks") ;; *) out+=("$t is required") ;; esac; done
    local IFS=';'; echo "${out[*]}"
  }
  do_install=0
  if [[ $INSTALL -eq 1 || "${TT_YES:-0}" == 1 ]]; then do_install=1
  elif [[ -t 0 ]]; then
    for t in "${missing[@]}"; do
      case "$t" in pnpm) v=" $TT_PNPM_VERSION" ;; node) v=" $TT_NODE_VERSION" ;; python) v=" $TT_PYTHON_VERSION" ;; *) v="" ;; esac
      confirm "$t$v is missing. Install it into ${TOOLS_DIR#"$ROOT"/}/ now (no root needed)?" y && do_install=1 || { do_install=0; break; }
    done
  fi
  if [[ $do_install -eq 1 ]]; then
    step "install ${list} into ${TOOLS_DIR#"$ROOT"/}" "$ROOT/scripts/prereqs.sh" install "${missing[@]}" --user
    hash -r
  else
    fail TT-E100 "$list missing" \
      "$(why_needed "${missing[@]}"); $( [[ -t 0 ]] && echo "you declined the install" || echo "no terminal to ask and neither --install-missing nor --yes was given")" \
      "$(tt_cmd setup --install-missing "$( [[ $WITH_DB -eq 1 ]] && echo --with-db)")   (or only the tools: $(tt_cmd prereqs install "${missing[@]}"))"
  fi
fi

# ---------- 2. configuration -------------------------------------------------------------------------------------------
title "2/5 Configuration (.env)"
if [[ -f .env ]]; then
  ok ".env exists (left unchanged)"
else
  run cp .env.example .env
  if [[ $PROD -eq 1 ]] && ! dry_run; then
    session="$("$(tt_python)" -c 'import secrets;print(secrets.token_urlsafe(48))')"
    secret="$("$(tt_python)" -c 'import secrets;print(secrets.token_urlsafe(48))')"
    sed -i.bak -e 's/^TT_ENV=.*/TT_ENV=prod/' -e 's/^TT_DEV_AUTH=.*/TT_DEV_AUTH=0/' -e "s|^TT_SESSION_SECRET=.*|TT_SESSION_SECRET=$session|" .env && rm -f .env.bak
    printf '\n# generated by %s setup --prod\nTT_SECRET_KEY=%s\n' "$TT_TTCTL" "$secret" >> .env
    chmod 600 .env
    ok ".env created for production (TT_ENV=prod, random secrets) - set TT_DATABASE_URL and sign-in next"
  else
    ok ".env created from .env.example (dev defaults: local PostgreSQL tt/tt, Development sign-in on)"
  fi
fi
if [[ $WITH_DB -eq 1 && "$DBMODE" != native ]] && ! grep -q '^TT_DB_MODE=' .env 2>/dev/null; then
  dry_run || printf '\n# database mode chosen by setup --db\nTT_DB_MODE=%s\n' "$DBMODE" >> .env
  ok "TT_DB_MODE=$DBMODE saved in .env"
fi
load_env

# ---------- 3. Python ----------------------------------------------------------------------------------------------------
title "3/5 Python dependencies"
if [[ "${TT_OFFLINE:-0}" == 1 ]]; then
  need_python_env; ok "Python environment from the bundle (.venv)"
else
  if [[ $PROD -eq 1 ]]; then step "uv sync (no dev tools)" uv sync --all-packages --no-dev --frozen
  else step "uv sync" uv sync --all-packages --frozen; fi
  ok "Python workspace ready (.venv)"
fi

# ---------- 4. web -------------------------------------------------------------------------------------------------------
title "4/5 Web console and deck viewer"
if [[ $PREBUILT -eq 1 && ! -d node_modules ]]; then
  ok "prebuilt (apps/web/dist, apps/presentation/dist) - no Node needed on this host"
else
  tt_need_network "pnpm install" pnpm --with-dev
  step "pnpm install" pnpm install --frozen-lockfile
  step "build the deck viewer" pnpm --filter @tt/presentation build
  [[ $PROD -eq 1 ]] && step "build the web console" pnpm --filter @tt/web build
  ok "web dependencies installed, deck viewer built"
fi
if [[ $BROWSERS -eq 1 ]]; then
  if [[ "${TT_OFFLINE:-0}" == 1 ]]; then "$ROOT/scripts/prereqs.sh" install chromium --from-bundle "${TT_BUNDLE_DIR:-/nonexistent}"
  else step "install Chromium into ${TOOLS_DIR#"$ROOT"/}" "$ROOT/scripts/prereqs.sh" install chromium; fi
  # its system libraries are OS packages (root): shown, asked, never silent
  "$ROOT/scripts/prereqs.sh" install chromium-libs --system || warn "Chromium's libraries were not installed - PDF/PNG export may fail ($(tt_cmd prereqs install chromium-libs --system))"
fi

# ---------- 5. database --------------------------------------------------------------------------------------------------
title "5/5 Database"
if [[ $WITH_DB -eq 1 ]]; then
  export TT_DB_MODE="$DBMODE"
  if [[ "$DBMODE" == native ]] && ! pg_server_bin >/dev/null && ! { have pg_isready && pg_isready -q -h "$(db_part host)" -p "$(db_part port)"; }; then
    warn "PostgreSQL $TT_PG_MAJOR (server + contrib) is not installed on this host"
    if [[ "${TT_OFFLINE:-0}" == 1 ]]; then
      fail TT-E401 "PostgreSQL $TT_PG_MAJOR is not installed and this host is offline" "the bundle was installed without its database packages" \
        "$(tt_cmd bundle install FILE --with-db)" "$(tt_cmd bundle install FILE --with-db)"
    fi
    "$ROOT/scripts/prereqs.sh" install postgresql --system || true
    pg_server_bin >/dev/null || dry_run || fail TT-E206 "PostgreSQL $TT_PG_MAJOR is still not installed" "the system install was skipped or failed" \
      "$(tt_cmd prereqs install postgresql --system), or use another database: $(tt_cmd setup --with-db --db docker) / --db external"
  fi
  "$ROOT/scripts/db.sh" start --db "$DBMODE"
  "$ROOT/scripts/db.sh" create --db "$DBMODE"
  "$ROOT/scripts/db.sh" check --db "$DBMODE"
  "$ROOT/scripts/db.sh" migrate --db "$DBMODE"
  [[ $SEED -eq 1 ]] && "$ROOT/scripts/db.sh" seed --db "$DBMODE"
else
  say "skipped (run: $(tt_cmd db setup), or re-run with --with-db)"
fi

title "Done"
say "Next:"
say "  $(tt_cmd start)           start API, worker and web console in the background"
say "  $(tt_cmd status)          see what runs"
say "  open http://localhost:$TT_WEB_PORT/login   (Development sign-in: workspace acme, handle @mahmoud)"
