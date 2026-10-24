"""Regression tests for the security review: each test replays an attack that worked before."""
import io
import json
import socket
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path

from helpers import Server

import battery_passport
from aasforge import aasx, canonical, formula, security
from aasforge.canonical import b64url_encode as enc

SECRET = 'review-secret'
SM = 'urn:sm'
RULES = {'AllAccessPermissionRules': {'rules': [
    {'ACL': {'ATTRIBUTES': [{'CLAIM': 'role'}], 'RIGHTS': ['ALL'], 'ACCESS': 'ALLOW'}, 'OBJECTS': [{'ROUTE': '*'}],
     'FORMULA': {'$eq': [{'$attribute': {'CLAIM': 'role'}}, {'$strVal': 'admin'}]}},
    {'ACL': {'ATTRIBUTES': [{'CLAIM': 'role'}], 'RIGHTS': ['READ'], 'ACCESS': 'ALLOW'}, 'OBJECTS': [{'ROUTE': '*'}],
     'FORMULA': {'$eq': [{'$attribute': {'CLAIM': 'role'}}, {'$strVal': 'reader'}]},
     'FILTER': {'FRAGMENT': '$sme', 'CONDITION': {'$ne': [{'$field': '$sme#idShort'}, {'$strVal': 'Secret'}]}}},
    {'ACL': {'ATTRIBUTES': [{'CLAIM': 'role'}], 'RIGHTS': ['UPDATE'], 'ACCESS': 'ALLOW'},
     'OBJECTS': [{'REFERABLE': '$sme("urn:sm").Public'}],
     'FORMULA': {'$eq': [{'$attribute': {'CLAIM': 'role'}}, {'$strVal': 'reader'}]}},
    {'ACL': {'ATTRIBUTES': [{'CLAIM': 'role'}], 'RIGHTS': ['UPDATE', 'CREATE'], 'ACCESS': 'ALLOW'},
     'OBJECTS': [{'IDENTIFIABLE': '$sm("urn:*")'}],
     'FORMULA': {'$and': [{'$eq': [{'$attribute': {'CLAIM': 'role'}}, {'$strVal': 'drafter'}]},
                          {'$eq': [{'$field': '$sm#idShort'}, {'$strVal': 'Draft'}]}]}},
]}}


def bearer(claims):
    return {'Authorization': 'Bearer ' + security.make_hs256_token(claims, SECRET)}


ADMIN, READER, DRAFTER = bearer({'sub': 'a', 'role': 'admin'}), bearer({'sub': 'r', 'role': 'reader'}), \
    bearer({'sub': 'd', 'role': 'drafter'})


def submodel(**extra):
    return {'modelType': 'Submodel', 'id': SM, 'idShort': 'Prod', 'submodelElements': [
        {'modelType': 'Property', 'idShort': 'Public', 'valueType': 'xs:string', 'value': 'p'},
        {'modelType': 'Property', 'idShort': 'Secret', 'valueType': 'xs:string', 'value': 'TOPSECRET'},
        {'modelType': 'Operation', 'idShort': 'Op'}], **extra}


def multipart(field_name, filename, data, content_type='application/octet-stream', extra=None):
    boundary = uuid.uuid4().hex
    body = b''
    for name, value in (extra or {}).items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
             f'Content-Type: {content_type}\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    return body, {'Content-Type': f'multipart/form-data; boundary={boundary}'}


class CustomRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        rules = Path(cls.tmp.name) / 'rules.json'
        rules.write_text(json.dumps(RULES))
        cls.server = Server(access_rules=str(rules), hs256_secret=SECRET)

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        cls.tmp.cleanup()

    def setUp(self):
        status, _, _ = self.call('PUT', f'/submodels/{enc(SM)}', submodel(), headers=ADMIN)
        assert status in (201, 204), status

    def call(self, method, path, body=None, **kwargs):
        return self.server.request(method, '/api/v3.1' + path, body, **kwargs)

    def test_element_grant_does_not_cover_other_elements_or_whole_submodel(self):
        path = f'/submodels/{enc(SM)}/submodel-elements'
        self.assertEqual(self.call('PATCH', f'{path}/Public/$value', 'ok', headers=READER)[0], 204)
        self.assertEqual(self.call('PATCH', f'{path}/Secret/$value', 'pwned', headers=READER)[0], 403)
        self.assertEqual(self.call('PUT', f'/submodels/{enc(SM)}', submodel(submodelElements=[]), headers=READER)[0], 403)
        self.assertEqual(self.server.forge.store.get('submodel', SM)['submodelElements'][1]['value'], 'TOPSECRET')

    def test_put_is_authorised_against_the_stored_object(self):
        status, _, _ = self.call('PUT', f'/submodels/{enc(SM)}', submodel(idShort='Draft'), headers=DRAFTER)
        self.assertEqual(status, 403)
        self.assertEqual(self.server.forge.store.get('submodel', SM)['idShort'], 'Prod')

    def test_package_export_applies_filters(self):
        status, _, data = self.call('GET', '/serialization', headers={**READER, 'Accept':
                                    'application/asset-administration-shell-package+xml'}, raw=True)
        self.assertEqual(status, 200)
        archive = zipfile.ZipFile(io.BytesIO(data))
        self.assertFalse(any(b'TOPSECRET' in archive.read(n) for n in archive.namelist()))

    def test_stored_package_is_rebuilt_for_filtered_readers(self):
        env = {'submodels': [submodel(id='urn:sm:pkg')]}
        body, headers = multipart('file', 'p.json', json.dumps(env).encode(), 'application/json')
        # a JSON environment is accepted by the package importer as well
        package = aasx.write(env, {})
        body, headers = multipart('file', 'p.aasx', package, 'application/asset-administration-shell-package')
        status, _, created = self.call('POST', '/packages', body, headers={**headers, **ADMIN})
        self.assertEqual(status, 201, created)
        status, _, data = self.call('GET', f'/packages/{created["packageId"]}', headers=READER, raw=True)
        self.assertEqual(status, 200)
        archive = zipfile.ZipFile(io.BytesIO(data))
        self.assertFalse(any(b'TOPSECRET' in archive.read(n) for n in archive.namelist()))
        status, _, raw = self.call('GET', f'/packages/{created["packageId"]}', headers=ADMIN, raw=True)
        self.assertEqual(raw, package)        # unrestricted readers get the original bytes

    def test_operation_delegation_needs_allow_list(self):
        delegated = submodel(submodelElements=[{'modelType': 'Operation', 'idShort': 'Op', 'qualifiers': [
            {'type': 'invocationDelegation', 'valueType': 'xs:string', 'value': 'file:///etc/hostname'}]}])
        self.call('PUT', f'/submodels/{enc(SM)}', delegated, headers=ADMIN)
        status, _, _ = self.call('POST', f'/submodels/{enc(SM)}/submodel-elements/Op/invoke', {}, headers=ADMIN)
        self.assertEqual(status, 403)
        delegated['submodelElements'][0]['qualifiers'][0]['value'] = 'http://127.0.0.1:9/internal'
        self.call('PUT', f'/submodels/{enc(SM)}', delegated, headers=ADMIN)
        status, _, _ = self.call('POST', f'/submodels/{enc(SM)}/submodel-elements/Op/invoke', {}, headers=ADMIN)
        self.assertEqual(status, 403)

    def test_attachment_filename_cannot_inject_headers(self):
        file_sm = submodel(submodelElements=[{'modelType': 'File', 'idShort': 'Doc', 'contentType': 'text/html'}])
        self.call('PUT', f'/submodels/{enc(SM)}', file_sm, headers=ADMIN)
        body, headers = multipart('file', 'a.html', b'<script>alert(1)</script>', 'text/html',
                                  {'fileName': 'x.pdf"\r\nSet-Cookie: injected=1'})
        path = f'/submodels/{enc(SM)}/submodel-elements/Doc/attachment'
        self.assertEqual(self.call('PUT', path, body, headers={**headers, **ADMIN})[0], 204)
        status, response_headers, _ = self.call('GET', path, headers=ADMIN, raw=True)
        self.assertEqual(status, 200)
        self.assertNotIn('Set-Cookie', response_headers)
        self.assertTrue(response_headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(response_headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn('sandbox', response_headers['Content-Security-Policy'])

    def test_audit_hides_actors_from_readers(self):
        _, _, audit = self.call('GET', f'/submodels/{enc(SM)}/$audit', headers=READER)
        self.assertTrue(audit['revisions'])
        self.assertFalse(any('actor' in r for r in audit['revisions']))
        _, _, audit = self.call('GET', f'/submodels/{enc(SM)}/$audit', headers=ADMIN)
        self.assertIn('actor', audit['revisions'][-1])

    def test_events_do_not_carry_actors(self):
        _, _, events = self.call('GET', '/events', headers=READER)
        self.assertTrue(events['events'])
        self.assertFalse(any('actor' in e for e in events['events']))

    def test_handles_need_read(self):
        self.assertEqual(self.call('GET', '/packages-async/status/unknown')[0], 403)
        self.assertEqual(self.call('GET', f'/submodels/{enc(SM)}/submodel-elements/Op/operation-status/x')[0], 403)


class DppTiers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(access_rules='dpp', hs256_secret=SECRET)
        operator = bearer({'sub': 'op', 'dpp_tier': 'economic-operator'})
        spec = battery_passport.spec(base=cls.server.base)
        spec['tiers'] = {'dbp-carbon-footprint': 'authority'}
        status, _, cls.created = cls.server.request('POST', '/dpp/passports', spec, headers=operator)
        assert status == 201, cls.created
        cls.aas = enc(cls.created['aasId'])
        cls.condition = next(i for i in cls.created['submodels'] if i.endswith('dbp-product-condition'))

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_shell_query_cannot_probe_hidden_values(self):
        query = {'$condition': {'$eq': [{'$field': '$sme.RemainingCapacity.RemainingCapacityValue#value'},
                                        {'$numVal': 213.4}]}, '$select': 'id'}
        _, _, legit = self.server.request('POST', '/api/v3.1/query/shells', query,
                                          headers=bearer({'sub': 'l', 'dpp_tier': 'legitimate-interest'}))
        status, _, public = self.server.request('POST', '/api/v3.1/query/shells', query)
        self.assertEqual(status, 200)
        self.assertEqual(public['result'], [])
        self.assertEqual(legit['result'], [self.created['aasId']])     # the probe itself works for the right tier

    def test_regex_query_is_bounded(self):
        query = {'$condition': {'$regex': [{'$strVal': 'a' * 40 + '!'}, {'$strVal': '(a|aa)+$'}]}}
        start = time.monotonic()
        status, _, _ = self.server.request('POST', '/api/v3.1/query/shells', query)
        self.assertLess(time.monotonic() - start, 6)
        self.assertIn(status, (200, 400))

    def test_restricted_submodel_is_hidden_from_derived_endpoints(self):
        status, _, rollup = self.server.get(f'/dpp/passports/{self.aas}/carbon-rollup')
        self.assertEqual(status, 200)
        self.assertIsNone(rollup['own'])
        _, _, rollup = self.server.get(f'/dpp/passports/{self.aas}/carbon-rollup',
                                       headers=bearer({'sub': 'x', 'dpp_tier': 'authority'}))
        self.assertEqual(rollup['own'], 61.4)
        _, _, checks = self.server.get(f'/dpp/passports/{self.aas}/template-check')
        self.assertNotIn('dbp-carbon-footprint', checks)
        self.assertEqual(self.server.get('/dpp/access-rules')[0], 403)

    def test_element_tier_extension_is_enforced_on_raw_api(self):
        operator = bearer({'sub': 'op', 'dpp_tier': 'economic-operator'})
        path = f'/api/v3.1/submodels/{enc(self.condition)}'
        _, _, current = self.server.get(path, headers=operator)
        target = next(e for e in current['submodelElements'] if e.get('idShort') == 'StateOfCharge')
        target.setdefault('extensions', []).append({'name': 'https://norruva.com/aasforge/dpp/accessTier',
                                                    'valueType': 'xs:string', 'value': 'authority'})
        self.assertEqual(self.server.request('PUT', path, current, headers=operator)[0], 204)
        _, _, public = self.server.get(path)
        self.assertNotIn('StateOfCharge', json.dumps(public))
        _, _, authority = self.server.get(path, headers=bearer({'sub': 'x', 'dpp_tier': 'authority'}))
        self.assertIn('StateOfCharge', json.dumps(authority))

    def test_roles_claim_does_not_grant_operator(self):
        token = {'Authorization': 'Bearer ' + security.make_hs256_token({'sub': 'x', 'roles': ['economic-operator']},
                                                                        SECRET)}
        status, _, _ = self.server.request('POST', '/dpp/passports', battery_passport.spec(), headers=token)
        self.assertEqual(status, 403)

    def test_descriptors_and_signature_oracle(self):
        hidden = next(i for i in self.created['submodels'] if i.endswith(':2'))      # authority-only test reports
        status, _, _ = self.server.get(f'/api/v3.1/submodel-descriptors/{enc(hidden)}')
        self.assertEqual(status, 404)
        _, _, shell_descriptor = self.server.get(f'/api/v3.1/shell-descriptors/{self.aas}')
        self.assertNotIn(hidden, json.dumps(shell_descriptor))
        _, _, page = self.server.get('/api/v3.1/submodel-descriptors')
        self.assertNotIn(hidden, json.dumps(page))


class Transport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(hs256_secret=SECRET)

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def raw(self, payload: bytes) -> bytes:
        host, port = self.server.base.rsplit(':', 1)
        with socket.create_connection((host.split('//')[1], int(port)), timeout=5) as sock:
            sock.sendall(payload)
            return sock.recv(200)

    def test_negative_content_length_is_rejected(self):
        response = self.raw(b'POST /api/v3.1/upload HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n' + b'x' * 100)
        self.assertIn(b' 400 ', response.split(b'\r\n')[0] + b' ')

    def test_token_without_expiry_and_bare_bearer(self):
        import hmac
        from hashlib import sha256
        header = canonical.b64url_bytes(b'{"alg":"HS256","typ":"JWT"}')
        payload = canonical.b64url_bytes(b'{"sub":"forever"}')
        signature = canonical.b64url_bytes(hmac.new(SECRET.encode(), f'{header}.{payload}'.encode(), sha256).digest())
        status, _, _ = self.server.get('/api/v3.1/shells', headers={'Authorization': f'Bearer {header}.{payload}.{signature}'})
        self.assertEqual(status, 401)
        self.assertEqual(self.server.get('/api/v3.1/shells', headers={'Authorization': 'Bearer '})[0], 401)
        bad_exp = canonical.b64url_bytes(b'{"sub":"x","exp":"never"}')
        signature = canonical.b64url_bytes(hmac.new(SECRET.encode(), f'{header}.{bad_exp}'.encode(), sha256).digest())
        status, _, _ = self.server.get('/api/v3.1/shells', headers={'Authorization': f'Bearer {header}.{bad_exp}.{signature}'})
        self.assertEqual(status, 401)

    def test_zip_bomb_is_refused(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for i in range(6):
                archive.writestr(f'aasx/files/zero{i}.bin', b'\0' * (100 * 1024 * 1024))
        with self.assertRaises(aasx.AasxError):
            aasx.read(buffer.getvalue())

    def test_malformed_xml_is_a_client_error(self):
        status, _, _ = self.server.request('POST', '/api/v3.1/upload?fileName=x.xml', b'<environment xmlns="https://admin-shell.io/aas/3/1"><oops>',
                                           headers={'Content-Type': 'application/xml'})
        self.assertEqual(status, 400)

    def test_formula_regex_caps(self):
        ctx = formula.Context('submodel', {})
        start = time.monotonic()
        self.assertIsNot(formula.evaluate(ctx, {'$regex': [{'$strVal': 'a' * 40 + '!'}, {'$strVal': '(a|aa)+$'}]}), True)
        self.assertLess(time.monotonic() - start, 1)
        self.assertIsNot(formula.evaluate(ctx, {'$regex': [{'$strVal': 'x'}, {'$strVal': 'a' * 600}]}), True)
        self.assertTrue(formula.evaluate(ctx, {'$regex': [{'$strVal': 'battery'}, {'$strVal': '^bat'}]}))


if __name__ == '__main__':
    unittest.main()
