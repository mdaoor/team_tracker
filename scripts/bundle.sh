#!/usr/bin/env bash
# Air-gapped delivery: build one file on a connected machine, install it on a host without network.
source "$(dirname "$0")/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/versions.env"

TARGETS="rhel8-x86_64 rhel9-x86_64 ubuntu22.04-amd64 ubuntu24.04-amd64"
usage() {
  cat <<EOF
ttctl bundle - air-gapped delivery in two steps

SYNOPSIS
  Step A, on a connected machine with Docker:
    $TT_TTCTL bundle create --target TARGET [--with-db rpm|deb|image] [--with-browsers] [--with-llm MODEL]
                           [--with-docker-images] [--with-dev] [--out DIR] [--compress zst|gz] [--sign GPG_KEY]
  Step B, on the air-gapped host (after copying the file):
    tar -xf tt-bundle-VERSION-TARGET.tar.zst && cd tt-bundle-VERSION-TARGET && ./install.sh [OPTIONS]
    (= $TT_TTCTL bundle install [FILE|--from-dir DIR] [--prefix DIR] [--with-db] [--mode dev|prod])
  Anywhere:
    $TT_TTCTL bundle info FILE        show the manifest (versions, target, components, sizes)
    $TT_TTCTL bundle verify FILE      check every file against SHA256SUMS (and the signature when present)

TARGETS
  $TARGETS
  The bundle is built inside a container of the target OS, so every binary matches its glibc and package set.

CREATE OPTIONS
  --with-db rpm|deb    PostgreSQL $TT_PG_MAJOR server, contrib and client packages with all dependencies (rpm for rhel*, deb for ubuntu*)
  --with-db image      the postgres:$TT_PG_MAJOR Docker image instead (host runs --db docker)
  --with-browsers      Chromium + its system library packages (PDF/PNG export of decks)
  --browsers-from DIR  copy Chromium from an existing Playwright browser directory instead of downloading it
  --with-llm MODEL     Ollama + MODEL for the LLM gateway (local_only); without it the app uses the deterministic parser
  --with-docker-images docker save tars of team-tracker-server, team-tracker-llm-gateway and postgres:$TT_PG_MAJOR
  --with-dev           Node, pnpm and an offline pnpm store, dev Python wheels (to rebuild and test on the host)
  --out DIR            output directory (default dist/)       --compress zst|gz   (default zst; gz for hosts without zstd)
  --sign KEY           detached GPG signature of SHA256SUMS   --allow-dirty       include uncommitted changes
  Always included: source of the commit, uv, Python $TT_PYTHON_VERSION, a hash-pinned wheelhouse of every locked
  dependency + the workspace wheels, the PREBUILT web console and deck viewer (no Node on the server), the client kit
  (tt for Linux/macOS/Windows), docs/guide and scripts/.

INSTALL OPTIONS
  --prefix DIR     where releases go (default /opt/team-tracker as root, ~/team-tracker otherwise): DIR/releases/VERSION, DIR/current
  --with-db        install the bundled PostgreSQL packages (root; asks first) or load the image
  --mode dev|prod  dev: ready for "ttctl db setup" + "ttctl start"; prod: also "ttctl prod install" (systemd, root)
  --force          install although the bundle was built for another OS
  The install verifies checksums first, then uses only the bundle. It writes TT_OFFLINE=1 (and UV_OFFLINE, PIP_NO_INDEX,
  npm_config_offline, COREPACK_ENABLE_NETWORK=0, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD) to .ttctl.env: from then on every
  ttctl action that would need the network stops with exit 4 and names the missing bundle option.

EXAMPLES
  $TT_TTCTL bundle create --target rhel8-x86_64 --with-db rpm --with-browsers
  $TT_TTCTL bundle create --target ubuntu24.04-amd64 --with-db deb --compress gz
  $TT_TTCTL bundle verify dist/tt-bundle-1.0.0-rhel8-x86_64.tar.zst
  ./install.sh --with-db --mode prod

SEE ALSO
  $GUIDE/03-install-airgapped.md   $GUIDE/07-upgrade-and-rollback.md
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
SUB="${1:-}"; shift || true

py_host() { have python3 && echo python3 && return; [[ -x /usr/libexec/platform-python ]] && echo /usr/libexec/platform-python && return; echo ""; }
archive_py() {   # archive.py with zstandard when the zstd program is missing
  local f="$1"; shift
  if [[ "$f" == *.gz || "$f" == *.tgz ]] || have zstd; then "$(py_host)" "$ROOT/scripts/bundle/archive.py" "$@"
  elif have uv && [[ "${TT_OFFLINE:-0}" != 1 ]]; then uv run --quiet --no-project --with zstandard python "$ROOT/scripts/bundle/archive.py" "$@"
  else fail TT-E100 "reading a .tar.zst bundle needs zstd" "neither the zstd program nor Python's zstandard is available" \
         "install zstd from the OS installation media ($(sudo_prefix)dnf install zstd / apt-get install zstd), or rebuild the bundle with --compress gz"; fi
}
# Read a bundle archive with standard tools only (the offline host may have no Python).
decomp() {   # decomp FILE -> archive bytes on stdout
  case "$1" in
    *.gz|*.tgz) gzip -dc "$1" ;;
    *.zst) have zstd || fail TT-E100 "unpacking $(basename "$1") needs zstd" "the zstd program is not installed on this host" \
             "install zstd from the OS installation media ($(sudo_prefix)dnf -y install zstd / apt-get install -y zstd), or ask for a bundle built with --compress gz"
           zstd -dc -q "$1" ;;
    *) usage_fail "not a bundle file: $1" "expected .tar.zst or .tar.gz" ;;
  esac
}
unpack_to() { decomp "$1" | tar -xf - -C "$2"; }
target_image() { case "$1" in rhel8-x86_64) echo rockylinux:8 ;; rhel9-x86_64) echo rockylinux:9 ;; ubuntu22.04-amd64) echo ubuntu:22.04 ;; ubuntu24.04-amd64) echo ubuntu:24.04 ;; esac; }
target_os() { case "$1" in rhel8-*) echo rhel8 ;; rhel9-*) echo rhel9 ;; ubuntu22.04-*) echo ubuntu22.04 ;; ubuntu24.04-*) echo ubuntu24.04 ;; esac; }

