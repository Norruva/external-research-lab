"""Several worker processes sharing one socket and one database (SQLite file and PostgreSQL)."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from helpers import ROOT

import battery_passport

from aasforge import security
from aasforge.canonical import b64url_encode as enc

SECRET = 'workers-secret'
OPERATOR = {'Authorization': 'Bearer ' + security.make_hs256_token({'sub': 'op', 'dpp_tier': 'economic-operator'}, SECRET, 3600)}


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def call(base, method, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data, {'Content-Type': 'application/json', **(headers or {})},
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class Workers:
    def __init__(self, database, folder):
        self.port = free_port()
        self.base = f'http://127.0.0.1:{self.port}'
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'aasforge', '--data-dir', folder, '--database', database, 'serve', '--port',
             str(self.port), '--workers', '3', '--access-rules', 'dpp', '--secret', SECRET, '--public-url', self.base],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(150):
            try:
                if call(self.base, 'GET', '/api/v3.1/health')[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError('workers did not start: ' + self.process.stdout.read(2000).decode(errors='replace'))

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(20)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, 9)


class WorkerTests:
    database = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workers = Workers(self.database(self.tmp.name), self.tmp.name)

    def tearDown(self):
        self.workers.close()
        self.tmp.cleanup()

    def test_event_stream_sees_every_workers_changes(self):
        import http.client
        base = self.workers.base
        for n in range(3):          # warm up all workers
            call(base, 'GET', '/api/v3.1/health')
        connection = http.client.HTTPConnection('127.0.0.1', self.workers.port, timeout=10)
        connection.request('GET', '/api/v3.1/events', headers={'Accept': 'text/event-stream', **OPERATOR})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        ids = [f'urn:example:workers:stream:{n}' for n in range(12)]
        for identifier in ids:      # separate connections: the listener's worker writes only some of these
            call(base, 'POST', '/api/v3.1/submodels', {'modelType': 'Submodel', 'id': identifier}, OPERATOR)
        seen, deadline = set(), time.monotonic() + 8
        while time.monotonic() < deadline and not set(ids) <= seen:
            line = response.fp.readline().decode()
            if line.startswith('data:'):
                seen.add(json.loads(line[5:])['id'])
        connection.close()
        self.assertTrue(set(ids) <= seen, sorted(set(ids) - seen))

    def test_concurrent_writes_and_shared_state(self):
        base = self.workers.base
        sm = 'urn:example:workers:sm'
        body = {'modelType': 'Submodel', 'id': sm, 'idShort': 'Counter', 'submodelElements': [
            {'modelType': 'Property', 'idShort': 'N', 'valueType': 'xs:int', 'value': '0'}]}
        self.assertEqual(call(base, 'POST', '/api/v3.1/submodels', body, OPERATOR)[0], 201)
        for _ in range(9):          # every worker compiles its tier rules before the passport exists
            call(base, 'GET', f'/api/v3.1/submodels/{enc(sm)}')
        errors = []

        def writer(offset):
            for n in range(10):
                status, _ = call(base, 'PATCH', f'/api/v3.1/submodels/{enc(sm)}/submodel-elements/N/$value',
                                 offset * 100 + n + 1, OPERATOR)
                if status != 204:
                    errors.append(status)
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        _, audit = call(base, 'GET', f'/api/v3.1/submodels/{enc(sm)}/$audit', headers=OPERATOR)
        self.assertEqual([r['revision'] for r in audit['revisions']], list(range(1, 82)))

        spec = battery_passport.spec(base=base)
        status, created = call(base, 'POST', '/dpp/passports', spec, OPERATOR)
        self.assertEqual(status, 201, created)
        hidden = next(i for i in created['submodels'] if i.endswith(':2'))
        time.sleep(1.5)             # watchers deliver the change to the other workers
        for _ in range(12):         # whichever worker answers, the authority-only submodel stays hidden
            self.assertEqual(call(base, 'GET', f'/api/v3.1/submodels/{enc(hidden)}')[0], 403)
            _, view = call(base, 'GET', f'/dpp/passports/{enc(created["aasId"])}')
            self.assertNotIn('RemainingCapacity', json.dumps(view))
        latest = {max(e['seq'] for e in call(base, 'GET', '/api/v3.1/events?since=0', headers=OPERATOR)[1]['events'])
                  for _ in range(9)}
        self.assertEqual(len(latest), 1)


class SqliteWorkers(WorkerTests, unittest.TestCase):
    @staticmethod
    def database(folder):
        return str(Path(folder) / 'shared.sqlite3')


@unittest.skipUnless(os.environ.get('AASFORGE_TEST_DATABASE'), 'set AASFORGE_TEST_DATABASE for PostgreSQL')
class PostgresWorkers(WorkerTests, unittest.TestCase):
    @staticmethod
    def database(folder):
        dsn = os.environ['AASFORGE_TEST_DATABASE']
        return f'{dsn}{"&" if "?" in dsn else "?"}aasforge_schema=w_{uuid.uuid4().hex[:10]}'


if __name__ == '__main__':
    unittest.main()
