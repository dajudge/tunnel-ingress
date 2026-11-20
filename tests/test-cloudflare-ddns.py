#!/usr/bin/env python3
"""Parallel DDNS safety tests, tunnel preservation and offline runtime health checks."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import unittest
import sys
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


updater = module('updater', ROOT / 'helm/files/cloudflare-ddns/update.py')
Z, R = 'a' * 32, 'b' * 32


class API:
    def __init__(self, **changes):
        self.record = dict(id=R, type='A', name='www.example.test', content='89.58.40.24', ttl=600, proxied=False)
        self.record.update(changes)
        self.calls = []
        self.records = [self.record]

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == 'GET':
            return self.records
        assert method == 'PATCH' and body == {'content': '89.58.40.25'}
        return dict(self.record, **body)


class DDNS(unittest.TestCase):
    def reconcile(self, api, **kwargs):
        return updater.reconcile(api, Z, R, 'www.example.test', '89.58.40.25', **kwargs)

    def test_default_is_read_only(self):
        api = API()
        self.assertEqual(self.reconcile(api)['event'], 'would_update')
        self.assertEqual([c[0] for c in api.calls], ['GET'])

    def test_explicit_write_changes_only_content(self):
        api = API()
        self.assertEqual(self.reconcile(api, dry_run=False)['event'], 'updated')
        self.assertEqual(api.calls[-1], ('PATCH', f'/zones/{Z}/dns_records/{R}', {'content': '89.58.40.25'}))

    def test_unchanged_has_no_write(self):
        api = API(content='89.58.40.25')
        self.assertEqual(self.reconcile(api, dry_run=False)['event'], 'unchanged')
        self.assertEqual(len(api.calls), 1)

    def test_missing_duplicate_foreign_proxied_records_fail_closed(self):
        for api in (API(id='c' * 32), API(proxied=True), API(type='CNAME'), API(name='other.example.test')):
            with self.assertRaises(ValueError):
                self.reconcile(api, dry_run=False)
            self.assertEqual(len(api.calls), 1)
        for records in ([], [API().record, API().record]):
            api = API()
            api.records = records
            with self.assertRaises(ValueError):
                self.reconcile(api, dry_run=False)
            self.assertEqual(len(api.calls), 1)

    def test_only_public_single_ipv4(self):
        for value in ('127.0.0.1', '10.1.2.3', '192.168.1.1', '0.0.0.0', '::1', '2001:4860:4860::8888'):
            with self.assertRaises(ValueError):
                updater.public_ipv4(value)
        def resolve(*args):
            return [(None, None, None, None, ('89.58.40.25', 0))]
        self.assertEqual(updater.public_ipv4('tunnel.example.test', resolve), '89.58.40.25')
        with self.assertRaises(ValueError):
            updater.public_ipv4('tunnel.example.test', lambda *a: [])
        with self.assertRaises(ValueError):
            updater.public_ipv4('tunnel.example.test', lambda *a: resolve() + [(None, None, None, None, ('89.58.40.26', 0))])
        with self.assertRaises(ValueError):
            updater.reconcile(API(), Z, R, 'www.example.test', 'www.example.test')


def documents(text):
    return json.loads(subprocess.check_output(['yq', 'eval-all', '-o=json', '. as $doc ireduce ([]; . + [$doc])', '-'], input=text, text=True))


def render(*args):
    command = ['helm', 'template', 'tunnel-ingress', str(ROOT / 'helm'), '--namespace', 'tunnel-ingress', *args]
    output = subprocess.check_output(command, text=True)
    assert output == subprocess.check_output(command, text=True)
    return [d for d in documents(output) if d], output


def contracts():
    baseline, _ = render()
    assert len(baseline) == 1 and baseline[0]['metadata']['name'] == 'tunnel-ingress'
    fixtures = ['--set', 'cloudflareDdns.enabled=true', '--set', 'cloudflareDdns.zoneId=' + Z,
                '--set', 'cloudflareDdns.recordId=' + R, '--set', 'cloudflareDdns.recordName=www.example.test']
    docs, rendered = render(*fixtures)
    tunnel = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'tunnel-ingress')
    assert tunnel == baseline[0], 'Enabling Cloudflare must not change the existing tunnel pod'
    assert tunnel['spec']['template']['spec']['containers'][0]['name'] == 'remote-ping'
    assert len(docs) == 4
    assert all(d['metadata']['namespace'] == 'tunnel-ingress' for d in docs)
    assert not any(d['kind'] in ('Secret', 'SealedSecret', 'Role', 'ClusterRole') for d in docs)
    deployment = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cloudflare-ddns')
    pod = deployment['spec']['template']['spec']
    assert deployment['spec']['strategy']['type'] == 'Recreate'
    assert pod['automountServiceAccountToken'] is False
    assert pod['securityContext']['runAsNonRoot'] is True
    container = pod['containers'][0]
    env = {e['name']: e['value'] for e in container['env']}
    assert env['DRY_RUN'] == 'true'
    assert env['TUNNEL_HOST'] == 'ip or hostname of the external host'
    assert container['readinessProbe']['exec']['command'] == ['sh', '/app/health.sh']
    assert container['readinessProbe']['timeoutSeconds'] == 10
    assert container['securityContext']['readOnlyRootFilesystem'] is True
    assert '@sha256:' in container['image']
    policy = next(d for d in docs if d['kind'] == 'NetworkPolicy')
    assert policy['spec']['podSelector']['matchLabels'] == {'app': 'cloudflare-ddns'}
    assert policy['spec']['ingress'] == [] and len(policy['spec']['egress']) == 2
    overrides, _ = render(*fixtures, '--set', 'host=89.58.40.25', '--set', 'cloudflareDdns.dryRun=false')
    for d in overrides:
        if d['kind'] == 'Deployment':
            c = d['spec']['template']['spec']['containers'][0]
            values = {e['name']: e.get('value') for e in c.get('env', [])}
            assert values.get('HOST', values.get('TUNNEL_HOST')) == '89.58.40.25'
            if d['metadata']['name'] == 'cloudflare-ddns':
                assert values['DRY_RUN'] == 'false'
    subprocess.run(['helm', 'lint', str(ROOT / 'helm'), *fixtures], check=True)
    subprocess.run(['kubeconform', '-strict', '-summary', '-'], input=rendered, text=True, check=True)
    # A string "false" must never become an implicit write-enabled boolean.
    invalid = subprocess.run(['helm', 'template', 'test', str(ROOT / 'helm'), *fixtures,
                              '--set-string', 'cloudflareDdns.dryRun=false'], capture_output=True)
    assert invalid.returncode != 0
    if os.environ.get('DDNS_IMAGE_SMOKE') == '1':
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            for path in ['helm/files/cloudflare-ddns/update.py', 'helm/files/cloudflare-ddns/health.sh',
                         'tests/test-cloudflare-ddns.py']:
                tar.add(ROOT / path, arcname=path)
        # CPU quota is exercised on GitHub; the local Docker host has threaded cgroups.
        subprocess.run(['docker', 'run', '--rm', '-i', '--network=none', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--user=65532:65532',
            *(['--cpus=0.1'] if os.environ.get('CI') == 'true' else []),
            '--tmpfs=/work:uid=65532,gid=65532', '--tmpfs=/state:uid=65532,gid=65532',
            '-w', '/work', container['image'], 'sh', '-ec',
            'tar xf -; python3 -B tests/test-cloudflare-ddns.py --unit-only; '
            'check_failure() { status=0; timeout 10 sh helm/files/cloudflare-ddns/health.sh || status=$?; test "$status" -eq 1; }; '
            'check_failure; touch /state/last-success; timeout 10 sh helm/files/cloudflare-ddns/health.sh; '
            'touch -t 202001010000 /state/last-success; check_failure; '
            'touch -t 209901010000 /state/last-success; check_failure; '
            'touch /state/last-success; INTERVAL_SECONDS=invalid check_failure'], input=archive.getvalue(), check=True)
    print('Passed: default disabled, unchanged dogado/tunnel Deployment, shared host, dry-run, scoped pod and fast health checks.')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    if '--unit-only' not in sys.argv:
        contracts()
