"""Query Language semantics (spec examples), access rules with FILTER, tokens and JWS signatures."""
import json
import unittest

import helpers  # noqa: F401 - sets sys.path

from aasforge import formula, security, signing

AAS = {'modelType': 'AssetAdministrationShell', 'id': 'https://example.com/asset-administration-shell-1',
       'assetInformation': {'assetKind': 'Instance', 'globalAssetId': 'urn:asset-administration-shell-1',
                            'specificAssetIds': [{'name': 'supplierId', 'value': 'aas-1'},
                                                 {'name': 'customerId', 'value': 'aas-2'}]},
       'submodels': [{'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': 'https://example.com/submodel-1'}]},
                     {'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': 'https://example.com/submodel-2'}]}]}
SUBMODEL = {'modelType': 'Submodel', 'id': 'urn:sm:1', 'semanticId': {'type': 'ExternalReference', 'keys': [
    {'type': 'GlobalReference', 'value': 'urn:semantic:nameplate'}]}, 'submodelElements': [
    {'modelType': 'Property', 'idShort': 'Width', 'valueType': 'xs:double', 'value': '80',
     'semanticId': {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': '0173-1#02-BAF016#006'}]}},
    {'modelType': 'Property', 'idShort': 'Secret', 'valueType': 'xs:string', 'value': 'x',
     'semanticId': {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': 'urn:restricted'}]}},
    {'modelType': 'SubmodelElementList', 'idShort': 'Docs', 'typeValueListElement': 'Property', 'valueTypeListElement': 'xs:string',
     'value': [{'modelType': 'Property', 'valueType': 'xs:string', 'value': 'public.pdf'},
               {'modelType': 'Property', 'valueType': 'xs:string', 'value': 'internal.pdf'}]}]}


def f(path):
    return {'$field': path}


class SpecExamples(unittest.TestCase):
    """Examples from IDTA-01002 Query Language chapter (comparison and $match tables)."""

    def run_case(self, expression, expected):
        self.assertEqual(formula.evaluate(formula.Context('shell', AAS), expression), expected, expression)

    def test_comparisons(self):
        cases = [
            ({'$eq': [f('$aas#idShort'), f('$aas#assetInformation.assetType')]}, True),
            ({'$le': [f('$aas#idShort'), f('$aas#assetInformation.assetType')]}, True),
            ({'$ne': [f('$aas#idShort'), f('$aas#assetInformation.assetType')]}, False),
            ({'$le': [{'$numVal': 1}, {'$numVal': 2}]}, True),
            ({'$gt': [{'$numVal': 1}, {'$numVal': 2}]}, False),
            ({'$eq': [{'$numVal': 13}, {'$strVal': '13'}]}, False),
            ({'$lt': [{'$strVal': 'a'}, {'$strVal': 'b'}]}, True),
            ({'$gt': [{'$strVal': '11'}, {'$strVal': '2'}]}, False),
            ({'$eq': [f('$aas#assetInformation.assetKind'), f('$aas#submodels[]')]}, False),
            ({'$ne': [f('$aas#assetInformation.assetKind'), f('$aas#submodels[]')]}, True),
        ]
        for expression, expected in cases:
            self.run_case(expression, expected)

    def test_match_semantics(self):
        name, value = f('$aas#assetInformation.specificAssetIds[].name'), f('$aas#assetInformation.specificAssetIds[].value')
        self.run_case({'$match': [{'$eq': [name, {'$strVal': 'supplierId'}]}, {'$eq': [value, {'$strVal': 'aas-1'}]}]}, True)
        self.run_case({'$match': [{'$eq': [name, {'$strVal': 'supplierId'}]}, {'$eq': [value, {'$strVal': 'aas-2'}]}]}, False)
        self.run_case({'$and': [{'$eq': [name, {'$strVal': 'supplierId'}]}, {'$eq': [value, {'$strVal': 'aas-2'}]}]}, True)

    def test_numeric_query_and_invalid_comparison(self):
        ctx = formula.Context('submodel', SUBMODEL)
        query = {'$and': [{'$eq': [f('$sme.Width#semanticId'), {'$strVal': '0173-1#02-BAF016#006'}]},
                          {'$lt': [f('$sme.Width#value'), {'$numVal': 100}]}]}
        self.assertTrue(formula.matches(ctx, query))
        self.assertIsNone(formula.evaluate(ctx, {'$gt': [f('$sme.Secret#value'), {'$numVal': 1}]}))

    def test_run_query_select_and_filters(self):
        query = {'$condition': {'$eq': [f('$sm#semanticId'), {'$strVal': 'urn:semantic:nameplate'}]}, '$select': 'id'}
        self.assertEqual(formula.run_query(query, 'submodel', [SUBMODEL]), ['urn:sm:1'])
        query = {'$condition': {'$boolean': True}, '$filters': [
            {'$fragment': '$sme.Docs[]', '$condition': {'$eq': [f('$sme.Docs[]#value'), {'$strVal': 'public.pdf'}]}}]}
        result = formula.run_query(query, 'submodel', [SUBMODEL])[0]
        docs = next(e for e in result['submodelElements'] if e['idShort'] == 'Docs')
        self.assertEqual([d['value'] for d in docs['value']], ['public.pdf'])

    def test_schema_validation(self):
        with self.assertRaises(formula.FormulaError):
            formula.run_query({'$condition': {'$and': [{'$boolean': True}]}}, 'submodel', [])


RULES = {'AllAccessPermissionRules': {
    'DEFACLS': [{'name': 'readers', 'acl': {'ATTRIBUTES': [{'CLAIM': 'role'}], 'RIGHTS': ['READ'], 'ACCESS': 'ALLOW'}}],
    'rules': [
        {'ACL': {'ATTRIBUTES': [{'GLOBAL': 'ANONYMOUS'}], 'RIGHTS': ['READ'], 'ACCESS': 'ALLOW'},
         'OBJECTS': [{'ROUTE': '/submodels'}], 'FORMULA': {'$boolean': True},
         'FILTER': {'FRAGMENT': '$sme', 'CONDITION': {'$ne': [f('$sme#semanticId'), {'$strVal': 'urn:restricted'}]}}},
        {'USEACL': 'readers', 'OBJECTS': [{'IDENTIFIABLE': '$sm("urn:sm:*")'}],
         'FORMULA': {'$eq': [{'$attribute': {'CLAIM': 'role'}}, {'$strVal': 'auditor'}]}},
        {'ACL': {'ATTRIBUTES': [{'CLAIM': 'role'}], 'RIGHTS': ['ALL'], 'ACCESS': 'DISABLED'},
         'OBJECTS': [{'ROUTE': '*'}], 'FORMULA': {'$boolean': True}},
    ]}}


class AccessRuleTests(unittest.TestCase):
    def setUp(self):
        self.rules = security.AccessRules(RULES)

    def decide(self, claims, right='READ', anonymous=False):
        subject = security.Subject(claims, anonymous)
        return self.rules.decide(subject, right, security.Target('/submodels/abc', 'submodel', SUBMODEL))

    def test_anonymous_gets_filtered_view(self):
        decision = self.decide({}, anonymous=True)
        self.assertTrue(decision.allowed)
        filtered = security.restrict_submodel(SUBMODEL, decision)
        self.assertNotIn('Secret', [e.get('idShort') for e in filtered['submodelElements']])

    def test_claim_based_full_view_and_union_of_filters(self):
        decision = self.decide({'role': 'auditor'})
        self.assertIsNone(decision.filters)      # one allowing rule has no FILTER -> nothing is pruned
        self.assertFalse(self.decide({'role': 'auditor'}, right='DELETE').allowed)

    def test_invalid_rules_rejected(self):
        with self.assertRaises(ValueError):
            security.AccessRules({'AllAccessPermissionRules': {'rules': [{'ACL': {}}]}})


class TokenAndSignatureTests(unittest.TestCase):
    def test_hs256_tokens(self):
        auth = security.Authenticator(hs256_secret='s3cret', api_keys={'k1': {'sub': 'svc'}})
        token = security.make_hs256_token({'sub': 'alice'}, 's3cret')
        self.assertEqual(auth.authenticate({'Authorization': f'Bearer {token}'}).claims['sub'], 'alice')
        self.assertEqual(auth.authenticate({'X-API-Key': 'k1'}).claims['sub'], 'svc')
        with self.assertRaises(security.AuthError):
            auth.authenticate({'Authorization': f'Bearer {token[:-2]}xx'})
        with self.assertRaises(security.AuthError):
            auth.authenticate({'Authorization': 'Bearer ' + security.make_hs256_token({'sub': 'a'}, 's3cret', -120)})
        self.assertTrue(auth.authenticate({}).anonymous)

    def test_jws_round_trip_and_tamper_detection(self):
        signer = signing.Signer.self_signed('test')
        jws = signer.sign(SUBMODEL)
        result = signing.verify(jws, [signer.certificate_pem()])
        self.assertTrue(result['trusted'])
        self.assertEqual(result['payload']['id'], 'urn:sm:1')
        self.assertEqual(set(result['header']), {'alg', 'typ', 'sid', 'sigT'})
        header, payload, signature = jws.split('.')
        from aasforge import canonical
        tampered = canonical.b64url_bytes(json.dumps({**SUBMODEL, 'id': 'urn:sm:evil'}).encode())
        with self.assertRaises(signing.SignatureError):
            signing.verify(f'{header}.{tampered}.{signature}')
        self.assertFalse(signing.verify(jws)['trusted'])


if __name__ == '__main__':
    unittest.main()
