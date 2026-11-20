#!/usr/bin/env python3
"""Shared remote-ping loop, credential transport and provider isolation tests."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import tempfile
import sys
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


updater = module('updater', ROOT / 'scripts/remote-ddns.py')
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


def fields():
    return ['https://dogado.example.test/?credential=fixture', 'fixture-token', Z, R,
            'www.example.test', '89.58.40.25', 'true', '60']


class SharedLoop(unittest.TestCase):
    def test_each_provider_is_attempted_even_if_other_fails(self):
        for failure in ('dogado', 'cloudflare', None):
            calls = []
            def ping(url):
                calls.append('dogado')
                if failure == 'dogado':
                    raise RuntimeError('sensitive error must not be logged')
            def update(*args):
                calls.append('cloudflare')
                if failure == 'cloudflare':
                    raise RuntimeError('sensitive error must not be logged')
                return {'event': 'unchanged'}
            events = updater.cycle(updater.configuration(fields()), ping, update)
            self.assertEqual(calls, ['dogado', 'cloudflare'])
            self.assertEqual([e['provider'] for e in events], calls)
            self.assertNotIn('sensitive', json.dumps(events))
            self.assertNotIn('fixture', json.dumps(events))

    def test_configuration_is_explicit_and_single_line(self):
        self.assertTrue(updater.configuration(fields())['dry_run'])
        f = fields(); f[6] = 'false'
        self.assertFalse(updater.configuration(f)['dry_run'])
        for index, value in [(0, 'file:///tmp/foo'), (1, ''), (1, 'token\ncode'),
                             (2, 'wrong'), (6, 'False'), (7, '0')]:
            f = fields(); f[index] = value
            with self.assertRaises(ValueError):
                updater.configuration(f)

    def test_dogado_url_is_stdin_not_argv_and_response_is_suppressed(self):
        with patch.object(updater.subprocess, 'run') as run:
            updater.dogado_ping(fields()[0])
            args, kwargs = run.call_args
            self.assertNotIn(fields()[0], args[0])
            self.assertIn(fields()[0], kwargs['input'])
            self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)

    def wrapper(self, overrides=None, expected_code=0):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name)
        (path / 'remote-ping.sh').write_text((ROOT / 'scripts/remote-ping.sh').read_text())
        (path / 'remote-ddns.py').write_text((ROOT / 'scripts/remote-ddns.py').read_text())
        (path / 'ssh-access.sh').write_text('''remote() {
  if [[ "$1" == python3* ]]; then
    [[ $(declare -p CF_API_TOKEN) != 'declare -x '* ]] || return 99
    [[ $(declare -p PING_URL) != 'declare -x '* ]] || return 99
    printf '%s' "$1" > "$TEST_DIR/command"
    cat > "$TEST_DIR/input"
    return "${MOCK_COMBINED_EXIT:-0}"
  fi
  echo legacy > "$TEST_DIR/legacy"
}
''')
        env = dict(os.environ, PING_URL=fields()[0], CF_API_TOKEN=fields()[1],
                   CF_ZONE_ID=Z, CF_RECORD_ID=R, CF_RECORD_NAME=fields()[4],
                   HOST=fields()[5], CF_DDNS_ENABLED='true', CF_DRY_RUN='true', TEST_DIR=str(path))
        env.update(overrides or {})
        result = subprocess.run(['bash', str(path / 'remote-ping.sh')], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected_code, result.stderr)
        self.assertNotIn(fields()[0], result.stdout + result.stderr)
        self.assertNotIn(fields()[1], result.stdout + result.stderr)
        return path

    def test_wrapper_sends_config_as_stdin_then_source_over_one_connection(self):
        path = self.wrapper()
        self.assertFalse((path / 'legacy').exists())
        command = (path / 'command').read_text()
        self.assertNotIn(fields()[0], command)
        self.assertNotIn(fields()[1], command)
        header = (path / 'input').read_text().splitlines()[:8]
        self.assertEqual(header, fields())
        self.assertIn('def cycle(', (path / 'input').read_text())
        # Execute the exact fixed SSH bootstrap against synthetic data and a
        # harmless program. No SSH, API, real token or remote host is involved.
        program = 'assert ddns_config == ' + repr(fields()) + '\n'
        result = subprocess.run(['bash', '-c', command], input='\n'.join(fields()) + '\n' + program,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ssh_failure_exits_so_both_providers_are_retried(self):
        path = self.wrapper({'MOCK_COMBINED_EXIT': '255'}, expected_code=255)
        self.assertFalse((path / 'legacy').exists())

    def test_missing_python_or_missing_token_falls_back_to_dogado(self):
        for overrides in ({'MOCK_COMBINED_EXIT': '127'}, {'CF_API_TOKEN': ''},
                          {'CF_API_TOKEN': 'bad\nvalue'}, {'CF_DDNS_ENABLED': 'false'}):
            path = self.wrapper(overrides)
            self.assertTrue((path / 'legacy').exists())


def documents(text):
    return json.loads(subprocess.check_output(['yq', 'eval-all', '-o=json', '. as $doc ireduce ([]; . + [$doc])', '-'], input=text, text=True))


def render(*args):
    command = ['helm', 'template', 'tunnel-ingress', str(ROOT / 'helm'), '--namespace', 'tunnel-ingress', *args]
    output = subprocess.check_output(command, text=True)
    assert output == subprocess.check_output(command, text=True)
    return [d for d in documents(output) if d], output


def contracts():
    baseline, _ = render()
    assert len(baseline) == 1 and baseline[0]['kind'] == 'Deployment'
    fixture = ['--set', 'cloudflareDdns.enabled=true', '--set', 'cloudflareDdns.zoneId=' + Z,
               '--set', 'cloudflareDdns.recordId=' + R, '--set', 'cloudflareDdns.recordName=www.example.test']
    docs, output = render(*fixture)
    assert len(docs) == 1 and docs[0]['metadata']['name'] == 'tunnel-ingress'
    pod = docs[0]['spec']['template']['spec']
    assert len(pod['containers']) == 1 and pod['containers'][0]['name'] == 'remote-ping'
    assert pod['initContainers'] == baseline[0]['spec']['template']['spec']['initContainers']
    env = {e['name']: e for e in pod['containers'][0]['env']}
    assert env['CF_DRY_RUN']['value'] == 'true'
    assert env['CF_API_TOKEN']['valueFrom']['secretKeyRef'] == {'name': 'cloudflare-api-token', 'key': 'api-token', 'optional': True}
    assert env['PING_URL'] == baseline[0]['spec']['template']['spec']['containers'][0]['env'][1]
    clean = copy.deepcopy(docs[0])
    clean['spec']['template']['spec']['containers'][0]['env'] = [e for e in clean['spec']['template']['spec']['containers'][0]['env'] if not e['name'].startswith('CF_')]
    assert clean == baseline[0], 'Cloudflare must add only environment inputs to existing remote-ping'
    assert not any(d['kind'] in ('Secret', 'SealedSecret', 'ConfigMap', 'NetworkPolicy') for d in docs)
    chart = documents((ROOT / 'helm/Chart.yaml').read_text())[0]
    values = documents((ROOT / 'helm/values.yaml').read_text())[0]
    assert str(chart['version']) == str(chart['appVersion']) == str(values['tag']) == '0.0.10'
    subprocess.run(['helm', 'lint', str(ROOT / 'helm'), *fixture], check=True)
    subprocess.run(['kubeconform', '-strict', '-summary', '-'], input=output, text=True, check=True)
    invalid = subprocess.run(['helm', 'template', 'test', str(ROOT / 'helm'), *fixture,
                              '--set-string', 'cloudflareDdns.dryRun=false'], capture_output=True)
    assert invalid.returncode != 0
    print('Passed: one existing pod/container/SSH loop, independent providers, stdin-only credential transport, dry-run default.')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    contracts()
