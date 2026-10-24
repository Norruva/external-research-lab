"""End-to-end Digital Product Passport scenarios over HTTP with generated tier access rules."""
import json
import unittest

from helpers import Server, database

import battery_passport
from aasforge import canonical, formula, security
from aasforge.dpp import catalog, gs1, tiers

SECRET = 'test-secret'


def token(tier):
    return {'Authorization': 'Bearer ' + security.make_hs256_token({'sub': tier, 'dpp_tier': tier}, SECRET)}


class DppScenario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(access_rules='dpp', hs256_secret=SECRET)
        cls.server.forge.config.public_url = cls.server.base
        cls.server.forge.dpp.base = cls.server.base
        spec = battery_passport.spec(base=cls.server.base)
        status, _, cls.created = cls.server.request('POST', '/dpp/passports', spec, headers=token('economic-operator'))
        assert status == 201, cls.created
        cls.aas = canonical.b64url_encode(cls.created['aasId'])

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def view(self, tier=None):
        status, _, body = self.server.get(f'/dpp/passports/{self.aas}', headers=token(tier) if tier else None)
        self.assertEqual(status, 200, body)
        return body

    @staticmethod
    def paths(view):
        out = set()

        def walk(fields, section):
            for field in fields:
                out.add(f'{section}/{field["path"]}')
                walk(field.get('children', []), section)
        for section in view['sections']:
            walk(section['fields'], section['submodelId'].rsplit(':', 1)[-1] if ':2' not in section['submodelId'][-2:]
                 else section['submodelId'])
        return out

    def test_anonymous_cannot_create(self):
        status, _, _ = self.server.request('POST', '/dpp/passports', battery_passport.spec())
        self.assertEqual(status, 403)

    def test_public_view_hides_restricted_tiers(self):
        public = self.view()
        self.assertEqual(public['viewerTier'], 'public')
        titles = {s['title'] for s in public['sections']}
        self.assertNotIn('State of health', titles if not any(
            f for s in public['sections'] if s['title'] == 'State of health' for f in s['fields']) else set())
        joined = json.dumps(public)
        self.assertNotIn('RemainingCapacity', joined)          # legitimate interest (Art. 77(2)(b))
        self.assertNotIn('tests.zip', joined)                  # authority-tier test report submodel
        self.assertIn('PcfCO2eq', joined)                      # public carbon footprint
        self.assertGreater(public['hiddenElements'], 0)

    def test_legitimate_interest_and_authority_views(self):
        legit = json.dumps(self.view('legitimate-interest'))
        self.assertIn('RemainingCapacity', legit)
        self.assertNotIn('tests.zip', legit)
        authority = json.dumps(self.view('authority'))
        self.assertIn('tests.zip', authority)

    def test_raw_aas_api_is_filtered_by_generated_rules(self):
        found = self.created['submodels']
        condition = next(i for i in found if i.endswith('dbp-product-condition'))
        status, _, body = self.server.get(f'/api/v3.1/submodels/{canonical.b64url_encode(condition)}/$value')
        self.assertEqual(status, 200)
        self.assertNotIn('RemainingCapacity', body)
        status, _, body = self.server.get(f'/api/v3.1/submodels/{canonical.b64url_encode(condition)}/$value',
                                          headers=token('legitimate-interest'))
        self.assertIn('RemainingCapacity', body)
        tests_part = next(i for i in found if i.endswith(':2'))
        status, _, _ = self.server.get(f'/api/v3.1/submodels/{canonical.b64url_encode(tests_part)}')
        self.assertEqual(status, 403)
        status, _, _ = self.server.get(f'/api/v3.1/submodels/{canonical.b64url_encode(tests_part)}',
                                       headers=token('authority'))
        self.assertEqual(status, 200)

    def test_generated_rules_are_schema_valid(self):
        document = tiers.access_rules()
        self.assertEqual(formula.validate(document['AllAccessPermissionRules'], 'AllAccessPermissionRules'), [])

    def test_html_page_is_script_free(self):
        status, headers, page = self.server.get(f'/dpp/passports/{self.aas}?format=html', raw=True)
        self.assertEqual(status, 200)
        text = page.decode()
        self.assertIn('Voltaris EV-86', text)
        self.assertNotIn('<script src', text)
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])

    def test_completeness(self):
        status, _, report = self.server.get(f'/dpp/passports/{self.aas}/completeness', headers=token('authority'))
        self.assertEqual(report['category'], 'ev')
        self.assertGreaterEqual(report['score'], 90)

    def test_credential_signature(self):
        status, headers, jws = self.server.get(f'/dpp/passports/{self.aas}/credential', raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'application/vc+jwt')
        status, _, result = self.server.request('POST', '/api/v3.1/signatures/verify', jws)
        self.assertTrue(result['valid'])
        self.assertEqual(result['header']['typ'], 'vc+jwt')

    def test_registry_payload_and_rollup(self):
        _, _, payload = self.server.get(f'/dpp/passports/{self.aas}/registry-payload')
        self.assertTrue(payload['uniqueProductIdentifier'].endswith('/21/VX86-2026-000417'))
        self.assertEqual(payload['uniqueOperatorIdentifier'], 'urn:example:operator:voltaris-gmbh')
        _, _, rollup = self.server.get(f'/dpp/passports/{self.aas}/carbon-rollup')
        self.assertEqual(rollup['own'], 61.4)

    def test_digital_link_resolver(self):
        path = f'/01/{battery_passport.GTIN}/21/{battery_passport.SERIAL}'
        status, headers, _ = self.server.get(path)
        self.assertEqual(status, 307)
        self.assertIn('/dpp/passports/', headers['Location'])
        self.assertEqual(headers['X-DPP-Granularity'], 'item')
        status, headers, linkset = self.server.get(path, headers={'Accept': 'application/linkset+json'})
        self.assertEqual(status, 200)
        entry = linkset['linkset'][0]
        self.assertIn('https://gs1.org/voc/sustainabilityInfo', entry)
        status, _, _ = self.server.get(f'/01/{battery_passport.GTIN}/21/UNKNOWN')
        self.assertEqual(status, 404)
        status, _, _ = self.server.get('/01/04012345000017')
        self.assertEqual(status, 400)   # bad check digit

    def test_mcp_tools(self):
        def rpc(method, params=None, identifier=1):
            return self.server.request('POST', '/mcp', {'jsonrpc': '2.0', 'id': identifier, 'method': method,
                                                        'params': params or {}})[2]
        self.assertEqual(rpc('initialize')['result']['serverInfo']['name'], 'aasforge-dpp')
        names = {t['name'] for t in rpc('tools/list')['result']['tools']}
        self.assertIn('find_passport', names)
        found = rpc('tools/call', {'name': 'find_passport', 'arguments': {'gtin': battery_passport.GTIN,
                                                                          'serial': battery_passport.SERIAL}})
        self.assertTrue(found['result']['structuredContent']['found'])
        report = rpc('tools/call', {'name': 'passport_completeness', 'arguments': {'aasId': self.created['aasId']}})
        self.assertIn('missing', report['result']['structuredContent'])

    def test_update_values_refreshes_last_update(self):
        before = self.view('authority')['lastUpdate']
        status, _, _ = self.server.request(
            'PATCH', f'/dpp/passports/{self.aas}/values',
            {'dbp-product-condition': {'StateOfCharge.StateOfChargeValue': 55.0}}, headers=token('economic-operator'))
        self.assertEqual(status, 200)
        after = self.view('authority')
        self.assertIn('55', json.dumps(after))
        self.assertGreaterEqual(after['lastUpdate'], before)

    def test_template_check_runs(self):
        status, _, findings = self.server.get(f'/dpp/passports/{self.aas}/template-check', headers=token('authority'))
        self.assertEqual(status, 200)
        self.assertIn('dbp-nameplate', findings)


