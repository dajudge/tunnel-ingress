# Tunnel ingress and DDNS

`remote-ping` keeps the existing dogado updater and optionally updates Cloudflare
from the same public tunnel host, through the same SSH connection and loop.
There is no extra Deployment, sidecar, ConfigMap, or readiness probe.

`cloudflareDdns.enabled` defaults to false, preserving the original dogado loop.
When enabled, configure the existing Cloudflare DNS-only A record's `zoneId`,
`recordId`, and `recordName`. `dryRun` defaults to true. `tokenSecret.name/key`
select an existing Secret in the release namespace, just as `pingSecret` supplies
the dogado URL. The token reference is optional so a missing Cloudflare Secret
cannot prevent dogado from running; it produces a fallback diagnostic instead.

The public SSH host needs Python 3.8+ and curl. Configuration is sent as eight
single-line fields over SSH stdin, followed by the public runner source.
Neither the Cloudflare token nor the dogado URL appears in the combined SSH
command's arguments, remote files, or logs. They remain in process memory.
The public runner is compiled in memory and both requests originate on that
host. Its root SSH process can access the token; this is an intentional trust
boundary. The existing migration token's permissions exceed one record.

Each minute, the remote loop attempts the dogado curl request and Cloudflare
reconciliation independently, with provider failures logged only by class.
Cloudflare reconciles the same `host` already used for SSH, never the home WAN.
It requires a unique public IPv4 target and the pinned existing DNS-only A
record. Dry-run reads only. Later explicit write enablement PATCHes only
`content`, preserving TTL and metadata; it never creates or deletes records.
ExternalDNS must continue excluding this anchor.

A missing/old Python interpreter or invalid configuration falls back to the
original dogado-only loop. Transient SSH failure exits the container so its
normal restart retries both providers. Neither condition reruns the initializer
unless the whole Pod is replaced. API failures stay inside the combined loop.

Run `python3 -B tests/test-cloudflare-ddns.py` with Helm, yq, and kubeconform.
Tests use fake providers and SSH, verify stdin framing and secret-free logs,
exercise both failure directions and fallback/retry behavior, and prove Helm
adds only Cloudflare environment inputs to the existing container. No tests
connect to the tunnel host or use real credentials.

Delivery requires reviewing/merging this change and publishing version 0.0.10
through the existing release workflow. A separate bootstrap draft prepares its
chart pin and encrypted credential and removes the standalone observer. No live
host, cluster, DNS records, ownership markers or nameservers are changed by
preparation. The Dockerfile and tunnel setup scripts are unchanged.

**Rollout remains gated:** replacing the existing Pod reruns the initializer,
which rebuilds WireGuard and iptables. The current implementation cannot promise
zero downtime. Keep bootstrap activation unmerged until a separately reviewed
rollout addresses that constraint or an interruption is explicitly accepted.
Disabling Cloudflare restores dogado-only behavior, but a Helm rollback also
replaces the Pod and has the same tunnel-reinitialization risk. Imported DNS
records remain intact throughout; disabling a writer does not remove them.
