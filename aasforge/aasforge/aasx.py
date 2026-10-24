"""AASX (IDTA-01005, OPC) package reading and writing.

Reading is tolerant but never silent: defects found in published IDTA templates are repaired and
reported as ``repairs`` (``TargetMode="External"`` on the aasx-origin relationship, supplementary
relationships pointing at missing parts, both ``admin-shell.io`` relationship spellings, JSON or XML
spec parts, several spec parts).

Writing is strict and deterministic: one internal aasx-origin relationship, one JSON or XML spec
part, every File/thumbnail path present as a part, fixed timestamps and sorted entries so that the
same environment and files always produce the same bytes.
"""
from __future__ import annotations

import io
import json
import posixpath
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

from . import versioning

REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
REL_ORIGIN = 'http://admin-shell.io/aasx/relationships/aasx-origin'
REL_SPEC = 'http://admin-shell.io/aasx/relationships/aas-spec'
REL_SUPPL = 'http://admin-shell.io/aasx/relationships/aas-suppl'
REL_THUMB = 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail'
ALT_PREFIX = 'http://www.admin-shell.io/aasx/relationships/'
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
MAX_PART_BYTES = 256 * 1024 * 1024


class AasxError(ValueError):
    pass


@dataclass
class Package:
    normalised: versioning.Normalised
    files: dict = field(default_factory=dict)          # part name ("/aasx/files/x.pdf") -> bytes
    content_types: dict = field(default_factory=dict)  # part name -> media type
    thumbnail: str | None = None
    repairs: list = field(default_factory=list)


def _rel_type(value: str) -> str:
    return value.replace(ALT_PREFIX, 'http://admin-shell.io/aasx/relationships/')


def _rels_name(part: str) -> str:
    directory, name = posixpath.split(part.lstrip('/'))
    return posixpath.join(directory, '_rels', name + '.rels')


def _resolve(source_part: str, target: str) -> str:
    if target.startswith('/'):
        return posixpath.normpath(target)
    return posixpath.normpath(posixpath.join(posixpath.dirname('/' + source_part.lstrip('/')), target))


def _relationships(archive: zipfile.ZipFile, rels_part: str, source_part: str, repairs: list) -> list:
    if rels_part not in archive.namelist():
        return []
    tree = ElementTree.fromstring(archive.read(rels_part))
    result = []
    for rel in tree.findall(f'{{{REL_NS}}}Relationship'):
        target = rel.get('Target', '')
        mode = rel.get('TargetMode', 'Internal')
        rel_type = _rel_type(rel.get('Type', ''))
        if rel.get('Type', '') != rel_type:
            repairs.append(f'{rels_part}: normalised relationship type {rel.get("Type")}')
        if mode == 'External' and not target.lower().startswith(('http:', 'https:', 'file:')):
            repairs.append(f'{rels_part}: relationship to {target} declared TargetMode="External" '
                           'but points inside the package; treated as internal')
            mode = 'Internal'
        result.append({'type': rel_type, 'target': target if mode == 'External' else _resolve(source_part, target),
                       'mode': mode})
    return result


def read(data: bytes) -> Package:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise AasxError('not a ZIP/OPC package') from error
    names = set(archive.namelist())
    for info in archive.infolist():
        if info.file_size > MAX_PART_BYTES or '..' in info.filename.split('/'):
            raise AasxError(f'refusing part {info.filename!r}')
    repairs: list = []
    content_types = {}
    if '[Content_Types].xml' in names:
        tree = ElementTree.fromstring(archive.read('[Content_Types].xml'))
        defaults = {d.get('Extension', '').lower(): d.get('ContentType') for d in tree.findall(f'{{{CT_NS}}}Default')}
        overrides = {o.get('PartName'): o.get('ContentType') for o in tree.findall(f'{{{CT_NS}}}Override')}
        for name in names:
            part = '/' + name
            content_types[part] = overrides.get(part) or defaults.get(posixpath.splitext(name)[1][1:].lower())
    root_rels = _relationships(archive, '_rels/.rels', '/', repairs)
    origins = [r['target'] for r in root_rels if r['type'] == REL_ORIGIN]
    thumbnail = next((r['target'] for r in root_rels if r['type'] == REL_THUMB), None)
    if not origins:
        if 'aasx/aasx-origin' in names:
            origins = ['/aasx/aasx-origin']
            repairs.append('root aasx-origin relationship missing; used /aasx/aasx-origin')
        else:
            raise AasxError('no aasx-origin relationship and no /aasx/aasx-origin part')
    spec_rels = []
    for origin in origins:
        spec_rels += [r for r in _relationships(archive, _rels_name(origin), origin, repairs) if r['type'] == REL_SPEC]
    if not spec_rels:
        raise AasxError('aasx-origin has no aas-spec relationship')
    environments, files = [], {}
    for spec in spec_rels:
        name = spec['target'].lstrip('/')
        if name not in names:
            raise AasxError(f'aas-spec part {spec["target"]} is missing')
        payload = archive.read(name)
        normalised = versioning.parse_json(payload) if name.lower().endswith('.json') else versioning.parse_xml(payload)
        environments.append(normalised)
        for rel in _relationships(archive, _rels_name(name), name, repairs):
            if rel['type'] != REL_SUPPL or rel['mode'] == 'External':
                continue
            part = rel['target'].lstrip('/')
            if part in names:
                files['/' + part] = archive.read(part)
            else:
                repairs.append(f'supplementary file {rel["target"]} is referenced but missing from the package')
    # Files referenced by File elements but not declared as aas-suppl are still loaded when present.
    merged = environments[0]
    for extra in environments[1:]:
        for key in ('assetAdministrationShells', 'submodels', 'conceptDescriptions'):
            merged.environment.setdefault(key, []).extend(extra.environment.get(key, []))
        merged.notes += extra.notes
        merged.sidecar += extra.sidecar
    if len(environments) > 1:
        repairs.append(f'merged {len(environments)} aas-spec parts')
    for path in _file_paths(merged.environment):
        part = path.lstrip('/')
        if path not in files and part in names:
            files[path] = archive.read(part)
            repairs.append(f'{path} used by a File element but not declared as aas-suppl; loaded anyway')
    if thumbnail and thumbnail.lstrip('/') in names:
        files[thumbnail] = archive.read(thumbnail.lstrip('/'))
    return Package(merged, files, {k: v for k, v in content_types.items() if k in files}, thumbnail, repairs)


