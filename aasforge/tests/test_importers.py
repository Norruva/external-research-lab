"""SAMM aspect models (checked against the ESMF generator's own AAS output) and ECLASS / IEC CDD
property tables."""
import gzip
import json
import re
import unittest

from helpers import FIXTURES

from aasforge import dictionary, elements, samm, smt, versioning

ROOT = FIXTURES / 'samm'
MODELS = {
    'BatteryPass': 'io.catenax.battery.battery_pass/6.1.0',
    'Pcf': 'io.catenax.pcf/10.0.0',
}


def generic(submodel):
    return {re.sub(r'\[\d+\]', '[]', p): e for p, e in elements.walk_elements(submodel)}


class SammTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {}
        for name, folder in MODELS.items():
            model = samm.AspectModel([ROOT / folder / f'{name}.ttl'], models_root=ROOT)
            template, cds = model.template()
            reference = versioning.parse_xml(gzip.open(ROOT / folder / 'gen' / f'{name}-aas.xml.gz').read()).environment
            payload = json.loads((ROOT / folder / 'gen' / f'{name}.json').read_text())
            cls.results[name] = template, cds, reference, payload

    def test_templates_match_the_esmf_generator(self):
        for name, (template, cds, reference, _) in self.results.items():
            ours, theirs = generic(template), generic(reference['submodels'][0])
            self.assertEqual(set(ours), set(theirs), name)
            self.assertEqual(template['id'], reference['submodels'][0]['id'])
            self.assertEqual(len(cds), len(reference['conceptDescriptions']), name)
            for path, element in ours.items():
                other = theirs[path]
                self.assertEqual((element['modelType'], element.get('valueType')),
                                 (other['modelType'], other.get('valueType')), f'{name} {path}')
                if other.get('semanticId'):       # the generator leaves collections without one; we add it
                    self.assertEqual(element.get('semanticId'), other['semanticId'], f'{name} {path}')

    def test_templates_and_concept_descriptions_verify(self):
        for name, (template, cds, _, _) in self.results.items():
            self.assertEqual(versioning.verify({'submodels': [template], 'conceptDescriptions': cds}), [], name)
            self.assertTrue(all(q['type'].startswith('SMT/') for _, e in elements.walk_elements(template)
                                for q in e.get('qualifiers') or []))

    def test_payloads_round_trip_through_instances(self):
        def norm(x):
            if isinstance(x, dict):
                return {k: norm(v) for k, v in x.items()}
            if isinstance(x, list):
                return [norm(v) for v in x]
            return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else x
        for name, (template, _, _, payload) in self.results.items():
            instance = samm.instance(template, payload, f'urn:example:{name}:1')
            self.assertEqual(versioning.verify({'submodels': [instance]}), [], name)
            self.assertEqual(norm(samm.payload(instance, template)), norm(payload), name)
            errors = [f for f in smt.check(instance, template) if f['severity'] == 'error']
            self.assertEqual(errors, [], name)

    def test_units_and_cardinality(self):
        template, cds, _, _ = self.results['BatteryPass']
        units = {c['id']: c['embeddedDataSpecifications'][0]['dataSpecificationContent'].get('unit') for c in cds}
        self.assertTrue(any(u for u in units.values()))
        optional = elements.get(template, 'specVersion')
        self.assertIn({'kind': 'TemplateQualifier', 'type': 'SMT/Cardinality', 'valueType': 'xs:string',
                       'value': 'ZeroToOne'}, optional['qualifiers'])


