"""Minimal HTTP framework on the standard library: routing, request/response objects, the AAS API
``Result`` error format, multipart parsing and a threaded server with Server-Sent Events."""
from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

MAX_BODY = 512 * 1024 * 1024


class HttpError(Exception):
    def __init__(self, status: int, text: str, code: str | None = None, details: list | None = None):
        super().__init__(text)
        self.status, self.text, self.code, self.details = status, text, code, details or []


def result_messages(status: int, text: str, code: str | None = None, details: list | None = None) -> dict:
    """AAS API Part 2 ``Result`` object."""
    stamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    messages = [{'code': code or str(status), 'correlationId': str(uuid.uuid4()),
                 'messageType': 'Error' if status >= 400 else 'Info', 'text': text, 'timestamp': stamp}]
    for detail in details or []:
        messages.append({'code': 'detail', 'messageType': 'Warning' if status < 400 else 'Error',
                         'text': detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False),
                         'timestamp': stamp})
    return {'messages': messages}


@dataclass
class Request:
    method: str
    path: str
    query: dict
    headers: dict
    body: bytes = b''
    params: dict = field(default_factory=dict)
    subject: object = None
    base: str = ''

    def arg(self, name, default=None):
        values = self.query.get(name)
        return values[0] if values else default

    def args(self, name):
        return self.query.get(name, [])

    def json(self):
        if not self.body:
            raise HttpError(400, 'request body is empty')
        try:
            return json.loads(self.body)
        except json.JSONDecodeError as error:
            raise HttpError(400, f'request body is not JSON: {error}') from error

    def accept(self, *options):
        header = self.headers.get('Accept') or self.headers.get('accept') or ''
        for option in options:
            if option in header:
                return option
        return options[0]

    def multipart(self) -> dict:
        content_type = self.headers.get('Content-Type') or self.headers.get('content-type') or ''
        if 'multipart/form-data' not in content_type:
            raise HttpError(400, 'expected multipart/form-data')
        message = BytesParser(policy=default_policy).parsebytes(
            b'Content-Type: ' + content_type.encode() + b'\r\nMIME-Version: 1.0\r\n\r\n' + self.body)
        parts = {}
        for part in message.iter_parts():
            name = part.get_param('name', header='content-disposition')
            parts[name] = {'filename': part.get_filename(), 'content_type': part.get_content_type(),
                           'data': part.get_payload(decode=True) or b''}
        return parts


@dataclass
class Response:
    status: int = 200
    body: bytes = b''
    content_type: str = 'application/json'
    headers: dict = field(default_factory=dict)
    stream: object = None   # generator of bytes for SSE

    @classmethod
    def json(cls, data, status=200, headers=None):
        return cls(status, json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json', headers or {})

    @classmethod
    def text(cls, text, status=200, content_type='text/plain; charset=utf-8'):
        return cls(status, text.encode('utf-8'), content_type)

    @classmethod
    def empty(cls, status=204):
        return cls(status, b'', '')


class Router:
    def __init__(self):
        self.routes = []

    def add(self, method: str, pattern: str, handler, name: str | None = None):
        """``pattern`` uses ``{name}`` for one segment and ``{name:path}`` for the rest."""
        pieces = []
        for piece in re.split(r'(\{\w+(?::path)?\})', pattern):
            placeholder = re.fullmatch(r'\{(\w+)(:path)?\}', piece)
            if placeholder:
                pieces.append(f'(?P<{placeholder.group(1)}>.+)' if placeholder.group(2) else
                              f'(?P<{placeholder.group(1)}>[^/]+)')
            else:
                pieces.append(re.escape(piece))
        self.routes.append((method, re.compile('^' + ''.join(pieces) + '$'), handler, name or pattern, pattern))

    def match(self, method: str, path: str):
        allowed = set()
        for route_method, regex, handler, name, pattern in self.routes:
            m = regex.match(path)
            if m:
                if route_method == method:
                    return handler, m.groupdict(), pattern
                allowed.add(route_method)
        if allowed:
            raise HttpError(405, f'method {method} not allowed; allowed: {", ".join(sorted(allowed))}')
        raise HttpError(404, f'no such resource: {path}')


class App:
    """WSGI-less application: ``handle(Request) -> Response``."""

    def __init__(self, router: Router, bases=('/api/v3.1', '/api/v3.0', '/api/v3.2'), cors_origins=()):
        self.router = router
        self.bases = bases
        self.cors_origins = set(cors_origins)
        self.before = []   # callables(request) run before routing (authentication)

    def handle(self, request: Request) -> Response:
        try:
            for hook in self.before:
                hook(request)
            path = request.path
            for base in self.bases:
                if path == base or path.startswith(base + '/'):
                    request.base = base
                    path = path[len(base):] or '/'
                    break
            handler, params, _pattern = self.router.match(request.method, path)
            request.params = params
            request.path = path
            response = handler(request)
        except HttpError as error:
            response = Response.json(result_messages(error.status, error.text, error.code, error.details), error.status)
        except PermissionError as error:
            status = 401 if 'token' in str(error) or 'API key' in str(error) else 403
            response = Response.json(result_messages(status, str(error) or 'forbidden'), status)
        origin = request.headers.get('Origin') or request.headers.get('origin')
        if origin and origin in self.cors_origins:
            response.headers.setdefault('Access-Control-Allow-Origin', origin)
            response.headers.setdefault('Vary', 'Origin')
        return response


def serve(app: App, host: str = '127.0.0.1', port: int = 8081):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'aasforge'

        def _dispatch(self):
            split = urlsplit(self.path)
            length = int(self.headers.get('Content-Length') or 0)
            if length > MAX_BODY:
                self.send_error(413)
                return
            body = self.rfile.read(length) if length else b''
            request = Request(self.command, split.path, parse_qs(split.query, keep_blank_values=True),
                              {k: v for k, v in self.headers.items()}, body)
            response = app.handle(request)
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            if response.stream is not None:
                self.send_header('Content-Type', response.content_type)
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    for chunk in response.stream:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True
                return
            if response.content_type:
                self.send_header('Content-Type', response.content_type)
            self.send_header('Content-Length', str(len(response.body)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(response.body)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _dispatch

        def do_OPTIONS(self):
            self.send_response(204)
            origin = self.headers.get('Origin')
            if origin and origin in app.cors_origins:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type, X-API-Key')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
