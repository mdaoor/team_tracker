#!/usr/bin/env bash
# Online acceptance test: a fresh container of TARGET prepared like a minimal server (curl, python3, git, ca-certificates),
# then the "runnable" blocks of the guide pages 01, 02, 04 and 05 word for word: prereqs check, setup --with-db
# --install-missing --browsers --yes, start, status, smoke (health, seed, web, PDF export), stop, db status, db backup.
#   ci/ops/online.sh TARGET
source "$(dirname "$0")/lib.sh"
TARGET="$1"
case "$TARGET" in rhel8-*) IMG=rockylinux:8 ;; rhel9-*) IMG=rockylinux:9 ;; ubuntu22.04-*) IMG=ubuntu:22.04 ;; ubuntu24.04-*) IMG=ubuntu:24.04 ;; *) echo "unknown target $TARGET"; exit 2 ;; esac
SRC="$(mktemp -d)"; ci_snapshot "$SRC/team_tracker"; trap 'rm -rf "$SRC"' EXIT
STEPS="$(cd "$CI_ROOT" && python3 tools/run_doc_blocks.py --tag runnable docs/guide/01-requirements.md docs/guide/02-install-online.md \
         docs/guide/04-run-and-stop.md docs/guide/05-database.md)"
mapfile -t NET < <(ci_net_flags)
CA=(); [[ -n "${TT_CI_EXTRA_CA:-}" ]] && CA=(-v "$TT_CI_EXTRA_CA:/extra-ca.crt:ro")
# where the Playwright CDN is blocked: TT_CI_BROWSERS_FROM=/opt/pw-browsers is mounted and used via TT_BROWSERS_FROM
[[ -n "${TT_CI_BROWSERS_FROM:-}" ]] && CA+=(-v "$TT_CI_BROWSERS_FROM:/browsers:ro" -e TT_BROWSERS_FROM=/browsers)
docker run --rm "${NET[@]}" "${CA[@]}" -v "$SRC/team_tracker:/src:ro" "$IMG" bash -euo pipefail -c "
  echo '== prepare a minimal server: curl python3 git ca-certificates (what a standard install has)'
  if command -v dnf >/dev/null; then
    [[ -f /extra-ca.crt ]] && { cp /extra-ca.crt /etc/pki/ca-trust/source/anchors/; update-ca-trust; }
    [[ -n \"\${HTTPS_PROXY:-}\" ]] && echo \"proxy=\$HTTPS_PROXY\" >> /etc/dnf/dnf.conf
    dnf -y -q install git python3 tar gzip procps-ng sudo >/dev/null
  else
    export DEBIAN_FRONTEND=noninteractive
    if [[ -n \"\${HTTPS_PROXY:-}\" ]]; then
      printf 'Acquire::https::Proxy \"%s\";\n' \"\$HTTPS_PROXY\" > /etc/apt/apt.conf.d/90proxy
      [[ -f /extra-ca.crt ]] && printf 'Acquire::https::CaInfo \"/extra-ca.crt\";\n' >> /etc/apt/apt.conf.d/90proxy
      sed -i 's#http://\(archive\|security\).ubuntu.com#https://\1.ubuntu.com#g' /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true
    fi
    apt-get update -qq && apt-get install -y -qq curl ca-certificates python3 git sudo >/dev/null
    [[ -f /extra-ca.crt ]] && { cp /extra-ca.crt /usr/local/share/ca-certificates/extra.crt; update-ca-certificates >/dev/null; }
  fi
  export NODE_EXTRA_CA_CERTS=\$( [[ -f /etc/pki/tls/certs/ca-bundle.crt ]] && echo /etc/pki/tls/certs/ca-bundle.crt || echo /etc/ssl/certs/ca-certificates.crt)
  export SSL_CERT_FILE=\$NODE_EXTRA_CA_CERTS UV_NATIVE_TLS=1
  cp -a /src /root/team_tracker && cd /root/team_tracker
  $STEPS
  echo 'ONLINE TEST PASSED ($TARGET)'
"