ECLASS_EN = '''Supplier;IdPR;Identifier;VersionNumber;VersionDate;RevisionNumber;PreferredName;ShortName;Definition;SourceOfDefinition;Note;Remark;PreferredSymbol;IrdiUN;ISOLanguageCode;ISOCountryCode;Category;AttributeType;DefinitionClass;DataType;IrdiPR;CurrencyAlphaCode
0173-1#01-AAA001#001;0173-1#02-AAB713#005;AAB713;005;2019-11-30;02;Rated voltage;U rated;Voltage at which the battery is designed to operate;IEC 60050;;;;0173-1#05-AAA480#003;en;US;;DIRECT;;REAL_MEASURE;0173-1#02-AAB713#005;
0173-1#01-AAA001#001;0173-1#02-AAO677#002;AAO677;002;2019-11-30;01;Manufacturer name;;legally valid designation of the natural or judicial person which is directly responsible for the design, production, packaging and labeling of a product;;;;;;en;US;;DIRECT;;STRING_TRANSLATABLE;0173-1#02-AAO677#002;
'''
ECLASS_DE = '''IdPR;PreferredName;Definition;ISOLanguageCode;DataType;IrdiPR;IrdiUN
0173-1#02-AAB713#005;Bemessungsspannung;Spannung, fuer die die Batterie ausgelegt ist;de;REAL_MEASURE;0173-1#02-AAB713#005;0173-1#05-AAA480#003
'''
ECLASS_UNITS = '''Supplier;IdUN;Identifier;PreferredName;ShortName;Definition;IrdiUN
0173-1#01-AAA001#001;0173-1#05-AAA480#003;AAA480;volt;V;SI unit of electric potential;0173-1#05-AAA480#003
'''
CDD = '''IRDI,Code,Version,Preferred name,Short name,Definition,Data type,Primary unit
0112/2///61360_4#AAE001#001,AAE001,001,nominal capacity,C_n,capacity declared by the manufacturer,REAL_MEASURE_TYPE,A·h
0112/2///61360_4#AAE002#001,AAE002,001,cell count,,number of cells,INT_TYPE,
0112/2///61360_4#AAE003#001,AAE003,001,manufacturing date,,date of manufacture,DATE_TYPE,
'''


class DictionaryTests(unittest.TestCase):
    def test_eclass_languages_and_units_merge(self):
        units = dictionary.read_units(ECLASS_UNITS.encode())
        self.assertEqual(units, {'0173-1#05-AAA480#003': 'V'})
        cds = {c['id']: c for c in dictionary.concept_descriptions(ECLASS_EN.encode(), ECLASS_DE.encode(), units=units)}
        voltage = cds['0173-1#02-AAB713#005']['embeddedDataSpecifications'][0]['dataSpecificationContent']
        self.assertEqual({n['language'] for n in voltage['preferredName']}, {'en', 'de'})
        self.assertEqual((voltage['unit'], voltage['dataType']), ('V', 'REAL_MEASURE'))
        self.assertEqual(voltage['unitId']['keys'][0]['value'], '0173-1#05-AAA480#003')
        self.assertEqual(cds['0173-1#02-AAO677#002']['administration'], {'version': '2', 'revision': '1'})
        self.assertEqual(versioning.verify({'conceptDescriptions': list(cds.values())}), [])

    def test_iec_cdd_table(self):
        cds = dictionary.concept_descriptions(CDD.encode())
        content = {c['id']: c['embeddedDataSpecifications'][0]['dataSpecificationContent'] for c in cds}
        self.assertEqual(content['0112/2///61360_4#AAE001#001']['unit'], 'A·h')
        self.assertEqual([content[i]['dataType'] for i in sorted(content)], ['REAL_MEASURE', 'INTEGER_COUNT', 'DATE'])
        self.assertEqual(versioning.verify({'conceptDescriptions': cds}), [])

    def test_data_type_mapping(self):
        cases = {'STRING': 'STRING', 'STRING_TRANSLATABLE': 'STRING_TRANSLATABLE', 'INTEGER_MEASURE': 'INTEGER_MEASURE',
                 'INT_MEASURE_TYPE': 'INTEGER_MEASURE', 'REAL_CURRENCY': 'REAL_CURRENCY', 'BOOLEAN_TYPE': 'BOOLEAN',
                 'DATE_TIME_TYPE': 'TIMESTAMP', 'URL': 'IRI', 'RATIONAL_MEASURE': 'RATIONAL_MEASURE', 'IRDI': 'IRDI'}
        for text, expected in cases.items():
            self.assertEqual(dictionary.iec_data_type(text), expected, text)

    def test_missing_columns_are_reported(self):
        with self.assertRaises(dictionary.DictionaryError):
            dictionary.concept_descriptions(b'foo;bar\n1;2\n')


if __name__ == '__main__':
    unittest.main()
