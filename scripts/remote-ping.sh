#! /bin/bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "${SCRIPT_DIR}/ssh-access.sh"

echo "Starting keep-alive..."
if [[ -z "${PING_URL:-}" && -z "${PING_CONFIG:-}" ]]; then
  echo "Configure PING_URL or PING_CONFIG" >&2
  exit 1
fi

# PING_URL is only a compatibility adapter into the native curl configuration.
# All requests use the same transport, expansion and execution loop below.
url=${PING_URL:-}
url=${url//\\/\\\\}
url=${url//\"/\\\"}
[[ "$url" != *$'\n'* && "$url" != *$'\r'* ]] || exit 1
export -n PING_URL PING_CONFIG
# The repeat loop belongs to the container, never to a detached remote shell.
while :; do
  {
    if [[ -n "$url" ]]; then
      printf 'url = "%s"\nrequest = "GET"\nsilent\nshow-error\nfail\nmax-time = 20\noutput = "/dev/null"\n' "$url"
      if [[ -n "${PING_CONFIG:-}" ]]; then
        printf 'next\n'
      fi
    fi
    printf '%s\n' "${PING_CONFIG:-}"
  } | remote 'timeout --signal=TERM --kill-after=5s 120s bash -euo pipefail -c '\''config=$(cat)
      read -r _ _ server_ip _ <<< "${SSH_CONNECTION:-}"
      if [[ "$config" == *"{{SSH_SERVER_IP}}"* && -z "$server_ip" ]]; then
        echo "SSH server address is unavailable" >&2
        exit 1
      fi
      config=${config//\{\{SSH_SERVER_IP\}\}/$server_ip}
      date
      printf "%s\n" "$config" | curl --disable --config - || true'\'''
  sleep 60
done
