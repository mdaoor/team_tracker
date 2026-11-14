#!/usr/bin/env bash
# Restart components (stop, then start).
source "$(dirname "$0")/lib/common.sh"
usage() {
  cat <<EOF2
ttctl restart - stop, then start

SYNOPSIS
  $TT_TTCTL restart [COMPONENT...]

OPTIONS
  COMPONENT    as for start/stop (default: stop everything that runs, start server worker web)

EXAMPLES
  $TT_TTCTL restart
  $TT_TTCTL restart server
  $TT_TTCTL -v restart web

SEE ALSO
  $GUIDE/04-run-and-stop.md
EOF2
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
"$ROOT/scripts/stop.sh" "$@"
"$ROOT/scripts/start.sh" "$@"
