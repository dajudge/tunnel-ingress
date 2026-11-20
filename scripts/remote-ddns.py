#!/usr/bin/env python3
"""Reconcile one Cloudflare A record to the configured public tunnel host."""
import ipaddress
import json
import re
import socket
import subprocess
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


def configuration(fields):
    if len(fields) != 8 or any('\n' in f or '\r' in f for f in fields):
        raise ValueError('Expected eight single-line configuration fields')
    url, token, zone_id, record_id, name, host, mode, interval = fields
    if urllib.parse.urlsplit(url).scheme not in ('https', 'http'):
        raise ValueError('Expected an HTTP(S) dogado URL')
    if mode not in ('true', 'false') or not 30 <= int(interval) <= 3600:
        raise ValueError('Invalid dry-run mode or interval')
    if not all(re.fullmatch(r'[a-f0-9]{32}', value) for value in (zone_id, record_id)):
        raise ValueError('Expected explicit Cloudflare IDs')
    return dict(url=url, api=Cloudflare(token), zone_id=zone_id, record_id=record_id,
                name=name, host=host, dry_run=mode == 'true', interval=int(interval))


def dogado_ping(url):
    # Keep curl on the public tunnel host; credential-bearing URL goes to stdin.
    subprocess.run(['curl', '--silent', '--show-error', '--fail', '--max-time', '20', '--config', '-'],
                   input='url = ' + json.dumps(url) + '\n', text=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def cycle(config, ping=dogado_ping, update=reconcile):
    events = []
    try:
        ping(config['url'])
        events.append({'provider': 'dogado', 'event': 'pinged'})
    except Exception as error:
        events.append({'provider': 'dogado', 'event': 'failed', 'error': type(error).__name__})
    # A provider failure must not prevent attempting the other provider.
    try:
        result = update(config['api'], config['zone_id'], config['record_id'],
                        config['name'], config['host'], config['dry_run'])
        events.append(dict(result, provider='cloudflare', dry_run=config['dry_run']))
    except Exception as error:
        events.append({'provider': 'cloudflare', 'event': 'failed', 'error': type(error).__name__})
    return events


def main(fields):
    config = configuration(fields)
    while True:
        started = time.monotonic()
        for event in cycle(config):
            print(json.dumps(event), flush=True)
        time.sleep(max(1, config['interval'] - (time.monotonic() - started)))


if __name__ == '__main__':
    # The fixed SSH bootstrap reads data, then executes this public source.
    try:
        main(ddns_config)
    except ValueError:
        print(json.dumps({'event': 'invalid_configuration'}), flush=True)
        raise SystemExit(64)
