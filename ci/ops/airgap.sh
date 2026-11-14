#!/usr/bin/env bash
# Air-gapped acceptance test: install a bundle in a container started with --network none and run the guide's offline
# flow: install.sh -> db setup -> start -> smoke (health, seed, web, PDF export) -> offline guard -> stop.
#   ci/ops/airgap.sh TARGET BUNDLE_FILE
# Any network attempt fails: the container has no network interface besides loopback.
source "$(dirname "$0")/lib.sh"
TARGET="$1"; BUNDLE="$(readlink -f "$2")"
case "$TARGET" in rhel8-*) IMG=rockylinux:8 ;; rhel9-*) IMG=rockylinux:9 ;; ubuntu22.04-*) IMG=ubuntu:22.04 ;; ubuntu24.04-*) IMG=ubuntu:24.04 ;; *) echo "unknown target"; exit 2 ;; esac
NAME="$(basename "$BUNDLE")"
docker run --rm --network none -v "$BUNDLE:/media/$NAME:ro" -v "$CI_ROOT/ci/ops/smoke.py:/smoke.py:ro" "$IMG" bash -euo pipefail -c "
  echo '== network interfaces (expect only lo)'; ls /sys/class/net
  mkdir -p /root/bundle && cd /root/bundle
  case '$NAME' in *.gz) tar -xzf /media/$NAME ;; *.zst) zstd -dc /media/$NAME | tar -xf - ;; esac
  cd tt-bundle-*/
  echo '== ./install.sh --with-db --yes'
  ./install.sh --with-db --yes
  cd /opt/team-tracker/current
  echo '== ttctl db setup'; scripts/ttctl db setup
  echo '== ttctl start';    scripts/ttctl start
  echo '== ttctl status';   scripts/ttctl status
  echo '== smoke';          /opt/team-tracker/current/.venv/bin/python /smoke.py
  echo '== offline guard: a download must be refused with exit 4'
  set +e; scripts/ttctl prereqs install node; rc=\$?; set -e
  [[ \$rc -eq 4 ]] || { echo \"expected exit 4, got \$rc\"; exit 1; }
  echo '== ttctl stop';     scripts/ttctl stop
  echo 'AIRGAP TEST PASSED'
"
