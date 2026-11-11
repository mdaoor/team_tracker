#!/usr/bin/env bash
# Restart components (stop, then start).
#
#   scripts/restart.sh               server + worker + web
#   scripts/restart.sh server        only the API
source "$(dirname "$0")/lib/common.sh"
[[ "${1:-}" =~ ^(-h|--help)$ ]] && { sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
run_script stop.sh "$@"
run_script start.sh "$@"
