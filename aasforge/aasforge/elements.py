"""idShortPath navigation and the API serialization modifiers ($value, $metadata, $reference, $path)
with ``level`` (deep|core) and ``extent`` (withoutBlobValue|withBlobValue) support.

Elements are handled as AAS 3.1 JSON dicts.
"""
from __future__ import annotations

import copy
import re
from urllib.parse import unquote

CHILD_KEYS = {'SubmodelElementCollection': 'value', 'SubmodelElementList': 'value', 'Entity': 'statements',
              'AnnotatedRelationshipElement': 'annotations'}
KEY_TYPE = {'SubmodelElementCollection': 'SubmodelElementCollection', 'SubmodelElementList': 'SubmodelElementList',
            'Property': 'Property', 'MultiLanguageProperty': 'MultiLanguageProperty', 'Range': 'Range',
            'File': 'File', 'Blob': 'Blob', 'ReferenceElement': 'ReferenceElement', 'Entity': 'Entity',
            'RelationshipElement': 'RelationshipElement',
            'AnnotatedRelationshipElement': 'AnnotatedRelationshipElement', 'Operation': 'Operation',
            'Capability': 'Capability', 'BasicEventElement': 'BasicEventElement'}
NUMERIC = {'xs:decimal', 'xs:integer', 'xs:double', 'xs:float', 'xs:int', 'xs:long', 'xs:short', 'xs:byte',
           'xs:nonNegativeInteger', 'xs:positiveInteger', 'xs:nonPositiveInteger', 'xs:negativeInteger',
           'xs:unsignedInt', 'xs:unsignedLong', 'xs:unsignedShort', 'xs:unsignedByte'}
INTEGRAL = NUMERIC - {'xs:decimal', 'xs:double', 'xs:float'}
SEGMENT = re.compile(r'([^.\[\]]+)|\[(\d+)\]')


class PathError(KeyError):
    pass


class ValueTypeError(ValueError):
    """A ValueOnly value is not a valid lexical form of the element's ``valueType``."""


def parse_path(path: str) -> list:
    """Split ``a.b[2].c`` (optionally URL-encoded) into ['a', 'b', 2, 'c']."""
    path = unquote(path)
    segments, position = [], 0
    for match in SEGMENT.finditer(path):
        if match.start() != position and path[position:match.start()] != '.':
            raise PathError(f'invalid idShortPath {path!r}')
        segments.append(match.group(1) if match.group(1) is not None else int(match.group(2)))
        position = match.end()
    if position != len(path) or not segments or isinstance(segments[0], int):
        raise PathError(f'invalid idShortPath {path!r}')
    return segments


def format_path(segments: list) -> str:
    out = ''
    for segment in segments:
        out += f'[{segment}]' if isinstance(segment, int) else (('.' if out else '') + segment)
    return out


def children(element: dict) -> list | None:
    key = CHILD_KEYS.get(element.get('modelType'))
    return element.setdefault(key, []) if key else None


def locate(submodel: dict, segments: list) -> tuple[list, int, dict]:
    """Return (container list, index, element) for the element at ``segments``."""
    container = submodel.setdefault('submodelElements', [])
    parent_type = 'Submodel'
    element = None
    index = -1
    for depth, segment in enumerate(segments):
        if isinstance(segment, int):
            if parent_type != 'SubmodelElementList' or segment >= len(container):
                raise PathError(f'index [{segment}] not found at {format_path(segments[:depth])!r}')
            index = segment
        else:
            index = next((i for i, e in enumerate(container) if e.get('idShort') == segment), -1)
            if index < 0:
                raise PathError(f'{format_path(segments[:depth + 1])!r} not found')
        element = container[index]
        if depth < len(segments) - 1:
            nested = children(element)
            if nested is None:
                raise PathError(f'{format_path(segments[:depth + 1])!r} has no children')
            container, parent_type = nested, element.get('modelType')
    return container, index, element


def get(submodel: dict, path: str) -> dict:
    return locate(submodel, parse_path(path))[2]


