"""Authored adapter checks; temporary fixture keys and loopback only."""
from email.message import Message
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from eidos.routed_service import RoutedApplication, initialize_local
from eidos.decision_service import DecisionServer, FORMAT, signature

PIN = '8e' * 32
SECRET = 'b4' * 32
NOW = 1_800_000_000


class FixtureEngine:
    registry_sha256 = PIN
    identity = {'id': 'routed-adapter-fixture', 'feature_version': 'v4-test',
                'calibration_status': 'unfitted'}

    def decide(self, raw):
        return {'format': 'eidos-decision-response-v2', 'status': 'review_required',
                'value': None, 'review_required': True,
                'authorization': 'recommendation_only_no_action_authorized'}


class RoutedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='eidos-routed-service-')
        self.root = Path(self.temp.name)
        self.config = self.root / 'fresh-config.json'
        self.registry = self.root / 'fresh-registry'
        self.engine = FixtureEngine()

    def tearDown(self):
        self.temp.cleanup()

    def initialized(self):
        with patch('eidos.routed_service.RoutedEngine.from_registry', return_value=self.engine), \
                patch('eidos.routed_service.secrets.token_hex', return_value=SECRET):
            return initialize_local(self.config, self.registry)

    def app(self):
        return RoutedApplication(self.config, engine_factory=lambda *a, **k: self.engine,
                                     clock=lambda: NOW)

    def signed(self, nonce='routed-nonce-00000001', body=b'{}'):
        headers = Message()
        for key, value in {'X-Eidos-Key': 'routed-operator', 'X-Eidos-Time': str(NOW),
                'X-Eidos-Nonce': nonce, 'X-Eidos-Signature': signature(bytes.fromhex(SECRET),
                    'routed-operator', str(NOW), nonce, 'POST', '/v2/decisions', body)}.items():
            headers[key] = value
        return headers

    def test_new_config_pins_routed_registry_and_never_returns_key(self):
        result = self.initialized()
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        config = json.loads(self.config.read_text())
        self.assertEqual(config['format'], FORMAT)
        self.assertEqual(config['registry_sha256'], PIN)
        self.assertEqual(config['registry'], str(self.registry))
        self.assertFalse(result['key_values_printed'])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn('secret', result)

    def test_default_factory_uses_routed_loader_with_external_pin(self):
        self.initialized()
        with patch('eidos.routed_service.RoutedEngine.from_registry', return_value=self.engine) as factory:
            app = RoutedApplication(self.config)
        factory.assert_called_once_with(str(self.registry), expected_registry_sha256=PIN)
        self.assertIs(app.engine, self.engine)

    def test_loader_refusal_leaves_no_configuration(self):
        with patch('eidos.routed_service.RoutedEngine.from_registry', side_effect=ValueError('unsupported_registry')):
            with self.assertRaises(ValueError):
                initialize_local(self.config, self.registry)
        self.assertFalse(self.config.exists())

    def test_existing_config_symlink_and_parent_escape_are_refused(self):
        self.initialized()
        before = self.config.read_bytes()
        with self.assertRaises(ValueError):
            initialize_local(self.config, self.registry)
        self.assertEqual(self.config.read_bytes(), before)
        (self.root / 'alias').symlink_to(self.root, target_is_directory=True)
        for path in (self.root / 'alias' / 'new.json', self.root / 'unused' / '..' / 'new.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                initialize_local(path, self.registry)

    def test_invalid_identifier_and_missing_registry_pin_are_refused(self):
        with self.assertRaises(ValueError):
            initialize_local(self.config, self.registry, key_id='bad key')
        self.engine.registry_sha256 = None
        with patch('eidos.routed_service.RoutedEngine.from_registry', return_value=self.engine):
            with self.assertRaises(ValueError):
                initialize_local(self.config, self.registry)
        self.assertFalse(self.config.exists())

    def test_authentication_and_replay_policy_survive_adapter(self):
        self.initialized(); app = self.app()
        with self.assertRaises(PermissionError):
            app.handle('POST', '/v2/decisions', Message(), b'{}')
        headers = self.signed()
        code, result = app.handle('POST', '/v2/decisions', headers, b'{}')
        self.assertEqual(code, 200)
        self.assertTrue(result['review_required']); self.assertIsNone(result['value'])
        with self.assertRaisesRegex(PermissionError, 'replayed_request'):
            app.handle('POST', '/v2/decisions', headers, b'{}')

    def test_real_http_uses_adapter_and_stops_without_external_bind(self):
        self.initialized(); server = DecisionServer(('127.0.0.1', 0), self.app())
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        worker.start()
        try:
            client = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
            headers = dict(self.signed().items()); headers['Content-Type'] = 'application/json'
            try:
                client.request('POST', '/v2/decisions', body=b'{}', headers=headers)
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertTrue(json.loads(response.read())['review_required'])
            finally:
                client.close()
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=3)
        self.assertFalse(worker.is_alive())


if __name__ == '__main__':
    unittest.main()
