"""Run the corpus through schema validators, aas-core 3.0/3.1 and the BaSyx Python SDK.

Usage: python3 run_python_tools.py <corpus-dir> <schemas-dir> > results-python.json

<schemas-dir> holds v3.0.9/, v3.1.2/ and v3.2.0/ each with aas.json and AAS.xsd.
Each result row: {file, tool, status (pass|fail|error), detail, objects?}.
"""
import io
import json
import logging
import sys
import zipfile
from pathlib import Path

import aas_core3.jsonization as j30
import aas_core3.verification as v30
import aas_core3.xmlization as x30
import aas_core3_1.jsonization as j31
import aas_core3_1.verification as v31
import aas_core3_1.xmlization as x31
import jsonschema
from basyx.aas import model
from basyx.aas.adapter import aasx
from basyx.aas.adapter.json import read_aas_json_file
from basyx.aas.adapter.xml import read_aas_xml_file
from lxml import etree

logging.disable(logging.CRITICAL)
SCHEMA_VERSIONS = ('v3.0.9', 'v3.1.2', 'v3.2.0')


def payloads(path):
    """Yield (payload-name, bytes) for a file or for the AAS spec parts of an AASX package."""
    if path.suffix.lower() != '.aasx':
        yield path.name, path.read_bytes()
        return
    with zipfile.ZipFile(path) as package:
        for name in package.namelist():
            if name.startswith('aasx/') and name.endswith(('.xml', '.json')) and 'aasx-origin' not in name:
                yield name, package.read(name)


def short(error):
    text = f'{type(error).__name__}: {error}'
    return ' '.join(text.split())[:300]


def schema_checks(name, data, schemas):
    rows = []
    for version in SCHEMA_VERSIONS:
        tool = f'schema-{version}'
        try:
            if name.endswith('.json'):
                errors = sorted(schemas[version]['json'].iter_errors(json.loads(data)), key=lambda e: e.path)
                rows.append((tool, 'pass' if not errors else 'fail',
                             '' if not errors else f'{len(errors)} errors; first: {errors[0].message[:200]}'))
            else:
                document = etree.fromstring(data)
                ok = schemas[version]['xsd'].validate(document)
                log = schemas[version]['xsd'].error_log
                rows.append((tool, 'pass' if ok else 'fail',
                             '' if ok else f'{len(log)} errors; first: {str(log[0].message)[:200]}'))
        except Exception as error:  # noqa: BLE001
            rows.append((tool, 'error', short(error)))
    return rows


def aas_core_check(tool, name, data, jsonization, xmlization, verification):
    try:
        if name.endswith('.json'):
            environment = jsonization.environment_from_jsonable(json.loads(data))
        else:
            environment = xmlization.environment_from_str(data.decode('utf-8'))
    except Exception as error:  # noqa: BLE001
        return tool, 'fail', 'deserialization: ' + short(error)
    errors = list(verification.verify(environment))
    if errors:
        return tool, 'fail', f'{len(errors)} verification errors; first: {errors[0].path}: {errors[0].cause}'[:300]
    return tool, 'pass', ''


def count_identifiables(store):
    return len(list(store)) if store is not None else 0


def basyx_payload_check(name, data):
    rows = []
    reader = read_aas_json_file if name.endswith('.json') else read_aas_xml_file
    try:
        store = reader(io.StringIO(data.decode('utf-8')) if name.endswith('.json') else io.BytesIO(data),
                       failsafe=False)
        rows.append(('basyx-python-2.2.0-strict', 'pass', '', count_identifiables(store)))
    except Exception as error:  # noqa: BLE001
        rows.append(('basyx-python-2.2.0-strict', 'fail', short(error), None))
    try:
        store = reader(io.StringIO(data.decode('utf-8')) if name.endswith('.json') else io.BytesIO(data))
        rows.append(('basyx-python-2.2.0-default', 'pass', '', count_identifiables(store)))
    except Exception as error:  # noqa: BLE001
        rows.append(('basyx-python-2.2.0-default', 'fail', short(error), None))
    return rows


def basyx_aasx_check(path):
    rows = []
    for mode, failsafe in (('strict', False), ('default', True)):
        tool = f'basyx-python-2.2.0-aasx-{mode}'
        try:
            store = model.DictIdentifiableStore()
            files = aasx.DictSupplementaryFileContainer()
            with aasx.AASXReader(str(path), failsafe=failsafe) as reader:
                reader.read_into(store, files)
            rows.append((tool, 'pass', f'{len(list(files))} supplementary files', count_identifiables(store)))
        except Exception as error:  # noqa: BLE001
            rows.append((tool, 'fail', short(error), None))
    return rows


def expected_identifiables(name, data):
    """Identifiable count straight from the payload, for comparison with what a reader kept."""
    try:
        if name.endswith('.json'):
            env = json.loads(data)
            return sum(len(env.get(k, [])) for k in ('assetAdministrationShells', 'submodels', 'conceptDescriptions'))
        root = etree.fromstring(data)
        return sum(1 for e in root.iter() if etree.QName(e).localname in
                   ('assetAdministrationShell', 'submodel', 'conceptDescription')
                   and etree.QName(e.getparent()).localname in
                   ('assetAdministrationShells', 'submodels', 'conceptDescriptions'))
    except Exception:  # noqa: BLE001
        return None


def main():
    corpus, schema_dir = Path(sys.argv[1]), Path(sys.argv[2])
    schemas = {}
    for version in SCHEMA_VERSIONS:
        json_schema = json.loads((schema_dir / version / 'aas.json').read_text())
        schemas[version] = {'json': jsonschema.Draft201909Validator(json_schema),
                            'xsd': etree.XMLSchema(etree.parse(str(schema_dir / version / 'AAS.xsd')))}
    results = []
    for path in sorted(corpus.rglob('*')):
        if path.suffix.lower() not in ('.aasx', '.json', '.xml') or path.name == 'probes-manifest.json':
            continue
        rel = str(path.relative_to(corpus))
        expected = 0
        for name, data in payloads(path):
            expected += expected_identifiables(name, data) or 0
            rows = schema_checks(name, data, schemas)
            rows.append(aas_core_check('aas-core3.0-python-1.1.4', name, data, j30, x30, v30))
            rows.append(aas_core_check('aas-core3.1-python-1.0.0', name, data, j31, x31, v31))
            for tool, status, detail in rows:
                results.append({'file': rel, 'payload': name, 'tool': tool, 'status': status, 'detail': detail})
            if path.suffix.lower() != '.aasx':
                for tool, status, detail, objects in basyx_payload_check(name, data):
                    results.append({'file': rel, 'payload': name, 'tool': tool, 'status': status,
                                    'detail': detail, 'objects': objects})
        if path.suffix.lower() == '.aasx':
            for tool, status, detail, objects in basyx_aasx_check(path):
                results.append({'file': rel, 'payload': '(package)', 'tool': tool, 'status': status,
                                'detail': detail, 'objects': objects})
        for row in results:
            if row['file'] == rel and 'objects' in row:
                row['expected_objects'] = expected
    json.dump(results, sys.stdout, indent=1)


main()
