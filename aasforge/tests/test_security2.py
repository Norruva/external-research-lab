"""Regression tests for the second security review (OPC UA, MQTT, Catena-X, templates, importers)."""
import asyncio
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import Server

from aasforge import samm
from aasforge.canonical import b64url_encode as enc

OWNER, BPN1 = 'BPNL00000000OWNR', 'BPNL000000000001'
AAS = 'urn:uuid:sec2-shell'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def subject(*values):
    return {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': v} for v in values]}


class CatenaXLeaks(unittest.TestCase):
    """Hidden specificAssetIds must not leave the server on any path."""

    @classmethod
    def setUpClass(cls):
        cls.server = Server(catenax={'bpn': OWNER}, api_keys={'owner': {'sub': 'owner', 'bpn': OWNER},
                                                              'partner': {'sub': 'p', 'bpn': BPN1}})
        cls.server.request('POST', '/api/v3.1/shells', {
            'modelType': 'AssetAdministrationShell', 'id': AAS, 'idShort': 'Twin',
            'assetInformation': {'assetKind': 'Instance', 'globalAssetId': 'urn:uuid:asset', 'specificAssetIds': [
                {'name': 'partInstanceId', 'value': 'SECRET-SN'},
                {'name': 'customerPartId', 'value': 'C-100', 'externalSubjectId': subject(BPN1)}]}},
            headers={'X-API-Key': 'owner'})
        cls.hidden_twin = 'urn:uuid:sec2-hidden'
        cls.server.request('POST', '/api/v3.1/shells', {
            'modelType': 'AssetAdministrationShell', 'id': cls.hidden_twin,
            'assetInformation': {'assetKind': 'Instance', 'specificAssetIds': [{'name': 'partInstanceId', 'value': 'X'}]}},
            headers={'X-API-Key': 'owner'})

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def assert_hidden(self, text):
        self.assertNotIn('SECRET-SN', text)

    def test_output_paths_apply_the_shell_view(self):
        partner = {'X-API-Key': 'partner'}
        for path in (f'/api/v3.1/lookup/shells/{enc(AAS)}', '/api/v3.1/serialization',
                     f'/api/v3.1/shells/{enc(AAS)}/$history?date=2999-01-01T00:00:00Z',
                     f'/api/v3.1/shells/{enc(AAS)}/$signed', '/api/v3.1/opcua/nodeset'):
            for headers in (partner, {}):
                status, _, body = self.server.get(path, headers=headers, raw=True)
                self.assert_hidden(body.decode('utf-8', 'replace'))
        _, _, links = self.server.get(f'/api/v3.1/lookup/shells/{enc(AAS)}', headers=partner)
        self.assertEqual(sorted(link['name'] for link in links), ['customerPartId', 'globalAssetId'])
        status, _, body = self.server.request('POST', '/api/v3.1/query/shells', {'$condition': {'$boolean': True}},
                                              headers=partner)
        self.assert_hidden(json.dumps(body))

    def test_guesses_reveal_nothing(self):
        guess = {'$condition': {'$eq': [{'$field': '$aas#assetInformation.specificAssetIds[].value'},
                                        {'$strVal': 'SECRET-SN'}]}, '$select': 'id'}
        _, _, body = self.server.request('POST', '/api/v3.1/query/shells', guess, headers={'X-API-Key': 'partner'})
        self.assertEqual(body['result'], [])
        link = enc(json.dumps({'name': 'partInstanceId', 'value': 'SECRET-SN'}))
        _, _, body = self.server.get(f'/api/v3.1/shells?assetIds={link}', headers={'X-API-Key': 'partner'})
        self.assertEqual(body['result'], [])

    def test_discovery_and_events_hide_invisible_twins(self):
        _, _, body = self.server.get('/api/v3.1/lookup/shells', headers={'X-API-Key': 'partner'})
        self.assertNotIn(self.hidden_twin, body['result'])
        _, _, body = self.server.get('/api/v3.1/lookup/shells', headers={'X-API-Key': 'owner'})
        self.assertIn(self.hidden_twin, body['result'])
        _, _, events = self.server.get('/api/v3.1/events', headers={'X-API-Key': 'partner'})
        self.assertFalse(any(e['id'] == self.hidden_twin for e in events['events']))
        _, _, events = self.server.get('/api/v3.1/events', headers={'X-API-Key': 'owner'})
        self.assertTrue(any(e['id'] == self.hidden_twin for e in events['events']))

    def test_mqtt_payloads_apply_the_shell_view(self):
        from aasforge.mqtt import MqttBridge
        bridge = MqttBridge(self.server.forge, 'mqtt://127.0.0.1:1', 'x')
        messages = bridge.messages({'kind': 'shell', 'id': AAS, 'op': 'created', 'seq': 1})
        self.assertTrue(messages)
        self.assertFalse(any('SECRET-SN' in str(payload) for _, payload, _ in messages))


class TemplateFilters(unittest.TestCase):
    def test_templates_are_read_through_filters(self):
        rules = {'AllAccessPermissionRules': {'rules': [
            {'ACL': {'ATTRIBUTES': [{'CLAIM': 'role'}], 'RIGHTS': ['ALL'], 'ACCESS': 'ALLOW'}, 'OBJECTS': [{'ROUTE': '*'}],
             'FORMULA': {'$eq': [{'$attribute': {'CLAIM': 'role'}}, {'$strVal': 'admin'}]}},
            {'ACL': {'ATTRIBUTES': [{'CLAIM': 'role'}], 'RIGHTS': ['READ'], 'ACCESS': 'ALLOW'}, 'OBJECTS': [{'ROUTE': '*'}],
             'FORMULA': {'$eq': [{'$attribute': {'CLAIM': 'role'}}, {'$strVal': 'reader'}]},
             'FILTER': {'FRAGMENT': '$sme', 'CONDITION': {'$ne': [{'$field': '$sme#idShort'}, {'$strVal': 'Secret'}]}}}]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rules.json'
            path.write_text(json.dumps(rules))
            server = Server(access_rules=str(path), api_keys={'admin': {'sub': 'a', 'role': 'admin'},
                                                              'reader': {'sub': 'r', 'role': 'reader'}})
            try:
                server.request('POST', '/api/v3.1/submodels', {'modelType': 'Submodel', 'id': 'urn:tpl', 'kind': 'Template',
                    'submodelElements': [{'modelType': 'Property', 'idShort': 'Secret', 'valueType': 'xs:string',
                                          'value': 'TOPSECRET'}]}, headers={'X-API-Key': 'admin'})
                status, _, body = server.request('POST', '/api/v3.1/templates/instantiate',
                                                 {'template': 'urn:tpl', 'id': 'urn:copy', 'keepExamples': True},
                                                 headers={'X-API-Key': 'reader'})
                self.assertNotIn('TOPSECRET', json.dumps(body))
                _, _, body = server.request('POST', '/api/v3.1/templates/check',
                                            {'template': 'urn:tpl', 'submodel': {'modelType': 'Submodel', 'id': 'x'}},
                                            headers={'X-API-Key': 'reader'})
                self.assertNotIn('Secret', json.dumps(body))
            finally:
                server.close()


class OpcUaHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import asyncua  # noqa: F401
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest('asyncua missing')
        from aasforge import opcua
        cls.server = Server(api_keys={'writer': {'sub': 'w'}})
        cls.server.forge.repository.save('submodel', {'modelType': 'Submodel', 'id': 'urn:dev', 'idShort': 'Dev',
            'submodelElements': [{'modelType': 'Property', 'idShort': 'T', 'valueType': 'xs:double', 'value': '1'},
                                 {'modelType': 'Blob', 'idShort': 'B', 'contentType': 'text/plain', 'value': 'aGk='}]})
        cls.endpoint = f'opc.tcp://127.0.0.1:{free_port()}/aasforge/'
        cls.opc = opcua.OpcUaServer(cls.server.forge, cls.endpoint, writable=False).start()

    @classmethod
    def tearDownClass(cls):
        cls.opc.stop()
        cls.server.close()

    def run_client(self, body, password=None):
        async def main():
            from asyncua import Client
            client = Client(self.endpoint, timeout=10)
            if password:
                client.set_user('w')
                client.set_password(password)
            async with client:
                ns = await client.get_namespace_index('urn:aasforge:opcua')
                return await body(client, ns)
        return asyncio.run(main())

    def test_writes_off_means_off(self):
        async def write(client, ns):
            from asyncua import ua
            node = client.get_node(ua.NodeId('sm|urn:dev|T/Value', ns))
            try:
                await node.write_value(ua.DataValue(ua.Variant(99.0, ua.VariantType.Double)))
            except ua.UaStatusCodeError:
                pass
        self.run_client(write, password='writer')
        self.assertEqual(self.server.forge.store.get('submodel', 'urn:dev')['submodelElements'][0]['value'], '1')

    def test_passwords_are_encrypted(self):
        async def endpoints(client, ns):
            return await client.get_endpoints()
        found = self.run_client(endpoints)
        policies = {e.SecurityPolicyUri.rsplit('#', 1)[-1] for e in found}
        self.assertIn('Basic256Sha256', policies)
        for endpoint in found:
            if not endpoint.SecurityPolicyUri.endswith('#None'):
                continue          # the encrypted channel protects the password itself
            for token in endpoint.UserIdentityTokens:
                if token.TokenType.name == 'UserName':
                    self.assertTrue(token.SecurityPolicyUri.endswith('Basic256Sha256'))

    def test_file_handles_are_bounded(self):
        from aasforge import opcua

        async def open_many(client, ns):
            from asyncua import ua
            file_node = client.get_node(ua.NodeId('sm|urn:dev|B/File', ns))
            method = client.get_node(ua.NodeId('sm|urn:dev|B/File/Open', ns))
            for _ in range(opcua.MAX_OPEN_FILES + 40):
                await file_node.call_method(method, ua.Variant(1, ua.VariantType.Byte))
        self.run_client(open_many)
        self.assertLessEqual(len(self.opc.space.open_files), opcua.MAX_OPEN_FILES)

    def test_nodeset_export_is_cached(self):
        start = time.monotonic()
        first = self.server.get('/api/v3.1/opcua/nodeset', raw=True)[2]
        middle = time.monotonic()
        second = self.server.get('/api/v3.1/opcua/nodeset', raw=True)[2]
        self.assertEqual(first, second)
        self.assertLess(time.monotonic() - middle, (middle - start) / 3)


class MqttIngestOrder(unittest.TestCase):
    def test_ingest_is_ordered_and_uses_one_worker(self):
        from aasforge.mqtt import MqttBridge
        server = Server()
        try:
            server.forge.repository.save('submodel', {'modelType': 'Submodel', 'id': 'urn:m', 'submodelElements': [
                {'modelType': 'Property', 'idShort': 'N', 'valueType': 'xs:int', 'value': '0'}]})
            bridge = MqttBridge(server.forge, 'mqtt://127.0.0.1:1', 'r', ingest_claims={'sub': 'telemetry'})
            bridge._ingester.start()
            threads = threading.active_count()

            class Message:
                def __init__(self, n):
                    self.topic = f'aasforge/r/submodels/{enc("urn:m")}/submodelElements/N/set'
                    self.payload = str(n).encode()
            for n in range(1, 301):
                bridge._on_message(None, None, Message(n))
            self.assertLessEqual(threading.active_count(), threads + 1)
            bridge.ingest_queue.put(None)
            bridge._ingester.join(60)
            self.assertEqual(server.forge.store.get('submodel', 'urn:m')['submodelElements'][0]['value'], '300')
        finally:
            server.close()


class SammImportBounds(unittest.TestCase):
    def test_namespaces_cannot_leave_the_models_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'models'
            (root / 'io.ok' / '1.0.0').mkdir(parents=True)
            outside = Path(folder) / 'outside' / '1.0.0'
            outside.mkdir(parents=True)
            (outside / 'Secret.ttl').write_text('@prefix samm: <urn:samm:org.eclipse.esmf.samm:meta-model:2.1.0#> .\n'
                                                '<urn:samm:secret:1.0.0#Leak> a samm:Aspect .\n')
            main = root / 'io.ok' / '1.0.0' / 'Main.ttl'
            main.write_text('@prefix samm: <urn:samm:org.eclipse.esmf.samm:meta-model:2.1.0#> .\n'
                            '<urn:samm:io.ok:1.0.0#Main> a samm:Aspect ; samm:properties ( <urn:samm:../outside:1.0.0#x> ) .\n')
            model = samm.AspectModel([main], models_root=root)
            self.assertNotIn('urn:samm:secret:1.0.0#Leak', model.aspects())

    def test_cyclic_characteristics_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Cycle.ttl'
            path.write_text('''@prefix samm: <urn:samm:org.eclipse.esmf.samm:meta-model:2.1.0#> .
@prefix samm-c: <urn:samm:org.eclipse.esmf.samm:characteristic:2.1.0#> .
@prefix : <urn:samm:io.cycle:1.0.0#> .
:Cycle a samm:Aspect ; samm:properties ( :p ) .
:p a samm:Property ; samm:characteristic :Loop .
:Loop a samm-c:Either ; samm-c:left :Loop ; samm-c:right :Loop .
''')
            with self.assertRaises(samm.SammError):
                samm.AspectModel([path]).template()


if __name__ == '__main__':
    unittest.main()
