"""SAMM (Semantic Aspect Meta Model, Eclipse ESMF) to AAS: aspect models become submodel templates
with ConceptDescriptions, and SAMM JSON payloads become submodel instances.

The mapping follows the conventions of the ESMF aspect model generator (the
``gen/*-aas.xml`` files published in eclipse-tractusx/sldt-semantic-models), so templates line up
with what Catena-X partners generate:

* Aspect: Submodel ``<aspect urn>/submodel`` (kind Template), semanticId = the aspect URN,
  supplementalSemanticIds from ``samm:see``;
* Property: an element with idShort = the property name. Its semanticId is the property URN,
  displayName/description come from ``samm:preferredName``/``samm:description``, and there is a
  ConceptDescription ``<property urn>`` with IEC 61360 data (preferredName, shortName,
  definition, dataType, unit);
* scalar characteristics become Property (``rdf:langString``: MultiLanguageProperty); an Entity
  becomes a SubmodelElementCollection, including the properties of the entities it ``samm:extends``;
  Collection/Set/List/SortedSet/TimeSeries become a SubmodelElementList (whose semanticId is the
  characteristic), ordered for List and SortedSet; Either becomes a collection with ``left`` and
  ``right``; Trait takes its base characteristic, and RangeConstraint becomes ``SMT/AllowedRange``;
  Enumeration/State values become ``SMT/AllowedValue``; Measurement/Quantifiable units become the
  concept description's unit (symbol from the SAMM unit catalogue);
* ``samm:optional`` sets ``SMT/Cardinality`` to ZeroToOne (lists: ZeroToMany), otherwise One
  (lists: OneToMany), and ``samm:exampleValue`` sets ``SMT/ExampleValue``. Templates therefore
  work with ``smt.instantiate`` and ``smt.check``.

Deviation from the generator: list items carry no idShort (AASd-120 in metamodel 3.0, and
recommended in 3.1).

Payloads use ``samm:payloadName`` when it is set. :func:`instance` maps them onto idShorts.
``rdflib`` is an optional dependency.
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path

META = re.compile(r'^urn:samm:org\.eclipse\.esmf\.samm:(meta-model|characteristic|entity|unit):(\d+\.\d+\.\d+)#(.*)$')
XSD = 'http://www.w3.org/2001/XMLSchema#'
RDF_LANG = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#langString'
IEC61360 = 'https://admin-shell.io/DataSpecificationTemplates/DataSpecificationIec61360/3/0'
IDSHORT_BAD = re.compile(r'[^A-Za-z0-9_-]')
LIST_TYPES = {'Collection': False, 'Set': False, 'List': True, 'SortedSet': True, 'TimeSeries': True}
NUMERIC = {'decimal', 'integer', 'double', 'float', 'int', 'long', 'short', 'byte', 'nonNegativeInteger',
           'positiveInteger', 'nonPositiveInteger', 'negativeInteger', 'unsignedInt', 'unsignedLong', 'unsignedShort',
           'unsignedByte'}
INTEGRAL = NUMERIC - {'decimal', 'double', 'float'}
# predefined characteristic instances of the SAMM catalogue (samm-c:Boolean, ...): their data types
# are defined in the meta-model, not in aspect model files
PREDEFINED = {'Text': XSD + 'string', 'Boolean': XSD + 'boolean', 'Locale': XSD + 'string', 'Language': XSD + 'string',
              'MultiLanguageText': RDF_LANG, 'MimeType': XSD + 'string', 'ResourcePath': XSD + 'anyURI',
              'Timestamp': XSD + 'dateTime', 'UnitReference': XSD + 'string'}


class SammError(ValueError):
    pass


@lru_cache(maxsize=1)
def units() -> dict:
    return json.loads(resources.files('aasforge.data').joinpath('samm-units.json').read_text())['units']


def _rdflib():
    try:
        import rdflib
    except ImportError as error:  # pragma: no cover - optional dependency
        raise RuntimeError('SAMM import needs rdflib: pip install rdflib') from error
    return rdflib


def _local(uri) -> str:
    text = str(uri)
    return text.split('#', 1)[1] if '#' in text else text.rsplit('/', 1)[-1]


def _id_short(name: str) -> str:
    name = IDSHORT_BAD.sub('_', name)
    if not name or not name[0].isalpha():
        name = 'x' + name
    return name.rstrip('_-') or 'x'


def _lang_strings(values, limit=None):
    result = []
    for value in values:
        language = getattr(value, 'language', None) or 'en'
        text = str(value)
        result.append({'language': language, 'text': text[:limit] if limit else text})
    return sorted(result, key=lambda item: (item['language'] != 'en', item['language']))


def _external(value: str) -> dict:
    return {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': value}]}


def _qualifier(kind: str, value: str, value_type: str = 'xs:string') -> dict:
    return {'kind': 'TemplateQualifier', 'type': f'SMT/{kind}', 'valueType': value_type, 'value': value}


class AspectModel:
    """Aspect models loaded from Turtle files, resolving imports from a models root laid out as
    ``<namespace>/<version>/<Name>.ttl`` (the sldt-semantic-models layout)."""

    def __init__(self, files=(), models_root: str | Path | None = None):
        rdflib = _rdflib()
        self.rdflib = rdflib
        self.graph = rdflib.Graph()
        self.models_root = Path(models_root) if models_root else None
        self._loaded = set()
        for file in files:
            self.load(file)
        self.primary = self.aspects()          # aspects of the files given, before imports are resolved
        if self.models_root:
            self.resolve_imports()

    def load(self, path):
        path = Path(path).resolve()
        if path in self._loaded:
            return
        self.graph.parse(str(path), format='turtle')
        self._loaded.add(path)

    def resolve_imports(self):
        """Load the files of namespaces that are referenced but not defined, until closure."""
        while True:
            missing = set()
            defined = {str(s).split('#')[0] for s in self.graph.subjects()}
            for term in set(self.graph.subjects()) | set(self.graph.objects()):
                text = str(term)
                if text.startswith('urn:samm:') and not META.match(text) and '#' in text:
                    namespace = text.split('#')[0]
                    if namespace not in defined:
                        missing.add(namespace)
            loaded_any = False
            for namespace in sorted(missing):
                name, _, version = namespace[len('urn:samm:'):].rpartition(':')
                folder = self.models_root / name / version
                for file in sorted(folder.glob('*.ttl')) if folder.is_dir() else []:
                    if file.resolve() not in self._loaded:
                        self.load(file)
                        loaded_any = True
            if not loaded_any:
                return

    # -- vocabulary helpers ------------------------------------------------------------------------
    def _is(self, node, kind: str, local: str) -> bool:
        match = META.match(str(node))
        return bool(match) and match.group(1) == kind and match.group(3) == local

    def _value(self, node, local: str, kind: str = 'meta-model'):
        for predicate, obj in self.graph.predicate_objects(node):
            if self._is(predicate, kind, local):
                return obj
        return None

    def _values(self, node, local: str, kind: str = 'meta-model'):
        return [o for p, o in self.graph.predicate_objects(node) if self._is(p, kind, local)]

    def _types(self, node):
        return [o for o in self.graph.objects(node, self.rdflib.RDF.type)]

    def _type_local(self, node, kind: str):
        for t in self._types(node):
            match = META.match(str(t))
            if match and match.group(1) == kind:
                return match.group(3)
        return None

    def _list(self, node):
        return list(self.rdflib.collection.Collection(self.graph, node)) if node is not None else []

    def aspects(self) -> list[str]:
        return sorted(str(s) for s, _, o in self.graph.triples((None, self.rdflib.RDF.type, None))
                      if self._is(o, 'meta-model', 'Aspect'))

    # -- template generation -------------------------------------------------------------------------
    def template(self, aspect: str | None = None) -> tuple[dict, list[dict]]:
        """(submodel template, concept descriptions) for an aspect (default: the only one)."""
        aspects = self.primary or self.aspects()
        if aspect is None:
            if len(aspects) != 1:
                raise SammError(f'choose one aspect of {aspects}')
            aspect = aspects[0]
        node = self.rdflib.URIRef(aspect)
        self.cds: dict = {}
        submodel = {'modelType': 'Submodel', 'id': f'{aspect}/submodel', 'idShort': _id_short(_local(aspect)),
                    'kind': 'Template', 'semanticId': _external(aspect)}
        self._describe(submodel, node)
        see = [str(s) for s in self._values(node, 'see')]
        if see:
            submodel['supplementalSemanticIds'] = [_external(s) for s in see]
        elements = [self._property(entry, stack=()) for entry in self._list(self._value(node, 'properties'))]
        if elements:
            submodel['submodelElements'] = [e for e in elements if e]
        self.cds[aspect] = self._cd(node, aspect, None, None)
        return submodel, list(self.cds.values())

    def _describe(self, target: dict, node):
        names = _lang_strings(self._values(node, 'preferredName'), 128)
        descriptions = _lang_strings(self._values(node, 'description'), 1023)
        if names:
            target['displayName'] = names
        if descriptions:
            target['description'] = descriptions

    def _entry(self, entry):
        """(property node, optional, payloadName) for an item of samm:properties."""
        prop = self._value(entry, 'property')
        if prop is None:
            return entry, False, None
        optional = str(self._value(entry, 'optional') or 'false').lower() == 'true'
        payload = self._value(entry, 'payloadName')
        return prop, optional, str(payload) if payload is not None else None

    def _property(self, entry, stack):
        prop, optional, payload_name = self._entry(entry)
        if self._value(entry, 'notInPayload') is not None and str(self._value(entry, 'notInPayload')).lower() == 'true':
            return None
        name = _local(prop)
        characteristic = self._value(prop, 'characteristic')
        element = self._characteristic(characteristic, name, stack + (str(prop),))
        element['idShort'] = _id_short(name)
        self._describe(element, prop)
        if element.get('modelType') != 'SubmodelElementList':
            element['semanticId'] = _external(str(prop))
        is_list = element.get('modelType') == 'SubmodelElementList'
        cardinality = ('ZeroToMany' if optional else 'OneToMany') if is_list else ('ZeroToOne' if optional else 'One')
        qualifiers = [_qualifier('Cardinality', cardinality)] + element.pop('_qualifiers', [])
        example = self._value(prop, 'exampleValue')
        if example is not None and element.get('modelType') == 'Property':
            qualifiers.append(_qualifier('ExampleValue', str(example), element.get('valueType', 'xs:string')))
        element['qualifiers'] = qualifiers
        if payload_name:
            element.setdefault('extensions', []).append({'name': 'samm:payloadName', 'valueType': 'xs:string',
                                                         'value': payload_name})
        self.cds[str(prop)] = self._cd(prop, str(prop), element.pop('_datatype', None), element.pop('_unit', None))
        return element

    def _characteristic(self, node, name, stack):
        """The element (without idShort) a characteristic maps to."""
        if node is None:
            return {'modelType': 'Property', 'valueType': 'xs:string', '_datatype': 'STRING'}
        predefined = META.match(str(node))
        if predefined and predefined.group(1) == 'characteristic' and predefined.group(3) in PREDEFINED:
            return self._scalar(self.rdflib.URIRef(PREDEFINED[predefined.group(3)]))
        kind = self._type_local(node, 'characteristic') or self._type_local(node, 'meta-model')
        if kind == 'Trait':
            base = self._characteristic(self._value(node, 'baseCharacteristic', 'characteristic'), name, stack)
            for constraint in self._values(node, 'constraint', 'characteristic'):
                ctype = self._type_local(constraint, 'characteristic')
                if ctype == 'RangeConstraint':
                    low = self._value(constraint, 'minValue', 'characteristic')
                    high = self._value(constraint, 'maxValue', 'characteristic')
                    base.setdefault('_qualifiers', []).append(_qualifier(
                        'AllowedRange', f'[{"" if low is None else low}, {"" if high is None else high}]'))
                elif ctype == 'RegularExpressionConstraint':
                    pattern = self._value(constraint, 'value')
                    if pattern is not None:
                        base.setdefault('_qualifiers', []).append(_qualifier('AllowedValue', str(pattern)))
            return base
        if kind in LIST_TYPES:
            return self._list_element(node, kind, name, stack)
        if kind == 'Either':
            left = self._characteristic(self._value(node, 'left', 'characteristic'), 'left', stack)
            right = self._characteristic(self._value(node, 'right', 'characteristic'), 'right', stack)
            left.update(idShort='left', qualifiers=[_qualifier('Cardinality', 'ZeroToOne')])
            right.update(idShort='right', qualifiers=[_qualifier('Cardinality', 'ZeroToOne')])
            for side in (left, right):
                side.pop('_datatype', None)
                side.pop('_unit', None)
            return {'modelType': 'SubmodelElementCollection', 'value': [left, right]}
        datatype = self._value(node, 'dataType')
        if datatype is not None and self._is_entity(datatype):
            return self._entity(datatype, stack)
        element = self._scalar(datatype)
        if kind in ('Enumeration', 'State'):
            values = [v for v in self._list(self._value(node, 'values', 'characteristic')) if not self._is_entity_instance(v)]
            if values:
                element.setdefault('_qualifiers', []).append(_qualifier(
                    'AllowedValue', '^(' + '|'.join(re.escape(str(v)) for v in values) + ')$'))
        unit = self._value(node, 'unit', 'characteristic')
        if unit is not None:
            element['_unit'] = units().get(_local(unit), {}).get('symbol') or _local(unit)
        return element

    def _list_element(self, node, kind, name, stack):
        item_characteristic = self._value(node, 'elementCharacteristic', 'characteristic')
        datatype = self._value(node, 'dataType')
        if item_characteristic is not None:
            item = self._characteristic(item_characteristic, name, stack)
        elif datatype is not None and self._is_entity(datatype):
            item = self._entity(datatype, stack)
        else:
            item = self._scalar(datatype)
        result = {'modelType': 'SubmodelElementList', 'semanticId': _external(str(node)) if not isinstance(
            node, self.rdflib.BNode) else _external(f'{stack[-1]}'), 'orderRelevant': LIST_TYPES[kind],
            'typeValueListElement': item['modelType']}
        if item['modelType'] in ('Property', 'Range'):
            result['valueTypeListElement'] = item.get('valueType', 'xs:string')
        result['_datatype'] = item.pop('_datatype', None)
        result['_unit'] = item.pop('_unit', None)
        result['_qualifiers'] = item.pop('_qualifiers', [])
        if result['_qualifiers'] and item['modelType'] == 'Property':
            item['qualifiers'] = result.pop('_qualifiers')
        if item['modelType'] in ('Property', 'MultiLanguageProperty', 'Range') and stack:
            item['semanticId'] = _external(stack[-1])       # scalar items: the list's property
        item.pop('idShort', None)
        result['value'] = [item]
        return result

    def _is_entity(self, node) -> bool:
        return any(self._is(t, 'meta-model', k) for t in self._types(node) for k in ('Entity', 'AbstractEntity'))

    def _is_entity_instance(self, node) -> bool:
        return any(self._is_entity(t) for t in self._types(node))

    def _entity(self, node, stack):
        if str(node) in stack:
            return {'modelType': 'SubmodelElementCollection'}    # recursive model: stop here
        entries, current, seen = [], node, set()
        while current is not None and str(current) not in seen:
            seen.add(str(current))
            entries = self._list(self._value(current, 'properties')) + entries
            current = self._value(current, 'extends')
        children = [self._property(entry, stack + (str(node),)) for entry in entries]
        result = {'modelType': 'SubmodelElementCollection'}
        children = [c for c in children if c]
        if children:
            result['value'] = children
        return result

    def _scalar(self, datatype):
        text = str(datatype) if datatype is not None else XSD + 'string'
        if text == RDF_LANG:
            return {'modelType': 'MultiLanguageProperty', '_datatype': 'STRING_TRANSLATABLE'}
        local = text[len(XSD):] if text.startswith(XSD) else 'string'
        if local not in ('string', 'boolean', 'decimal', 'integer', 'double', 'float', 'date', 'time', 'dateTime',
                         'dateTimeStamp', 'gYear', 'gMonth', 'gDay', 'gYearMonth', 'gMonthDay', 'duration',
                         'yearMonthDuration', 'dayTimeDuration', 'byte', 'short', 'int', 'long', 'unsignedByte',
                         'unsignedShort', 'unsignedInt', 'unsignedLong', 'positiveInteger', 'nonNegativeInteger',
                         'negativeInteger', 'nonPositiveInteger', 'hexBinary', 'base64Binary', 'anyURI'):
            local = 'string'           # samm:curie and others
        if local == 'dateTimeStamp':
            local = 'dateTime'
        return {'modelType': 'Property', 'valueType': f'xs:{local}', '_datatype': _iec_type(local)}

    def _cd(self, node, identifier, datatype, unit):
        names = _lang_strings(self._values(node, 'preferredName'), 255) or [{'language': 'en', 'text': _local(identifier)}]
        content = {'modelType': 'DataSpecificationIec61360', 'preferredName': names,
                   'shortName': [{'language': 'en', 'text': _local(identifier)[:18]}]}
        definition = _lang_strings(self._values(node, 'description'), 1023)
        if definition:
            content['definition'] = definition
        if datatype:
            content['dataType'] = 'REAL_MEASURE' if datatype == 'REAL_COUNT' and unit else \
                ('INTEGER_MEASURE' if datatype == 'INTEGER_COUNT' and unit else datatype)
        if unit:
            content['unit'] = unit
        return {'modelType': 'ConceptDescription', 'id': identifier, 'idShort': _id_short(_local(identifier)),
                'embeddedDataSpecifications': [{'dataSpecification': _external(IEC61360),
                                                'dataSpecificationContent': content}]}


def _iec_type(local: str) -> str:
    if local in INTEGRAL:
        return 'INTEGER_COUNT'
    if local in NUMERIC:
        return 'REAL_COUNT'
    return {'boolean': 'BOOLEAN', 'date': 'DATE', 'time': 'TIME', 'dateTime': 'TIMESTAMP', 'anyURI': 'IRI'}.get(local, 'STRING')


# -- payloads -------------------------------------------------------------------------------------
def _payload_key(element: dict) -> str:
    for extension in element.get('extensions') or []:
        if extension.get('name') == 'samm:payloadName':
            return extension['value']
    return element['idShort']


def instance(template: dict, payload: dict, identifier: str, id_short: str | None = None) -> dict:
    """A submodel instance of ``template`` carrying the values of a SAMM JSON ``payload``.
    Absent optional values are left out; list items are replicated from the list's prototype."""
    submodel = {k: copy.deepcopy(v) for k, v in template.items() if k not in ('submodelElements', 'kind')}
    submodel.update(id=identifier, kind='Instance')
    if id_short:
        submodel['idShort'] = id_short
    submodel['submodelElements'] = _fill_children(template.get('submodelElements') or [], payload)
    if not submodel['submodelElements']:
        del submodel['submodelElements']
    _strip_template(submodel)
    return submodel


