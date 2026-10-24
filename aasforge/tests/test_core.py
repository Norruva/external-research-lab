"""Versioning, AASX, element modifiers and SMT templates."""
import copy
import io
import json
import zipfile
import unittest

from helpers import FIXTURES, PROBES

from aasforge import aasx, elements, smt, versioning
from aasforge.dpp import catalog
from aasforge.repository import Repository
from aasforge.store import Store


def probe(name):
    return (PROBES / name).read_bytes()


class VersioningTests(unittest.TestCase):
    def test_detects_versions_from_features(self):
        self.assertEqual(versioning.parse_json(probe('p00_baseline.json')).source_version, '3.0')
        self.assertEqual(versioning.parse_json(probe('p31_idshort_hyphen.json')).source_version, '3.1')
        self.assertEqual(versioning.parse_json(probe('p32_assetkind_batch.json')).source_version, '3.2')

    def test_xml_namespaces(self):
        for name, version in (('p00_baseline_ns30.xml', '3.0'), ('p00_baseline_ns31.xml', '3.1'),
                              ('p00_baseline_ns32.xml', '3.2')):
            normalised = versioning.parse_xml(probe(name))
            self.assertEqual(normalised.source_version, version)
            self.assertEqual(len(normalised.environment['submodels']), 1)

    def test_32_fields_round_trip_through_sidecar(self):
        repo = Repository(Store())
        repo.import_bytes(probe('p32_admin_created_at.json'), 'a.json')
        repo.import_bytes(probe('p32_assetkind_batch.json').replace(b'probe', b'probe-batch'), 'b.json')
        env31 = repo.environment()
        self.assertNotIn('createdAt', json.dumps(env31))
        env32 = repo.environment(version='3.2')
        admin = next(s for s in env32['submodels'] if s['id'] == 'urn:example:norruva:sm:probe')['administration']
        self.assertEqual(admin['createdAt'], '2026-10-23T08:00:00Z')
        self.assertEqual(admin['updatedAt'], '2026-10-23T09:00:00Z')
        kinds = {s['assetInformation']['assetKind'] for s in env32['assetAdministrationShells']}
        self.assertIn('Batch', kinds)

    def test_schema_location_attribute_is_tolerated(self):
        xml = probe('p00_baseline_ns30.xml').decode().replace(
            '<environment xmlns="https://admin-shell.io/aas/3/0">',
            '<environment xmlns="https://admin-shell.io/aas/3/0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:schemaLocation="https://admin-shell.io/aas/3/0 AAS.xsd">')
        normalised = versioning.parse_xml(xml)
        self.assertIn('removed xsi:schemaLocation from root element', normalised.notes)

    def test_rejects_non_aas(self):
        with self.assertRaises(versioning.VersionError):
            versioning.parse_json(b'{"submodels": [{"modelType": "Nope"}]}')


class AasxTests(unittest.TestCase):
    def test_repairs_external_origin_and_missing_parts(self):
        package = aasx.read((FIXTURES / 'idta-02099-1-dpp.aasx').read_bytes())
        self.assertTrue(any('TargetMode="External"' in r for r in package.repairs))
        self.assertEqual(len(package.normalised.environment['submodels']), 1)
        # drop a supplementary part that the spec relationships still list
        source = zipfile.ZipFile(io.BytesIO((FIXTURES / 'idta-02099-1-dpp.aasx').read_bytes()))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as target:
            for item in source.infolist():
                if item.filename != 'aasx/files/title-page.png':
                    target.writestr(item, source.read(item))
        package = aasx.read(buffer.getvalue())
        self.assertTrue(any('title-page.png' in r and 'missing' in r for r in package.repairs))

    def test_reads_stray_text_and_30_packages_losslessly(self):
        repo = Repository(Store())
        for name in ('idta-02023-carbon-footprint-v31.aasx', 'catenax-batterypass-6.0.0.aasx',
                     'idta-02035-6-material-composition.aasx'):
            report = repo.import_bytes((FIXTURES / name).read_bytes(), name)
            self.assertTrue(report.ok, report.lost)
            self.assertEqual(sum(report.stored.values()), report.expected)

    def test_writer_is_deterministic_and_readable(self):
        env = json.loads(probe('p00_baseline.json'))
        files = {'/aasx/files/DeclarationOfConformity.pdf': b'%PDF-1.7 example'}
        first = aasx.write(env, files, {'/aasx/files/DeclarationOfConformity.pdf': 'application/pdf'})
        second = aasx.write(copy.deepcopy(env), dict(files), {'/aasx/files/DeclarationOfConformity.pdf': 'application/pdf'})
        self.assertEqual(first, second)
        package = aasx.read(first)
        self.assertEqual(package.repairs, [])
        self.assertEqual(package.files, files)

    def test_writer_refuses_dangling_file_parts(self):
        with self.assertRaises(aasx.AasxError):
            aasx.write(json.loads(probe('p00_baseline.json')), {})