# ======================================================================================================================
create() {
  local target="" withdb=none browsers=0 browsers_from="" llm="" images=0 dev=0 out="$ROOT/dist" compress=zst sign="" dirty=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --target) target="${2:-}"; shift ;; --with-db) withdb="${2:-}"; shift ;; --with-browsers) browsers=1 ;;
      --browsers-from) browsers=1; browsers_from="$(cd "${2:?}" && pwd)"; shift ;; --with-llm) llm="${2:-}"; shift ;;
      --with-docker-images) images=1 ;; --with-dev) dev=1 ;; --out) out="${2:-}"; shift ;; --compress) compress="${2:-}"; shift ;;
      --sign) sign="${2:-}"; shift ;; --allow-dirty) dirty=1 ;;
      *) usage_fail "unknown option '$1' for bundle create" ;;
    esac; shift
  done
  [[ -n "$target" ]] || usage_fail "bundle create needs --target" "targets: $TARGETS"
  [[ " $TARGETS " == *" $target "* ]] || usage_fail "unknown target '$target'" "targets: $TARGETS"
  case "$withdb" in none|image) ;; rpm) [[ "$target" == rhel* ]] || usage_fail "--with-db rpm is for rhel targets" "use --with-db deb for $target" ;;
    deb) [[ "$target" == ubuntu* ]] || usage_fail "--with-db deb is for ubuntu targets" "use --with-db rpm for $target" ;;
    *) usage_fail "--with-db must be rpm, deb or image" ;; esac
  case "$compress" in zst|gz) ;; *) usage_fail "--compress must be zst or gz" ;; esac
  tt_need_network "bundle create (downloads every component)"
  have docker && probe docker info >/dev/null 2>&1 || fail TT-E108 "bundle create builds inside a container of the target OS" "Docker is missing or not running" \
    "install/start Docker on this connected machine ($(tt_cmd prereqs install docker --system) prints how)"
  have git || fail TT-E100 "bundle create needs git" "git is not on PATH" "$(sudo_prefix)dnf -y install git   (or apt-get install -y git)"
  [[ -n "$(py_host)" ]] || fail TT-E100 "bundle create needs python3 on this machine" "no python3 found" "$(sudo_prefix)dnf -y install python3"
  local version commit
  commit="$(git -C "$ROOT" rev-parse --short HEAD)"
  version="$(git -C "$ROOT" describe --tags --exact-match 2>/dev/null || echo "0.0.0-$commit")"
  if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]]; then
    [[ $dirty -eq 1 ]] || fail TT-E012 "the working tree has uncommitted changes" "a bundle must be reproducible from one commit" \
      "commit or stash them, or add --allow-dirty to include them (the manifest records it)"
    version="$version-dirty"
  fi
  local name="tt-bundle-$version-$target" stage
  stage="$RUN_DIR/bundle/$name"
  run rm -rf "$stage"; run mkdir -p "$stage/app" "$out"
  title "Bundle $name"
  # 1. source snapshot of the commit (+ uncommitted changes with --allow-dirty)
  if [[ $dirty -eq 1 ]]; then
    step "source snapshot (working tree)" bash -c "cd '$ROOT' && git ls-files -z --cached --others --exclude-standard | xargs -0 tar -cf - 2>/dev/null | tar -xf - -C '$stage/app'"
  else
    step "source snapshot of $commit" bash -c "git -C '$ROOT' archive HEAD | tar -xf - -C '$stage/app'"
  fi
  dry_run || echo "$version" > "$stage/VERSION"; dry_run || echo "$version" > "$stage/app/VERSION"
  # 2. uv for the target, from PyPI (github.com is not needed)
  local uvbin="$RUN_DIR/bundle/uv-$TT_UV_VERSION"
  if [[ ! -x "$uvbin" ]]; then
    step "uv $TT_UV_VERSION from PyPI" bash -c "
      url=\$(curl -fsS https://pypi.org/pypi/uv/$TT_UV_VERSION/json | $(py_host) -c 'import json,sys;print([u[\"url\"] for u in json.load(sys.stdin)[\"urls\"] if \"manylinux\" in u[\"filename\"] and \"x86_64\" in u[\"filename\"]][0])')
      curl -fsSL -o '$uvbin.whl' \"\$url\" && $(py_host) -c 'import zipfile,sys;z=zipfile.ZipFile(sys.argv[1]);n=[x for x in z.namelist() if x.endswith(\"/scripts/uv\")][0];open(sys.argv[2],\"wb\").write(z.read(n))' '$uvbin.whl' '$uvbin' && chmod +x '$uvbin' && rm '$uvbin.whl'"
  fi
  # 3. everything platform-specific, inside the target OS
  local img; img="$(target_image "$target")"
  local dflags=(--rm -v "$stage:/out" -v "$uvbin:/host-uv:ro" -v "$ROOT/scripts/bundle/builder.sh:/builder.sh:ro" -v "tt-bundle-cache:/cache"
    -e TARGET="$target" -e WITH_DB="$withdb" -e WITH_BROWSERS="$browsers" -e WITH_DEV="$dev" -e WITH_LLM="$llm"
    -e TT_PYTHON_VERSION -e TT_NODE_VERSION -e TT_PNPM_VERSION -e TT_PG_MAJOR)
  [[ -n "$browsers_from" ]] && dflags+=(-v "$browsers_from:/browsers:ro")
  [[ -n "${TT_CI_EXTRA_CA:-}" && -f "${TT_CI_EXTRA_CA:-}" ]] && dflags+=(-v "$TT_CI_EXTRA_CA:/extra-ca.crt:ro")
  [[ -n "${HTTPS_PROXY:-}" ]] && dflags+=(--network host -e HTTPS_PROXY -e https_proxy -e NO_PROXY -e no_proxy)
  export TT_PYTHON_VERSION TT_NODE_VERSION TT_PNPM_VERSION TT_PG_MAJOR
  if [[ "$withdb" == rpm || "$withdb" == deb || $browsers -eq 1 ]]; then
    if [[ "$target" == ubuntu22.04-* && "$withdb" == deb ]]; then   # PostgreSQL 16 is not in the 22.04 archive: PGDG
      step "PGDG signing key" curl -fsSL -o "$RUN_DIR/bundle/pgdg.asc" https://www.postgresql.org/media/keys/ACCC4CF8.asc
      dflags+=(-v "$RUN_DIR/bundle/pgdg.asc:/pgdg.asc:ro")
    fi
    step "OS packages in a fresh $img ($( [[ $withdb == rpm || $withdb == deb ]] && echo "PostgreSQL $TT_PG_MAJOR")$( [[ $browsers == 1 ]] && echo " Chromium libraries"))" \
      docker run "${dflags[@]}" "$img" bash /builder.sh packages
  fi
  step "build inside $img (Python, wheelhouse, prebuilt web apps$( [[ $browsers == 1 ]] && echo ", Chromium")$( [[ -n $llm ]] && echo ", $llm"), client kit)" \
    docker run "${dflags[@]}" "$img" bash /builder.sh main
  # 4. images
  if [[ "$withdb" == image || $images -eq 1 ]]; then
    run mkdir -p "$stage/images"
    step "postgres:$TT_PG_MAJOR image" bash -c "docker pull -q postgres:$TT_PG_MAJOR && docker save postgres:$TT_PG_MAJOR -o '$stage/images/postgres-$TT_PG_MAJOR.tar'"
  fi
  if [[ $images -eq 1 ]]; then
    step "team-tracker-server image" docker build -q -f "$ROOT/apps/server/Dockerfile" -t "team-tracker-server:$version" "$stage/app"
    step "team-tracker-llm-gateway image" docker build -q -f "$ROOT/apps/llm_gateway/Dockerfile" -t "team-tracker-llm-gateway:$version" "$stage/app"
    step "save images" docker save "team-tracker-server:$version" "team-tracker-llm-gateway:$version" -o "$stage/images/team-tracker-$version.tar"
  fi
  # 5. installer, manifest, checksums, signature
  if ! dry_run; then
    cat > "$stage/install.sh" <<'INST'
#!/usr/bin/env bash
# Install this Team Tracker bundle on this (air-gapped) host. Options: see ./install.sh --help
here="$(cd "$(dirname "$0")" && pwd)"
export TT_INVOKED_AS="./install.sh" TT_TTCTL="${TT_TTCTL:-./app/scripts/ttctl}"
exec "$here/app/scripts/ttctl" bundle install --from-dir "$here" "$@"
INST
    chmod +x "$stage/install.sh"
    "$(py_host)" - "$stage" "$version" "$commit" "$target" "$withdb" "$browsers" "$llm" "$images" "$dev" "$dirty" <<'PY'
import json, os, sys, datetime, subprocess
stage, version, commit, target, withdb, browsers, llm, images, dev, dirty = sys.argv[1:]
def size(p):
    p = os.path.join(stage, p)
    if os.path.isfile(p): return os.path.getsize(p)
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs if not os.path.islink(os.path.join(r, f)))
def ver(cmd):
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip().splitlines()[0]
    except Exception: return None
