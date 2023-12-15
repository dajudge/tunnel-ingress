#! /bin/bash

set -eu

DIR=$(dirname "$(readlink -f "$0")")

if [ "" == "${VERSION:-}" ]; then
  echo "Skipping version tests..."
  exit 0
fi

function assert_equals() {
  if [ "$2" != "$3" ]; then
    echo "$1" >&2
    echo "expected: \"$2\", actual: \"$3\"" >&2
    exit 1
  fi
}

function yq() {
  docker run --rm -i -v "${DIR}/..:/workdir" mikefarah/yq "$@"
}

assert_equals "Chart.yaml version doesn't match" "$VERSION" "$(yq e '.version' "/workdir/helm/Chart.yaml")"
assert_equals "Chart.yaml appVersion doesn't match" "$VERSION" "$(yq e '.appVersion' "/workdir/helm/Chart.yaml")"
assert_equals "values.yaml tag doesn't match" "$VERSION" "$(yq e '.tag' "/workdir/helm/values.yaml")"