def container_for(submodel: dict, path: str | None) -> tuple[list, str]:
    """Children list (and owner modelType) of the element at ``path`` or of the submodel."""
    if not path:
        return submodel.setdefault('submodelElements', []), 'Submodel'
    element = get(submodel, path)
    nested = children(element)
    if nested is None:
        raise PathError(f'{path!r} cannot contain submodel elements')
    return nested, element.get('modelType')


def insert(submodel: dict, path: str | None, element: dict) -> str:
    container, owner = container_for(submodel, path)
    if owner != 'SubmodelElementList':
        if not element.get('idShort'):
            raise PathError('submodel element needs an idShort')
        if any(e.get('idShort') == element['idShort'] for e in container):
            raise FileExistsError(element['idShort'])
    container.append(element)
    if owner == 'SubmodelElementList':
        return f'{path}[{len(container) - 1}]'
    return f'{path}.{element["idShort"]}' if path else element['idShort']


def replace(submodel: dict, path: str, element: dict) -> None:
    container, index, _ = locate(submodel, parse_path(path))
    container[index] = element


def remove(submodel: dict, path: str) -> None:
    container, index, _ = locate(submodel, parse_path(path))
    del container[index]


def remove_many(submodel: dict, paths) -> None:
    """Remove several elements; deeper paths and higher list indices go first so that earlier
    removals never shift the indices of later ones."""
    for segments in sorted((parse_path(p) for p in set(paths)), reverse=True):
        try:
            container, index, _ = locate(submodel, segments)
        except PathError:
            continue   # already removed together with an ancestor
        del container[index]


def all_paths(submodel: dict, level: str = 'deep') -> list[str]:
    result = []

    def walk(container, prefix, owner):
        for index, element in enumerate(container):
            if owner == 'SubmodelElementList':
                path = f'{prefix}[{index}]'
            else:
                path = f'{prefix}.{element.get("idShort")}' if prefix else element.get('idShort')
            result.append(path)
            nested = CHILD_KEYS.get(element.get('modelType'))
            if nested and level == 'deep':
                walk(element.get(nested, []), path, element.get('modelType'))

    walk(submodel.get('submodelElements', []), '', 'Submodel')
    return result


def walk_elements(submodel: dict):
    """Yield (idShortPath, element) for every submodel element, depth first."""
    for path in all_paths(submodel):
        yield path, get(submodel, path)


# -- level / extent ----------------------------------------------------------------------------
def apply_level_extent(node, level: str = 'deep', extent: str = 'withoutBlobValue', _depth: int = 0):
    """Return a copy honouring ``level=core`` (no grandchildren) and ``extent`` for Blob values."""
    if isinstance(node, list):
        return [apply_level_extent(n, level, extent, _depth) for n in node]
    if not isinstance(node, dict):
        return node
    result = dict(node)
    model_type = node.get('modelType')
    if model_type == 'Blob' and extent != 'withBlobValue':
        result.pop('value', None)
    key = 'submodelElements' if model_type == 'Submodel' else CHILD_KEYS.get(model_type)
    if key and key in node:
        if level == 'core' and _depth >= 1:
            result.pop(key)
        else:
            result[key] = [apply_level_extent(c, level, extent, _depth + 1) for c in node[key]]
    return result


# -- $value --------------------------------------------------------------------------------------
def _typed(value, value_type):
    if value is None:
        return None
    if value_type == 'xs:boolean':
        return value.strip().lower() in ('true', '1')
    if value_type in NUMERIC:
        try:
            return int(value) if value_type in INTEGRAL else float(value)
        except ValueError:
            return value
    return value


def _reference_value(reference):
    return copy.deepcopy(reference) if reference else None


