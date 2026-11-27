#!/usr/bin/env python3
"""Exercise actual curl against local HTTP fixtures; no SSH or real credentials."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


def run_script(url, config, connection='192.0.2.1 12345 89.58.40.25 22', capture=False):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / 'remote-ping.sh').write_text((ROOT / 'scripts/remote-ping.sh').read_text())
        (p / 'ssh-access.sh').write_text('''remote() {
  printf '%s' "$1" > "$TEST_DIR/argv"
  if [[ "$CAPTURE_ONLY" == true ]]; then cat > "$TEST_DIR/input"; return; fi
  command=${1/while :/for once in 1}
  bash -c "$command"
}
''')
        (p / 'sleep').write_text('#!/bin/sh\nexit 0\n')
        (p / 'sleep').chmod(0o755)
        env = dict(os.environ, PATH=str(p) + ':' + os.environ['PATH'], PING_URL=url or '',
                   PING_CONFIG=config, SSH_CONNECTION=connection, TEST_DIR=tmp,
                   CAPTURE_ONLY='true' if capture else 'false')
        if url is None:
            env.pop('PING_URL', None)
        process = subprocess.Popen(['bash', str(p / 'remote-ping.sh')], env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, start_new_session=True)
        try:
            out, err = process.communicate(timeout=10)
        finally:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        return process.returncode, out, err, (p / 'argv').read_text() if (p / 'argv').exists() else '', (p / 'input').read_text() if capture else ''


class Pings(unittest.TestCase):
    def test_methods_headers_bodies_and_failure_isolation(self):
        for failed_path, native_get in [(p, n) for p in ('/legacy', '/second') for n in (False, True)]:
            requests = []
            class Handler(BaseHTTPRequestHandler):
                def record(self):
                    requests.append((self.path, self.command, self.headers.get('Authorization'),
                                     self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()))
                    self.send_response(500 if self.path == failed_path else 200)
                    self.end_headers()
                do_GET = do_PATCH = record
                def log_message(self, *args): pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                base = 'http://127.0.0.1:' + str(server.server_port)
                body = '{"content":"{{SSH_SERVER_IP}}","literal":"$(echo never-executed)"}'
                config = '\n'.join(['url = ' + json.dumps(base + '/second'), 'request = "PATCH"',
                    'header = "Authorization: Bearer fixture-token"', 'data = ' + json.dumps(body),
                    'silent', 'show-error', 'fail', 'max-time = 2', 'output = "/dev/null"', 'next',
                    'url = ' + json.dumps(base + '/third'), 'request = "GET"',
                    'silent', 'show-error', 'fail', 'max-time = 2', 'output = "/dev/null"'])
                legacy_url = base + '/legacy'
                if native_get:
                    config = '\n'.join(['url = ' + json.dumps(legacy_url), 'request = "GET"',
                        'silent', 'show-error', 'fail', 'max-time = 2', 'output = "/dev/null"', 'next', config])
                    legacy_url = None
                code, out, err, argv, _ = run_script(legacy_url, config)
                self.assertEqual(code, 0, err)
                self.assertEqual(requests, [('/legacy', 'GET', None, ''),
                    ('/second', 'PATCH', 'Bearer fixture-token', '{"content":"89.58.40.25","literal":"$(echo never-executed)"}'),
                    ('/third', 'GET', None, '')])
                self.assertNotIn('fixture-token', argv + out + err)
                self.assertNotIn(config, argv)
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_secret_config_is_stdin_and_url_escaping_is_literal(self):
        url = 'https://example.test/?key="quoted\\value"'
        config = 'url = "https://other.example.test/"\nheader = "X-Key: fixture-token"'
        code, out, err, argv, data = run_script(url, config, capture=True)
        self.assertEqual(code, 0, err)
        self.assertNotIn('fixture-token', argv + out + err)
        self.assertNotIn(url, argv)
        self.assertIn('url = "https://example.test/?key=\\"quoted\\\\value\\""', data)
        self.assertTrue(data.endswith(config + '\n'))

    def test_url_only_and_native_get_use_the_same_loop(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get('Authorization')))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'response-must-not-be-logged')
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            url = 'http://127.0.0.1:' + str(server.server_port) + '/get?token=fixture-token&ip={{SSH_SERVER_IP}}'
            native = '\n'.join(['url = ' + json.dumps(url), 'request = "GET"',
                'silent', 'show-error', 'fail', 'max-time = 20', 'output = "/dev/null"'])
            commands = []
            for legacy_url, config in ((url, ''), (None, native)):
                code, out, err, argv, _ = run_script(legacy_url, config)
                self.assertEqual(code, 0, err)
                self.assertNotIn('fixture-token', argv + out + err)
                self.assertNotIn('response-must-not-be-logged', out + err)
                commands.append(argv)
            self.assertEqual(requests, [('/get?token=fixture-token&ip=89.58.40.25', None)] * 2)
            self.assertEqual(commands[0], commands[1])
            code, _, _, _, data = run_script(url, '', capture=True)
            self.assertEqual(code, 0)
            self.assertNotIn('next', data)
            self.assertIn('max-time = 20', data)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_config_only_with_absent_or_empty_url(self):
        for url in (None, ''):
            requests = []
            class Handler(BaseHTTPRequestHandler):
                def do_PATCH(self):
                    requests.append((self.path, self.headers.get('Authorization'),
                        self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()))
                    self.send_response(200)
                    self.end_headers()
                def log_message(self, *args): pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                config = '\n'.join(['url = "http://127.0.0.1:' + str(server.server_port) + '/only"',
                    'request = "PATCH"', 'header = "Authorization: Bearer fixture-token"',
                    'data = ' + json.dumps('{"content":"{{SSH_SERVER_IP}}"}'),
                    'silent', 'show-error', 'fail', 'output = "/dev/null"', 'max-time = 2'])
                code, out, err, argv, _ = run_script(url, config)
                self.assertEqual(code, 0, err)
                self.assertEqual(requests, [('/only', 'Bearer fixture-token', '{"content":"89.58.40.25"}')])
                self.assertNotIn('fixture-token', argv + out + err)
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_no_inputs_rejected_before_ssh(self):
        for url in (None, ''):
            code, _, err, argv, _ = run_script(url, '')
            self.assertNotEqual(code, 0)
            self.assertIn('Configure PING_URL or PING_CONFIG', err)
            self.assertEqual(argv, '')

    def test_missing_address_rejects_placeholder_before_http(self):
        code, _, err, _, _ = run_script('https://example.test/', 'url="https://example.test/{{SSH_SERVER_IP}}"', connection='')
        self.assertNotEqual(code, 0)
        self.assertIn('SSH server address is unavailable', err)


def documents(text):
    return json.loads(subprocess.check_output(['yq', 'ea', '-o=json', '. as $doc ireduce ([]; . + [$doc])', '-'], input=text, text=True))


def contracts():
    def render(*args):
        command = ['helm', 'template', 'tunnel-ingress', str(ROOT / 'helm'), '--namespace', 'tunnel-ingress', *args]
        output = subprocess.check_output(command, text=True)
        assert output == subprocess.check_output(command, text=True)
        return documents(output), output
    baseline, _ = render()
    enabled, output = render('--set', 'pingConfigSecret.name=http-pings')
    assert len(baseline) == len(enabled) == 1
    pod = enabled[0]['spec']['template']['spec']
    assert len(pod['containers']) == 1 and pod['containers'][0]['name'] == 'remote-ping'
    added = pod['containers'][0]['env'].pop()
    assert added == {'name': 'PING_CONFIG', 'valueFrom': {'secretKeyRef': {'name': 'http-pings', 'key': 'config'}}}
    assert enabled == baseline, 'Only an optional generic Secret reference may change the existing pod'
    assert not any(d['kind'] != 'Deployment' for d in enabled)
    config_only, config_output = render('--set', 'pingConfigSecret.name=http-pings', '--set', 'pingSecret.name=')
    dual, _ = render('--set', 'pingConfigSecret.name=http-pings')
    expected = copy.deepcopy(dual)
    c = expected[0]['spec']['template']['spec']['containers'][0]
    c['env'] = [e for e in c['env'] if e['name'] != 'PING_URL']
    assert config_only == expected, 'Config-only must remove only the legacy URL environment input'
    invalid = subprocess.run(['helm', 'template', 'test', str(ROOT / 'helm'), '--set', 'pingSecret.name='], capture_output=True, text=True)
    assert invalid.returncode != 0 and 'Configure pingSecret.name or pingConfigSecret.name' in invalid.stderr
    subprocess.run(['kubeconform', '-strict', '-summary', '-'], input=config_output, text=True, check=True)
    chart = documents((ROOT / 'helm/Chart.yaml').read_text())[0]
    values = documents((ROOT / 'helm/values.yaml').read_text())[0]
    assert str(chart['version']) == str(chart['appVersion']) == str(values['tag']) == '0.0.11'
    subprocess.run(['helm', 'lint', str(ROOT / 'helm')], check=True)
    subprocess.run(['kubeconform', '-strict', '-summary', '-'], input=output, text=True, check=True)
    print('Passed: generic curl requests, legacy URL compatibility, method/body/header isolation, no provider-specific runtime.')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Pings))
    if not result.wasSuccessful(): raise SystemExit(1)
    contracts()
