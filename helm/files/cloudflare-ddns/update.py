#!/usr/bin/env python3
"""Reconcile one Cloudflare A record to the configured public tunnel host."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import time
import urllib.parse
import urllib.request

API = 'https://api.cloudflare.com/client/v4'


def public_ipv4(host, resolve=socket.getaddrinfo):
    try:
        addresses = {str(ipaddress.ip_address(host))}
    except ValueError:
        addresses = {r[4][0] for r in resolve(host, None, socket.AF_INET, socket.SOCK_STREAM)}
    if len(addresses) != 1:
        raise ValueError('Tunnel host must resolve to exactly one IPv4 address')
    address = ipaddress.ip_address(addresses.pop())
    if address.version != 4 or not address.is_global:
        raise ValueError('Tunnel host address must be globally routable IPv4')
    return str(address)


class Cloudflare:
    def __init__(self, token):
        if not token or any(c.isspace() for c in token):
            raise ValueError("Expected a nonempty API token without whitespace")
        self.token = token

    def request(self, method, path, body=None):
        request = urllib.request.Request(API + path, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'},
            data=None if body is None else json.dumps(body).encode())
        # Never follow a redirect with the authorization header.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            result = json.load(response)
        if not result.get('success'):
            raise RuntimeError('Cloudflare API reported failure')
        return result['result']


def reconcile(api, zone_id, record_id, name, host, dry_run=True, resolve=socket.getaddrinfo):
    if not all(re.fullmatch(r'[a-f0-9]{32}', value) for value in (zone_id, record_id)):
        raise ValueError('Zone and record IDs must be explicit Cloudflare IDs')
    if host.lower().rstrip('.') == name.lower().rstrip('.'):
        raise ValueError('Tunnel host must not reference the DDNS record itself')
    address = public_ipv4(host, resolve)
    query = urllib.parse.urlencode({'type': 'A', 'name': name, 'per_page': 100})
    records = api.request('GET', f'/zones/{zone_id}/dns_records?{query}')
    if len(records) != 1:
        raise ValueError('Expected exactly one existing A record; refusing to create or choose')
    record = records[0]
    if (record['id'] != record_id or record['type'] != 'A'
            or record['name'].lower().rstrip('.') != name.lower().rstrip('.')
            or record.get('proxied', False)):
        raise ValueError('Existing DNS-only A record does not match the configured identity')
    if record['content'] == address:
        return {'event': 'unchanged', 'address': address}
    if dry_run:
        return {'event': 'would_update', 'address': address}
    # PATCH only content; preserve TTL, proxy state, comments and other record metadata.
    updated = api.request('PATCH', f'/zones/{zone_id}/dns_records/{record_id}', {'content': address})
    if updated.get('content') != address or updated.get('id') != record_id:
        raise RuntimeError('Cloudflare update response did not confirm the expected record')
    return {'event': 'updated', 'address': address}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    interval = int(os.environ.get('INTERVAL_SECONDS', '60'))
    if not 30 <= interval <= 3600:
        raise ValueError('Interval must be between 30 and 3600 seconds')
    state = Path('/state/last-success')
    # Require an explicit false to enable writes; no implicit truthiness conversion.
    mode = os.environ.get('DRY_RUN', 'true')
    if mode not in ('true', 'false'):
        raise ValueError('DRY_RUN must be true or false')
    api = Cloudflare(Path(os.environ['TOKEN_FILE']).read_text().strip())
    while True:
        try:
            result = reconcile(api, os.environ['ZONE_ID'], os.environ['RECORD_ID'],
                               os.environ['RECORD_NAME'], os.environ['TUNNEL_HOST'], mode == 'true')
            state.touch(mode=0o600)
            print(json.dumps(dict(result, dry_run=mode == 'true')), flush=True)
        except Exception as error:
            # Do not log response bodies, request headers, or exception text containing credentials.
            print(json.dumps({'event': 'cycle_failed', 'error': type(error).__name__}), flush=True)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return
        time.sleep(interval)


if __name__ == '__main__':
    main()
