# HTTP keep-alive requests

Every request runs through one native curl configuration. The container repeats
one bounded remote curl batch, then waits 60 seconds before opening a fresh SSH
connection for the next batch.
Use `pingConfigSecret.name/key` (key defaults to `config`) for new deployments;
set `pingSecret.name: ""` to disable the legacy input. A URL-only DDNS provider
uses a GET entry; a provider needing authentication headers and a JSON body uses
a PATCH entry. Both can live in the same Secret, separated by curl's `next`:

```text
url = "https://ddns.example.test/update?hostname=host.example.test"
request = "GET"
silent
show-error
fail
max-time = 20
output = "/dev/null"
next
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
```

Curl handles methods, headers and bodies. `next` resets per-request options,
including authentication headers. Supply each entry's timeout/output/error
options; omit `fail-early` when later requests should run after an earlier one
fails. There is no provider-specific execution path.

For compatibility, `pingSecret` still supplies `PING_URL`. Startup converts it
into a GET configuration entry before the common transport and execution loop;
if `PING_CONFIG` is also supplied, `next` separates them. The default URL Secret
reference is unchanged. URL-only execution now uses the same 20-second timeout,
HTTP-error reporting and response suppression as the combined mode, with URL
credentials sent over stdin rather than embedded in SSH arguments. Disable both
inputs and the chart/script reject the configuration before SSH.

Configuration travels over each SSH connection's stdin and is retained only
in that batch's remote process memory. It is never shell-evaluated. Keep
credentials in a Secret; only encrypted SealedSecrets belong in Git. There is no extra Pod,
container, ConfigMap or provider-specific runtime. Remote batches use GNU
`timeout` from the tunnel host's existing Ubuntu coreutils; no package is added.

The literal `{{SSH_SERVER_IP}}` is replaced with the server endpoint reported by
OpenSSH's `SSH_CONNECTION`, for every input form. It is not public-IP discovery;
a NAT/private endpoint must not be published. Missing address information
rejects configurations using the placeholder. Secret changes require the normal
container rollout to load the new configuration.

Run `python3 -B tests/test-ping.py` with Bash, curl, Helm, yq and kubeconform.
Tests use actual curl against local fixtures and compare native GET with the
legacy adapter, config-only PATCH, mixed native requests, header isolation,
failure continuation, placeholder expansion, no-input rejection and stdin-only
credential transport. Fault tests execute the actual Bash/curl batch against a
hung local HTTP fixture, drop its channel, and require the deadline to end it.
They also check repeated batches, failure propagation and the actual helper's
SSH options. No real credentials or live SSH service are used.

## Connection and process lifetime

Release 0.0.12 removes the remote infinite loop. A disconnected session cannot
keep scheduling requests: at most its single in-flight batch can remain. The
entire remote command, including stdin read and every curl transfer, has a
120-second deadline followed by TERM and a five-second KILL grace period. This
also bounds native configs that omit per-request timeouts. Keep normal batches
below that deadline; a timeout exits the container instead of starting another
batch. A killed batch may already have completed earlier requests; retries must
remain safe for the configured endpoints.

The shared SSH helper uses noninteractive authentication, a ten-second connect
timeout, and encrypted keepalive probes every 15 seconds with three unanswered
probes allowed. An unresponsive connection makes SSH exit; the ping script
propagates that failure so Kubernetes restarts it under its normal backoff.
HTTP failures within an otherwise responsive batch remain isolated as before.
The shared settings also detect dead connections in the one-time tunnel setup;
no initializer commands change. SSH handshakes now happen once per interval.

The deadline does not stop orphaned loops created by older releases. Inspect
and stop only those verified old processes through a separately approved cleanup;
changing the image or removing a Secret does not revoke credentials already in
those processes. Do not resume credential cleanup until that work and fresh
scheduled updates are verified.

Publish release 0.0.12 before consuming the fix. The Dockerfile and tunnel
initializer are unchanged. Replacing the existing Pod reruns that initializer and can briefly interrupt connectivity. Restore the
previous chart/image through the consuming GitOps repository to roll back,
keeping its reviewed Secret references and disabled legacy URL input; that replacement can cause another interruption.
