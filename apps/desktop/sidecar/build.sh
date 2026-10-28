#!/usr/bin/env bash
# Build the `tt-sidecar` binary and place it where Tauri's `externalBin` expects it:
#   apps/desktop/src-tauri/binaries/tt-sidecar-<rust target triple>[.exe]
# Run it once per target OS/arch (PyInstaller does not cross-compile). Not part of CI: needs PyInstaller and, for the triple, rustc.
set -euo pipefail
cd "$(dirname "$0")/../../.."

TRIPLE="${TT_TARGET_TRIPLE:-$(rustc -vV | sed -n 's/^host: //p')}"
[ -n "$TRIPLE" ] || { echo "set TT_TARGET_TRIPLE (e.g. x86_64-unknown-linux-gnu)" >&2; exit 1; }
EXT=""
case "$TRIPLE" in *windows*) EXT=".exe" ;; esac

uv run --frozen --all-packages --with pyinstaller pyinstaller --noconfirm --clean \
  --distpath apps/desktop/sidecar/dist --workpath apps/desktop/sidecar/build apps/desktop/sidecar/tt-sidecar.spec

mkdir -p apps/desktop/src-tauri/binaries
cp "apps/desktop/sidecar/dist/tt-sidecar${EXT}" "apps/desktop/src-tauri/binaries/tt-sidecar-${TRIPLE}${EXT}"

# smoke test: the frozen binary must answer with exactly one JSON document
TT_DATA_DIR="$(mktemp -d)" TT_CONFIG_DIR="$(mktemp -d)" TT_SECRETS=file \
  "apps/desktop/src-tauri/binaries/tt-sidecar-${TRIPLE}${EXT}" --verbs |
  python3 -c 'import json,sys; v=json.load(sys.stdin); assert "cmd-state" in v and "personal_day" in v; print("ok:", len(v), "verbs")'
echo "built apps/desktop/src-tauri/binaries/tt-sidecar-${TRIPLE}${EXT}"
