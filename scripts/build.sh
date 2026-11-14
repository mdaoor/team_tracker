#!/usr/bin/env bash
# Build release artefacts.
source "$(dirname "$0")/lib/common.sh"
usage() {
  cat <<EOF2
ttctl build - build release artefacts

SYNOPSIS
  $TT_TTCTL build [TARGET...]          default: presentation web desktop client

TARGETS
  presentation    single-file deck viewer -> apps/presentation/dist/presentation.html (Present / PDF / PNG)
  web             web console (static SPA) -> apps/web/dist (nginx, or "ttctl start web" without Node)
  desktop         palette + personal tracker + Mobile Lite web bundles -> apps/desktop/dist
  desktop-native  Tauri desktop app (Rust + Tauri system libraries)
  client          tt CLI wheels -> dist/client
  images          Docker images team-tracker-server, team-tracker-llm-gateway (tag: TT_IMAGE_TAG, default latest)
  openapi         docs/api/openapi.json and the web client types

EXAMPLES
  $TT_TTCTL build
  $TT_TTCTL build web presentation
  TT_IMAGE_TAG=1.0.0 $TT_TTCTL build images

SEE ALSO
  $GUIDE/06-production.md
EOF2
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
cd "$ROOT" || exit 1
TARGETS=("$@"); [[ ${#TARGETS[@]} -eq 0 ]] && TARGETS=(presentation web desktop client)
need_node() { have pnpm && [[ -d node_modules ]] || fail TT-E030 "building $1 needs Node, pnpm and node_modules" "they are not installed here" "$(tt_cmd setup)" "a bundle built with --with-dev brings Node and the pnpm store"; }
for t in "${TARGETS[@]}"; do
  case "$t" in
    presentation) need_node "$t"; step "build the deck viewer" pnpm --filter @tt/presentation build; ok "apps/presentation/dist/presentation.html" ;;
    web) need_node "$t"; step "build the deck viewer" pnpm --filter @tt/presentation build; step "build the web console" pnpm --filter @tt/web build; ok "apps/web/dist" ;;
    desktop) need_node "$t"; step "build the desktop bundles" pnpm --filter @tt/desktop build; ok "apps/desktop/dist" ;;
    desktop-native)
      have cargo || fail TT-E100 "building the native desktop app needs Rust" "cargo is not on PATH" "install Rust (https://rustup.rs) and the Tauri prerequisites, see docs/client/desktop.md"
      need_node "$t"; step "tauri build" pnpm --filter @tt/desktop exec tauri build; ok "see apps/desktop/src-tauri/target/release/bundle" ;;
    client) "$ROOT/scripts/client.sh" wheels ;;
    images)
      have docker || fail TT-E108 "building images needs Docker" "docker is not on PATH" "install Docker, or build on another machine"
      tt_need_network "docker build (base images)" docker-images --with-docker-images
      step "build team-tracker-server" docker build -f apps/server/Dockerfile -t "team-tracker-server:${TT_IMAGE_TAG:-latest}" .
      step "build team-tracker-llm-gateway" docker build -f apps/llm_gateway/Dockerfile -t "team-tracker-llm-gateway:${TT_IMAGE_TAG:-latest}" .
      ok "images tagged ${TT_IMAGE_TAG:-latest}" ;;
    openapi) need_node "$t"; step "openapi.json" "${PY_RUN[@]}" python -m tt_server.openapi docs/api/openapi.json; step "web client types" pnpm --filter @tt/web gen:api; ok "docs/api/openapi.json" ;;
    *) fail TT-E011 "unknown build target '$t'" "targets: presentation web desktop desktop-native client images openapi" "$TT_TTCTL help build" ;;
  esac
done
