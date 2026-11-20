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
        env = dict(os.environ, PATH=str(p) + ':' + os.environ['PATH'], PING_URL=url,
                   PING_CONFIG=config, SSH_CONNECTION=connection, TEST_DIR=tmp,
                   CAPTURE_ONLY='true' if capture else 'false')
        process = subprocess.Popen(['bash', str(p / 'remote-ping.sh')], env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, start_new_session=True)
        try:
            out, err = process.communicate(timeout=10)
        finally:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        return process.returncode, out, err, (p / 'argv').read_text(), (p / 'input').read_text() if capture else ''


class Pings(unittest.TestCase):
    def test_methods_headers_bodies_and_failure_isolation(self):
        for failed_path in ('/legacy', '/second'):
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
                code, out, err, argv, _ = run_script(base + '/legacy', config)
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

    def test_legacy_url_only_command_is_unchanged(self):
        code, _, _, argv, _ = run_script('https://example.test/ping', '', capture=True)
        self.assertEqual(code, 0)
        self.assertEqual(argv, 'while :; do date; curl \'https://example.test/ping\' || true; echo ""; sleep 60; done')

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
    chart = documents((ROOT / 'helm/Chart.yaml').read_text())[0]
    values = documents((ROOT / 'helm/values.yaml').read_text())[0]
    assert str(chart['version']) == str(chart['appVersion']) == str(values['tag']) == '0.0.10'
    subprocess.run(['helm', 'lint', str(ROOT / 'helm')], check=True)
    subprocess.run(['kubeconform', '-strict', '-summary', '-'], input=output, text=True, check=True)
    print('Passed: generic curl requests, legacy URL compatibility, method/body/header isolation, no provider-specific runtime.')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Pings))
    if not result.wasSuccessful(): raise SystemExit(1)
    contracts()
