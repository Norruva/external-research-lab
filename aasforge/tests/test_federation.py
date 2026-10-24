"""Registry federation: unknown descriptors come from upstream registries, under SSRF guards."""
import json
import unittest

from helpers import Server

from aasforge.canonical import b64url_encode as enc
from aasforge.federation import Federation, FederationError

AAS = 'https://plant.example.test/aas/battery-7'


class FederationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = Server()
        cls.upstream.request('POST', '/api/v3.1/shell-descriptors', {
            'id': AAS, 'idShort': 'Battery7', 'specificAssetIds': [{'name': 'serialNumber', 'value': 'B-7'}],
            'endpoints': [{'interface': 'AAS-3.0', 'protocolInformation': {'href': 'https://plant.example.test/shells/x'}}]})
        cls.upstream.request('POST', '/api/v3.1/lookup/shells/' + enc(AAS), [{'name': 'serialNumber', 'value': 'B-7'}])
        cls.local = Server(federation={'upstreams': [f'{cls.upstream.base}/api/v3.1'], 'allow_http': True,
                                       'allow_private': True, 'federate_discovery': True, 'ttl': 60})

    @classmethod
    def tearDownClass(cls):
        cls.local.close()
        cls.upstream.close()

    def test_descriptor_is_federated_and_cached(self):
        status, headers, body = self.local.get(f'/api/v3.1/shell-descriptors/{enc(AAS)}')
        self.assertEqual((status, body['idShort']), (200, 'Battery7'))
        self.assertEqual(headers['X-AAS-Federated-From'], f'{self.upstream.base}/api/v3.1')
        self.assertFalse(self.local.forge.store.exists('shell', AAS))              # not taken over
        self.upstream.request('DELETE', f'/api/v3.1/shell-descriptors/{enc(AAS)}')
        status, _, _ = self.local.get(f'/api/v3.1/shell-descriptors/{enc(AAS)}')
        self.assertEqual(status, 200)                                             # cached
        self.assertEqual(self.local.get(f'/api/v3.1/shell-descriptors/{enc("urn:none")}')[0], 404)

    def test_discovery_is_federated(self):
        link = enc(json.dumps({'name': 'serialNumber', 'value': 'B-7'}))
        _, _, body = self.local.get(f'/api/v3.1/lookup/shells?assetIds={link}')
        self.assertEqual(body['result'], [AAS])


class GuardTests(unittest.TestCase):
    def test_hierarchy_needs_allowed_domain(self):
        federation = Federation(hierarchy_domains=['example.test'])
        self.assertEqual(federation.hierarchy_registry(AAS), 'https://registry.plant.example.test/api/v3.0')
        self.assertIsNone(federation.hierarchy_registry('https://evil.example.org/aas/1'))
        self.assertIsNone(federation.hierarchy_registry('urn:uuid:123'))
        self.assertIsNone(federation.hierarchy_registry('https://example.test.evil.org/aas/1'))

    def test_private_and_plain_http_targets_are_refused(self):
        federation = Federation(upstreams=['https://127.0.0.1:9/api'])
        with self.assertRaises(FederationError):
            federation._check_url('https://127.0.0.1:9/api/shell-descriptors/x')
        with self.assertRaises(FederationError):
            federation._check_url('http://registry.example.org/api')
        with self.assertRaises(FederationError):
            federation._check_url('https://169.254.169.254/latest/meta-data')
        self.assertEqual(federation.descriptor('aas', 'urn:x'), (None, None))   # refused upstream is skipped


if __name__ == '__main__':
    unittest.main()
