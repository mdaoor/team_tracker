# shellcheck shell=bash
# Helpers for the container-based ops tests (ci/ops/*.sh). Source it.
set -euo pipefail
CI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# An intercepting proxy (TT_CI_EXTRA_CA=/path/ca.pem) needs its CA inside images and containers; empty elsewhere.
ci_prepare_ca() {
  local dir="$1"; mkdir -p "$dir/ca"; rm -f "$dir/ca/"*
  if [[ -n "${TT_CI_EXTRA_CA:-}" && -f "$TT_CI_EXTRA_CA" ]]; then cp "$TT_CI_EXTRA_CA" "$dir/ca/extra-ca.crt"; else touch "$dir/ca/.keep"; fi
}
# docker flags that let a container use the host's proxy (if any)
ci_net_flags() {
  if [[ -n "${HTTPS_PROXY:-}" ]]; then
    printf '%s\n' --network host -e HTTPS_PROXY -e https_proxy -e NO_PROXY -e no_proxy
  fi
}
ci_build_flags() {
  if [[ -n "${HTTPS_PROXY:-}" ]]; then
    printf '%s\n' --network host --build-arg HTTPS_PROXY --build-arg https_proxy --build-arg NO_PROXY --build-arg no_proxy
  fi
}
# A copy of the working tree without build output, so a test never writes into the checkout.
ci_snapshot() {
  local out="$1"; rm -rf "$out"; mkdir -p "$out"
  (cd "$CI_ROOT" && git ls-files -z --cached --others --exclude-standard | xargs -0 tar -cf - 2>/dev/null) | tar -xf - -C "$out"
}