def _fill_children(children, payload):
    out = []
    for child in children:
        key = _payload_key(child)
        if not isinstance(payload, dict) or key not in payload or payload[key] is None:
            continue
        filled = _fill(child, payload[key])
        if filled is not None:
            out.append(filled)
    return out


def _fill(element, value):
    from . import elements as aas
    result = {k: copy.deepcopy(v) for k, v in element.items() if k != 'value'}
    model_type = element.get('modelType')
    if model_type == 'SubmodelElementCollection':
        result['value'] = _fill_children(element.get('value') or [], value)
        if not result['value']:
            del result['value']
    elif model_type == 'SubmodelElementList':
        prototype = (element.get('value') or [None])[0]
        items = [_fill(prototype, v) for v in (value if isinstance(value, list) else [value])] if prototype else []
        result['value'] = [i for i in items if i is not None]
        if not result['value']:
            del result['value']
    elif model_type == 'MultiLanguageProperty':
        texts = value if isinstance(value, list) else [{k: v} for k, v in value.items()] if isinstance(value, dict) else \
            [{'en': str(value)}]
        aas.patch_value(result, texts)
    elif model_type == 'Property':
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        value_type = result.get('valueType')
        if isinstance(value, float) and value_type in ('xs:integer', 'xs:int', 'xs:long') and value.is_integer():
            value = int(value)
        try:
            aas.patch_value(result, value)
        except aas.ValueTypeError:
            result['value'] = str(value)          # kept; aas-core verification reports it
    return result