comps = []
def add(name, path, **kw):
    if os.path.exists(os.path.join(stage, path)): comps.append({"name": name, "path": path, "size_bytes": size(path), **kw})
pyexe = next((os.path.join(r, "bin", "python3") for r in sorted(os.listdir(os.path.join(stage, "tools/python")) and [os.path.join(stage, "tools/python", d) for d in os.listdir(os.path.join(stage, "tools/python")) if d.startswith("cpython")])), None)
add("app", "app", commit=commit)
add("web-prebuilt", "app/apps/web/dist"); add("presentation-prebuilt", "app/apps/presentation/dist")
add("uv", "tools/bin/uv"); add("python", "tools/python", version=os.path.basename(pyexe.split("/bin/")[0]) if pyexe else None)
add("wheelhouse", "wheelhouse", wheels=len([f for f in os.listdir(os.path.join(stage, "wheelhouse")) if f.endswith(".whl")]))
add("node", "tools/node"); add("pnpm", "tools/pnpm"); add("pnpm-store", "tools/pnpm-store")
add("postgresql-rpm", "packages/rpm"); add("postgresql-deb", "packages/deb"); add("postgresql-client", "tools/pgsql")
add("chromium", "tools/ms-playwright"); add("browser-libs", "packages/browser-libs")
add("ollama", "tools/bin/ollama"); add("llm-model", "llm", model=llm or None)
add("docker-images", "images"); add("client-kit", "client-kit")
m = {"format": 1, "product": "team-tracker", "version": version, "commit": commit, "dirty": dirty == "1",
     "target": target, "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
     "options": {"with_db": withdb, "with_browsers": browsers == "1", "with_llm": llm or None, "with_docker_images": images == "1", "with_dev": dev == "1"},
     "components": comps, "total_bytes": size(".")}
