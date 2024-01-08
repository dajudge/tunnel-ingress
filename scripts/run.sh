#! /bin/bash
set -eu

if [ "$JOB" = "network-init" ]; then
  /scripts/network-init.sh
elif [ "$JOB" = "keep-alive" ]; then
  /scripts/keep-alive-job.sh
else
  echo "Unknown job: ${JOB}"
  exit 1
fi