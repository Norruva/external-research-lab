"""AAS API Part 2 end to end over HTTP: CRUD, superpath, modifiers, history, query, registry,
discovery, packages and operations (local, async and delegated)."""
import json
import time
import unittest
import uuid

from helpers import FIXTURES, Server

from aasforge.canonical import b64url_encode as enc

SM_ID = 'urn:example:forge:sm:technical'
AAS_ID = 'urn:example:forge:aas:pack-1'
ASSET = 'urn:example:forge:asset:pack-1'
OP_SEMANTIC = 'urn:example:forge:op:double'


def shell():
    return {'modelType': 'AssetAdministrationShell', 'id': AAS_ID, 'idShort': 'Pack1',
            'assetInformation': {'assetKind': 'Instance', 'globalAssetId': ASSET,
                                 'specificAssetIds': [{'name': 'serialNumber', 'value': 'S-1'}]},
            'submodels': [{'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': SM_ID}]}]}


def submodel():
    variable = {'modelType': 'Property', 'idShort': 'x', 'valueType': 'xs:double'}
    return {'modelType': 'Submodel', 'id': SM_ID, 'idShort': 'Technical',
            'semanticId': {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': 'urn:sem:tech'}]},
            'submodelElements': [
                {'modelType': 'Property', 'idShort': 'Mass', 'valueType': 'xs:double', 'value': '452.0'},
                {'modelType': 'SubmodelElementCollection', 'idShort': 'Cell', 'value': [
                    {'modelType': 'Property', 'idShort': 'Voltage', 'valueType': 'xs:double', 'value': '3.7'}]},
                {'modelType': 'File', 'idShort': 'Manual', 'contentType': 'application/pdf'},
                {'modelType': 'Operation', 'idShort': 'Double',
                 'semanticId': {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': OP_SEMANTIC}]},
                 'inputVariables': [{'value': variable}],
                 'outputVariables': [{'value': {**variable, 'idShort': 'y'}}]}]}


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.forge.api.register_operation(OP_SEMANTIC, lambda args: {'y': float(args['x']) * 2})
        assert cls.server.request('POST', '/api/v3.1/submodels', submodel())[0] == 201
        assert cls.server.request('POST', '/api/v3.1/shells', shell())[0] == 201

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def call(self, method, path, body=None, **kwargs):
        return self.server.request(method, '/api/v3.1' + path, body, **kwargs)

    # -- repository --------------------------------------------------------------------------------
    def test_shell_crud_and_errors(self):
        status, _, page = self.call('GET', '/shells')
        self.assertEqual(status, 200)
        self.assertIn(AAS_ID, [s['id'] for s in page['result']])
        self.assertEqual(self.call('POST', '/shells', shell())[0], 409)
        status, _, body = self.call('GET', f'/shells/{enc("urn:missing")}')
        self.assertEqual(status, 404)
        self.assertEqual(body['messages'][0]['messageType'], 'Error')
        self.assertEqual(self.call('GET', '/shells?limit=0')[0], 400)
        self.assertEqual(self.call('PATCH', '/shells')[0], 405)
        status, _, refs = self.call('GET', f'/shells/{enc(AAS_ID)}/submodel-refs')
        self.assertEqual(refs['result'][0]['keys'][0]['value'], SM_ID)

    def test_asset_id_filter_and_paging(self):
        for i in range(3):
            body = {**shell(), 'id': f'urn:example:forge:aas:page-{i}', 'submodels': []}
            body['assetInformation'] = {'assetKind': 'Type', 'globalAssetId': f'urn:asset:page-{i}'}
            self.assertEqual(self.call('POST', '/shells', body)[0], 201)
        asset = enc(json.dumps({'name': 'globalAssetId', 'value': 'urn:asset:page-1'}))
        _, _, page = self.call('GET', f'/shells?assetIds={asset}')
        self.assertEqual([s['id'] for s in page['result']], ['urn:example:forge:aas:page-1'])
        _, _, first = self.call('GET', '/shells?limit=2')
        self.assertEqual(len(first['result']), 2)
        cursor = first['paging_metadata']['cursor']
        _, _, second = self.call('GET', f'/shells?limit=2&cursor={cursor}')
        self.assertFalse({s['id'] for s in first['result']} & {s['id'] for s in second['result']})

    def test_submodel_modifiers_and_superpath(self):
        sm = enc(SM_ID)
        _, _, value = self.call('GET', f'/submodels/{sm}/$value')
        self.assertEqual(value['Mass'], 452.0)
        _, _, metadata = self.call('GET', f'/submodels/{sm}/$metadata')
        self.assertNotIn('submodelElements', metadata)
        _, _, paths = self.call('GET', f'/submodels/{sm}/$path')
        self.assertIn('Cell.Voltage', paths)
        _, _, reference = self.call('GET', f'/submodels/{sm}/submodel-elements/Cell.Voltage/$reference')
        self.assertEqual([k['value'] for k in reference['keys']], [SM_ID, 'Cell', 'Voltage'])
        _, _, core = self.call('GET', f'/submodels/{sm}?level=core')
        self.assertNotIn('value', next(e for e in core['submodelElements'] if e['idShort'] == 'Cell'))
        status, _, via_shell = self.call('GET', f'/shells/{enc(AAS_ID)}/submodels/{sm}/submodel-elements/Mass')
        self.assertEqual((status, via_shell['value']), (200, '452.0'))

    def test_element_lifecycle_and_value_patch(self):
        sm = enc(SM_ID)
        new = {'modelType': 'Property', 'idShort': 'Temp', 'valueType': 'xs:int', 'value': '20'}
        self.assertEqual(self.call('POST', f'/submodels/{sm}/submodel-elements/Cell', new)[0], 201)
        self.assertEqual(self.call('POST', f'/submodels/{sm}/submodel-elements/Cell', new)[0], 409)
        self.assertEqual(self.call('PATCH', f'/submodels/{sm}/submodel-elements/Cell.Temp/$value', 25)[0], 204)
        _, _, element = self.call('GET', f'/submodels/{sm}/submodel-elements/Cell.Temp')
        self.assertEqual(element['value'], '25')
        status, _, body = self.call('PATCH', f'/submodels/{sm}/submodel-elements/Cell.Temp/$value', 'warm')
        self.assertEqual(status, 400, body)           # xs:int rejects a non-number
        self.assertEqual(self.call('DELETE', f'/submodels/{sm}/submodel-elements/Cell.Temp')[0], 204)
        self.assertEqual(self.call('GET', f'/submodels/{sm}/submodel-elements/Cell.Temp')[0], 404)

    def test_attachment_round_trip(self):
        sm, pdf = enc(SM_ID), b'%PDF-1.7 forge'
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="manual.pdf"\r\n'
                f'Content-Type: application/pdf\r\n\r\n').encode() + pdf + f'\r\n--{boundary}--\r\n'.encode()
        status, _, _ = self.call('PUT', f'/submodels/{sm}/submodel-elements/Manual/attachment', body,
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
        self.assertEqual(status, 204)
        status, headers, data = self.call('GET', f'/submodels/{sm}/submodel-elements/Manual/attachment', raw=True)
        self.assertEqual((status, data), (200, pdf))
        self.assertIn('application/pdf', headers['Content-Type'])
        _, _, element = self.call('GET', f'/submodels/{sm}/submodel-elements/Manual')
        self.assertTrue(element['value'])

    # -- versions ----------------------------------------------------------------------------------
    def test_history_and_recent_changes(self):
        body = {'modelType': 'Submodel', 'id': 'urn:example:forge:sm:hist', 'submodelElements': [
            {'modelType': 'Property', 'idShort': 'P', 'valueType': 'xs:string', 'value': 'v1'}]}
        self.assertEqual(self.call('POST', '/submodels', body)[0], 201)
        time.sleep(0.01)
        moment = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + f'.{int(time.time() * 1000) % 1000:03d}Z'
        time.sleep(0.01)
        body['submodelElements'][0]['value'] = 'v2'
        self.assertEqual(self.call('PUT', f'/submodels/{enc(body["id"])}', body)[0], 204)
        _, _, old = self.call('GET', f'/submodels/{enc(body["id"])}/$history?date={moment}')
        self.assertEqual(old['submodelElements'][0]['value'], 'v1')
        _, _, audit = self.call('GET', f'/submodels/{enc(body["id"])}/$audit')
        self.assertEqual(len(audit['revisions']), 2)
        _, _, changes = self.call('GET', '/submodels/$recent-changes')
        self.assertIn(body['id'], json.dumps(changes))
        self.assertEqual(self.call('GET', f'/submodels/{enc(body["id"])}/$history')[0], 400)

    def test_signed_submodel_verifies(self):
        status, _, jws = self.call('GET', f'/submodels/{enc(SM_ID)}/$signed', raw=True)
        self.assertEqual(status, 200)
        _, _, result = self.call('POST', '/signatures/verify', jws)
        self.assertTrue(result['valid'])
        self.assertTrue(result['matchesCurrent'])

    # -- query -------------------------------------------------------------------------------------
    def test_query_language(self):
        query = {'$condition': {'$and': [
            {'$eq': [{'$field': '$sm#semanticId'}, {'$strVal': 'urn:sem:tech'}]},
            {'$gt': [{'$field': '$sme.Mass#value'}, {'$numVal': 400}]}]}, '$select': 'id'}
        status, _, body = self.call('POST', '/query/submodels', query)
        self.assertEqual((status, body['result'], body['paging_metadata']['resultType']), (200, [SM_ID], 'Identifier'))
        query = {'$condition': {'$eq': [{'$field': '$aas#assetInformation.globalAssetId'}, {'$strVal': ASSET}]}}
        _, _, body = self.call('POST', '/query/shells', query)
        self.assertEqual([s['id'] for s in body['result']], [AAS_ID])
        self.assertEqual(self.call('POST', '/query/submodels', {'$condition': {'$nope': []}})[0], 400)

    # -- registry and discovery ----------------------------------------------------------------------
    def test_auto_registry_and_discovery(self):
        status, _, descriptor = self.call('GET', f'/shell-descriptors/{enc(AAS_ID)}')
        self.assertEqual(status, 200)
        self.assertEqual(descriptor['submodelDescriptors'][0]['id'], SM_ID)
        self.assertTrue(descriptor['endpoints'][0]['protocolInformation']['href'].endswith(enc(AAS_ID)))
        link = enc(json.dumps({'name': 'serialNumber', 'value': 'S-1'}))
        _, _, found = self.call('GET', f'/lookup/shells?assetIds={link}')
        self.assertEqual(found['result'], [AAS_ID])
        bulk = [{'id': 'urn:example:forge:aas:bulk', 'endpoints': [{'interface': 'AAS-3.0', 'protocolInformation': {
            'href': 'https://example.com/shells/x'}}]}]
        status, headers, _ = self.call('POST', '/bulk/shell-descriptors', bulk)
        self.assertEqual(status, 202)
        status, _, _ = self.server.request('GET', headers['Location'])
        self.assertIn(status, (200, 302))
        self.assertEqual(self.call('GET', f'/shell-descriptors/{enc("urn:example:forge:aas:bulk")}')[0], 200)

    # -- packages and serialization ------------------------------------------------------------------
    def test_packages_and_serialization(self):
        data = (FIXTURES / 'idta-02023-carbon-footprint-v31.aasx').read_bytes()
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="pcf.aasx"\r\n'
                f'Content-Type: application/asset-administration-shell-package\r\n\r\n').encode() + data + \
            f'\r\n--{boundary}--\r\n'.encode()
        headers = {'Content-Type': f'multipart/form-data; boundary={boundary}'}
        status, _, created = self.call('POST', '/packages', body, headers=headers)
        self.assertEqual(status, 201, created)
        self.assertTrue(created['import']['ok'])
        status, _, package = self.call('GET', f'/packages/{created["packageId"]}', raw=True)
        self.assertEqual((status, package[:2]), (200, b'PK'))
        status, _, fresh = self.call('GET', f'/packages/{created["packageId"]}?fresh=true', raw=True)
        self.assertEqual((status, fresh[:2]), (200, b'PK'))
        status, location, _ = self.call('POST', '/packages-async', body, headers=headers)
        self.assertEqual(status, 202)
        status, _, _ = self.server.request('GET', location['Location'])
        self.assertEqual(status, 302)
        status, _, xml = self.call('GET', f'/serialization?aasIds={enc(AAS_ID)}', headers={'Accept': 'application/xml'},
                                   raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b'https://admin-shell.io/aas/3/1', xml)
        _, _, env30 = self.call('GET', f'/serialization?submodelIds={enc(SM_ID)}&version=3.0')
        self.assertEqual([s['id'] for s in env30['submodels']], [SM_ID])
        self.assertEqual(self.call('DELETE', f'/packages/{created["packageId"]}')[0], 204)

    def test_upload_reports_versions(self):
        body = json.dumps({'submodels': [{'modelType': 'Submodel', 'id': 'urn:example:forge:sm:upload'}]}).encode()
        status, _, report = self.call('POST', '/upload?fileName=env.json', body)
        self.assertEqual(status, 200, report)
        self.assertTrue(report['ok'])

    # -- operations ------------------------------------------------------------------------------------
    def test_operations_sync_value_and_async(self):
        path = f'/submodels/{enc(SM_ID)}/submodel-elements/Double'
        arguments = {'inputArguments': [{'value': {'modelType': 'Property', 'idShort': 'x', 'valueType': 'xs:double',
                                                   'value': '21'}}]}
        _, _, result = self.call('POST', f'{path}/invoke', arguments)
        self.assertTrue(result['success'])
        self.assertEqual(result['outputArguments'][0]['value']['value'], '42.0')
        _, _, result = self.call('POST', f'{path}/invoke/$value', {'inputArguments': {'x': 5}})
        self.assertEqual(result['outputArguments'], {'y': 10.0})
        status, headers, _ = self.call('POST', f'{path}/invoke-async', arguments)
        self.assertEqual(status, 202)
        for _ in range(50):
            status, follow, state = self.server.request('GET', headers['Location'])
            if status == 302:
                break
            time.sleep(0.02)
        self.assertEqual((status, state['executionState']), (302, 'Completed'))
        _, _, result = self.server.request('GET', follow['Location'])
        self.assertEqual(result['outputArguments'][0]['value']['value'], '42.0')
        status, _, _ = self.call('POST', f'/submodels/{enc(SM_ID)}/submodel-elements/Mass/invoke', arguments)
        self.assertEqual(status, 400)

    def test_events_and_description(self):
        _, _, description = self.call('GET', '/description')
        self.assertIn('$history', description['aasforge']['apiExtensions'])
        self.assertTrue(any('SubmodelRepositoryServiceSpecification' in p for p in description['profiles']))
        status, _, health = self.call('GET', '/health')
        self.assertEqual((status, health['status']), (200, 'UP'))
        # every API base version serves the same store
        self.assertEqual(self.server.get(f'/api/v3.0/shells/{enc(AAS_ID)}')[0], 200)
        self.assertEqual(self.server.get(f'/api/v3.2/shells/{enc(AAS_ID)}')[0], 200)


if __name__ == '__main__':
    unittest.main()