json.dump(m, open(os.path.join(stage, "manifest.json"), "w"), indent=2)
PY
    (cd "$stage" && find . -type f ! -name SHA256SUMS ! -name SHA256SUMS.asc -print0 | sort -z | xargs -0 sha256sum | sed 's#  \./#  #' > SHA256SUMS)
    if [[ -n "$sign" ]]; then run gpg --batch --yes --local-user "$sign" --armor --detach-sign -o "$stage/SHA256SUMS.asc" "$stage/SHA256SUMS"; fi
  fi
  # 6. one file
  local file="$out/$name.tar.$compress"
  step "pack $(basename "$file")" archive_py "$file" pack "$stage" "$file"
  dry_run && return 0
  (cd "$out" && sha256sum "$(basename "$file")" > "$(basename "$file").sha256")
  ok "bundle: $file ($(du -h "$file" | cut -f1), unpacked $(du -sh "$stage" | cut -f1))"
  "$(py_host)" -c 'import json,sys;m=json.load(open(sys.argv[1]))
for c in m["components"]: print("   %-22s %8.1f MB" % (c["name"], c["size_bytes"]/1e6))' "$stage/manifest.json"
  say "Copy $(basename "$file") (and its .sha256) to the air-gapped host, then:"
  say "  tar -xf $(basename "$file")   ($( [[ $compress == zst ]] && echo "needs zstd; or: zstd -dc FILE | tar -xf -" || echo "gzip"))"
  say "  cd $name && ./install.sh --with-db"
  [[ "${TT_BUNDLE_KEEP_STAGE:-0}" == 1 ]] || run rm -rf "$stage"
}

