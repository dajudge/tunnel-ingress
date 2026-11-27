# HTTP keep-alive requests

Every request runs through one native curl configuration and one remote loop.
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

Configuration travels over the existing SSH connection's stdin and is retained
in remote process memory. It is never shell-evaluated. Keep credentials in a
Secret; only encrypted SealedSecrets belong in Git. No dependency, extra Pod,
container, ConfigMap or provider-specific runtime is introduced.

The literal `{{SSH_SERVER_IP}}` is replaced with the server endpoint reported by
OpenSSH's `SSH_CONNECTION`, for every input form. It is not public-IP discovery;
a NAT/private endpoint must not be published. Missing address information
rejects configurations using the placeholder. Secret changes require the normal
container rollout to load the new configuration.

Run `python3 -B tests/test-ping.py` with Bash, curl, Helm, yq and kubeconform.
Tests use actual curl against local fixtures and compare native GET with the
legacy adapter, config-only PATCH, mixed native requests, header isolation,
failure continuation, placeholder expansion, no-input rejection and stdin-only
credential transport. No real credentials or SSH are used.

Publish release 0.0.11 before consuming configuration-only mode. The Dockerfile,
SSH helper and tunnel initializer are unchanged. Replacing the existing Pod
reruns that initializer and can briefly interrupt connectivity. Restore the
previous chart/image and URL Secret reference through the consuming GitOps
repository to roll back; that replacement can cause another interruption.
