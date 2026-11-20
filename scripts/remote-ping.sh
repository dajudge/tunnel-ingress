#! /bin/bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "${SCRIPT_DIR}/ssh-access.sh"

echo "Starting keep-alive..."
if [[ -n "${PING_CONFIG:-}" ]]; then
  # Native curl config supports URLs, methods, headers, bodies and `next`.
  # Escape the existing URL as a config value; never evaluate secret contents.
  url=${PING_URL//\\/\\\\}
  url=${url//\"/\\\"}
  [[ "$url" != *$'\n'* && "$url" != *$'\r'* ]] || exit 1
  export -n PING_URL PING_CONFIG
  {
    printf 'url = "%s"\nsilent\nshow-error\nfail\nmax-time = 20\noutput = "/dev/null"\nnext\n' "$url"
    printf '%s\n' "$PING_CONFIG"
  } | remote 'bash -c '\''config=$(cat)
    read -r _ _ server_ip _ <<< "${SSH_CONNECTION:-}"
    if [[ "$config" == *"{{SSH_SERVER_IP}}"* && -z "$server_ip" ]]; then
      echo "SSH server address is unavailable" >&2
      exit 1
    fi
    config=${config//\{\{SSH_SERVER_IP\}\}/$server_ip}
    while :; do
      date
      printf "%s\n" "$config" | curl --disable --config - || true
      sleep 60
    done'\'''
else
  # Preserve the original URL-only mode.
  remote "while :; do date; curl '${PING_URL}' || true; echo \"\"; sleep 60; done"
fi
