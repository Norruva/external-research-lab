"""Authored service tests; stub engine isolates transport from model quality."""
import contextlib
from email.message import Message
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from eidos.decision_service import (DecisionApplication, DecisionServer, FORMAT,
                                    MAX_REQUEST_BYTES, signature)

NOW = 1_800_000_000
SECRET = '16' * 32
PIN = '7c' * 32


class StubEngine:
    identity = {'id': 'original-transport-fixture', 'calibration_status': 'unfitted'}

    def __init__(self):
        self.calls = []

    def decide(self, raw):
        self.calls.append(raw)
        if raw == b'broken-private-phrase':
            raise ValueError('broken-private-phrase')
        return {'format': 'eidos-decision-response-v2', 'status': 'review_required',
                'value': None, 'review_required': True,
                'authorization': 'recommendation_only_no_action_authorized'}


class DecisionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='eidos-original-service-test-')
        self.root = Path(self.temporary.name)
        self.config_path = self.root / 'new-config.json'
        self.config = {'format': FORMAT, 'registry': str(self.root / 'new-registry'),
                       'registry_sha256': PIN, 'key_id': 'original-test', 'secret': SECRET}
        self.write_config()
        self.engine = StubEngine()
        self.factory_calls = []
        def factory(path, *, expected_registry_sha256):
            self.factory_calls.append((path, expected_registry_sha256))
            return self.engine
        self.factory = factory
        self.app = DecisionApplication(self.config_path, engine_factory=factory, clock=lambda: NOW)
        self.sequence = 0

    def tearDown(self):
        self.temporary.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)

    def signed(self, method='POST', path='/v2/decisions', body=b'{}', *, timestamp=NOW, nonce=None):
        self.sequence += 1
        nonce = nonce or f'originalnonce{self.sequence:016d}'
        headers = Message()
        values = {'X-Eidos-Key': 'original-test', 'X-Eidos-Time': str(timestamp),
                  'X-Eidos-Nonce': nonce,
                  'X-Eidos-Signature': signature(bytes.fromhex(SECRET), 'original-test', str(timestamp), nonce, method, path, body)}
        for name, value in values.items():
            headers[name] = value
        return headers

    def test_initialization_pins_explicit_registry(self):
        self.assertEqual(self.factory_calls, [(str(self.root / 'new-registry'), PIN)])
        self.assertEqual(self.app.registry_sha256, PIN)

    def test_signed_request_reaches_only_stub_engine(self):
        body = b'{"authored":"transport-only"}'
        code, response = self.app.handle('POST', '/v2/decisions', self.signed(body=body), body)
        self.assertEqual(code, 200)
        self.assertIsNone(response['value'])
        self.assertTrue(response['review_required'])
        self.assertEqual(self.engine.calls, [body])

    def test_authenticated_model_exposes_only_identity_and_policy(self):
        code, response = self.app.handle('GET', '/v2/model', self.signed('GET', '/v2/model', b''), b'')
        self.assertEqual(code, 200)
        self.assertEqual(response['model'], self.engine.identity)
        self.assertNotIn('secret', response)
        self.assertNotIn('weights', response)
        self.assertEqual(self.engine.calls, [])

    def test_missing_authentication_is_refused(self):
        with self.assertRaises(PermissionError):
            self.app.handle('POST', '/v2/decisions', Message(), b'{}')
        self.assertEqual(self.engine.calls, [])

    def test_duplicate_authentication_header_is_refused(self):
        headers = self.signed()
        headers['X-Eidos-Key'] = 'original-test'
        with self.assertRaises(PermissionError):
            self.app.authenticate(headers, 'POST', '/v2/decisions', b'{}')

    def test_signature_binds_method_path_and_body(self):
        for method, path, body in [('GET', '/v2/decisions', b'{}'),
                                   ('POST', '/v2/model', b'{}'),
                                   ('POST', '/v2/decisions', b'{"changed":true}')]:
            with self.subTest(method=method, path=path, body=body):
                with self.assertRaises(PermissionError):
                    self.app.authenticate(self.signed(), method, path, body)

    def test_expired_and_far_future_timestamps_are_refused(self):
        for timestamp in (NOW - 61, NOW + 61):
            with self.assertRaises(PermissionError):
                self.app.authenticate(self.signed(timestamp=timestamp), 'POST', '/v2/decisions', b'{}')

    def test_future_nonce_is_retained_through_full_acceptance_window(self):
        nonce = 'original-future-nonce'
        headers = self.signed(timestamp=NOW + 50, nonce=nonce)
        self.app.authenticate(headers, 'POST', '/v2/decisions', b'{}')
        self.app.clock = lambda: NOW + 100
        with self.assertRaisesRegex(PermissionError, 'replayed_request'):
            self.app.authenticate(headers, 'POST', '/v2/decisions', b'{}')

    def test_replay_is_refused_before_second_inference(self):
        headers = self.signed()
        self.app.handle('POST', '/v2/decisions', headers, b'{}')
        with self.assertRaisesRegex(PermissionError, 'replayed_request'):
            self.app.handle('POST', '/v2/decisions', headers, b'{}')
        self.assertEqual(len(self.engine.calls), 1)

    def test_rate_limit_is_bounded_and_releases_on_next_minute(self):
        for _ in range(120):
            self.app.authenticate(self.signed(), 'POST', '/v2/decisions', b'{}')
        with self.assertRaisesRegex(PermissionError, 'request_rate_exceeded'):
            self.app.authenticate(self.signed(), 'POST', '/v2/decisions', b'{}')
        self.app.clock = lambda: NOW + 60
        self.app.authenticate(self.signed(timestamp=NOW + 60), 'POST', '/v2/decisions', b'{}')
        self.assertEqual(self.app.rate_count, 1)

    def test_body_size_limit_refuses_before_authentication(self):
        with self.assertRaises(ValueError):
            self.app.handle('POST', '/v2/decisions', Message(), b'x' * (MAX_REQUEST_BYTES + 1))
        self.assertEqual(self.engine.calls, [])

    def test_unknown_routes_have_no_inference(self):
        self.assertEqual(self.app.handle('POST', '/unknown', Message(), b'{}'),
                         (404, {'error': 'route_unavailable'}))
        self.assertEqual(self.engine.calls, [])

    def test_configuration_permissions_unknown_fields_and_symlink_are_refused(self):
        self.config_path.chmod(0o644)
        with self.assertRaises(ValueError):
            DecisionApplication(self.config_path, engine_factory=self.factory)
        self.write_config()
        self.config['unexpected'] = 'refused'
        self.write_config()
        with self.assertRaises(ValueError):
            DecisionApplication(self.config_path, engine_factory=self.factory)
        self.config.pop('unexpected')
        self.write_config()
        link = self.root / 'symlink-config.json'
        link.symlink_to(self.config_path)
        with self.assertRaises(ValueError):
            DecisionApplication(link, engine_factory=self.factory)

    def test_configuration_duplicate_keys_refused(self):
        self.config_path.write_text('{"format":"x","format":"y"}')
        with self.assertRaises(ValueError):
            DecisionApplication(self.config_path, engine_factory=self.factory)

    def test_nonloopback_bind_is_refused(self):
        with self.assertRaises(ValueError):
            DecisionServer(('0.0.0.0', 0), self.app)

    def http(self, server, method, path, body=b'', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read(2_000_001)
        finally:
            connection.close()

    def with_server(self, action):
        server = DecisionServer(('127.0.0.1', 0), self.app)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        try:
            return action(server)
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()
            self.assertFalse(thread.is_alive())

    def test_real_loopback_http_health_assets_and_unsigned_refusal(self):
        def check(server):
            for path in ('/healthz', '/', '/decision.js', '/style.css'):
                status, headers, payload = self.http(server, 'GET', path)
                self.assertEqual(status, 200)
                self.assertTrue(payload)
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
            status, _, body = self.http(server, 'POST', '/v2/decisions', b'{}', {'Content-Type': 'application/json'})
            self.assertEqual(status, 403)
            self.assertNotIn(SECRET.encode(), body)
        self.with_server(check)

    def test_real_loopback_http_signed_request_and_replay(self):
        def check(server):
            headers = dict(self.signed().items())
            headers['Content-Type'] = 'application/json'
            status, _, raw = self.http(server, 'POST', '/v2/decisions', b'{}', headers)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(raw)['review_required'])
            status, _, raw = self.http(server, 'POST', '/v2/decisions', b'{}', headers)
            self.assertEqual(status, 403)
            self.assertEqual(json.loads(raw)['error'], 'replayed_request')
        self.with_server(check)

    def test_real_http_wrong_origin_host_and_json_type_are_refused(self):
        def check(server):
            port = server.server_address[1]
            cases = [({'Origin': 'http://other.invalid', 'Content-Type': 'application/json'}, 403),
                     ({'Host': f'other.invalid:{port}', 'Content-Type': 'application/json'}, 403),
                     ({'Content-Type': 'text/plain'}, 400)]
            for headers, expected in cases:
                status, _, _ = self.http(server, 'POST', '/v2/decisions', b'{}', headers)
                self.assertEqual(status, expected)
        self.with_server(check)

    def test_real_http_exception_redaction_no_access_logs(self):
        def check(server):
            body = b'broken-private-phrase'
            headers = dict(self.signed(body=body).items())
            headers['Content-Type'] = 'application/json'
            with contextlib.redirect_stderr(io.StringIO()) as captured:
                status, _, raw = self.http(server, 'POST', '/v2/decisions', body, headers)
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(raw), {'error': 'invalid_request'})
            self.assertNotIn(b'broken-private-phrase', raw)
            self.assertEqual(captured.getvalue(), '')
        self.with_server(check)


if __name__ == '__main__':
    unittest.main()