def _file_paths(environment: dict):
    seen = []

    def walk(node):
        if isinstance(node, dict):
            if node.get('modelType') == 'File' and isinstance(node.get('value'), str):
                value = node['value']
                if value.startswith('/') and not value.startswith('//'):
                    seen.append(value)
            thumb = node.get('defaultThumbnail')
            if isinstance(thumb, dict) and isinstance(thumb.get('path'), str) and thumb['path'].startswith('/'):
                seen.append(thumb['path'])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(environment)
    return seen


def _xml(element: ElementTree.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + ElementTree.tostring(element, encoding='utf-8')


def write(environment: dict, files: dict, content_types: dict | None = None, fmt: str = 'json',
          version: str = '3.1', thumbnail: str | None = None) -> bytes:
    """Write a deterministic AASX package. ``files`` maps part names to bytes."""
    content_types = content_types or {}
    missing = [p for p in _file_paths(environment) if p not in files]
    if missing:
        raise AasxError(f'File elements reference parts that were not supplied: {missing[:5]}')
    spec_part = '/aasx/environment.aas.json' if fmt == 'json' else '/aasx/environment.aas.xml'
    payload = (json.dumps(environment, ensure_ascii=False, indent=1).encode() if fmt == 'json'
               else versioning.to_xml(environment, version).encode())
    parts = {'/aasx/aasx-origin': b'Intentionally empty.', spec_part: payload}
    parts.update(files)

    def rels(entries):
        root = ElementTree.Element('Relationships', xmlns=REL_NS)
        for index, (rel_type, target) in enumerate(entries, 1):
            ElementTree.SubElement(root, 'Relationship', Type=rel_type, Target=target, Id=f'R{index}')
        return _xml(root)

    root_entries = [(REL_ORIGIN, '/aasx/aasx-origin')]
    if thumbnail:
        root_entries.append((REL_THUMB, thumbnail))
    parts['/_rels/.rels'] = rels(root_entries)
    parts['/aasx/_rels/aasx-origin.rels'] = rels([(REL_SPEC, spec_part)])
    suppl = sorted(p for p in files if p != thumbnail)
    if suppl:
        parts['/' + _rels_name(spec_part)] = rels([(REL_SUPPL, p) for p in suppl])
    types = ElementTree.Element('Types', xmlns=CT_NS)
    ElementTree.SubElement(types, 'Default', Extension='rels',
                           ContentType='application/vnd.openxmlformats-package.relationships+xml')
    ElementTree.SubElement(types, 'Default', Extension='xml', ContentType='application/xml')
    ElementTree.SubElement(types, 'Default', Extension='json', ContentType='application/json')
    ElementTree.SubElement(types, 'Override', PartName='/aasx/aasx-origin', ContentType='text/plain')
    for part in sorted(files):
        ElementTree.SubElement(types, 'Override', PartName=part,
                               ContentType=content_types.get(part) or 'application/octet-stream')
    parts['/[Content_Types].xml'] = _xml(types)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(parts, key=lambda p: (p != '/[Content_Types].xml', p)):
            info = zipfile.ZipInfo(name.lstrip('/'), date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, parts[name])
    return buffer.getvalue()
