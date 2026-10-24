"""OPC UA (I4AAS): NodeSet2 export that loads next to the official NodeSet, and a live server that
follows the store, applies the access tiers, authorises writes and serves files and operations."""
import asyncio
import base64
import os
import socket
import tempfile
import time
import unittest

from helpers import database

import battery_passport

try:
    import asyncua  # noqa: F401
except ImportError:  # pragma: no cover
    asyncua = None

from aasforge import opcua, security
from aasforge.app import Config, Forge
from aasforge.dpp import passport

SECRET = 'opcua-secret'
OPERATOR_KEY = 'operator-key'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def extra_submodel():
    return {'modelType': 'Submodel', 'id': 'urn:example:opcua:sm', 'idShort': 'Device', 'submodelElements': [
        {'modelType': 'Property', 'idShort': 'Temperature', 'valueType': 'xs:double', 'value': '21.5'},
        {'modelType': 'Blob', 'idShort': 'Firmware', 'contentType': 'application/octet-stream',
         'value': base64.b64encode(b'firmware-image-bytes').decode()},
        {'modelType': 'Operation', 'idShort': 'Double',
         'semanticId': {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': 'urn:op:double'}]},
         'inputVariables': [{'value': {'modelType': 'Property', 'idShort': 'x', 'valueType': 'xs:double'}}],
         'outputVariables': [{'value': {'modelType': 'Property', 'idShort': 'y', 'valueType': 'xs:double'}}]}]}


def forge(access_rules='dpp'):
    instance = Forge(Config(database=database(), access_rules=access_rules, hs256_secret=SECRET,
                            api_keys={OPERATOR_KEY: {'sub': 'operator', 'dpp_tier': 'economic-operator'}}))
    passport.create(instance.repository, battery_passport.spec())
    instance.repository.save('submodel', extra_submodel())
    instance.api.register_operation('urn:op:double', lambda args: {'y': float(args['x']) * 2})
    return instance


@unittest.skipIf(asyncua is None, 'asyncua is not installed')
class NodesetExport(unittest.TestCase):
    def test_export_loads_next_to_i4aas_and_hides_restricted_data(self):
        instance = forge()
        xml = opcua.export_nodeset(instance)
        self.assertIn(b'PcfCO2eq', xml)
        self.assertNotIn(b'RemainingCapacity', xml)        # legitimate-interest data
        self.assertIn(b'http://opcfoundation.org/UA/I4AAS/', xml)

        async def reload():
            from asyncua import Server, ua
            server = Server()
            await server.init()
            await server.import_xml(opcua.nodeset_path())
            with tempfile.NamedTemporaryFile('wb', suffix='.xml', delete=False) as handle:
                handle.write(xml)
            try:
                await server.import_xml(handle.name)
            finally:
                os.unlink(handle.name)
            ns = await server.get_namespace_index(opcua.INSTANCE_URI)
            i4 = await server.get_namespace_index(opcua.I4AAS_URI)
            root = server.get_node(ua.NodeId('AASROOT', ns))
            shells = [await c.read_browse_name() for c in await root.get_children()]
            temperature = server.get_node(ua.NodeId('sm|urn:example:opcua:sm|Temperature/Value', ns))
            value = await temperature.read_value()
            type_def = await server.get_node(ua.NodeId('sm|urn:example:opcua:sm', ns)).read_type_definition()
            return [s.Name for s in shells], value, type_def, i4

        names, value, type_def, i4 = asyncio.run(reload())
        self.assertIn('ConceptDictionary', names)
        self.assertEqual(value, 21.5)
        self.assertEqual((type_def.Identifier, type_def.NamespaceIndex), (opcua.TYPES['Submodel'], i4))
        instance.store.db.drop_schema()


@unittest.skipIf(asyncua is None, 'asyncua is not installed')
class LiveServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.forge = forge()
        cls.endpoint = f'opc.tcp://127.0.0.1:{free_port()}/aasforge/'
        cls.server = opcua.OpcUaServer(cls.forge, cls.endpoint, writable=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.forge.store.db.drop_schema()

    def client(self, password=None):
        from asyncua import Client
        client = Client(self.endpoint, timeout=10)
        if password:
            client.set_user('operator')
            client.set_password(password)
        return client

    def run_client(self, coroutine_factory, password=None):
        async def main():
            client = self.client(password)
            async with client:
                ns = await client.get_namespace_index(opcua.INSTANCE_URI)
                return await coroutine_factory(client, ns)
        return asyncio.run(main())

    def node(self, client, ns, text):
        from asyncua import ua
        return client.get_node(ua.NodeId(text, ns))

    def test_browse_and_tier_filtering(self):
        async def body(client, ns):
            from asyncua import ua
            root = self.node(client, ns, 'AASROOT')
            names = [(await c.read_browse_name()).Name for c in await root.get_children()]
            condition = 'sm|urn:example:voltaris:ev86:000417:sm:dbp-product-condition'
            children = [(await c.read_browse_name()).Name for c in await self.node(client, ns, condition).get_children()]
            try:
                await self.node(client, ns, f'{condition}|RemainingCapacity').read_browse_name()
                hidden_present = True
            except ua.UaStatusCodeError:
                hidden_present = False
            return names, children, hidden_present
        names, children, hidden_present = self.run_client(body)
        self.assertIn('ConceptDictionary', names)
        self.assertIn('Identification', children)
        self.assertNotIn('StateOfCharge', children)      # legitimate interest, Battery Regulation Art. 77(2)(b)
        self.assertFalse(hidden_present)

    def test_api_changes_reach_subscribers(self):
        received = []

        class Handler:
            def datachange_notification(self, node, value, data):
                received.append(value)

        async def body(client, ns):
            node = self.node(client, ns, 'sm|urn:example:opcua:sm|Temperature/Value')
            subscription = await client.create_subscription(50, Handler())
            await subscription.subscribe_data_change(node)
            await asyncio.sleep(0.3)

            def change(submodel):
                submodel['submodelElements'][0]['value'] = '30.25'
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self.forge.repository.update_submodel('urn:example:opcua:sm', change))
            for _ in range(60):
                if 30.25 in received:
                    break
                await asyncio.sleep(0.1)
            return await node.read_value()
        self.assertEqual(self.run_client(body), 30.25)
        self.assertIn(30.25, received)

    def test_structural_change_rebuilds_only_that_submodel(self):
        async def body(client, ns):
            from asyncua import ua
            device = self.node(client, ns, 'sm|urn:example:opcua:sm')
            passport_node = self.node(client, ns, 'sm|urn:example:voltaris:ev86:000417:sm:dbp-nameplate')
            before = await passport_node.read_browse_name()

            def add(submodel):
                submodel['submodelElements'].append(
                    {'modelType': 'Property', 'idShort': 'Humidity', 'valueType': 'xs:int', 'value': '40'})
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self.forge.repository.update_submodel('urn:example:opcua:sm', add))
            for _ in range(60):
                names = [(await c.read_browse_name()).Name for c in await device.get_children()]
                if 'Humidity' in names:
                    break
                await asyncio.sleep(0.1)
            humidity = await self.node(client, ns, 'sm|urn:example:opcua:sm|Humidity/Value').read_value()
            return names, humidity, before == await passport_node.read_browse_name()
        names, humidity, untouched = self.run_client(body)
        self.assertIn('Humidity', names)
        self.assertEqual(humidity, 40)
        self.assertTrue(untouched)

    def test_writes_are_authorised_by_access_rules(self):
        async def write(client, ns, value):
            from asyncua import ua
            node = self.node(client, ns, 'sm|urn:example:opcua:sm|Temperature/Value')
            try:
                await node.write_value(ua.DataValue(ua.Variant(value, ua.VariantType.Double)))
                return 'ok'
            except ua.UaStatusCodeError as error:
                return type(error).__name__

        anonymous = self.run_client(lambda c, ns: write(c, ns, 99.0))
        self.assertNotEqual(anonymous, 'ok')
        self.assertNotEqual(self.forge.store.get('submodel', 'urn:example:opcua:sm')['submodelElements'][0]['value'], '99.0')
        operator = self.run_client(lambda c, ns: write(c, ns, 42.5), password=OPERATOR_KEY)
        self.assertEqual(operator, 'ok')
        self.assertEqual(self.forge.store.get('submodel', 'urn:example:opcua:sm')['submodelElements'][0]['value'], '42.5')
        self.assertTrue(self.forge.store.history('submodel', 'urn:example:opcua:sm')[-1]['actor'].startswith('opcua:'))
        token = security.make_hs256_token({'sub': 'bad'}, 'wrong-secret')
        with self.assertRaises(Exception):
            self.run_client(lambda c, ns: write(c, ns, 1.0), password=token)

    def test_blob_through_file_type(self):
        async def body(client, ns):
            from asyncua import ua
            file_node = self.node(client, ns, 'sm|urn:example:opcua:sm|Firmware/File')
            open_method = self.node(client, ns, 'sm|urn:example:opcua:sm|Firmware/File/Open')
            read_method = self.node(client, ns, 'sm|urn:example:opcua:sm|Firmware/File/Read')
            close_method = self.node(client, ns, 'sm|urn:example:opcua:sm|Firmware/File/Close')
            handle = await file_node.call_method(open_method, ua.Variant(1, ua.VariantType.Byte))
            data = await file_node.call_method(read_method, ua.Variant(handle, ua.VariantType.UInt32),
                                               ua.Variant(1024, ua.VariantType.Int32))
            await file_node.call_method(close_method, ua.Variant(handle, ua.VariantType.UInt32))
            size = await self.node(client, ns, 'sm|urn:example:opcua:sm|Firmware/File/Size').read_value()
            return data, size
        data, size = self.run_client(body)
        self.assertEqual(data, b'firmware-image-bytes')
        self.assertEqual(size, len(b'firmware-image-bytes'))


@unittest.skipIf(asyncua is None, 'asyncua is not installed')
class OperationsOnOpenServer(unittest.TestCase):
    def test_operation_method_runs_registered_function(self):
        instance = forge(access_rules=None)
        endpoint = f'opc.tcp://127.0.0.1:{free_port()}/aasforge/'
        server = opcua.OpcUaServer(instance, endpoint).start()
        try:
            async def main():
                from asyncua import Client, ua
                async with Client(endpoint, timeout=10) as client:
                    ns = await client.get_namespace_index(opcua.INSTANCE_URI)
                    parent = client.get_node(ua.NodeId('sm|urn:example:opcua:sm|Double', ns))
                    method = client.get_node(ua.NodeId('sm|urn:example:opcua:sm|Double/Operation', ns))
                    return await parent.call_method(method, ua.Variant(21.0, ua.VariantType.Double))
            start = time.monotonic()
            self.assertEqual(asyncio.run(main()), 42.0)
            self.assertLess(time.monotonic() - start, 30)
        finally:
            server.stop()
            instance.store.db.drop_schema()


if __name__ == '__main__':
    unittest.main()
