"""Catena-X DTR access semantics (eclipse-tractusx/sldt-digital-twin-registry, classic model) and
EDC Management API v3 provisioning against a fake EDC."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import Server

from aasforge import catenax
from aasforge.canonical import b64url_encode as enc

OWNER, BPN1, BPN2, BPN3 = 'BPNL00000000OWNR', 'BPNL000000000001', 'BPNL000000000002', 'BPNL000000000003'
AAS = 'urn:uuid:dtr-shell-1'
SM = 'urn:uuid:dtr-submodel-1'


def subject(*values):
    return {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': v} for v in values]}


def shell(asset_ids):
    return {'modelType': 'AssetAdministrationShell', 'id': AAS, 'idShort': 'Battery',
            'assetInformation': {'assetKind': 'Instance', 'globalAssetId': 'urn:uuid:asset-1', 'specificAssetIds': asset_ids},
            'submodels': [{'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': SM}]}]}


ASSET_IDS = [  # the example of the DTR documentation
    {'name': 'partInstanceId', 'value': 'SN-001'},
    {'name': 'customerPartId', 'value': 'C-100', 'externalSubjectId': subject(BPN1)},
    {'name': 'manufacturerId', 'value': OWNER, 'externalSubjectId': subject(BPN1, BPN2)},
    {'name': 'manufacturerPartId', 'value': 'MPN-77', 'externalSubjectId': subject('PUBLIC_READABLE')},
]


class DigitalTwinRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(catenax={'bpn': OWNER, 'dataplane_url': 'https://dp.example.com/api/public',
                                     'dsp_url': 'https://cp.example.com/api/v1/dsp'},
                            api_keys={'dp-key': {'sub': 'edc-dataplane', 'edc_dataplane': True},
                                      'owner-key': {'sub': 'owner', 'bpn': OWNER}})
        owner = {'X-API-Key': 'owner-key'}
        assert cls.server.request('POST', '/api/v3.1/submodels', {'modelType': 'Submodel', 'id': SM, 'idShort': 'PCF',
            'semanticId': {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference',
                                                                    'value': 'urn:samm:io.catenax.pcf:7.0.0#Pcf'}]}},
                                  headers=owner)[0] == 201
        status, _, body = cls.server.request('POST', '/api/v3.1/shells', shell(ASSET_IDS), headers=owner)
        assert status == 201, body

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def descriptor(self, bpn=None, key='dp-key'):
        headers = {'X-API-Key': key} if key else {}
        if bpn:
            headers['Edc-Bpn'] = bpn
        return self.server.get(f'/api/v3.1/shell-descriptors/{enc(AAS)}', headers=headers)

    def test_owner_sees_everything(self):
        status, _, body = self.descriptor(key='owner-key')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['specificAssetIds']), 4)

    def test_partner_sees_only_its_asset_ids_and_full_fields(self):
        status, _, body = self.descriptor(BPN1)
        self.assertEqual(status, 200)
        names = [a['name'] for a in body['specificAssetIds']]
        self.assertEqual(names, ['customerPartId', 'manufacturerId', 'manufacturerPartId'])
        manufacturer = next(a for a in body['specificAssetIds'] if a['name'] == 'manufacturerId')
        self.assertEqual([k['value'] for k in manufacturer['externalSubjectId']['keys']], [BPN1])   # BPN2 filtered out
        self.assertEqual(body['idShort'], 'Battery')
        self.assertEqual(body['globalAssetId'], 'urn:uuid:asset-1')

    def test_public_readable_only_gets_reduced_view(self):
        status, _, body = self.descriptor(BPN3)
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {'id', 'specificAssetIds', 'submodelDescriptors'})
        self.assertEqual([a['name'] for a in body['specificAssetIds']], ['manufacturerPartId'])

    def test_header_is_ignored_without_dataplane_identity(self):
        status, _, body = self.descriptor(BPN1, key=None)
        self.assertEqual(set(body), {'id', 'specificAssetIds', 'submodelDescriptors'})   # treated as no BPN

    def test_lookup_matches_only_visible_asset_ids(self):
        def lookup(name, value, bpn=None, key='dp-key'):
            link = enc(json.dumps({'name': name, 'value': value}))
            headers = {'X-API-Key': key}
            if bpn:
                headers['Edc-Bpn'] = bpn
            return self.server.get(f'/api/v3.1/lookup/shells?assetIds={link}', headers=headers)[2]['result']
        self.assertEqual(lookup('partInstanceId', 'SN-001', key='owner-key'), [AAS])
        self.assertEqual(lookup('partInstanceId', 'SN-001', BPN1), [])
        self.assertEqual(lookup('customerPartId', 'C-100', BPN1), [AAS])
        self.assertEqual(lookup('customerPartId', 'C-100', BPN3), [])
        self.assertEqual(lookup('manufacturerPartId', 'MPN-77', BPN3), [AAS])

    def test_public_readable_is_limited_to_allowed_names(self):
        bad = shell([{'name': 'partInstanceId', 'value': 'SN-2', 'externalSubjectId': subject('PUBLIC_READABLE')}])
        bad['id'] = 'urn:uuid:bad'
        status, _, body = self.server.request('POST', '/api/v3.1/shells', bad, headers={'X-API-Key': 'owner-key'})
        self.assertEqual(status, 400, body)
        status, _, _ = self.server.request('POST', '/api/v3.1/shell-descriptors',
                                           {'id': 'urn:uuid:bad2', 'specificAssetIds': bad['assetInformation']['specificAssetIds']},
                                           headers={'X-API-Key': 'owner-key'})
        self.assertEqual(status, 400)

    def test_submodel_descriptor_has_dsp_endpoint(self):
        _, _, body = self.descriptor(key='owner-key')
        endpoints = body['submodelDescriptors'][0]['endpoints']
        dsp = next(e for e in endpoints if e['protocolInformation'].get('subprotocol') == 'DSP')
        self.assertEqual(dsp['interface'], 'SUBMODEL-3.0')
        self.assertEqual(dsp['protocolInformation']['subprotocolBody'], f'id={SM};dspEndpoint=https://cp.example.com/api/v1/dsp')
        self.assertEqual(dsp['protocolInformation']['href'], 'https://dp.example.com/api/public')

    def test_repository_shell_filters_asset_ids_too(self):
        _, _, body = self.server.get(f'/api/v3.1/shells/{enc(AAS)}', headers={'X-API-Key': 'dp-key', 'Edc-Bpn': BPN2})
        self.assertEqual([a['name'] for a in body['assetInformation']['specificAssetIds']],
                         ['manufacturerId', 'manufacturerPartId'])


class FakeEdc:
    def __init__(self):
        self.requests, self.store = [], {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'null')
                fake.requests.append((self.command, self.path, self.headers.get('X-Api-Key'), body))
                if self.headers.get('X-Api-Key') != 'mgmt-key':
                    self.send_response(401)
                elif self.command == 'POST' and (self.path, body['@id']) in fake.store:
                    self.send_response(409)
                else:
                    fake.store[(self.path, body['@id'])] = body
                    self.send_response(200 if self.command == 'POST' else 204)
                self.send_header('Content-Length', '0')
                self.end_headers()

            do_POST = do_PUT = _handle

            def log_message(self, *args):
                pass
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.http.server_address[1]}/management'


def contract_policy_of(edc, asset_id):
    return edc.store[('/management/v3/contractdefinitions', f'{asset_id}#contract')]['contractPolicyId']


class EdcProvisioning(unittest.TestCase):
    def test_provision_is_idempotent_and_well_formed(self):
        server = Server(catenax={'bpn': OWNER})
        try:
            server.forge.repository.save('submodel', {'modelType': 'Submodel', 'id': SM, 'idShort': 'PCF', 'semanticId': {
                'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': 'urn:samm:io.catenax.pcf:7.0.0#Pcf'}]}})
            edc = FakeEdc()
            client = catenax.EdcClient(edc.url, 'mgmt-key')
            policy = catenax.bpn_policy('partners', [BPN1, BPN2])
            first = client.provision(server.forge, server.forge.catenax, 'https://aas.example.com', 'dp-secret', policy)
            self.assertEqual(first['assets'], {SM: 'created', 'aasforge-digital-twin-registry': 'created'})
            second = client.provision(server.forge, server.forge.catenax, 'https://aas.example.com', 'dp-secret', policy)
            self.assertEqual(set(second['assets'].values()), {'updated'})
            asset = edc.store[('/management/v3/assets', SM)]
            self.assertEqual(asset['dataAddress']['baseUrl'], f'https://aas.example.com/api/v3.1/submodels/{enc(SM)}')
            self.assertEqual(asset['dataAddress']['authCode'], 'dp-secret')
            self.assertEqual(asset['properties']['aas-semantics:semanticId'], {'@id': 'urn:samm:io.catenax.pcf:7.0.0#Pcf'})
            registry = edc.store[('/management/v3/assets', 'aasforge-digital-twin-registry')]
            self.assertEqual(registry['properties']['dct:type'], {'@id': 'cx-taxo:DigitalTwinRegistry'})
            self.assertEqual(registry['dataAddress']['proxyMethod'], 'true')
            stored_policy = edc.store[('/management/v3/policydefinitions', 'partners')]
            permission = stored_policy['policy']['permission'][0]
            self.assertEqual(permission['action'], 'access')
            self.assertEqual(permission['constraint'], {'leftOperand': 'BusinessPartnerNumber', 'operator': 'isAnyOf',
                                                        'rightOperand': [BPN1, BPN2]})
            usage = edc.store[('/management/v3/policydefinitions', 'partners-usage')]['policy']['permission'][0]
            self.assertEqual(usage['action'], 'use')
            self.assertEqual(contract_policy_of(edc, SM), 'partners-usage')
            contract = edc.store[('/management/v3/contractdefinitions', f'{SM}#contract')]
            self.assertEqual(contract['assetsSelector'][0]['operandRight'], SM)
            with self.assertRaises(catenax.EdcError):
                catenax.EdcClient(edc.url, 'wrong').provision(server.forge, None, 'https://x', 'k', policy)
            edc.http.shutdown()
        finally:
            server.close()


if __name__ == '__main__':
    unittest.main()
