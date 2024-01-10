#! /bin/bash
set -eu

source /scripts/ssh-access.sh

echo "Starting keep-alive..."

remote "while :; do date; curl '${PING_URL}' || true; echo ""; sleep 60; done"