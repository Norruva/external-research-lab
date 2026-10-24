"""Submodel Template (SMT) engine: instantiate submodels from IDTA templates and check instances
against the template's SMT qualifiers (``SMT/Cardinality`` and the older ``Cardinality`` /
``Multiplicity``, plus ``SMT/AllowedValue``, ``SMT/AllowedRange``, ``SMT/RequiredLang``).

Paths use the API idShortPath notation; list prototypes in templates are addressed as ``[0]`` and
instance list entries are created by cloning the prototype.
"""
from __future__ import annotations

import copy
import re

from . import elements

CARDINALITY_TYPES = ('SMT/Cardinality', 'Cardinality', 'Multiplicity')
MANDATORY = ('One', 'OneToMany')
SINGLE = ('One', 'ZeroToOne')
VALUE_KEYS = ('value', 'min', 'max', 'valueId')


def qualifier(element: dict, *types):
    for q in element.get('qualifiers') or []:
        if q.get('type') in types:
            return q.get('value')
    return None


def cardinality(element: dict) -> str:
    return qualifier(element, *CARDINALITY_TYPES) or 'One'


def generic(path: str) -> str:
    return re.sub(r'\[\d+\]', '[]', path)


def _strip_template_parts(node, keep_examples: bool):
    if isinstance(node, dict):
        if 'qualifiers' in node:
            kept = [q for q in node['qualifiers'] if q.get('kind') != 'TemplateQualifier'
                    and not str(q.get('type', '')).startswith('SMT/')]
            if kept:
                node['qualifiers'] = kept
            else:
                node.pop('qualifiers')
        if not keep_examples and node.get('modelType') in ('Property', 'MultiLanguageProperty', 'Range', 'File',
                                                           'Blob', 'ReferenceElement'):
            for key in VALUE_KEYS:
                node.pop(key, None)
        for key in ('value', 'statements', 'annotations', 'submodelElements'):
            if isinstance(node.get(key), list):
                for child in node[key]:
                    _strip_template_parts(child, keep_examples)
    return node


def instantiate(template: dict, submodel_id: str, values: dict | None = None, *, keep_examples=False,
                prune_optional=True, id_short: str | None = None) -> dict:
    """Create an Instance submodel from a template submodel.

    ``values`` maps idShortPaths (``A.B``, ``List[2].C``) to ValueOnly values. List prototypes are
    removed unless a value addresses an entry, in which case entries are cloned up to that index.
    Optional elements without a value are pruned when ``prune_optional`` is set.
    """
    values = values or {}
    instance = copy.deepcopy(template)
    instance['id'] = submodel_id
    instance['kind'] = 'Instance'
    if id_short:
        instance['idShort'] = id_short
    prototypes = {}

    def collect(container, prefix, owner):
        for element in container:
            path = (f'{prefix}[0]' if owner == 'SubmodelElementList'
                    else (f'{prefix}.{element["idShort"]}' if prefix else element['idShort']))
            if owner == 'SubmodelElementList':
                prototypes[generic(prefix) + '[]'] = copy.deepcopy(element)
            key = elements.CHILD_KEYS.get(element.get('modelType'))
            if key:
                collect(element.get(key, []), path, element.get('modelType'))

    collect(template.get('submodelElements', []), '', 'Submodel')
    _strip_template_parts(instance, keep_examples)
    # remove list prototypes; re-create entries on demand
    for path in sorted((p for p in elements.all_paths(instance) if p.endswith('[0]')), key=len, reverse=True):
        parent = path[:-3]
        try:
            elements.get(instance, parent)['value'] = []
        except elements.PathError:
            continue
    clean_prototypes = {k: _strip_template_parts(copy.deepcopy(v), keep_examples) for k, v in prototypes.items()}
    for path, value in sorted(values.items(), key=lambda item: item[0]):
        set_value(instance, path, value, clean_prototypes)
    if prune_optional:
        prune(instance, template, set(values))
    return tidy(instance)


def tidy(node):
    """Unset empty child lists ("not set or at least one item" invariant of AAS 3.1)."""
    if isinstance(node, dict):
        for key in ('value', 'statements', 'annotations', 'submodelElements'):
            if isinstance(node.get(key), list):
                if node.get('modelType') == 'MultiLanguageProperty' and key == 'value':
                    if not node[key]:
                        node.pop(key)
                    continue
                for child in node[key]:
                    tidy(child)
                if not node[key]:
                    node.pop(key)
    return node


def _ensure(submodel: dict, segments: list, prototypes: dict):
    """Make sure the element at ``segments`` exists, cloning list prototypes as needed."""
    for depth in range(1, len(segments) + 1):
        partial = segments[:depth]
        try:
            elements.locate(submodel, partial)
        except elements.PathError:
            if not isinstance(partial[-1], int):
                raise
            parent = elements.locate(submodel, partial[:-1])[2]
            key = generic(elements.format_path(partial[:-1])) + '[]'
            if key not in prototypes:
                raise
            items = parent.setdefault('value', [])
            while len(items) <= partial[-1]:
                items.append(_empty_nested_lists(copy.deepcopy(prototypes[key])))
    return elements.locate(submodel, segments)[2]