def value_only(element: dict, extent: str = 'withoutBlobValue', level: str = 'deep', _depth: int = 0):
    """ValueOnly serialization of one submodel element (IDTA-01001 Clause 11.4.3)."""
    model_type = element.get('modelType')
    if model_type == 'Property':
        return _typed(element.get('value'), element.get('valueType'))
    if model_type == 'MultiLanguageProperty':
        return [{s['language']: s['text']} for s in element.get('value', [])]
    if model_type == 'Range':
        return {'min': _typed(element.get('min'), element.get('valueType')),
                'max': _typed(element.get('max'), element.get('valueType'))}
    if model_type == 'File':
        return {'contentType': element.get('contentType'), 'value': element.get('value')}
    if model_type == 'Blob':
        result = {'contentType': element.get('contentType')}
        if extent == 'withBlobValue':
            result['value'] = element.get('value')
        return result
    if model_type == 'ReferenceElement':
        return _reference_value(element.get('value'))
    if model_type == 'RelationshipElement':
        return {'first': _reference_value(element.get('first')), 'second': _reference_value(element.get('second'))}
    if model_type == 'AnnotatedRelationshipElement':
        result = {'first': _reference_value(element.get('first')), 'second': _reference_value(element.get('second'))}
        if level == 'deep' or _depth == 0:
            result['annotations'] = [{a['idShort']: value_only(a, extent, level, _depth + 1)}
                                     for a in element.get('annotations', [])]
        return result
    if model_type == 'SubmodelElementCollection':
        if level == 'core' and _depth > 0:
            return {}
        return {c['idShort']: value_only(c, extent, level, _depth + 1) for c in element.get('value', [])}
    if model_type == 'SubmodelElementList':
        if level == 'core' and _depth > 0:
            return []
        return [value_only(c, extent, level, _depth + 1) for c in element.get('value', [])]
    if model_type == 'Entity':
        result = {'entityType': element.get('entityType')}
        if element.get('globalAssetId'):
            result['globalAssetId'] = element['globalAssetId']
        if element.get('specificAssetIds'):
            result['specificAssetIds'] = copy.deepcopy(element['specificAssetIds'])
        if level == 'deep' or _depth == 0:
            result['statements'] = {s['idShort']: value_only(s, extent, level, _depth + 1)
                                    for s in element.get('statements', [])}
        return result
    if model_type == 'BasicEventElement':
        return {'observed': _reference_value(element.get('observed'))}
    return None  # Operation, Capability have no value


def submodel_value_only(submodel: dict, extent='withoutBlobValue', level='deep') -> dict:
    return {e['idShort']: value_only(e, extent, level, 0) for e in submodel.get('submodelElements', [])
            if e.get('modelType') not in ('Operation', 'Capability')}


