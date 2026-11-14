#!/usr/bin/env bash
# Runs INSIDE a container of the target OS (started by "ttctl bundle create"), in two phases:
#
#   builder.sh packages   in a FRESH container: download the OS packages (PostgreSQL, Chromium's libraries) with the
#                         package manager's download-only mode, so they include every dependency a minimal target lacks
#   builder.sh main       everything else: Python, wheelhouse, prebuilt web apps, Chromium, client kit, optional Node/LLM
#
# Mounts:  /out (bundle staging dir, already containing app/)   /host-uv (uv binary)   /cache (download cache volume)
#          /browsers (optional Playwright browser dir to copy)   /extra-ca.crt (optional CA of an intercepting proxy)
#          /pgdg.asc (deb targets without PostgreSQL 16 in the archive: the PGDG signing key, fetched by the host)
# Env:     TARGET  WITH_DB(none|rpm|deb|image)  WITH_BROWSERS(0|1)  WITH_DEV(0|1)  WITH_LLM(model or empty)
#          TT_PYTHON_VERSION TT_NODE_VERSION TT_PNPM_VERSION TT_PG_MAJOR
set -euo pipefail
PHASE="${1:-main}"
log() { printf '[builder %s %s] %s\n' "$PHASE" "$(date +%H:%M:%S)" "$*"; }
fail() { printf '[builder %s] ERROR: %s\n' "$PHASE" "$*" >&2; exit 1; }
OUT=/out; APP=/out/app; WORK=/work
. /etc/os-release
FAMILY=deb; [[ "$ID" =~ ^(rocky|rhel|almalinux|centos|ol)$ || " ${ID_LIKE:-} " == *" rhel "* ]] && FAMILY=rpm
EL="${VERSION_ID%%.*}"
PG="$TT_PG_MAJOR"

# ---------- package manager configuration (proxy / extra CA only when the build machine needs them) ----------------
if [[ $FAMILY == deb ]]; then
  export DEBIAN_FRONTEND=noninteractive
  if [[ -n "${HTTPS_PROXY:-}" ]]; then
    printf 'Acquire::https::Proxy "%s";\n' "$HTTPS_PROXY" > /etc/apt/apt.conf.d/90proxy
    [[ -f /extra-ca.crt ]] && printf 'Acquire::https::CaInfo "/extra-ca.crt";\n' >> /etc/apt/apt.conf.d/90proxy
    # an HTTPS-only proxy: switch the archive sources to https
    sed -i 's#http://\(archive\|security\|ports\).ubuntu.com#https://\1.ubuntu.com#g' /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true
  fi
  apt-get update -qq || fail "apt-get update failed (repository unreachable?)"
else
  if [[ -f /extra-ca.crt ]]; then cp /extra-ca.crt /etc/pki/ca-trust/source/anchors/extra.crt && update-ca-trust; fi
  if [[ -n "${HTTPS_PROXY:-}" ]] && ! grep -q '^proxy=' /etc/dnf/dnf.conf; then echo "proxy=$HTTPS_PROXY" >> /etc/dnf/dnf.conf; fi
fi

