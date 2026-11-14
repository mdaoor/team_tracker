# shellcheck shell=bash
# Prerequisite detection and installation, shared by "ttctl prereqs", "ttctl setup" and "ttctl doctor". Source after common.sh.
#
# User-space tools go under .tools/ (no root):  uv, Python (uv-managed), Node, pnpm, Chromium, PostgreSQL client.
# System packages need root:                    PostgreSQL 16 server + contrib, Chromium's libraries, curl, git, Docker.
#   For those we PRINT the exact commands, ask before running them (or --yes), never call sudo silently, and only print with --dry-run.

# shellcheck disable=SC1091
source "$ROOT/scripts/lib/versions.env"
PREREQ_TOOLS=(python uv node pnpm curl git postgresql psql chromium docker)

# ---------- detection ------------------------------------------------------------------------------------------------
ver_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" == "$2" ]]; }   # ver_ge HAVE WANT
first_version() { grep -oE '[0-9]+(\.[0-9]+)+' | head -1; }

# The PostgreSQL server binary of major $TT_PG_MAJOR on this host (any packaging), or empty.
pg_server_bin() {
  local c
  for c in "/usr/pgsql-$TT_PG_MAJOR/bin/postgres" "/usr/lib/postgresql/$TT_PG_MAJOR/bin/postgres" \
           "/opt/homebrew/opt/postgresql@$TT_PG_MAJOR/bin/postgres" "/usr/local/opt/postgresql@$TT_PG_MAJOR/bin/postgres" \
           "$TOOLS_DIR/pgsql/bin/postgres" /usr/bin/postgres; do
    if [[ -x "$c" ]]; then
      local v; v="$(probe "$c" --version | first_version || true)"
      [[ "${v%%.*}" == "$TT_PG_MAJOR" ]] && { echo "$c"; return 0; }
    fi
  done
  return 1
}
# Does the server installation have the contrib extensions? (looks for their control files next to the binary)
pg_contrib_ok() {
  local bin="$1" share e
  share="$("$(dirname "$bin")/pg_config" --sharedir 2>/dev/null || true)"
  [[ -n "$share" ]] || share="$(cd "$(dirname "$bin")/.." && pwd)/share"
  for d in "$share" "$share/postgresql" "/usr/share/postgresql/$TT_PG_MAJOR" "/usr/pgsql-$TT_PG_MAJOR/share" "/usr/share/pgsql"; do
    local all=1; for e in $TT_PG_EXTENSIONS; do [[ -f "$d/extension/$e.control" ]] || all=0; done
    [[ $all -eq 1 ]] && return 0
  done
  return 1
}

