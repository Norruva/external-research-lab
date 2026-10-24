"""Shared test helpers: an in-memory server on a free port and a small JSON client."""
import json
import os
import socket
import sys
import uuid
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'examples'))
PROBES = REPO / 'dpp-rea' / 'aas-compat' / 'probes'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'

from aasforge.app import Config, Forge  # noqa: E402


def database() -> str:
    """A fresh database for one test: ``:memory:`` SQLite, or a new schema in the PostgreSQL
    database named by ``AASFORGE_TEST_DATABASE`` (e.g. postgresql://forge:forge@127.0.0.1/aasforge)."""
    dsn = os.environ.get('AASFORGE_TEST_DATABASE')
    if not dsn:
        return ':memory:'
    return f'{dsn}{"&" if "?" in dsn else "?"}aasforge_schema=t_{uuid.uuid4().hex[:12]}'


def store():
    from aasforge.store import Store
    return Store(database())


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Server:
    def __init__(self, **config):
        port = free_port()
        config.setdefault('database', database())
        self.forge = Forge(Config(host='127.0.0.1', port=port, **config))
        self.http = self.forge.serve()
        self.base = f'http://127.0.0.1:{port}'
        self.opener = urllib.request.build_opener(NoRedirect)

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.forge.store.db.drop_schema()

    def request(self, method, path, body=None, headers=None, raw=False):
        if body is None or isinstance(body, bytes):
            data = body
        else:
            data = json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data, {'Content-Type': 'application/json', **(headers or {})},
                                         method=method)
        try:
            with self.opener.open(request) as response:
                payload = response.read()
                return response.status, dict(response.headers), payload if raw else (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                parsed = json.loads(payload) if payload and not raw else payload
            except json.JSONDecodeError:
                parsed = payload
            return error.code, dict(error.headers), parsed

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)