def payload(submodel: dict, template: dict | None = None) -> dict:
    """The SAMM JSON payload of a submodel instance (ValueOnly with payload names restored)."""
    from . import elements as aas
    names = {}
    if template:
        def collect(children, prefix):
            for child in children or []:
                key = _payload_key(child)
                if key != child['idShort']:
                    names[(prefix, child['idShort'])] = key
                inner = child.get('value') if child.get('modelType') in ('SubmodelElementCollection', 'SubmodelElementList') else None
                if child.get('modelType') == 'SubmodelElementList':
                    inner = (inner or [{}])[0].get('value') if inner and inner[0].get('modelType') == 'SubmodelElementCollection' else None
                collect(inner, prefix + (child['idShort'],))
        collect(template.get('submodelElements'), ())

    def rename(value, prefix):
        if isinstance(value, dict):
            return {names.get((prefix, k), k): rename(v, prefix + (k,)) for k, v in value.items()}
        if isinstance(value, list):
            return [rename(v, prefix) for v in value]
        return value
    return rename(aas.submodel_value_only(submodel), ())


def _strip_template(node):
    """Instances carry neither template qualifiers (AASd-129) nor payload-name extensions."""
    if isinstance(node, dict):
        if node.get('qualifiers'):
            node['qualifiers'] = [q for q in node['qualifiers'] if q.get('kind') != 'TemplateQualifier']
            if not node['qualifiers']:
                del node['qualifiers']
        if node.get('extensions'):
            node['extensions'] = [e for e in node['extensions'] if e.get('name') != 'samm:payloadName']
            if not node['extensions']:
                del node['extensions']
        for value in node.values():
            _strip_template(value)
    elif isinstance(node, list):
        for item in node:
            _strip_template(item)
