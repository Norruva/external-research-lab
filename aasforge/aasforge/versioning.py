"""Metamodel version detection, 3.0 up-conversion and lossless 3.2 forward compatibility.

The canonical in-memory and stored form is an AAS 3.1 JSON environment (``dict``) that the released
aas-core3.1 SDK accepts. Inputs are normalised to it:

* 3.0 XML is read with aas-core3.0 and re-expressed as 3.1 JSON (the JSON form is unchanged
  between the versions for everything 3.0 can express).
* 3.0/3.1 JSON is accepted directly; the version is inferred from the features used.
* 3.2 content is reduced to 3.1 and every 3.2-only field is recorded in a *sidecar* of
  ``{"path": [...], "value": ...}`` entries so it can be re-applied on 3.2 export instead of being
  silently dropped (the behaviour of BaSyx Python 2.2.0 and AAS4J 2.0.2 observed in
  ``dpp-rea/aas-compat``).
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

import aas_core3.jsonization as j30
import aas_core3.xmlization as x30
import aas_core3_1.jsonization as j31
import aas_core3_1.verification as v31
import aas_core3_1.xmlization as x31

NAMESPACE = re.compile(r'https://admin-shell\.io/aas/(3)/([0-2])')
NS = {'3.0': 'https://admin-shell.io/aas/3/0', '3.1': 'https://admin-shell.io/aas/3/1',
      '3.2': 'https://admin-shell.io/aas/3/2'}
ID_SHORT_30 = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')
ADMIN_32_FIELDS = ('createdAt', 'updatedAt')
ASSET_KINDS_32 = ('Batch',)


class VersionError(ValueError):
    """Raised when content cannot be read as any supported metamodel version."""


@dataclass
class Normalised:
    """A 3.1 JSON environment plus what was needed to get there."""

    environment: dict
    source_version: str
    source_format: str
    sidecar: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def detect_xml_version(text: str) -> str:
    match = NAMESPACE.search(text[:8192])
    if not match:
        raise VersionError('XML document does not declare an AAS 3.x namespace')
    return f'{match.group(1)}.{match.group(2)}'


def _walk(node: Any, path: list, visit) -> None:
    visit(node, path)
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, path + [key], visit)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk(value, path + [index], visit)


def detect_json_version(environment: dict) -> str:
    """Infer the lowest metamodel version that can express a JSON environment."""
    found = {'3.1': False, '3.2': False}

    def visit(node, _path):
        if not isinstance(node, dict):
            return
        if any(key in node for key in ADMIN_32_FIELDS) or node.get('modelType') == 'DataSpecificationUom':
            found['3.2'] = True
        if node.get('assetKind') in ASSET_KINDS_32:
            found['3.2'] = True
        if node.get('assetKind') == 'Role':
            found['3.1'] = True
        id_short = node.get('idShort')
        if isinstance(id_short, str) and not ID_SHORT_30.match(id_short):
            found['3.1'] = True
        if node.get('modelType') in ('File', 'Blob') and 'contentType' not in node:
            found['3.1'] = True
        content_type = node.get('contentType')
        if isinstance(content_type, str) and len(content_type) > 100:
            found['3.1'] = True

    _walk(environment, [], visit)
    return '3.2' if found['3.2'] else '3.1' if found['3.1'] else '3.0'


def reduce_32(environment: dict) -> tuple[dict, list]:
    """Return a 3.1-expressible copy of a 3.2 environment and the sidecar of removed fields."""
    reduced = copy.deepcopy(environment)
    sidecar = []

    def strip(node, path):
        if not isinstance(node, dict):
            return
        for key in ADMIN_32_FIELDS:
            if key in node and path and path[-1] == 'administration':
                sidecar.append({'path': path + [key], 'value': node.pop(key)})
        if node.get('assetKind') in ASSET_KINDS_32:
            sidecar.append({'path': path + ['assetKind'], 'value': node['assetKind']})
            node['assetKind'] = 'Instance'
        specs = node.get('embeddedDataSpecifications')
        if isinstance(specs, list):
            kept = []
            for index, spec in enumerate(specs):
                content = spec.get('dataSpecificationContent', {}) if isinstance(spec, dict) else {}
                if content.get('modelType') == 'DataSpecificationUom':
                    sidecar.append({'path': path + ['embeddedDataSpecifications', index], 'value': spec,
                                    'insert': True})
                else:
                    kept.append(spec)
            if len(kept) != len(specs):
                if kept:
                    node['embeddedDataSpecifications'] = kept
                else:
                    node.pop('embeddedDataSpecifications')

    _walk(reduced, [], strip)
    return reduced, sidecar


def apply_sidecar(environment: dict, sidecar: list) -> dict:
    """Re-apply 3.2 fields recorded by :func:`reduce_32` (for 3.2 export)."""
    restored = copy.deepcopy(environment)
    for entry in sidecar:
        path = entry['path']
        owner_path, tail = (path[:-2], path[-2:]) if entry.get('insert') else (path[:-1], path[-1:])
        node = restored
        try:
            for key in owner_path:
                node = node[key]
        except (KeyError, IndexError, TypeError):
            continue
        if entry.get('insert'):
            target = node.setdefault(tail[0], [])
            target.insert(min(tail[1], len(target)), entry['value'])
        elif isinstance(node, dict):
            node[tail[0]] = entry['value']
    return restored


def parse_json(data: bytes | str) -> Normalised:
    environment = json.loads(data)
    if not isinstance(environment, dict):
        raise VersionError('AAS JSON environment must be an object')
    version = detect_json_version(environment)
    sidecar, notes = [], []
    if version == '3.2':
        environment, sidecar = reduce_32(environment)
        notes.append(f'reduced 3.2 content to 3.1; {len(sidecar)} field(s) kept in sidecar')
    try:
        j31.environment_from_jsonable(environment)
    except j31.DeserializationException as error:
        raise VersionError(f'not a valid AAS 3.1 JSON environment: {error.path}: {error.cause}') from error
    return Normalised(environment, version, 'json', sidecar, notes)


def parse_xml(data: bytes | str) -> Normalised:
    """Parse an AAS XML environment (3.0, 3.1 or 3.2); malformed XML raises VersionError."""
    from xml.etree.ElementTree import ParseError
    try:
        return _parse_xml(data)
    except (ParseError, UnicodeDecodeError) as error:
        raise VersionError(f'not well-formed XML: {error}') from error


def _parse_xml(data: bytes | str) -> Normalised:
    text = data.decode('utf-8-sig') if isinstance(data, bytes) else data
    version = detect_xml_version(text)
    # aas-core rejects attributes on the root element (e.g. xsi:schemaLocation, seen in the
    # Catena-X BatteryPass export); they carry no model content, so drop them.
    cleaned, removed = re.subn(r'(<(?:\w+:)?environment\b[^>]*?)\s+xsi:schemaLocation="[^"]*"', r'\1', text,
                               count=1)
    notes = ['removed xsi:schemaLocation from root element'] if removed else []
    if version == '3.0':
        try:
            environment = j30.to_jsonable(x30.environment_from_str(cleaned))
        except x30.DeserializationException as error:
            raise VersionError(f'not a valid AAS 3.0 XML environment: {error.path}: {error.cause}') from error
        notes.append('up-converted 3.0 XML to 3.1')
        return parse_json_dict(environment, '3.0', 'xml', notes)
    if version == '3.2':
        reduced, sidecar = _reduce_32_xml(cleaned)
        try:
            environment = j31.to_jsonable(x31.environment_from_str(reduced))
        except x31.DeserializationException as error:
            raise VersionError(f'not a valid AAS 3.2 XML environment (after reduction to 3.1): '
                               f'{error.path}: {error.cause}') from error
        notes.append(f'reduced 3.2 XML to 3.1; {len(sidecar)} field(s) kept in sidecar')
        return Normalised(environment, '3.2', 'xml', sidecar, notes)
    try:
        environment = j31.to_jsonable(x31.environment_from_str(cleaned))
    except x31.DeserializationException as error:
        raise VersionError(f'not a valid AAS 3.1 XML environment: {error.path}: {error.cause}') from error
    return Normalised(environment, version, 'xml', [], notes)


def _reduce_32_xml(text: str) -> tuple[str, list]:
    """Remove 3.2-only XML content from identifiables, recording it as a JSON-path sidecar, and
    rename the namespace to 3.1. Covers AdministrativeInformation createdAt/updatedAt and
    AssetKind Batch; DataSpecificationUom content is rejected explicitly."""
    from xml.etree import ElementTree
    ns = '{' + NS['3.2'] + '}'
    ElementTree.register_namespace('', NS['3.1'])
    root = ElementTree.fromstring(text)
    if root.find(f'.//{ns}dataSpecificationUom') is not None:
        raise VersionError('AAS 3.2 XML with DataSpecificationUom is not supported; submit it as JSON')
    sidecar = []
    for collection, item in (('assetAdministrationShells', 'assetAdministrationShell'), ('submodels', 'submodel'),
                             ('conceptDescriptions', 'conceptDescription')):
        container = root.find(f'{ns}{collection}')
        if container is None:
            continue
        for index, identifiable in enumerate(container.findall(f'{ns}{item}')):
            admin = identifiable.find(f'{ns}administration')
            if admin is not None:
                for key in ADMIN_32_FIELDS:
                    node = admin.find(f'{ns}{key}')
                    if node is not None:
                        sidecar.append({'path': [collection, index, 'administration', key], 'value': node.text})
                        admin.remove(node)
            kind = identifiable.find(f'{ns}assetInformation/{ns}assetKind')
            if kind is not None and kind.text in ASSET_KINDS_32:
                sidecar.append({'path': [collection, index, 'assetInformation', 'assetKind'], 'value': kind.text})
                kind.text = 'Instance'
    for element in root.iter():
        if element.tag.startswith(ns):
            element.tag = '{' + NS['3.1'] + '}' + element.tag[len(ns):]
    return ElementTree.tostring(root, encoding='unicode'), sidecar


def parse_json_dict(environment: dict, version: str, source_format: str, notes: list) -> Normalised:
    try:
        j31.environment_from_jsonable(environment)
    except j31.DeserializationException as error:
        raise VersionError(f'not a valid AAS 3.1 environment after conversion: {error.path}: {error.cause}') from error
    return Normalised(environment, version, source_format, [], notes)


def verify(environment: dict, limit: int = 200) -> list[dict]:
    """Constraint-verify a 3.1 JSON environment with the released aas-core3.1 verifier."""
    instance = j31.environment_from_jsonable(environment)
    errors = []
    for error in v31.verify(instance):
        errors.append({'path': str(error.path), 'cause': error.cause})
        if len(errors) >= limit:
            break
    return errors


def to_xml(environment: dict, version: str = '3.1') -> str:
    """Serialise a 3.1 JSON environment as XML (3.1, or 3.0 when expressible)."""
    if version == '3.0':
        return x30.to_str(j30.environment_from_jsonable(environment))
    return x31.to_str(j31.environment_from_jsonable(environment))


def export_json(environment: dict, sidecar: list | None, version: str = '3.1') -> dict:
    if version == '3.2' and sidecar:
        return apply_sidecar(environment, sidecar)
    if version == '3.0':
        j30.environment_from_jsonable(environment)  # raises if 3.1-only features are present
    return environment
