"""Signed loopback transport for the original question-conditioned model.

This single-process development service has no remote model client or action
executor. Keys/nonces are local process state; persistent tenant governance,
TLS termination, production load/recovery and policy qualification are separate.
"""
import argparse
import hashlib
import hmac
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import sys
import threading
import time

from .contracts import parse_json
from .decision_contract import MAX_REQUEST_BYTES
from .service import signature

FORMAT = 'eidos-decision-service-v2'
KEY = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
ASSETS = {'/': ('decision.html', 'text/html; charset=utf-8'),
          '/decision.js': ('decision.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}
AUTH_HEADERS = ('X-Eidos-Key', 'X-Eidos-Time', 'X-Eidos-Nonce', 'X-Eidos-Signature')


def _private_config(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('private_configuration_required')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o077 or not 0 < before.st_size <= 64000:
        raise ValueError('private_configuration_required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(64001)
        after = os.fstat(stream.fileno())
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or len(raw) != before.st_size:
        raise ValueError('configuration_changed_during_read')
    return parse_json(raw.decode('utf-8'), maximum=64000)


def initialize_local(config_path, registry, *, key_id='decision-operator'):
    """Explicitly create a new private config; never read an existing key file."""
    from .decision import DecisionEngine
    if not isinstance(key_id, str) or not KEY.fullmatch(key_id):
        raise ValueError('invalid_key_identifier')
    registry = Path(registry)
    if not registry.is_absolute():
        registry = Path.cwd() / registry
    if any(p.is_symlink() for p in (registry, *registry.parents)):
        raise ValueError('registry_symlink_refused')
    engine = DecisionEngine.from_registry(registry)
    pin = getattr(engine, 'registry_sha256', None)
    if not isinstance(pin, str) or not HEX.fullmatch(pin):
        raise ValueError('registry_pin_unavailable')
    config = {'format': FORMAT, 'registry': str(registry), 'registry_sha256': pin,
              'key_id': key_id, 'secret': secrets.token_hex(32)}
    destination = Path(config_path)
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('configuration_symlink_refused')
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(config, sort_keys=True, indent=2) + '\n')
    return {'configuration_created': str(destination), 'key_values_printed': False,
            'registry_sha256': pin, 'scope': 'loopback_development_review_only'}


class DecisionApplication:
    def __init__(self, config_path, *, engine_factory=None, clock=time.time):
        config = _private_config(config_path)
        fields = {'format', 'registry', 'registry_sha256', 'key_id', 'secret'}
        if type(config) is not dict or set(config) != fields or config['format'] != FORMAT:
            raise ValueError('unsupported_service_configuration')
        if (not isinstance(config['key_id'], str) or not KEY.fullmatch(config['key_id']) or
                not isinstance(config['secret'], str) or not HEX.fullmatch(config['secret']) or
                not isinstance(config['registry_sha256'], str) or not HEX.fullmatch(config['registry_sha256']) or
                not isinstance(config['registry'], str) or not Path(config['registry']).is_absolute()):
            raise ValueError('invalid_service_configuration')
        if engine_factory is None:
            from .decision import DecisionEngine
            engine_factory = DecisionEngine.from_registry
        self.engine = engine_factory(config['registry'], expected_registry_sha256=config['registry_sha256'])
        self.key_id, self.secret = config['key_id'], bytes.fromhex(config['secret'])
        self.clock = clock
        self.nonces = {}
        self.rate_minute, self.rate_count = -1, 0
        self.registry_sha256 = config['registry_sha256']

    def authenticate(self, headers, method, path, body):
        if any(len(headers.get_all(name, [])) != 1 for name in AUTH_HEADERS):
            raise PermissionError('authentication_required')
        key_id, timestamp, nonce, mac = [headers[name] for name in AUTH_HEADERS]
        if (key_id != self.key_id or not re.fullmatch(r'[0-9]{10}', timestamp) or
                not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', nonce) or not HEX.fullmatch(mac)):
            raise PermissionError('authentication_failed')
        now = self.clock()
        if abs(now - int(timestamp)) > 60:
            raise PermissionError('authentication_expired')
        expected = signature(self.secret, key_id, timestamp, nonce, method, path, body)
        if not hmac.compare_digest(mac, expected):
            raise PermissionError('authentication_failed')
        self.nonces = {n: deadline for n, deadline in self.nonces.items() if deadline >= now}
        if nonce in self.nonces:
            raise PermissionError('replayed_request')
        minute = int(now // 60)
        if minute != self.rate_minute:
            self.rate_minute, self.rate_count = minute, 0
        if self.rate_count >= 120 or len(self.nonces) >= 2048:
            raise PermissionError('request_rate_exceeded')
        self.rate_count += 1
        # Retain through the complete timestamp acceptance interval, including
        # a permitted future timestamp; expiry is not merely now + 60.
        self.nonces[nonce] = int(timestamp) + 60

    def handle(self, method, path, headers, body):
        if type(body) is not bytes or len(body) > MAX_REQUEST_BYTES:
            raise ValueError('request_size_bound')
        if (method, path) not in {('POST', '/v2/decisions'), ('GET', '/v2/model')}:
            return 404, {'error': 'route_unavailable'}
        if method == 'GET' and body:
            raise ValueError('GET_body_refused')
        self.authenticate(headers, method, path, body)
        if method == 'GET':
            return 200, {'format': 'eidos-decision-model-response-v2',
                         'model': self.engine.identity, 'registry_sha256': self.registry_sha256,
                         'review_required': True, 'calibration_status': 'unfitted',
                         'authorization': 'recommendation_only_no_action_authorized'}
        return 200, self.engine.decide(body)


class DecisionServer(http.server.HTTPServer):
    request_queue_size = 8
    allow_reuse_address = False

    def __init__(self, address, app):
        if address[0] != '127.0.0.1' or type(address[1]) is not int or not 0 <= address[1] <= 65535:
            raise ValueError('loopback_bind_required')
        self.app = app
        self.web = Path(__file__).resolve().parents[1] / 'web'
        super().__init__(address, DecisionHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address

    def handle_error(self, request, client_address):
        pass  # Parser or model exception values must never become access logs.


class DecisionHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'EIDOS'
    sys_version = ''

    def log_message(self, *_):
        pass

    def handle(self):
        def expire():
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(8, expire)
        timer.daemon = True
        timer.start()
        try:
            super().handle()
        finally:
            timer.cancel()

    def handle_expect_100(self):
        self.respond(417, {'error': 'unsupported_expectation'})
        return False

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {'error': 'invalid_http_request'})

    def respond(self, code, value, content_type='application/json; charset=utf-8'):
        payload = value if isinstance(value, bytes) else json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
        if len(payload) > 2_000_000:
            code, payload = 503, b'{"error":"response_size_bound"}'
            content_type = 'application/json; charset=utf-8'
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def dispatch(self):
        try:
            if self.request_version != 'HTTP/1.1' or len(self.headers.get_all('Host', [])) != 1:
                raise ValueError('host_required')
            port = self.server.server_address[1]
            if self.headers['Host'] not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
                raise PermissionError('host_denied')
            if (self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Content-Encoding') or
                    len(self.headers.get_all('Content-Length', [])) > 1):
                raise ValueError('ambiguous_framing')
            length = self.headers.get('Content-Length', '0')
            if not re.fullmatch(r'[0-9]{1,7}', length) or int(length) > MAX_REQUEST_BYTES:
                self.respond(413, {'error': 'body_limit'})
                return
            if self.command == 'GET' and int(length):
                raise ValueError('GET_body_refused')
            if self.command == 'POST' and (len(self.headers.get_all('Content-Type', [])) != 1 or
                                            self.headers['Content-Type'].lower() != 'application/json'):
                raise ValueError('JSON_required')
            if 'Origin' in self.headers:
                if (len(self.headers.get_all('Origin', [])) != 1 or
                        self.headers['Origin'] != 'http://' + self.headers['Host']):
                    raise PermissionError('origin_denied')
            if self.headers.get_all('Expect'):
                raise ValueError('expectation_refused')
            body = self.rfile.read(int(length))
            if len(body) != int(length):
                raise ValueError('incomplete_request')
            if self.command == 'GET' and self.path == '/healthz':
                self.respond(200, {'status': 'ok', 'service': FORMAT, 'review_required': True})
            elif self.command == 'GET' and self.path in ASSETS:
                name, content_type = ASSETS[self.path]
                self.respond(200, (self.server.web / name).read_bytes(), content_type)
            else:
                code, response = self.server.app.handle(self.command, self.path, self.headers, body)
                self.respond(code, response)
        except PermissionError as error:
            allowed = {'authentication_required', 'authentication_failed', 'authentication_expired',
                       'replayed_request', 'request_rate_exceeded', 'host_denied', 'origin_denied'}
            reason = str(error) if str(error) in allowed else 'authentication_failed'
            self.respond(429 if reason == 'request_rate_exceeded' else 403, {'error': reason})
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
            self.respond(400, {'error': 'invalid_request'})
        except (TimeoutError, socket.timeout):
            self.respond(408, {'error': 'request_timeout'})
        except (OSError, RuntimeError):
            self.respond(503, {'error': 'service_unavailable'})

    do_GET = dispatch
    do_POST = dispatch


def main():
    parser = argparse.ArgumentParser(description='Original EIDOS decision model: signed loopback development service')
    parser.add_argument('--config', required=True)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--init-local', action='store_true', help='Create a NEW private config and exit')
    parser.add_argument('--registry')
    parser.add_argument('--key-id', default='decision-operator')
    args = parser.parse_args()
    server = None
    try:
        if args.init_local:
            if not args.registry:
                parser.error('--registry is required with --init-local')
            print(json.dumps(initialize_local(args.config, args.registry, key_id=args.key_id)))
            return 0
        if args.registry:
            parser.error('--registry applies only to --init-local')
        if not 0 <= args.port <= 65535:
            parser.error('port outside supported range')
        app = DecisionApplication(args.config)
        server = DecisionServer(('127.0.0.1', args.port), app)
        print(json.dumps({'service': FORMAT, 'bind': '127.0.0.1', 'port': server.server_address[1],
                          'review_required': True, 'key_values_printed': False}), flush=True)
        server.serve_forever(poll_interval=.2)
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, TypeError, KeyError, UnicodeError):
        print(json.dumps({'error': 'decision_service_refused'}), file=sys.stderr)
        return 2
    finally:
        if server is not None:
            server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