class ElementTests(unittest.TestCase):
    def setUp(self):
        self.submodel = {'modelType': 'Submodel', 'id': 'urn:x:sm', 'submodelElements': [
            {'modelType': 'Property', 'idShort': 'Mass', 'valueType': 'xs:double', 'value': '452.0'},
            {'modelType': 'MultiLanguageProperty', 'idShort': 'Name', 'value': [{'language': 'en', 'text': 'Pack'}]},
            {'modelType': 'SubmodelElementList', 'idShort': 'Cells', 'typeValueListElement': 'SubmodelElementCollection',
             'value': [{'modelType': 'SubmodelElementCollection', 'value': [
                 {'modelType': 'Property', 'idShort': 'Voltage', 'valueType': 'xs:int', 'value': '4'}]}]},
            {'modelType': 'Blob', 'idShort': 'Raw', 'contentType': 'application/octet-stream', 'value': 'AAE='}]}

    def test_paths_and_reference(self):
        self.assertEqual(elements.parse_path('Cells[0].Voltage'), ['Cells', 0, 'Voltage'])
        self.assertEqual(elements.get(self.submodel, 'Cells%5B0%5D.Voltage')['value'], '4')
        self.assertIn('Cells[0].Voltage', elements.all_paths(self.submodel))
        keys = elements.reference(self.submodel, 'Cells[0].Voltage')['keys']
        self.assertEqual([k['value'] for k in keys], ['urn:x:sm', 'Cells', '0', 'Voltage'])

    def test_value_only_round_trip(self):
        value = elements.submodel_value_only(self.submodel)
        self.assertEqual(value['Mass'], 452.0)
        self.assertEqual(value['Cells'], [{'Voltage': 4}])
        self.assertNotIn('value', value['Raw'])
        self.assertEqual(elements.submodel_value_only(self.submodel, extent='withBlobValue')['Raw']['value'], 'AAE=')
        elements.patch_submodel_value(self.submodel, {'Mass': 450, 'Name': [{'de': 'Paket'}], 'Cells': [{'Voltage': 5}]})
        self.assertEqual(elements.get(self.submodel, 'Mass')['value'], '450')
        self.assertEqual(elements.get(self.submodel, 'Cells[0].Voltage')['value'], '5')

    def test_level_core_and_metadata(self):
        core = elements.apply_level_extent(self.submodel, level='core')
        self.assertNotIn('value', core['submodelElements'][2])      # core: direct children only
        self.assertIn('value', core['submodelElements'][0])
        self.assertNotIn('value', elements.metadata(self.submodel['submodelElements'][0]))

    def test_remove_many_is_index_safe(self):
        lst = {'modelType': 'Submodel', 'id': 'x', 'submodelElements': [
            {'modelType': 'SubmodelElementList', 'idShort': 'L', 'value': [
                {'modelType': 'Property', 'valueType': 'xs:string', 'value': str(i)} for i in range(4)]}]}
        elements.remove_many(lst, ['L[0]', 'L[2]'])
        self.assertEqual([e['value'] for e in lst['submodelElements'][0]['value']], ['1', '3'])


class SmtTests(unittest.TestCase):
    def test_instantiate_and_check(self):
        template = catalog.template('dbp-circularity')
        instance = smt.instantiate(template, 'urn:x:circ', {
            'RecycledContentInformation[0].RecycledMaterial': 'Cobalt',
            'RecycledContentInformation[0].PreConsumerShare': 12.5, 'RenewableContent': 4})
        self.assertEqual(versioning.verify({'submodels': [instance]}), [])
        self.assertNotIn('SMT/', json.dumps(instance))
        findings = smt.check(instance, template)
        self.assertTrue(any(f['path'] == 'DismantlingAndRemovalInformation' for f in findings))
        self.assertFalse(any(f['path'].startswith('RecycledContentInformation') and f['rule'] == 'mandatory-value'
                             for f in findings))


if __name__ == '__main__':
    unittest.main()
