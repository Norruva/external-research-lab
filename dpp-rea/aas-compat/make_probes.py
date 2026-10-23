"""Generate minimal AAS environments that each exercise one metamodel version boundary.

Usage: python3 make_probes.py <out-dir>

Writes <probe>.json for every probe plus baseline XML in the 3/0, 3/1 and 3/2 namespaces.
"""
import copy
import json
import sys
from pathlib import Path

BASE = {
    'assetAdministrationShells': [{
        'modelType': 'AssetAdministrationShell',
        'id': 'urn:example:norruva:aas:probe',
        'idShort': 'ProbeAAS',
        'assetInformation': {'assetKind': 'Instance', 'globalAssetId': 'urn:example:norruva:asset:probe'},
        'submodels': [{'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': 'urn:example:norruva:sm:probe'}]}],
    }],
    'submodels': [{
        'modelType': 'Submodel',
        'id': 'urn:example:norruva:sm:probe',
        'idShort': 'BatteryPassportProbe',
        'semanticId': {'type': 'ExternalReference',
                       'keys': [{'type': 'GlobalReference', 'value': 'urn:example:norruva:semantic:probe'}]},
        'submodelElements': [
            {'modelType': 'Property', 'idShort': 'BatteryCategory', 'valueType': 'xs:string', 'value': 'EV'},
            {'modelType': 'File', 'idShort': 'DeclarationOfConformity', 'contentType': 'application/pdf',
             'value': '/aasx/files/DeclarationOfConformity.pdf'},
        ],
    }],
}


def element(env, id_short):
    return next(e for e in env['submodels'][0]['submodelElements'] if e['idShort'] == id_short)


def probe(name, change, introduced, expectation):
    env = copy.deepcopy(BASE)
    change(env)
    return name, env, introduced, expectation


PROBES = [
    probe('p00_baseline', lambda e: None, '3.0', 'valid in 3.0, 3.1 and 3.2'),
    probe('p31_idshort_hyphen',
          lambda e: element(e, 'BatteryCategory').update(idShort='Battery-Category'),
          '3.1', 'idShort pattern relaxed to allow hyphens'),
    probe('p31_assetkind_role',
          lambda e: e['assetAdministrationShells'][0]['assetInformation'].update(assetKind='Role'),
          '3.1', 'AssetKind gains Role'),
    probe('p31_file_without_contenttype',
          lambda e: element(e, 'DeclarationOfConformity').pop('contentType'),
          '3.1', 'File.contentType no longer required'),
    probe('p31_contenttype_120_chars',
          lambda e: element(e, 'DeclarationOfConformity').update(
              contentType='application/vnd.openxmlformats-officedocument.' + 'x' * 70),
          '3.1', 'contentType max length 100 -> 128'),
    probe('p31_file_value_with_space',
          lambda e: element(e, 'DeclarationOfConformity').update(value='/aasx/files/EU Declaration.pdf'),
          '3.0-only', 'valid in 3.0; 3.1 adds a URI-reference pattern that rejects spaces'),
    probe('p32_assetkind_batch',
          lambda e: e['assetAdministrationShells'][0]['assetInformation'].update(assetKind='Batch'),
          '3.2', 'AssetKind gains Batch (batch-level passports)'),
    probe('p32_admin_created_at',
          lambda e: e['submodels'][0].update(administration={
              'version': '1', 'revision': '0', 'createdAt': '2026-10-23T08:00:00Z',
              'updatedAt': '2026-10-23T09:00:00Z'}),
          '3.2', 'AdministrativeInformation gains createdAt/updatedAt'),
]


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, env, introduced, expectation in PROBES:
        (out / f'{name}.json').write_text(json.dumps(env, indent=1))
        manifest[name] = {'introduced': introduced, 'expectation': expectation}
    (out / 'probes-manifest.json').write_text(json.dumps(manifest, indent=1))
    import aas_core3.jsonization as j30
    import aas_core3.xmlization as x30
    xml30 = x30.to_str(j30.environment_from_jsonable(BASE))
    (out / 'p00_baseline_ns30.xml').write_text(xml30)
    (out / 'p00_baseline_ns31.xml').write_text(xml30.replace('https://admin-shell.io/aas/3/0',
                                                             'https://admin-shell.io/aas/3/1'))
    (out / 'p00_baseline_ns32.xml').write_text(xml30.replace('https://admin-shell.io/aas/3/0',
                                                             'https://admin-shell.io/aas/3/2'))


main()
