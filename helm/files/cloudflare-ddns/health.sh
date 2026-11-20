#!/bin/sh
# Check the success timestamp without loading Python or making an API request.
set -eu
interval=${INTERVAL_SECONDS:-60}
case "$interval" in ''|*[!0-9]*) exit 1 ;; esac
[ "$interval" -ge 30 ] && [ "$interval" -le 3600 ]
[ -f /state/last-success ] || exit 1
age=$(( $(date +%s) - $(stat -c %Y /state/last-success) ))
[ "$age" -ge 0 ] && [ "$age" -lt $(( interval * 3 + 60 )) ]
