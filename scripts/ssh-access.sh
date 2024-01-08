#! /bin/bash
set -eu

echo "Import SSH key..."
mkdir -p /root/.ssh/
cp /ssh-key/* /root/.ssh/
chmod 0600 /root/.ssh -R

function remote() {
  ssh -o "StrictHostKeyChecking=no" -l root $HOST "$@"
}