"""Fetching a partner's twin through EDC, end to end: consumer EDC (fake management API) and the
provider's data plane (fake proxy that checks the EDR token and forwards with the consumer's BPN)
in front of a real provider server with Catena-X DTR rules."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import Server

from aasforge import catenax
from aasforge.canonical import b64url_encode as enc

PROVIDER, CONSUMER, STRANGER = 'BPNL00000000PROV', 'BPNL000000000001', 'BPNL000000000003'
AAS, SM = 'urn:uuid:partner-shell', 'urn:uuid:partner-pcf'
PCF = 'urn:samm:io.catenax.pcf:7.0.0#Pcf'


def serve(handler):
    http = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    return http, f'http://127.0.0.1:{http.server_address[1]}'


class Dataspace:
    """Provider server + provider data plane + consumer EDC."""

    def __init__(self, consumer_bpn):
        self.provider = Server(catenax={'bpn': PROVIDER, 'dataplane_url': 'PLACEHOLDER', 'dsp_url': 'https://provider/dsp'},
                               api_keys={'dp-key': {'sub': 'edc-dataplane', 'edc_dataplane': True},
                                         'owner': {'sub': 'owner', 'bpn': PROVIDER}})
        owner = {'X-API-Key': 'owner'}
        self.provider.request('POST', '/api/v3.1/submodels', {'modelType': 'Submodel', 'id': SM, 'idShort': 'PCF',
            'semanticId': {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': PCF}]},
            'submodelElements': [{'modelType': 'Property', 'idShort': 'pcfCO2eq', 'valueType': 'xs:double', 'value': '61.4'}]},
                              headers=owner)
        self.provider.request('POST', '/api/v3.1/shells', {'modelType': 'AssetAdministrationShell', 'id': AAS,
            'assetInformation': {'assetKind': 'Instance', 'globalAssetId': 'urn:uuid:battery', 'specificAssetIds': [
                {'name': 'manufacturerPartId', 'value': 'MPN-77', 'externalSubjectId': {'type': 'ExternalReference',
                 'keys': [{'type': 'GlobalReference', 'value': 'PUBLIC_READABLE'}]}},
                {'name': 'partInstanceId', 'value': 'SN-1', 'externalSubjectId': {'type': 'ExternalReference',
                 'keys': [{'type': 'GlobalReference', 'value': CONSUMER}]}}]},
            'submodels': [{'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': SM}]}]}, headers=owner)
        self.assets = {'aasforge-digital-twin-registry': f'{self.provider.base}/api/v3.1', SM:
                       f'{self.provider.base}/api/v3.1/submodels/{enc(SM)}'}
        self.consumer_bpn = consumer_bpn
        self.negotiated = []
        space = self

        class DataPlane(BaseHTTPRequestHandler):
            def _handle(self):
                asset, _, rest = self.path.lstrip('/').partition('/')
                if self.headers.get('Authorization') != f'token-{asset}' or asset not in space.assets:
                    self.send_response(403)
                    self.end_headers()
                    return
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0)) or None
                request = urllib.request.Request(f'{space.assets[asset]}/{rest}'.rstrip('/'), body, {
                    'X-API-Key': 'dp-key', 'Edc-Bpn': space.consumer_bpn, 'Content-Type': 'application/json'},
                    method=self.command)
                try:
                    with urllib.request.urlopen(request) as response:
                        status, payload = response.status, response.read()
                except urllib.error.HTTPError as error:
                    status, payload = error.code, error.read()
                self.send_response(status)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _handle

            def log_message(self, *args):
                pass

        class ConsumerEdc(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
                if self.path.endswith('/v3/catalog/request'):
                    datasets = [{'@id': 'aasforge-digital-twin-registry', '@type': 'Dataset',
                                 'dct:type': {'@id': 'https://w3id.org/catenax/taxonomy#DigitalTwinRegistry'},
                                 'hasPolicy': {'@id': 'offer-dtr', '@type': 'Offer', 'permission': {'action': 'use'}}},
                                {'@id': SM, '@type': 'Dataset', 'dct:type': {'@id': 'cx-taxo:Submodel'},
                                 'hasPolicy': [{'@id': 'offer-sm', '@type': 'Offer', 'permission': {'action': 'use'}}]}]
                    reply = (200, {'@type': 'Catalog', 'participantId': PROVIDER, 'dataset': datasets})
                elif self.path.endswith('/v3/edrs'):
                    space.negotiated.append(body)
                    reply = (200, {'@id': f'negotiation-{len(space.negotiated)}'})
                elif self.path.endswith('/v3/edrs/request'):
                    asset = body['filterExpression'][0]['operandRight']
                    done = any(n['policy']['target'] == asset for n in space.negotiated)
                    reply = (200, [{'transferProcessId': f'tp-{asset}', 'assetId': asset}] if done else [])
                else:
                    reply = (404, {})
                self.reply(*reply)

            def do_GET(self):
                asset = self.path.split('/v3/edrs/tp-', 1)[-1].split('/dataaddress')[0]
                asset = urllib.request.unquote(asset)
                self.reply(200, {'@type': 'DataAddress', 'endpoint': f'{space.dataplane_url}/{asset}',
                                 'authorization': f'token-{asset}', 'tx-auth:expiresIn': '300'})

            def reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.dataplane, self.dataplane_url = serve(DataPlane)
        self.edc, edc_url = serve(ConsumerEdc)
        self.provider.forge.catenax.dataplane_url = self.dataplane_url
        self.twins = catenax.PartnerTwins(catenax.EdcConsumer(catenax.EdcClient(f'{edc_url}/management', 'k'),
                                                              poll_interval=0.01, timeout=5), 'https://provider/dsp', PROVIDER)

    def close(self):
        self.dataplane.shutdown()
        self.edc.shutdown()
        self.provider.close()


class PartnerTwinTests(unittest.TestCase):
    def test_partner_fetches_twin_and_submodel(self):
        space = Dataspace(CONSUMER)
        try:
            ids = space.twins.lookup([{'name': 'partInstanceId', 'value': 'SN-1'}])
            self.assertEqual(ids, [AAS])
            descriptor = space.twins.shell(AAS)
            self.assertEqual({a['name'] for a in descriptor['specificAssetIds']}, {'manufacturerPartId', 'partInstanceId'})
            values = space.twins.submodel(descriptor, PCF, value_only=True)
            self.assertEqual(values, {'pcfCO2eq': 61.4})
            offers = [n['policy'] for n in space.negotiated]
            self.assertEqual([o['target'] for o in offers], ['aasforge-digital-twin-registry', SM])
            self.assertTrue(all(o['assigner'] == PROVIDER and o['@type'] == 'Offer' for o in offers))
        finally:
            space.close()

    def test_other_partner_sees_only_public_ids(self):
        space = Dataspace(STRANGER)
        try:
            self.assertEqual(space.twins.lookup([{'name': 'partInstanceId', 'value': 'SN-1'}]), [])
            self.assertEqual(space.twins.lookup([{'name': 'manufacturerPartId', 'value': 'MPN-77'}]), [AAS])
            descriptor = space.twins.shell(AAS)
            self.assertEqual(set(descriptor), {'id', 'specificAssetIds', 'submodelDescriptors'})
        finally:
            space.close()

    def test_missing_offer_is_reported(self):
        space = Dataspace(CONSUMER)
        try:
            with self.assertRaises(catenax.EdcError):
                space.twins.consumer.access('https://provider/dsp', PROVIDER, asset_id='urn:uuid:unknown')
        finally:
            space.close()


if __name__ == '__main__':
    unittest.main()
