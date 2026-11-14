#!/usr/bin/env bash
# Air-gapped acceptance test: in a container started with --network none, run the "runnable-offline" blocks of
# docs/guide/03-install-airgapped.md word for word (unpack, install.sh, db setup, start, status, smoke with a PDF
# export, stop), then check the offline guard. There is no network interface besides loopback: any download fails.
#   ci/ops/airgap.sh TARGET BUNDLE_FILE
source "$(dirname "$0")/lib.sh"
TARGET="$1"; BUNDLE="$(readlink -f "$2")"
case "$TARGET" in rhel8-*) IMG=rockylinux:8 ;; rhel9-*) IMG=rockylinux:9 ;; ubuntu22.04-*) IMG=ubuntu:22.04 ;; ubuntu24.04-*) IMG=ubuntu:24.04 ;; *) echo "unknown target $TARGET"; exit 2 ;; esac
[[ -f "$BUNDLE" && -f "$BUNDLE.sha256" ]] || { echo "need $BUNDLE and $BUNDLE.sha256"; exit 2; }
NAME="$(basename "$BUNDLE")"
STEPS="$(python3 "$CI_ROOT/tools/run_doc_blocks.py" --tag runnable-offline "$CI_ROOT/docs/guide/03-install-airgapped.md")"
docker run --rm --network none -v "$BUNDLE:/media/$NAME:ro" -v "$BUNDLE.sha256:/media/$NAME.sha256:ro" "$IMG" bash -euo pipefail -c "
  echo '== network interfaces (only lo expected):' \$(ls /sys/class/net)
  [[ \$(ls /sys/class/net) == lo ]]
  mkdir -p /root/copied && cp /media/$NAME /media/$NAME.sha256 /root/copied/ && cd /root/copied
  $STEPS
  echo; echo '== offline guard: a download must be refused with exit 4'
  set +e; scripts/ttctl prereqs install node; rc=\$?; set -e
  [[ \$rc -eq 4 ]] || { echo \"expected exit 4, got \$rc\"; exit 1; }
  echo 'AIRGAP TEST PASSED ($TARGET)'
"