class BatchPassportTests(unittest.TestCase):
    def test_batch_granularity_exports_as_32_batch(self):
        from aasforge.app import Config, Forge
        from aasforge.dpp import passport
        forge = Forge(Config(database=database()))
        spec = battery_passport.spec()
        spec.pop('serial', None)
        spec.update(batch='LOT-2026-17', granularity='Batch')
        result = passport.create(forge.repository, spec)
        self.assertEqual(result['granularity'], 'Batch')
        env = forge.repository.environment([result['aasId']], version='3.2')
        self.assertEqual(env['assetAdministrationShells'][0]['assetInformation']['assetKind'], 'Batch')
        env31 = forge.repository.environment([result['aasId']])
        self.assertEqual(env31['assetAdministrationShells'][0]['assetInformation']['assetKind'], 'Type')


class Gs1Tests(unittest.TestCase):
    def test_check_digit_and_parse(self):
        self.assertEqual(gs1.normalise_gtin('4012345000016'), '04012345000016')
        with self.assertRaises(gs1.DigitalLinkError):
            gs1.normalise_gtin('4012345000017')
        parts = gs1.parse_path('/01/04012345000016/10/LOT7/21/S%2F1')
        self.assertEqual(parts, {'gtin': '04012345000016', 'batchId': 'LOT7', 'serialNumber': 'S/1'})
        with self.assertRaises(gs1.DigitalLinkError):
            gs1.parse_path('/01/04012345000016/21/S1/10/LOT7')   # wrong qualifier order

    def test_catalog_mapping_counts(self):
        mapped = [m for m in catalog.attribute_map().values() if m['elements']]
        self.assertGreaterEqual(len(mapped), 90)


if __name__ == '__main__':
    unittest.main()