# ======================================================================================================================
info() {
  local f="${1:-}"; [[ -f "$f" ]] || usage_fail "bundle info needs a bundle file" "usage: $TT_TTCTL bundle info FILE"
  decomp "$f" | tar -xOf - --wildcards '*/manifest.json'
}
verify_file() {
  local f="${1:-}"; [[ -f "$f" ]] || usage_fail "bundle verify needs a bundle file" "usage: $TT_TTCTL bundle verify FILE"
  if [[ -f "$f.sha256" ]]; then
    (cd "$(dirname "$f")" && sha256sum -c --quiet "$(basename "$f").sha256") || \
      fail TT-E402 "$f does not match $f.sha256" "the file was changed or truncated in transit" "copy it again and compare the size with the original"
    ok "archive checksum matches $(basename "$f").sha256"
  fi
  local tmp; tmp="$(mktemp -d "$(dirname "$(readlink -f "$f")")/.verify.XXXXXX")"
  decomp "$f" | tar -xf - -C "$tmp" || { rm -rf "$tmp"; fail TT-E402 "$f cannot be unpacked" "the archive is truncated or damaged" "copy it again"; }
  verify_dir "$tmp"/tt-bundle-*; rm -rf "$tmp"
}
verify_dir() {
  local d="$1"
  [[ -f "$d/SHA256SUMS" ]] || fail TT-E402 "$d has no SHA256SUMS" "this is not an unpacked bundle, or it is incomplete" "unpack the bundle file again"
  if [[ -f "$d/SHA256SUMS.asc" ]]; then
    if have gpg; then gpg --batch --verify "$d/SHA256SUMS.asc" "$d/SHA256SUMS" >/dev/null 2>&1 && ok "signature valid" || \
      fail TT-E402 "the signature of SHA256SUMS does not verify" "the bundle was changed, or the signing key is not imported" "import the publisher's key (gpg --import KEY.asc) and retry"
    else warn "bundle is signed but gpg is not installed - signature not checked"; fi
  fi
  local out; out="$(cd "$d" && sha256sum -c --quiet SHA256SUMS 2>&1)" || \
    fail TT-E402 "files in $d do not match SHA256SUMS" "$(head -3 <<<"$out" | tr '\n' ' ')" "unpack the bundle file again (and verify it: $(tt_cmd bundle verify FILE))"
  ok "all $(wc -l < "$d/SHA256SUMS") files match SHA256SUMS"
}

