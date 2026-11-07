#!/usr/bin/env bash
# Build release artefacts.
#
#   scripts/build.sh                 everything below except desktop-native
#   scripts/build.sh presentation    single-file deck viewer  -> apps/presentation/dist/presentation.html (needed by Present/PDF/PNG)
#   scripts/build.sh web             web console (static SPA) -> apps/web/dist     (serve with nginx/Caddy, see docs/guide/04-production.md)
#   scripts/build.sh desktop         desktop palette + personal tracker + Mobile Lite web bundles -> apps/desktop/dist
#   scripts/build.sh desktop-native  Tauri desktop app (needs Rust + the Tauri system libraries)
#   scripts/build.sh client          tt CLI wheels -> dist/client
#   scripts/build.sh images          Docker images: team-tracker-server, team-tracker-llm-gateway
#   scripts/build.sh openapi         regenerate docs/api/openapi.json and the web client types
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT" || exit 1
TARGETS=("$@"); [[ ${#TARGETS[@]} -eq 0 ]] && TARGETS=(presentation web desktop client)

for t in "${TARGETS[@]}"; do
  case "$t" in
    presentation) pnpm --filter @tt/presentation build; ok "apps/presentation/dist/presentation.html" ;;
    web) pnpm --filter @tt/presentation build >/dev/null; pnpm --filter @tt/web build; ok "apps/web/dist" ;;
    desktop) pnpm --filter @tt/desktop build; ok "apps/desktop/dist" ;;
    desktop-native)
      need cargo "Install Rust (https://rustup.rs) and the Tauri prerequisites, see docs/client/desktop.md"
      pnpm --filter @tt/desktop exec tauri build; ok "see apps/desktop/src-tauri/target/release/bundle" ;;
    client) run_script client.sh wheels ;;
    images)
      need docker "Install Docker."
      docker build -f apps/server/Dockerfile -t "team-tracker-server:${TT_IMAGE_TAG:-latest}" .
      docker build -f apps/llm_gateway/Dockerfile -t "team-tracker-llm-gateway:${TT_IMAGE_TAG:-latest}" .
      ok "images tagged ${TT_IMAGE_TAG:-latest}" ;;
    openapi) "${PY_RUN[@]}" python -m tt_server.openapi docs/api/openapi.json; pnpm --filter @tt/web gen:api; ok "docs/api/openapi.json" ;;
    -h|--help|help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown build target '$t' (see scripts/build.sh --help)" ;;
  esac
done