def _lexical(value, value_type):
    if isinstance(value, bool):
        text = 'true' if value else 'false'
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = str(int(value)) if value.is_integer() and value_type in INTEGRAL else repr(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ValueTypeError(f'{type(value).__name__} is not a ValueOnly value for {value_type}')
    if value_type and not _consistent(text, value_type):
        raise ValueTypeError(f'{text!r} is not a valid {value_type} value')
    return text


def _consistent(text: str, value_type: str) -> bool:
    from aas_core3_1 import types, verification
    try:
        data_type = types.DataTypeDefXSD(value_type)
    except ValueError:
        return True          # unknown type: leave it to model verification
    return verification.value_consistent_with_xsd_type(text, data_type)


def patch_value(element: dict, value) -> None:
    """Apply a ValueOnly payload to an element in place (inverse of :func:`value_only`)."""
    model_type = element.get('modelType')
    if model_type == 'Property':
        element['value'] = None if value is None else _lexical(value, element.get('valueType'))
        if element['value'] is None:
            element.pop('value')
    elif model_type == 'MultiLanguageProperty':
        element['value'] = [{'language': k, 'text': v} for item in value for k, v in item.items()]
    elif model_type == 'Range':
        for bound in ('min', 'max'):
            if bound in value and value[bound] is None:
                element.pop(bound, None)
            elif bound in value:
                element[bound] = _lexical(value[bound], element.get('valueType'))
    elif model_type in ('File', 'Blob'):
        for key in ('contentType', 'value'):
            if key in value:
                element[key] = value[key]
    elif model_type == 'ReferenceElement':
        element['value'] = value
    elif model_type in ('RelationshipElement', 'AnnotatedRelationshipElement'):
        for key in ('first', 'second'):
            if key in value:
                element[key] = value[key]
        if model_type == 'AnnotatedRelationshipElement' and 'annotations' in value:
            by_id = {a['idShort']: a for a in element.get('annotations', [])}
            for item in value['annotations']:
                for id_short, sub in item.items():
                    if id_short in by_id:
                        patch_value(by_id[id_short], sub)
    elif model_type == 'SubmodelElementCollection':
        by_id = {c['idShort']: c for c in element.get('value', [])}
        for id_short, sub in value.items():
            if id_short not in by_id:
                raise PathError(f'{id_short!r} is not a child of {element.get("idShort")!r}')
            patch_value(by_id[id_short], sub)
    elif model_type == 'SubmodelElementList':
        items = element.get('value', [])
        if len(value) != len(items):
            raise PathError('ValueOnly list length must match the existing SubmodelElementList')
        for item, sub in zip(items, value):
            patch_value(item, sub)
    elif model_type == 'Entity':
        for key in ('entityType', 'globalAssetId', 'specificAssetIds'):
            if key in value:
                element[key] = value[key]
        by_id = {s['idShort']: s for s in element.get('statements', [])}
        for id_short, sub in (value.get('statements') or {}).items():
            if id_short in by_id:
                patch_value(by_id[id_short], sub)
    elif model_type == 'BasicEventElement':
        if 'observed' in value:
            element['observed'] = value['observed']
    else:
        raise PathError(f'{model_type} has no value to patch')


def patch_submodel_value(submodel: dict, value: dict) -> None:
    by_id = {e['idShort']: e for e in submodel.get('submodelElements', [])}
    for id_short, sub in value.items():
        if id_short not in by_id:
            raise PathError(f'{id_short!r} is not an element of the submodel')
        patch_value(by_id[id_short], sub)


# -- $metadata -------------------------------------------------------------------------------
VALUE_KEYS = {'Property': ('value', 'valueId'), 'MultiLanguageProperty': ('value', 'valueId'), 'Range': ('min', 'max'),
              'File': ('value',), 'Blob': ('value',), 'ReferenceElement': ('value',),
              'RelationshipElement': ('first', 'second'), 'AnnotatedRelationshipElement': ('first', 'second', 'annotations'),
              'SubmodelElementCollection': ('value',), 'SubmodelElementList': ('value',),
              'Entity': ('statements', 'globalAssetId', 'specificAssetIds'), 'BasicEventElement': ('observed',),
              'Submodel': ('submodelElements',)}


def metadata(element: dict) -> dict:
    result = copy.deepcopy(element)
    for key in VALUE_KEYS.get(element.get('modelType'), ()):
        result.pop(key, None)
    return result


def patch_metadata(element: dict, meta: dict) -> None:
    protected = set(VALUE_KEYS.get(element.get('modelType'), ())) | {'modelType', 'id', 'idShort'}
    for key, value in meta.items():
        if key not in protected:
            element[key] = value


# -- $reference ------------------------------------------------------------------------------
def reference(submodel: dict, path: str | None = None) -> dict:
    keys = [{'type': 'Submodel', 'value': submodel['id']}]
    if path:
        segments = parse_path(path)
        for depth in range(1, len(segments) + 1):
            element = locate(submodel, segments[:depth])[2]
            keys.append({'type': KEY_TYPE.get(element.get('modelType'), 'SubmodelElement'),
                         'value': str(segments[depth - 1])})
    return {'type': 'ModelReference', 'keys': keys}


def identifiable_reference(kind: str, identifier: str) -> dict:
    key_type = {'shell': 'AssetAdministrationShell', 'submodel': 'Submodel',
                'concept-description': 'ConceptDescription'}[kind]
    return {'type': 'ModelReference', 'keys': [{'type': key_type, 'value': identifier}]}