# tool_probe TOOL -> sets P_STATUS (ok|missing|old|optional-missing), P_VERSION, P_PATH, P_NEED, P_NOTE
tool_probe() {
  local t="$1" out=""
  P_STATUS=missing; P_VERSION=""; P_PATH=""; P_NOTE=""
  case "$t" in
    python)
      P_NEED=">= $TT_PYTHON_VERSION"
      if have uv && out="$(probe uv python find ">=$TT_PYTHON_VERSION" 2>/dev/null)" && [[ -n "$out" ]]; then
        P_PATH="$out"; P_VERSION="$(probe "$out" --version | first_version)"; P_STATUS=ok
      elif have python3; then
        P_PATH="$(command -v python3)"; P_VERSION="$(probe python3 --version | first_version)"
        if ver_ge "$P_VERSION" "$TT_PYTHON_VERSION"; then P_STATUS=ok; else P_STATUS=old; P_NOTE="uv installs a private $TT_PYTHON_VERSION; the system python3 is left alone"; fi
      fi ;;
    uv) P_NEED=">= 0.4"
      if have uv; then P_PATH="$(command -v uv)"; P_VERSION="$(probe uv --version | first_version)"; ver_ge "$P_VERSION" 0.4 && P_STATUS=ok || P_STATUS=old; fi ;;
    node) P_NEED="22.x"
      if have node; then P_PATH="$(command -v node)"; P_VERSION="$(probe node --version | first_version)"; [[ "${P_VERSION%%.*}" == 22 ]] && P_STATUS=ok || P_STATUS=old; fi
      if [[ -d "$ROOT/apps/web/dist" && -f "$ROOT/apps/presentation/dist/presentation.html" && "$P_STATUS" != ok ]]; then
        P_NOTE="not needed to run: the web console and deck viewer are prebuilt (needed only to rebuild them)"; [[ "$P_STATUS" == missing ]] && P_STATUS=optional-missing
      fi ;;
    pnpm) P_NEED="$TT_PNPM_VERSION"
      if have pnpm; then P_PATH="$(command -v pnpm)"; P_VERSION="$(probe pnpm --version | first_version)"; [[ "$P_VERSION" == "$TT_PNPM_VERSION" ]] && P_STATUS=ok || { P_STATUS=old; P_NOTE="the repository pins pnpm@$TT_PNPM_VERSION"; }; fi
      if [[ -d "$ROOT/apps/web/dist" && -f "$ROOT/apps/presentation/dist/presentation.html" && "$P_STATUS" == missing ]]; then
        P_STATUS=optional-missing; P_NOTE="not needed to run: the web console and deck viewer are prebuilt"
      fi ;;
    curl) P_NEED="any"; if have curl; then P_PATH="$(command -v curl)"; P_VERSION="$(probe curl --version | first_version)"; P_STATUS=ok; fi ;;
    git) P_NEED="any (optional)"; if have git; then P_PATH="$(command -v git)"; P_VERSION="$(probe git --version | first_version)"; P_STATUS=ok; else P_STATUS=optional-missing; P_NOTE="only for cloning and upgrades from git"; fi ;;
    postgresql) P_NEED="server $TT_PG_MAJOR + contrib ($TT_PG_EXTENSIONS)"
      if P_PATH="$(pg_server_bin)"; then
        P_VERSION="$(probe "$P_PATH" --version | first_version)"
        if pg_contrib_ok "$P_PATH"; then P_STATUS=ok; else P_STATUS=old; P_NOTE="server found but the contrib extensions are missing: install $(pg_contrib_pkg)"; fi
      else
        P_PATH=""; P_STATUS=missing
        P_NOTE="needed only for --db native; --db docker or --db external use another server"
      fi ;;
    psql) P_NEED=">= $TT_PG_MAJOR for db backup/restore/psql only"
      if have psql; then
        P_PATH="$(command -v psql)"; P_VERSION="$(probe psql --version | first_version)"
        if [[ "${P_VERSION%%.*}" -ge "$TT_PG_MAJOR" ]]; then P_STATUS=ok
        else P_STATUS=old; P_NOTE="client $P_VERSION is fine for setup/start (the server version is what counts, checked with SHOW server_version_num); db backup/restore/psql use a $TT_PG_MAJOR client from .tools or the postgres:$TT_PG_MAJOR container instead"; fi
      else P_STATUS=optional-missing; P_NOTE="only db backup/restore/psql need it; they fall back to .tools or the postgres:$TT_PG_MAJOR container"; fi ;;
    chromium) P_NEED="any (PDF/PNG export)"
      if P_PATH="$(chromium_bin)"; then P_STATUS=ok; P_VERSION="$(probe "$P_PATH" --version 2>/dev/null | first_version || true)"
      else P_PATH=""; P_STATUS=optional-missing; P_NOTE="needed only for PDF/PNG export of decks"; fi ;;
    docker) P_NEED="any (optional)"
      if have docker; then P_PATH="$(command -v docker)"; P_VERSION="$(probe docker --version | first_version)"
        if probe docker info >/dev/null 2>&1; then P_STATUS=ok; else P_STATUS=optional-missing; P_NOTE="installed but the daemon is not reachable"; fi
      else P_STATUS=optional-missing; P_NOTE="only for --db docker and ttctl docker"; fi ;;
    *) usage_fail "unknown tool '$t'" "known tools: ${PREREQ_TOOLS[*]}" ;;
  esac
  detail "tool $t: status=$P_STATUS version=${P_VERSION:--} path=${P_PATH:--} need=$P_NEED ${P_NOTE:+note=$P_NOTE}"
  return 0
}

chromium_bin() {
  local c
  [[ -n "${TT_CHROMIUM:-}" && -x "${TT_CHROMIUM:-}" ]] && { echo "$TT_CHROMIUM"; return 0; }
  for c in "${PLAYWRIGHT_BROWSERS_PATH:-/nonexistent}"/chromium-*/chrome-linux*/chrome "$TOOLS_DIR"/ms-playwright/chromium-*/chrome-linux*/chrome \
           /opt/pw-browsers/chromium-*/chrome-linux*/chrome "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux*/chrome; do
    [[ -x "$c" ]] && { echo "$c"; return 0; }
  done
  return 1
}

# How a missing tool is installed: user (no root, into .tools) | system (root package manager)
tool_kind() { case "$1" in python|uv|node|pnpm|chromium) echo user ;; *) echo system ;; esac; }

