#! /bin/bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "${SCRIPT_DIR}/ssh-access.sh"

legacy_ping() {
  remote "while :; do date; curl '${PING_URL}' || true; echo \"\"; sleep 60; done"
}

combined_ping() {
  local field
  for field in "${PING_URL}" "${CF_API_TOKEN:-}" "${CF_ZONE_ID:-}" "${CF_RECORD_ID:-}" \
               "${CF_RECORD_NAME:-}" "${HOST}" "${CF_DRY_RUN:-true}"; do
    if [[ -z "$field" || "$field" == *$'\n'* || "$field" == *$'\r'* ]]; then
      return 64
    fi
  done
  # Credentials remain data on stdin, never SSH arguments or remote files.
  export -n CF_API_TOKEN PING_URL
  {
    printf '%s\n' "${PING_URL}" "${CF_API_TOKEN}" "${CF_ZONE_ID}" "${CF_RECORD_ID}" \
      "${CF_RECORD_NAME}" "${HOST}" "${CF_DRY_RUN:-true}" 60
    cat "${SCRIPT_DIR}/remote-ddns.py"
  } | remote 'python3 -u -c '\''import sys; sys.exit(127) if sys.version_info < (3, 8) else None; ddns_config = [sys.stdin.readline().rstrip("\n") for _ in range(8)]; exec(compile(sys.stdin.read(), "remote-ddns.py", "exec"))'\'''
}

echo "Starting keep-alive..."
if [[ "${CF_DDNS_ENABLED:-false}" == true ]]; then
  status=0
  combined_ping || status=$?
  case "$status" in
    0) ;;
    64|127)
      echo "Combined DDNS unavailable; preserving dogado-only updates." >&2
      legacy_ping
      ;;
    *)
      echo "SSH DDNS session ended; retry both providers through container restart." >&2
      exit "$status"
      ;;
  esac
else
  legacy_ping
fi
