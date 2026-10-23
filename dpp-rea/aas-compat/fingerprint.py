"""Fingerprint AAS files (JSON, XML, AASX) for metamodel version and version-sensitive features.

Usage: python3 fingerprint.py <corpus-dir> > fingerprints.json

Reads files as data only. For AASX packages it inspects the OPC parts without extracting to disk.
"""
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

NAMESPACE = re.compile(r'https://admin-shell\.io/aas/(\d)/(\d)')
ORIGIN_RELS = ('http://admin-shell.io/aasx/relationships/aasx-origin',
               'http://www.admin-shell.io/aasx/relationships/aasx-origin')
ID_SHORT_30 = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')


def walk_json(node, found):
    if isinstance(node, dict):
        model_type = node.get('modelType')
        if model_type:
            found['model_types'][model_type] = found['model_types'].get(model_type, 0) + 1
        id_short = node.get('idShort')
        if isinstance(id_short, str) and not ID_SHORT_30.match(id_short):
            found['idshort_needs_3_1'].append(id_short)
        kind = node.get('assetKind')
        if kind:
            found['asset_kinds'].add(kind)
        content_type = node.get('contentType')
        if isinstance(content_type, str) and len(content_type) > 100:
            found['long_content_type'] += 1
        if model_type in ('File', 'Blob') and 'contentType' not in node:
            found['file_without_content_type'] += 1
        if 'createdAt' in node or 'updatedAt' in node:
            found['admin_timestamps'] += 1
        if model_type == 'DataSpecificationUom':
            found['uom_data_spec'] += 1
        if model_type == 'Submodel':
            semantic = node.get('semanticId', {}).get('keys', [{}])
            found['submodel_semantic_ids'].append(semantic[0].get('value') if semantic else None)
        for value in node.values():
            walk_json(value, found)
    elif isinstance(node, list):
        for value in node:
            walk_json(value, found)


def walk_xml(root, found):
    for element in root.iter():
        tag = element.tag.split('}')[-1]
        if tag in ('submodel', 'property', 'file', 'blob', 'submodelElementCollection',
                   'submodelElementList', 'multiLanguageProperty', 'entity', 'range',
                   'referenceElement', 'relationshipElement', 'operation'):
            name = tag[0].upper() + tag[1:]
            found['model_types'][name] = found['model_types'].get(name, 0) + 1
        if tag == 'idShort' and element.text and not ID_SHORT_30.match(element.text):
            found['idshort_needs_3_1'].append(element.text)
        if tag == 'assetKind' and element.text:
            found['asset_kinds'].add(element.text)
        if tag == 'contentType' and element.text and len(element.text) > 100:
            found['long_content_type'] += 1
        if tag in ('createdAt', 'updatedAt'):
            found['admin_timestamps'] += 1


def new_found():
    return {'model_types': {}, 'idshort_needs_3_1': [], 'asset_kinds': set(), 'long_content_type': 0,
            'file_without_content_type': 0, 'admin_timestamps': 0, 'uom_data_spec': 0,
            'submodel_semantic_ids': []}


def analyse_payload(name, data, found, report):
    if name.lower().endswith('.json'):
        report['payload_format'] = 'json'
        walk_json(json.loads(data), found)
    else:
        report['payload_format'] = 'xml'
        text = data.decode('utf-8', errors='replace')
        report['xml_namespaces'] = sorted({'.'.join(m) for m in NAMESPACE.findall(text[:4000])})
        root = ElementTree.fromstring(data)
        walk_xml(root, found)
        for element in root.iter():
            if element.tag.split('}')[-1] == 'submodel':
                for child in element.iter():
                    if child.tag.split('}')[-1] == 'value' and child.text and '/' in child.text:
                        found['submodel_semantic_ids'].append(child.text)
                        break


def analyse(path):
    report = {'file': str(path), 'container': path.suffix.lower().lstrip('.')}
    found = new_found()
    try:
        if path.suffix.lower() == '.aasx':
            with zipfile.ZipFile(path) as package:
                names = package.namelist()
                rels = [n for n in names if n.endswith('.rels')]
                origin_types = set()
                spec_parts = []
                for rel in rels:
                    tree = ElementTree.fromstring(package.read(rel))
                    for item in tree:
                        rel_type = item.get('Type', '')
                        if 'aasx-origin' in rel_type:
                            origin_types.add(rel_type)
                        if rel_type.endswith('/aas-spec'):
                            spec_parts.append(str(PurePosixPath(rel).parent.parent / item.get('Target', '')).lstrip('/'))
                report['aasx_origin_rel'] = sorted(origin_types)
                report['aasx_origin_rel_conventional'] = bool(origin_types) and origin_types <= set(ORIGIN_RELS)
                report['aasx_parts'] = len(names)
                report['aasx_supplementary'] = len([n for n in names if not n.endswith(('.rels', '.xml', '.json', '].xml'))
                                                    and not n.startswith('aasx/')])
                spec = [n for n in names if n.startswith('aasx/') and n.endswith(('.xml', '.json'))
                        and 'aasx-origin' not in n]
                report['aasx_spec_parts'] = spec
                for name in spec:
                    analyse_payload(name, package.read(name), found, report)
        else:
            analyse_payload(path.name, path.read_bytes(), found, report)
    except Exception as error:  # noqa: BLE001 - recorded as a finding
        report['error'] = f'{type(error).__name__}: {error}'
    found['asset_kinds'] = sorted(found['asset_kinds'])
    found['idshort_needs_3_1'] = sorted(set(found['idshort_needs_3_1']))[:10]
    found['submodel_semantic_ids'] = sorted({s for s in found['submodel_semantic_ids'] if s})
    report.update(found)
    needs = []
    if found['idshort_needs_3_1'] or 'Role' in found['asset_kinds'] or found['long_content_type']:
        needs.append('3.1')
    if 'Batch' in found['asset_kinds'] or found['admin_timestamps'] or found['uom_data_spec']:
        needs.append('3.2')
    report['min_version_from_features'] = needs[-1] if needs else '3.0'
    return report


def main():
    corpus = Path(sys.argv[1])
    results = [analyse(p) for p in sorted(corpus.rglob('*')) if p.suffix.lower() in ('.aasx', '.json', '.xml')]
    for r in results:
        r['file'] = str(Path(r['file']).relative_to(corpus))
    json.dump(results, sys.stdout, indent=1)


main()