# ---------- system package commands (printed, run only after confirmation) ------------------------------------------
pg_contrib_pkg() {
  case "$(tt_os_id)" in
    rhel8|rhel9) if [[ -x "/usr/pgsql-$TT_PG_MAJOR/bin/postgres" ]]; then echo "postgresql$TT_PG_MAJOR-contrib"; else echo "postgresql-contrib (module postgresql:$TT_PG_MAJOR)"; fi ;;
    ubuntu*|debian*) echo "postgresql-$TT_PG_MAJOR (contrib is part of it)" ;;
    macos) echo "postgresql@$TT_PG_MAJOR (Homebrew)" ;;
    *) echo "the PostgreSQL $TT_PG_MAJOR contrib package" ;;
  esac
}
# rhel: is the postgresql:16 AppStream module available? (else PGDG)
rhel_pg_source() {
  [[ -n "${TT_PG_SOURCE:-}" ]] && { echo "$TT_PG_SOURCE"; return; }
  if probe dnf -q module list postgresql 2>/dev/null | grep -qE "^postgresql +$TT_PG_MAJOR "; then echo module; else echo pgdg; fi
}
# system_cmds TOOL -> one shell command per line (without sudo); empty when not applicable
system_cmds() {
  local t="$1" os; os="$(tt_os_id)"; local el="${os#rhel}"
  case "$t:$os" in
    postgresql:rhel8|postgresql:rhel9|psql:rhel8|psql:rhel9)
      if [[ "$(rhel_pg_source)" == module ]]; then
        echo "dnf -y module reset postgresql"
        echo "dnf -y module enable postgresql:$TT_PG_MAJOR"
        if [[ "$t" == psql ]]; then echo "dnf -y install postgresql"; else
        echo "dnf -y install postgresql-server postgresql-contrib"
        echo "postgresql-setup --initdb"
        echo "systemctl enable --now postgresql"; fi
      else
        echo "dnf -y install https://download.postgresql.org/pub/repos/yum/reporpms/EL-$el-x86_64/pgdg-redhat-repo-latest.noarch.rpm"
        [[ "$el" == 8 ]] && echo "dnf -qy module disable postgresql"
        if [[ "$t" == psql ]]; then echo "dnf -y install postgresql$TT_PG_MAJOR"; else
        echo "dnf -y install postgresql$TT_PG_MAJOR-server postgresql$TT_PG_MAJOR-contrib"
        echo "/usr/pgsql-$TT_PG_MAJOR/bin/postgresql-$TT_PG_MAJOR-setup initdb"
        echo "systemctl enable --now postgresql-$TT_PG_MAJOR"; fi
      fi ;;
    postgresql:ubuntu24.04|psql:ubuntu24.04)
      echo "apt-get update"
      [[ "$t" == psql ]] && echo "apt-get install -y postgresql-client-$TT_PG_MAJOR" || echo "apt-get install -y postgresql-$TT_PG_MAJOR postgresql-client-$TT_PG_MAJOR" ;;
    postgresql:ubuntu22.04|postgresql:debian12|psql:ubuntu22.04|psql:debian12)
      echo "apt-get update"
      echo "apt-get install -y postgresql-common ca-certificates"
      echo "/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y"
      [[ "$t" == psql ]] && echo "apt-get install -y postgresql-client-$TT_PG_MAJOR" || echo "apt-get install -y postgresql-$TT_PG_MAJOR postgresql-client-$TT_PG_MAJOR" ;;
    postgresql:macos|psql:macos) echo "brew install postgresql@$TT_PG_MAJOR"; [[ "$t" == postgresql ]] && echo "brew services start postgresql@$TT_PG_MAJOR" ;;
    curl:rhel*|git:rhel*) echo "dnf -y install $t" ;;
    curl:ubuntu*|curl:debian*|git:ubuntu*|git:debian*) echo "apt-get update"; echo "apt-get install -y $t" ;;
    chromium-libs:rhel*)
      echo "dnf -y install nss nspr atk at-spi2-atk cups-libs libdrm libxkbcommon libXcomposite libXdamage libXext libXfixes libXrandr mesa-libgbm pango cairo alsa-lib libxshmfence" ;;
    chromium-libs:ubuntu*|chromium-libs:debian*)
      echo "apt-get update"
      echo "apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64 fonts-liberation || apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 fonts-liberation" ;;
    docker:*) echo "# Docker: follow https://docs.docker.com/engine/install/ for $os (needs the Docker repository)" ;;
    *) ;;
  esac
}