def _empty_nested_lists(element: dict) -> dict:
    """Cloned list entries start without the prototypes of their own nested lists."""
    key = elements.CHILD_KEYS.get(element.get('modelType'))
    if element.get('modelType') == 'SubmodelElementList':
        element['value'] = []
    elif key:
        for child in element.get(key, []):
            _empty_nested_lists(child)
    return element


def set_value(submodel: dict, path: str, value, prototypes: dict | None = None) -> dict:
    element = _ensure(submodel, elements.parse_path(path), prototypes or {})
    if element.get('modelType') == 'MultiLanguageProperty' and isinstance(value, str):
        value = [{'en': value}]
    if element.get('modelType') == 'File' and isinstance(value, str):
        value = {'value': value, 'contentType': element.get('contentType') or 'application/octet-stream'}
    if element.get('modelType') == 'SubmodelElementList' and isinstance(value, list) and not value:
        element['value'] = []
        return element
    elements.patch_value(element, value)
    return element


def _has_value(element: dict) -> bool:
    model_type = element.get('modelType')
    if model_type in ('Property', 'File', 'Blob'):
        return element.get('value') not in (None, '')
    if model_type == 'MultiLanguageProperty':
        return any(s.get('text') for s in element.get('value') or [])
    if model_type == 'Range':
        return element.get('min') not in (None, '') or element.get('max') not in (None, '')
    if model_type == 'ReferenceElement':
        return bool((element.get('value') or {}).get('keys'))
    key = elements.CHILD_KEYS.get(model_type)
    if key:
        return any(_has_value(c) for c in element.get(key, []))
    return model_type in ('Operation', 'Capability', 'RelationshipElement', 'BasicEventElement', 'Entity')


def _template_index(template: dict) -> dict:
    index = {}
    for path, element in elements.walk_elements(template):
        index[generic(path)] = element
    return index


def prune(instance: dict, template: dict, keep_paths=()) -> None:
    """Drop optional elements without values (never those addressed in ``keep_paths``)."""
    index = _template_index(template)
    keep = {elements.format_path(elements.parse_path(p)) for p in keep_paths}
    doomed = []
    for path, element in elements.walk_elements(instance):
        spec = index.get(generic(path))
        if spec is None or path in keep or any(k.startswith(path + '.') or k.startswith(path + '[') for k in keep):
            continue
        if cardinality(spec) not in MANDATORY and not _has_value(element):
            doomed.append(path)
    elements.remove_many(instance, doomed)


def check(instance: dict, template: dict) -> list[dict]:
    """Validate an instance against its template; returns findings with severity error|warning."""
    findings = []
    index = _template_index(template)
    present = {}
    for path, element in elements.walk_elements(instance):
        gpath = generic(path)
        present.setdefault(gpath, []).append((path, element))
        spec = index.get(gpath)
        if spec is None:
            findings.append({'severity': 'warning', 'path': path, 'rule': 'unknown-element',
                             'text': 'element is not part of the template'})
            continue
        if spec.get('modelType') != element.get('modelType'):
            findings.append({'severity': 'error', 'path': path, 'rule': 'model-type',
                             'text': f'template expects {spec.get("modelType")}, found {element.get("modelType")}'})
        if spec.get('valueType') and element.get('valueType') and spec['valueType'] != element['valueType']:
            findings.append({'severity': 'error', 'path': path, 'rule': 'value-type',
                             'text': f'template expects {spec["valueType"]}, found {element["valueType"]}'})
        allowed = qualifier(spec, 'SMT/AllowedValue')
        if allowed and element.get('value') and not re.fullmatch(allowed, str(element['value'])):
            findings.append({'severity': 'error', 'path': path, 'rule': 'allowed-value',
                             'text': f'value does not match {allowed!r}'})
        required_lang = qualifier(spec, 'SMT/RequiredLang')
        if required_lang and element.get('modelType') == 'MultiLanguageProperty' and \
                required_lang not in {s.get('language') for s in element.get('value') or []}:
            findings.append({'severity': 'error', 'path': path, 'rule': 'required-lang',
                             'text': f'language {required_lang!r} is required'})
        if cardinality(spec) in MANDATORY and element.get('modelType') not in elements.CHILD_KEYS and \
                not _has_value(element):
            findings.append({'severity': 'error', 'path': path, 'rule': 'mandatory-value',
                             'text': 'mandatory element has no value'})
    for gpath, spec in index.items():
        parent = gpath.rsplit('.', 1)[0] if '.' in gpath else None
        parent_present = parent is None or parent in present or (parent.endswith('[]') and parent in present)
        if gpath.endswith('[]'):
            list_path = gpath[:-2]
            if list_path in present:
                count = sum(len(e.get('value') or []) for _, e in present[list_path])
                if cardinality(spec) in MANDATORY and count == 0:
                    findings.append({'severity': 'error', 'path': list_path, 'rule': 'cardinality',
                                     'text': 'list needs at least one entry'})
            continue
        if cardinality(spec) in MANDATORY and gpath not in present and parent_present:
            findings.append({'severity': 'error', 'path': gpath, 'rule': 'cardinality',
                             'text': f'mandatory element ({cardinality(spec)}) is missing'})
    return findings
