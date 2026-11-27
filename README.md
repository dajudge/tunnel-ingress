# HTTP keep-alive requests

The existing `pingSecret` supplies a URL for the remote `curl` loop. Its default
behavior is unchanged. Optionally supply `pingConfigSecret.name/key` (key defaults
to `config`) for additional requests using native curl configuration:

```text
url = "https://api.example.test/resource"
request = "PATCH"
header = "Authorization: Bearer ..."
header = "Content-Type: application/json"
data = "{\"content\":\"{{SSH_SERVER_IP}}\"}"
silent
show-error
fail
max-time = 20
output = "/dev/null"
next
url = "https://another.example.test/ping"
request = "GET"
```

Curl handles the URL, method, headers and body. `next` separates requests and
resets per-request options, including authentication headers. Supply each
request's desired timeout/output/error options; do not use `fail-early` when all
requests should be attempted after one fails. The existing URL is prepended as
a separate GET, so its Secret need not change.

Configuration travels over the existing SSH connection's stdin and is retained
in remote process memory. It is not a shell script and is never evaluated as
one. No new runtime, Pod, container, ConfigMap or provider-specific logic is
introduced. Keep credential-bearing configuration in a Secret; only encrypted
SealedSecrets belong in Git. Curl responses follow the supplied output options.

The optional literal `{{SSH_SERVER_IP}}` is replaced with the server address
reported by OpenSSH's `SSH_CONNECTION`. It is not a public-IP discovery service;
on a host behind NAT this can be a private address. Verify it is the intended
address before using it in an update body. Missing address information rejects
a configuration using the placeholder. Configuration is loaded when the SSH
session starts; Secret changes require the normal container rollout to reload.

Run `python3 -B tests/test-ping.py` with Bash, curl, Helm, yq and kubeconform.
Tests exercise real curl against local HTTP fixtures, method/body/header
separation, continued requests after HTTP failures, legacy compatibility and
stdin-only credential transport. They do not use real credentials or SSH.

Version 0.0.11 must be published before consuming the new feature. The Dockerfile,
SSH helper and tunnel initializer are unchanged. Updating the existing Pod still
reruns that initializer and can interrupt the tunnel; review activation separately
rather than claiming a zero-downtime rollout. Disabling the extra configuration
restores URL-only operation but can also require Pod replacement.

For configuration-only requests, set `pingSecret.name: ""` and supply
`pingConfigSecret.name/key`. This omits `PING_URL` and sends only the supplied
curl configuration, without an empty request or leading `next`. Defaults retain
the URL-only mode; supplying both inputs retains the separate legacy GET.
Neither input being configured is rejected by the chart and script before SSH.
Publish release 0.0.11 before consuming this mode. Pod replacement can briefly
interrupt tunnel connectivity; roll back by restoring the previous chart/image
and URL Secret reference through the consuming GitOps repository.
