# Tunnel ingress and parallel DDNS

The existing `tunnel-ingress` Deployment runs tunnel setup and the dogado
`remote-ping` updater. Those resources, image tag and scripts are unchanged.

The chart optionally owns a separate `cloudflare-ddns` Deployment alongside it.
`cloudflareDdns.enabled` defaults to false; `dryRun` defaults to true. Both
updaters use the existing chart `host` value, the public tunnel destination.
Cloudflare DDNS does not discover the cluster's home WAN address.

To configure the observer, supply `cloudflareDdns.zoneId`, `recordId`, and
`recordName`, plus an existing Secret (default `cloudflare-api-token`) with key
`api-token` in the release namespace. The chart neither creates nor reads the
Secret during rendering. Bootstrap owns its strict-scoped SealedSecret.

The new Deployment has no Kubernetes API token, runs non-root with a read-only
root filesystem, and allows DNS plus public HTTPS. Its digest-pinned Python
runtime executes only standard-library code with site-package loading disabled.
The official base includes pip dependencies with known findings; pip is unused.

Dry-run only reads the existing DNS-only A record. Later explicit write enablement
can PATCH only its content; absent, duplicate, proxied or mismatched records and
private/ambiguous target addresses are rejected. It never creates or deletes
records. ExternalDNS must continue to exclude this DDNS anchor.

A shell readiness check reads the last successful reconciliation timestamp. It
rejects missing, stale or future timestamps without importing Python or making
API requests. The previous standalone observer's Python health check took about
24 seconds on the cluster node despite passing under CI's CPU limit.

Run `DDNS_IMAGE_SMOKE=1 python3 -B tests/test-cloudflare-ddns.py` with Helm, yq,
kubeconform and Docker available. Tests prove enabling the observer leaves the
existing tunnel Deployment identical, validate read-only/write safeguards, and
exercise real health checks in the pinned runtime without network or credentials.
CI repeats runtime tests with the 100m CPU limit.

Delivery is staged: merge this chart change, then review the bootstrap update
that pins the reviewed commit, seals the namespace-specific credential, enables
the dry-run observer, and removes the old standalone Argo application. Do not
change DNS ownership, writes or nameservers as part of this move. Disabling only
`cloudflareDdns.enabled` removes this observer while preserving dogado and the
tunnel pod. No tunnel runtime rebuild or release is needed for this Git-pinned
chart update; keep the existing `tag` and `appVersion` at 0.0.9.