# ======================================================================================================================
install_bundle() {
  local file="" dir="" prefix="" withdb=0 mode=dev force=0 upgrade=0 switch=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --from-dir) dir="$(cd "${2:?}" && pwd)"; shift ;; --prefix) prefix="${2:-}"; shift ;; --with-db) withdb=1 ;;
      --mode) mode="${2:-}"; shift ;; --force) force=1 ;; --upgrade) upgrade=1 ;; --no-switch) switch=0 ;;
      -*) usage_fail "unknown option '$1' for bundle install" ;; *) file="$1" ;;
    esac; shift
  done
  case "$mode" in dev|prod) ;; *) usage_fail "--mode must be dev or prod" ;; esac
  if [[ -z "$dir" ]]; then
    [[ -n "$file" && -f "$file" ]] || usage_fail "bundle install needs FILE or --from-dir DIR" "usage: $TT_TTCTL bundle install FILE"
    local base; base="$(dirname "$(readlink -f "$file")")"
    step "unpack $(basename "$file")" unpack_to "$file" "$base"
    dir="$base/$(basename "$file" | sed -E 's/\.tar\.(zst|gz)$//')"
  fi
  title "Install $(cat "$dir/VERSION" 2>/dev/null) from $dir"
  # 1. verify before anything else
  step "verify checksums" verify_dir "$dir"
  local version target; version="$(cat "$dir/VERSION")"
  target="$(sed -n 's/^ *"target": "\([^"]*\)".*/\1/p' "$dir/manifest.json" | head -1)"   # no Python needed on the host
  # 2. right platform?
  if [[ "$(target_os "$target")" != "$(tt_os_id)" || "$(tt_arch)" != x86_64 ]]; then
    [[ $force -eq 1 ]] || fail TT-E403 "this bundle is for $target, this host is $(tt_os_id) $(tt_arch)" \
      "binaries (Python, PostgreSQL, Chromium) are built for the target's C library and packages" \
      "on the connected machine: $(tt_cmd bundle create --target "$(bundle_target_for_host)") ; or --force to try anyway"
    warn "installing a $target bundle on $(tt_os_id) because of --force"
  fi
  # 3. release directory
  [[ -n "$prefix" ]] || { if [[ "$(id -u)" -eq 0 ]]; then prefix=/opt/team-tracker; else prefix="$HOME/team-tracker"; fi; }
  local rel="$prefix/releases/$version"
  if [[ -d "$rel/.venv" && $upgrade -eq 0 ]]; then
    ok "release $version already installed in $rel"
  else
    run mkdir -p "$prefix/releases"
    step "copy the application to $rel" bash -c "rm -rf '$rel.tmp' && cp -a '$dir/app' '$rel.tmp' && rm -rf '$rel' && mv '$rel.tmp' '$rel'"
    run mkdir -p "$rel/.tools"
    step "install tools (uv, Python$( [[ -d $dir/tools/node ]] && echo ", Node, pnpm")$( [[ -d $dir/tools/ms-playwright ]] && echo ", Chromium"))" \
      cp -a "$dir/tools/." "$rel/.tools/"
    run ln -sfn "$dir/wheelhouse" "$rel/wheelhouse"
    # Python environment from the wheelhouse only
    local py; py="$(ls -d "$rel"/.tools/python/cpython-*/bin/python3 2>/dev/null | head -1)"
    [[ -n "$py" ]] || dry_run || fail TT-E401 "the bundle has no Python" "tools/python is missing" "rebuild the bundle (it always includes Python): $(tt_cmd bundle create --target "$target")"
    export UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never
    step "create .venv" "$rel/.tools/bin/uv" venv -q --python "$py" "$rel/.venv"
    step "install dependencies from the wheelhouse (hash-checked)" "$rel/.tools/bin/uv" pip install -q --python "$rel/.venv/bin/python" \
      --offline --no-index --no-deps --find-links "$dir/wheelhouse" --require-hashes -r "$dir/wheelhouse/requirements.txt"
    step "install the Team Tracker packages" bash -c "'$rel/.tools/bin/uv' pip install -q --python '$rel/.venv/bin/python' --offline --no-index --no-deps '$dir'/wheelhouse/workspace/*.whl"
    step "run them from the release's source tree" "$rel/.venv/bin/python" "$rel/scripts/bundle/link_sources.py" "$rel"
    if [[ -d "$dir/wheelhouse/dev" ]]; then
      step "install dev tools" "$rel/.tools/bin/uv" pip install -q --python "$rel/.venv/bin/python" --offline --no-index --no-deps --find-links "$dir/wheelhouse/dev" --require-hashes -r "$dir/wheelhouse/requirements-dev.txt"
    fi
    if [[ -d "$dir/tools/pnpm-store" ]]; then
      step "pnpm install from the offline store" bash -c "cd '$rel' && PATH='$rel/.tools/node/bin:$rel/.tools/pnpm/bin:\$PATH' pnpm install --offline --frozen-lockfile --store-dir '$rel/.tools/pnpm-store'"
    fi
    if ! dry_run; then
      {
        echo "# written by ttctl bundle install - offline install from bundle $version ($target)"
        echo "TT_OFFLINE=1"; echo "TT_BUNDLE_VERSION=$version"; echo "TT_BUNDLE_TARGET=$target"; echo "TT_BUNDLE_DIR=$dir"
        echo "TT_NO_SYNC=1"; echo "UV_OFFLINE=1"; echo "UV_PYTHON_DOWNLOADS=never"; echo "PIP_NO_INDEX=1"
        echo "npm_config_offline=true"; echo "COREPACK_ENABLE_NETWORK=0"; echo "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1"
        local chrome; chrome="$(ls -d "$rel"/.tools/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | head -1)"
        [[ -n "$chrome" ]] && echo "TT_CHROMIUM=$chrome"
        [[ -x "$rel/.tools/bin/ollama" ]] && { echo "OLLAMA_MODELS=$dir/llm/models"; echo "TT_GATEWAY_MODE=local"; }
      } > "$rel/.ttctl.env"
      [[ -d "$dir/client-kit" ]] && ln -sfn "$dir/client-kit" "$rel/client-kit"
    fi
    ok "release $version installed in $rel"
  fi
  # 4. configuration (kept across releases in the prefix)
  if [[ ! -f "$prefix/.env" ]] && ! dry_run; then
    cp "$rel/.env.example" "$prefix/.env"
    if [[ $mode == prod ]]; then
      sed -i -e 's/^TT_ENV=.*/TT_ENV=prod/' -e 's/^TT_DEV_AUTH=.*/TT_DEV_AUTH=0/' \
        -e "s|^TT_SESSION_SECRET=.*|TT_SESSION_SECRET=$("$rel/.venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(48))')|" "$prefix/.env"
      printf 'TT_SECRET_KEY=%s\n' "$("$rel/.venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(48))')" >> "$prefix/.env"
      chmod 600 "$prefix/.env"
    fi
    ok "configuration: $prefix/.env (kept across upgrades)"
  fi
  dry_run || ln -sfn "$prefix/.env" "$rel/.env"
  # 5. database packages / image
  if [[ $withdb -eq 1 ]]; then
    if [[ -d "$dir/packages/rpm" || -d "$dir/packages/deb" ]]; then install_db_packages "$dir"
    elif [[ -f "$dir/images/postgres-$TT_PG_MAJOR.tar" ]]; then
      have docker || fail TT-E108 "the bundle carries PostgreSQL as a Docker image, and Docker is not installed" "--with-db image needs Docker on this host" \
        "install Docker from your OS media, or rebuild the bundle with --with-db $( [[ $target == rhel* ]] && echo rpm || echo deb)"
      step "load postgres:$TT_PG_MAJOR" docker load -i "$dir/images/postgres-$TT_PG_MAJOR.tar"
      dry_run || { grep -q '^TT_DB_MODE=' "$prefix/.env" || echo "TT_DB_MODE=docker" >> "$prefix/.env"; }
    else
      fail TT-E401 "--with-db, but the bundle has no PostgreSQL" "it was built without --with-db" \
        "on the connected machine: $(tt_cmd bundle create --target "$target" --with-db "$( [[ $target == rhel* ]] && echo rpm || echo deb)")"
    fi
  fi
  if [[ -d "$dir/packages/browser-libs" ]] && ls "$dir"/packages/browser-libs/* >/dev/null 2>&1; then install_os_packages "$dir/packages/browser-libs" "Chromium's system libraries" || true; fi
  if [[ -d "$dir/images" ]] && have docker && ls "$dir"/images/team-tracker-*.tar >/dev/null 2>&1; then step "load Team Tracker images" docker load -i "$(ls "$dir"/images/team-tracker-*.tar | head -1)"; fi
  # 6. switch "current" (upgrade does this itself after migrating)
  if [[ $switch -eq 1 && $upgrade -eq 0 ]]; then
    if [[ -L "$prefix/current" ]] && ! dry_run; then cp -P "$prefix/current" "$prefix/previous"; fi
    run ln -sfn "$rel" "$prefix/current"
  fi
  if [[ $mode == prod && $upgrade -eq 0 ]]; then
    "$rel/scripts/prod/install.sh" --mode "${TT_PROD_MODE:-proxy}" --prefix "$prefix"
  fi
  [[ $upgrade -eq 1 ]] && { echo "$rel"; return 0; }
  title "Done"
  local tt="$prefix/current/scripts/ttctl"
  say "Installed in $prefix/current (offline mode: TT_OFFLINE=1)."
  [[ -x "$rel/.tools/bin/ollama" ]] || say "No local LLM model in this bundle: the app uses the deterministic parser (build with --with-llm MODEL to add one)."
  if [[ $mode == dev ]]; then
    say "Next:"; say "  $tt db setup$( grep -q '^TT_DB_MODE=docker' "$prefix/.env" 2>/dev/null && echo ' --db docker')"; say "  $tt start"; say "  $tt status"
  fi
  say "Users' computers: give them $dir/client-kit/ (install.sh / install.ps1)."
}
bundle_target_for_host() { case "$(tt_os_id)" in rhel8) echo rhel8-x86_64 ;; rhel9) echo rhel9-x86_64 ;; ubuntu22.04) echo ubuntu22.04-amd64 ;; ubuntu24.04) echo ubuntu24.04-amd64 ;; *) echo "<target>" ;; esac; }

# OS packages from the bundle: root, asks first, never silent
install_os_packages() {
  local d="$1" what="$2" pre cmd; pre="$(sudo_prefix)"
  if ls "$d"/*.rpm >/dev/null 2>&1; then cmd="dnf -y --disablerepo='*' install $d/*.rpm"
  elif ls "$d"/*.deb >/dev/null 2>&1; then cmd="DEBIAN_FRONTEND=noninteractive dpkg -i --skip-same-version $d/*.deb"
  else return 0; fi
  say "  $what come as OS packages and need root:"; say "    $pre$cmd"
  dry_run && return 0
  if [[ -n "$pre" ]] && ! have sudo; then
    fail TT-E020 "installing $what needs root" "you are $(id -un) and sudo is not available" "ask an administrator to run the command above, then re-run ./install.sh"
  fi
  confirm "Install $what now?" y || { warn "skipped - run the command above yourself"; return 1; }
  run ${pre:+sudo} bash -c "$cmd" || fail TT-E120 "installing $what from the bundle failed" "the package manager returned an error (output above)" \
    "check that the bundle target matches this OS release ($(tt_os_id)); run the command above by hand to see the details"
}
install_db_packages() {
  local d="$1" pre; pre="$(sudo_prefix)"
  if [[ -d "$d/packages/rpm" ]]; then
    [[ "$(tt_os_id)" == rhel8 ]] && { run ${pre:+sudo} bash -c "dnf -qy module disable postgresql >/dev/null 2>&1 || true"; }
    install_os_packages "$d/packages/rpm" "PostgreSQL $TT_PG_MAJOR (server, contrib, client)" || return 0
    local data=/var/lib/pgsql/$TT_PG_MAJOR/data
    if [[ ! -f "$data/PG_VERSION" ]]; then
      if have systemctl && [[ -d /run/systemd/system ]]; then run ${pre:+sudo} "/usr/pgsql-$TT_PG_MAJOR/bin/postgresql-$TT_PG_MAJOR-setup" initdb
      else run ${pre:+sudo} runuser -u postgres -- "/usr/pgsql-$TT_PG_MAJOR/bin/initdb" -D "$data" --auth-local=peer --auth-host=scram-sha-256 >/dev/null; fi
    fi
    have systemctl && [[ -d /run/systemd/system ]] && run ${pre:+sudo} systemctl enable --now "postgresql-$TT_PG_MAJOR"
  else
    install_os_packages "$d/packages/deb" "PostgreSQL $TT_PG_MAJOR (server, contrib, client)" || return 0
  fi
  ok "PostgreSQL $TT_PG_MAJOR installed from the bundle"
}

case "$SUB" in
  create) create "$@" ;;
  info) info "$@" ;;
  verify) verify_file "$@" ;;
  install) install_bundle "$@" ;;
  "") fail TT-E012 "'bundle' needs a subcommand" "subcommands: create, info, verify, install" "$TT_TTCTL help bundle" ;;
  *) fail TT-E011 "unknown bundle subcommand '$SUB'" "subcommands: create, info, verify, install" "$TT_TTCTL help bundle" ;;
esac