# ======================================================================================================================
if [[ "$PHASE" == packages ]]; then
  if [[ $FAMILY == rpm ]]; then
    if [[ "${WITH_DB:-none}" == rpm ]]; then
      log "PostgreSQL $PG rpms (PGDG) with every dependency missing from a minimal $ID $VERSION_ID"
      dnf -y -q install "https://download.postgresql.org/pub/repos/yum/reporpms/EL-$EL-x86_64/pgdg-redhat-repo-latest.noarch.rpm" >/dev/null || fail "PGDG repository unreachable"
      [[ "$EL" == 8 ]] && dnf -qy module disable postgresql >/dev/null
      mkdir -p "$OUT/packages/rpm"
      dnf -y -q install --downloadonly --downloaddir="$OUT/packages/rpm" "postgresql$PG-server" "postgresql$PG-contrib" "postgresql$PG" >/dev/null
    fi
    if [[ "${WITH_BROWSERS:-0}" == 1 ]]; then
      log "Chromium system library rpms"
      mkdir -p "$OUT/packages/browser-libs"
      dnf -y -q install --downloadonly --downloaddir="$OUT/packages/browser-libs" nss nspr atk at-spi2-atk cups-libs libdrm libxkbcommon \
        libXcomposite libXdamage libXext libXfixes libXrandr mesa-libgbm pango cairo alsa-lib libxshmfence liberation-fonts >/dev/null
    fi
  else
    if [[ "${WITH_DB:-none}" == deb ]] && ! apt-cache show "postgresql-$PG" >/dev/null 2>&1; then
      [[ -f /pgdg.asc ]] || fail "PostgreSQL $PG is not in the $ID $VERSION_ID archive and the PGDG key was not provided"
      log "adding the PGDG apt repository"
      echo "deb [signed-by=/pgdg.asc] http://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list
      apt-get update -qq || fail "apt-get update with PGDG failed"
    fi
    if [[ "${WITH_DB:-none}" == deb ]]; then
      log "PostgreSQL $PG debs with every dependency missing from a minimal $ID $VERSION_ID"
      apt-get clean
      apt-get install -y -qq --no-install-recommends --download-only "postgresql-$PG" "postgresql-client-$PG" >/dev/null
      mkdir -p "$OUT/packages/deb"; cp /var/cache/apt/archives/*.deb "$OUT/packages/deb/"
    fi
    if [[ "${WITH_BROWSERS:-0}" == 1 ]]; then
      log "Chromium system library debs"
      apt-get clean
      libs="libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 fonts-liberation"
      if apt-cache show libasound2t64 >/dev/null 2>&1; then libs="$libs libasound2t64"; else libs="$libs libasound2"; fi
      # shellcheck disable=SC2086
      apt-get install -y -qq --no-install-recommends --download-only $libs >/dev/null
      mkdir -p "$OUT/packages/browser-libs"; cp /var/cache/apt/archives/*.deb "$OUT/packages/browser-libs/"
    fi
  fi
  log "done"
  exit 0
fi

# ======================================================================================================================
# main phase
if [[ $FAMILY == deb ]]; then
  apt-get install -y -qq curl ca-certificates xz-utils >/dev/null
  if [[ -f /extra-ca.crt ]]; then mkdir -p /usr/local/share/ca-certificates; cp /extra-ca.crt /usr/local/share/ca-certificates/extra.crt; update-ca-certificates >/dev/null; fi
  export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
else
  if [[ -d "$OUT/packages/rpm" ]]; then dnf -y -q install cpio >/dev/null || fail "installing cpio failed"; fi
  export SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt
fi
export NODE_EXTRA_CA_CERTS="$SSL_CERT_FILE" REQUESTS_CA_BUNDLE="$SSL_CERT_FILE" PIP_CERT="$SSL_CERT_FILE" UV_NATIVE_TLS=1
mkdir -p "$OUT/tools/bin" "$OUT/wheelhouse" "$WORK"
install -m 755 /host-uv "$OUT/tools/bin/uv"
export PATH="$OUT/tools/bin:$PATH" UV_CACHE_DIR=/cache/uv UV_PYTHON_INSTALL_DIR="$OUT/tools/python"

# ---------- Python (standalone, uv-managed) ------------------------------------------------------------------------
log "Python $TT_PYTHON_VERSION"
uv python install "$TT_PYTHON_VERSION" >/dev/null
PY="$(uv python find "$TT_PYTHON_VERSION")"
log "python: $PY ($("$PY" --version))"

# ---------- wheelhouse (hash-pinned) -------------------------------------------------------------------------------
log "wheelhouse"
cp -a "$APP" "$WORK/src"
cd "$WORK/src"
uv export --frozen --all-packages --no-dev --no-emit-workspace --format requirements-txt -o "$OUT/wheelhouse/requirements.txt" >/dev/null
uv run --no-project --python "$PY" --with pip python -m pip wheel --quiet --require-hashes --no-deps \
  -r "$OUT/wheelhouse/requirements.txt" -w "$OUT/wheelhouse" 2>&1 | grep -v -i 'warning' || true
mkdir -p "$OUT/wheelhouse/workspace"
uv build --all-packages --wheel -o "$OUT/wheelhouse/workspace" >/dev/null 2>&1 || fail "building the workspace wheels failed"
# requirements.txt is the lock's complete, resolved, hash-pinned closure: the offline host installs it with --no-deps
# (no resolver involved). Proof, run OUTSIDE the project exactly like the host: every pinned requirement that applies to
# this platform (markers such as win32-only are skipped) has a wheel here with a matching hash.
uv venv -q --python "$PY" "$WORK/check-venv"
(cd / && uv pip install -q --dry-run --offline --no-index --no-deps --find-links "$OUT/wheelhouse" --require-hashes \
  --python "$WORK/check-venv/bin/python" -r "$OUT/wheelhouse/requirements.txt") || fail "the wheelhouse does not satisfy requirements.txt offline"
log "wheelhouse: $(ls "$OUT"/wheelhouse/*.whl | wc -l) dependency wheels + $(ls "$OUT"/wheelhouse/workspace/*.whl | wc -l) workspace wheels"
if [[ "${WITH_DEV:-0}" == 1 ]]; then
  uv export --frozen --all-packages --only-dev --no-emit-workspace --format requirements-txt -o "$OUT/wheelhouse/requirements-dev.txt" >/dev/null
  mkdir -p "$OUT/wheelhouse/dev"
  uv run --no-project --python "$PY" --with pip python -m pip wheel --quiet --require-hashes --no-deps -r "$OUT/wheelhouse/requirements-dev.txt" \
    -w "$OUT/wheelhouse/dev" 2>&1 | grep -v -i warning || true
fi

# ---------- Node: prebuilt web console + deck viewer (Node itself only with --with-dev) ----------------------------
log "Node $TT_NODE_VERSION, pnpm $TT_PNPM_VERSION, web builds"
NODE_DIR="$WORK/node"; mkdir -p "$NODE_DIR"
curl -fsSL "https://nodejs.org/dist/v$TT_NODE_VERSION/node-v$TT_NODE_VERSION-linux-x64.tar.gz" | tar -xz -C "$NODE_DIR" --strip-components=1
export PATH="$NODE_DIR/bin:$PATH"
npm install -g --silent --prefix "$WORK/pnpm" "pnpm@$TT_PNPM_VERSION"
export PATH="$WORK/pnpm/bin:$PATH"
STORE="$WORK/pnpm-store"; [[ "${WITH_DEV:-0}" == 1 ]] && STORE="$OUT/tools/pnpm-store"
pnpm config set store-dir "$STORE" >/dev/null
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 pnpm install --frozen-lockfile --silent
pnpm --filter @tt/presentation build >/dev/null
pnpm --filter @tt/web build >/dev/null
pnpm --filter @tt/desktop build >/dev/null || log "desktop web bundles did not build (optional)"
for d in apps/web/dist apps/presentation/dist apps/desktop/dist; do
  if [[ -d "$d" ]]; then rm -rf "${APP:?}/$d"; cp -a "$d" "$APP/$d"; fi
done
[[ -f "$APP/apps/web/dist/index.html" && -f "$APP/apps/presentation/dist/presentation.html" ]] || fail "web builds missing"
if [[ "${WITH_DEV:-0}" == 1 ]]; then
  cp -a "$NODE_DIR" "$OUT/tools/node"; cp -a "$WORK/pnpm" "$OUT/tools/pnpm"
  log "Node, pnpm and the pnpm store ($(du -sh "$STORE" | cut -f1)) included"
fi

# ---------- PostgreSQL client programs for user space (backup/restore/psql without root), from the packages --------
if [[ -d "$OUT/packages/rpm" ]]; then
  mkdir -p "$WORK/pgx" "$OUT/tools/pgsql"
  (cd "$WORK/pgx" && for r in "$OUT"/packages/rpm/postgresql"$PG"-[0-9]*.rpm "$OUT"/packages/rpm/postgresql"$PG"-libs-*.rpm; do rpm2cpio "$r" | cpio -idm --quiet; done)
  cp -a "$WORK/pgx/usr/pgsql-$PG/." "$OUT/tools/pgsql/"
elif [[ -d "$OUT/packages/deb" ]]; then
  mkdir -p "$WORK/pgx" "$OUT/tools/pgsql/lib"
  for d in "$OUT"/packages/deb/postgresql-client-"$PG"_*.deb "$OUT"/packages/deb/libpq5_*.deb; do dpkg-deb -x "$d" "$WORK/pgx"; done
  cp -a "$WORK/pgx/usr/lib/postgresql/$PG/." "$OUT/tools/pgsql/"
  cp -a "$WORK"/pgx/usr/lib/x86_64-linux-gnu/libpq.so* "$OUT/tools/pgsql/lib/" 2>/dev/null || true
fi

# ---------- Chromium (the full browser only: the server launches it through TT_CHROMIUM) ---------------------------
if [[ "${WITH_BROWSERS:-0}" == 1 ]]; then
  log "Chromium"
  mkdir -p "$OUT/tools/ms-playwright"
  if [[ -d /browsers ]] && ls -d /browsers/chromium-[0-9]* >/dev/null 2>&1; then
    cp -a "$(ls -d /browsers/chromium-[0-9]* | sort -V | tail -1)" "$OUT/tools/ms-playwright/"
    log "copied from the provided browser directory"
  else
    uv venv -q --python "$PY" "$WORK/pwvenv" && uv pip install -q --python "$WORK/pwvenv/bin/python" --no-index --find-links "$OUT/wheelhouse" playwright
    PLAYWRIGHT_BROWSERS_PATH="$OUT/tools/ms-playwright" "$WORK/pwvenv/bin/playwright" install --no-shell chromium >/dev/null || \
      fail "playwright install chromium failed (CDN unreachable? use --browsers-from DIR)"
  fi
fi

# ---------- local LLM (Ollama + model) -----------------------------------------------------------------------------
if [[ -n "${WITH_LLM:-}" ]]; then
  log "Ollama + model $WITH_LLM"
  mkdir -p "$OUT/llm/models" "$WORK/ollama"
  curl -fsSL "https://ollama.com/download/ollama-linux-amd64.tgz" | tar -xz -C "$WORK/ollama" || fail "Ollama download failed (ollama.com unreachable?)"
  cp "$WORK/ollama/bin/ollama" "$OUT/tools/bin/ollama"
  if [[ -d "$WORK/ollama/lib/ollama" ]]; then mkdir -p "$OUT/tools/lib"; cp -a "$WORK/ollama/lib/ollama" "$OUT/tools/lib/"; fi
  OLLAMA_MODELS="$OUT/llm/models" "$OUT/tools/bin/ollama" serve >/tmp/ollama.log 2>&1 &
  sleep 3
  OLLAMA_MODELS="$OUT/llm/models" "$OUT/tools/bin/ollama" pull "$WITH_LLM" || fail "ollama pull $WITH_LLM failed"
  kill %1 2>/dev/null || true
  echo "$WITH_LLM" > "$OUT/llm/MODEL"
fi

# ---------- client kit ---------------------------------------------------------------------------------------------
log "client kit (tt CLI for Linux, macOS, Windows)"
CK="$OUT/client-kit"; mkdir -p "$CK/wheelhouse"
cp "$APP/scripts/client/install.sh" "$APP/scripts/client/install.ps1" "$APP/scripts/client/README.md" "$CK/"
cp "$OUT"/wheelhouse/workspace/tt_cli-*.whl "$OUT"/wheelhouse/workspace/tt_client-*.whl "$OUT"/wheelhouse/workspace/tt_schema-*.whl "$CK/wheelhouse/"
uv export --frozen --package tt-cli --no-dev --no-emit-workspace --no-hashes --format requirements-txt -o "$CK/requirements.txt" >/dev/null
# pip takes --platform literally, so each OS gets the full list of tags its wheels may carry.
# Intel macOS is not offered: cryptography (locked version) ships macOS wheels for arm64 only.
declare -A CK_TAGS=(
  [linux-x86_64]="manylinux2014_x86_64 manylinux_2_17_x86_64 manylinux_2_28_x86_64 manylinux_2_34_x86_64"
  [macos-arm64]="macosx_11_0_arm64 macosx_10_9_universal2 macosx_10_12_universal2"
  [windows-amd64]="win_amd64"
)
for os in "${!CK_TAGS[@]}"; do
  args=(); for t in ${CK_TAGS[$os]}; do args+=(--platform "$t"); done
  uv run --no-project --python "$PY" --with pip python -m pip download --quiet --only-binary=:all: --no-deps "${args[@]}" \
    --python-version "$TT_PYTHON_VERSION" -r "$CK/requirements.txt" -d "$CK/wheelhouse" \
    || fail "client kit: not every tt dependency has a wheel for $os (see the pip error above)"
  log "client kit: $os ok"
done
echo "linux-x86_64 macos-arm64 windows-amd64" > "$CK/PLATFORMS"
mkdir -p "$CK/ca"; echo "Put your internal CA (PEM) here as ca.pem; the installers configure tt to trust it." > "$CK/ca/README.txt"
log "done: $(du -sh "$OUT" | cut -f1)"