# run_system TOOL: show the commands, ask, then run them with sudo when not root. Never silent.
run_system() {
  local t="$1" cmds pre; pre="$(sudo_prefix)"
  mapfile -t cmds < <(system_cmds "$t")
  if [[ ${#cmds[@]} -eq 0 ]]; then
    fail TT-E110 "no install recipe for '$t' on $(tt_os_id)" "supported: RHEL/Rocky/Alma 8 and 9, Ubuntu 22.04 and 24.04, Debian 12, macOS (development)" \
      "install it with your OS tools, then: $(tt_cmd prereqs check)"
  fi
  say "  '$t' needs root. These commands will run:"
  local c; for c in "${cmds[@]}"; do say "    ${pre}$c"; done
  if dry_run; then return 0; fi
  if [[ -n "$pre" ]] && ! have sudo; then
    fail TT-E020 "installing '$t' needs root and sudo is not available" "you are $(id -un), not root" \
      "ask an administrator to run the commands above, then: $(tt_cmd prereqs check)"
  fi
  confirm "Run them now?" y || { warn "skipped '$t' - run the commands above yourself"; return 1; }
  [[ "${TT_OFFLINE:-0}" == 1 ]] && tt_need_network "installing $t from the OS repositories" "$t" "--with-db rpm|deb"
  for c in "${cmds[@]}"; do
    [[ "$c" == \#* ]] && continue
    run ${pre:+sudo} bash -c "$c" || fail TT-E120 "installing '$t' failed at: $c" "the package manager returned an error (output above)" \
      "fix the repository/proxy problem and re-run: $(tt_cmd prereqs install "$t" --system)"
  done
}

# ---------- user-space installs (into .tools) ------------------------------------------------------------------------
fetch() {   # fetch URL OUT
  tt_need_network "downloading $1"
  run curl -fsSL --retry 3 -o "$2" "$1"
}
any_python() { local p; for p in python3 /usr/libexec/platform-python python; do have "$p" && { command -v "$p"; return 0; }; done; return 1; }

install_uv() {
  local from="${1:-}" py whl url
  run mkdir -p "$TOOLS_DIR/bin"
  if [[ -n "$from" ]]; then
    [[ -x "$from/tools/bin/uv" ]] || fail TT-E401 "the bundle at $from has no uv" "tools/bin/uv is missing" "rebuild the bundle: $(tt_cmd bundle create --target "$(bundle_target_guess)")"
    run cp "$from/tools/bin/uv" "$TOOLS_DIR/bin/uv"; return
  fi
  py="$(any_python)" || fail TT-E100 "installing uv needs any Python 3 to unpack it" "no python3 or platform-python found" "$(sudo_prefix)dnf -y install python3   (or apt-get install -y python3)"
  tt_need_network "downloading uv $TT_UV_VERSION" uv
  url="$(curl -fsS "https://pypi.org/pypi/uv/$TT_UV_VERSION/json" | "$py" -c 'import json,sys,platform
m={"x86_64":"x86_64","aarch64":"aarch64","arm64":"arm64"}[platform.machine()]
fs=[u["url"] for u in json.load(sys.stdin)["urls"] if (("manylinux" in u["filename"] and m in u["filename"]) if sys.platform.startswith("linux") else ("macosx" in u["filename"] and (m in u["filename"] or "universal2" in u["filename"])))]
print(fs[0])')"
  whl="$(mktemp)"; fetch "$url" "$whl"
  dry_run || "$py" - "$whl" "$TOOLS_DIR/bin" <<'PY'
import os, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
for n in z.namelist():
    b = n.rsplit("/", 1)[-1]
    if "/scripts/" in n and b in ("uv", "uvx"):
        p = os.path.join(sys.argv[2], b); open(p, "wb").write(z.read(n)); os.chmod(p, 0o755)
PY
  rm -f "$whl"
}
install_python() {
  local from="${1:-}"
  if [[ -n "$from" ]]; then run mkdir -p "$TOOLS_DIR"; run cp -a "$from/tools/python" "$TOOLS_DIR/"; return; fi
  have uv || install_uv
  tt_need_network "downloading Python $TT_PYTHON_VERSION" python
  run env UV_PYTHON_INSTALL_DIR="$TOOLS_DIR/python" uv python install "$TT_PYTHON_VERSION"
}
node_platform() { case "$(uname -s)-$(tt_arch)" in Linux-x86_64) echo linux-x64 ;; Linux-aarch64) echo linux-arm64 ;; Darwin-x86_64) echo darwin-x64 ;; Darwin-aarch64) echo darwin-arm64 ;; esac; }
install_node() {
  local from="${1:-}" plat tgz sums
  if [[ -n "$from" ]]; then
    [[ -d "$from/tools/node" ]] || fail TT-E401 "the bundle has no Node.js" "it was built without --with-dev; Node is only needed to rebuild the web console" "$(tt_cmd bundle create --target "$(bundle_target_guess)" --with-dev)"
    run rm -rf "$TOOLS_DIR/node"; run cp -a "$from/tools/node" "$TOOLS_DIR/node"; return
  fi
  plat="$(node_platform)"; tgz="node-v$TT_NODE_VERSION-$plat.tar.gz"
  tt_need_network "downloading Node.js $TT_NODE_VERSION" node --with-dev
  local tmp; tmp="$(mktemp -d)"
  fetch "https://nodejs.org/dist/v$TT_NODE_VERSION/$tgz" "$tmp/$tgz"
  fetch "https://nodejs.org/dist/v$TT_NODE_VERSION/SHASUMS256.txt" "$tmp/SHASUMS256.txt"
  if ! dry_run; then
    (cd "$tmp" && grep " $tgz\$" SHASUMS256.txt | sha256sum -c - >/dev/null) || fail TT-E120 "the Node.js download failed its checksum" "$tgz does not match SHASUMS256.txt" "retry: $(tt_cmd prereqs install node)"
  fi
  run rm -rf "$TOOLS_DIR/node"; run mkdir -p "$TOOLS_DIR/node"
  run tar -xzf "$tmp/$tgz" -C "$TOOLS_DIR/node" --strip-components=1
  rm -rf "$tmp"
}
install_pnpm() {
  local from="${1:-}"
  have node || install_node "$from"
  run mkdir -p "$TOOLS_DIR/bin"
  if [[ -n "$from" ]]; then
    [[ -d "$from/tools/pnpm" ]] || fail TT-E401 "the bundle has no pnpm" "it was built without --with-dev" "$(tt_cmd bundle create --target "$(bundle_target_guess)" --with-dev)"
    run rm -rf "$TOOLS_DIR/pnpm"; run cp -a "$from/tools/pnpm" "$TOOLS_DIR/pnpm"
  else
    tt_need_network "downloading pnpm $TT_PNPM_VERSION from the npm registry" pnpm --with-dev
    run npm install -g --silent --prefix "$TOOLS_DIR/pnpm" "pnpm@$TT_PNPM_VERSION"
  fi
  run ln -sf ../pnpm/bin/pnpm "$TOOLS_DIR/bin/pnpm"
}
install_chromium() {
  local from="${1:-}"
  if [[ -n "$from" ]]; then
    [[ -d "$from/tools/ms-playwright" ]] || fail TT-E401 "the bundle has no Chromium" "it was built without --with-browsers" "$(tt_cmd bundle create --target "$(bundle_target_guess)" --with-browsers)"
    run rm -rf "$TOOLS_DIR/ms-playwright"; run cp -a "$from/tools/ms-playwright" "$TOOLS_DIR/"; return
  fi
  need_python_env
  tt_need_network "downloading Chromium" chromium --with-browsers
  run env PLAYWRIGHT_BROWSERS_PATH="$TOOLS_DIR/ms-playwright" "${PY_RUN[@]}" playwright install chromium
  say "  Chromium also needs system libraries. If PDF export fails with 'error while loading shared libraries':"
  local c; while IFS= read -r c; do say "    $(sudo_prefix)$c"; done < <(system_cmds chromium-libs)
}
bundle_target_guess() { case "$(tt_os_id)" in rhel8) echo rhel8-x86_64 ;; rhel9) echo rhel9-x86_64 ;; ubuntu22.04) echo ubuntu22.04-amd64 ;; ubuntu24.04) echo ubuntu24.04-amd64 ;; *) echo "<target>" ;; esac; }

# install_tool TOOL [--user|--system|--auto] [FROM_BUNDLE_DIR]
install_tool() {
  local t="$1" scope="${2:---auto}" from="${3:-}"
  case "$t" in
    uv) install_uv "$from" ;; python) install_python "$from" ;; node) install_node "$from" ;; pnpm) install_pnpm "$from" ;;
    chromium) install_chromium "$from" ;;
    postgresql|psql|curl|git|docker|chromium-libs)
      if [[ -n "$from" && "$t" == psql && -d "$from/tools/pgsql" ]]; then run rm -rf "$TOOLS_DIR/pgsql"; run cp -a "$from/tools/pgsql" "$TOOLS_DIR/"; return; fi
      if [[ "$scope" == --user ]]; then
        warn "'$t' is a system package (needs root); skipped because of --user. Commands for an administrator:"
        local c; while IFS= read -r c; do say "    $(sudo_prefix)$c"; done < <(system_cmds "$t"); return 0
      fi
      run_system "$t" ;;
    *) usage_fail "unknown tool '$t'" "known tools: ${PREREQ_TOOLS[*]} chromium-libs" ;;
  esac
}
