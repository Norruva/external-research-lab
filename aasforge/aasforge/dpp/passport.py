"""Digital Product Passports on top of the AAS repository.

A passport is an Asset Administration Shell whose submodels follow IDTA 02099 (DppMetadata,
EN 18223 metadata) and the IDTA 02035 Digital Battery Passport parts. This module creates and
updates passports from the bundled templates, assembles tier-filtered passport views, scores
completeness against the Battery Regulation attribute catalogue, and rolls up carbon footprints
over bills of material.
"""
from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime, timezone

from .. import canonical, elements, smt
from ..store import NotFound
from . import catalog, gs1, tiers

DPP_SCHEMA_VERSION = 'IDTA-02099-1:1.0.2;EN 18223:2026'
CATEGORY_PATTERNS = {'ev': r'\bev\b|electric vehicle', 'lmt': r'\blmt\b|light means', 'industrial': r'industrial',
                     'stationary': r'stationary|\bbess\b|energy storage', 'portable': r'portable', 'sli': r'\bsli\b|starting'}


class PassportError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _ref(submodel_id):
    return {'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': submodel_id}]}


def submodels_of(store, shell):
    found = {}
    others = []
    for ref in shell.get('submodels') or []:
        key = (ref.get('keys') or [{}])[-1].get('value')
        try:
            submodel = store.get('submodel', key)
        except NotFound:
            continue
        template_key = catalog.template_key_for(submodel)
        if template_key:
            found.setdefault(template_key, []).append(submodel)
        else:
            others.append(submodel)
    return found, others


def is_passport(store, shell) -> bool:
    found, _ = submodels_of(store, shell)
    return bool(found)


def detect_category(found: dict) -> str | None:
    texts = []
    for key in ('dbp-nameplate', 'dbp-technical-data'):
        for submodel in found.get(key, []):
            for path, element in elements.walk_elements(submodel):
                if path.endswith('BatteryCategory'):
                    value = elements.value_only(element)
                    texts.append(' '.join(v for item in value for v in item.values())
                                 if isinstance(value, list) else str(value or ''))
    text = ' '.join(texts).lower()
    for category, pattern in CATEGORY_PATTERNS.items():
        if re.search(pattern, text):
            return category
    return None


# -- creation --------------------------------------------------------------------------------------
def create(repository, spec: dict, actor=None) -> dict:
    """Create a passport (shell + submodels) from templates.

    ``spec`` keys: gtin, serial?, batch?, granularity (Item|Batch|Model, inferred), category?,
    name?, operatorId, facilityId?, base (resolver base URL), values {template-key: {path: value}},
    tiers {template-key: tier}, parts [template keys] (default: all), idPrefix?,
    extraParts [{template, values, tier?, idShort?}] for further instances (e.g. restricted test reports).
    """
    gtin = gs1.normalise_gtin(spec.get('gtin', ''))
    serial, batch = spec.get('serial'), spec.get('batch')
    granularity = spec.get('granularity') or ('Item' if serial else 'Batch' if batch else 'Model')
    if granularity not in ('Item', 'Batch', 'Model'):
        raise PassportError('granularity must be Item, Batch or Model')
    if not spec.get('operatorId'):
        raise PassportError('operatorId (unique operator identifier, ESPR Art. 12) is required')
    digital_link = gs1.digital_link(spec.get('base') or gs1.DEFAULT_BASE, gtin, serial=serial, batch=batch)
    prefix = spec.get('idPrefix') or f'urn:uuid:{uuid.uuid4()}'
    parts = spec.get('parts') or list(catalog.TEMPLATE_KEYS)
    values = spec.get('values') or {}
    unknown = set(values) - set(catalog.TEMPLATE_KEYS)
    if unknown:
        raise PassportError(f'unknown template keys: {sorted(unknown)}')
    passport_id = f'{prefix}:dpp'
    metadata_values = {
        'digitalProductPassportId': passport_id, 'uniqueProductIdentifier': digital_link, 'granularity': granularity,
        'dppSchemaVersion': DPP_SCHEMA_VERSION, 'dppStatus': spec.get('status', 'Active'), 'lastUpdate': _now(),
        'economicOperatorId': spec['operatorId']}
    if spec.get('facilityId'):
        metadata_values['facilityId'] = spec['facilityId']
    content_specs = [catalog.manifest()[k]['semanticId'] for k in parts if k != 'dpp-metadata']
    for index, semantic in enumerate(content_specs):
        metadata_values[f'contentSpecificationIds[{index}]'] = semantic
    metadata_values.update(values.get('dpp-metadata', {}))
    created = []
    with repository.store.transaction():
        for key in ['dpp-metadata'] + [p for p in parts if p != 'dpp-metadata']:
            submodel_values = metadata_values if key == 'dpp-metadata' else values.get(key, {})
            instance = smt.instantiate(catalog.template(key), f'{prefix}:sm:{key}', submodel_values)
            tier = (spec.get('tiers') or {}).get(key)
            if tier:
                instance.setdefault('extensions', []).append({'name': catalog.TIER_EXTENSION, 'valueType': 'xs:string',
                                                              'value': tier})
            repository.save('submodel', instance, actor=actor, must_exist=False)
            created.append(instance['id'])
        for index, extra in enumerate(spec.get('extraParts') or []):
            if extra.get('template') not in catalog.TEMPLATE_KEYS:
                raise PassportError(f'unknown template in extraParts: {extra.get("template")!r}')
            instance = smt.instantiate(catalog.template(extra['template']), f'{prefix}:sm:{extra["template"]}:{index + 2}',
                                       extra.get('values') or {}, id_short=extra.get('idShort'))
            if extra.get('tier'):
                instance.setdefault('extensions', []).append({'name': catalog.TIER_EXTENSION, 'valueType': 'xs:string',
                                                              'value': extra['tier']})
            repository.save('submodel', instance, actor=actor, must_exist=False)
            created.append(instance['id'])
        specific = [{'name': 'gtin', 'value': gtin}]
        if serial:
            specific.append({'name': 'serialNumber', 'value': serial})
        if batch:
            specific.append({'name': 'batchId', 'value': batch})
        shell = {'modelType': 'AssetAdministrationShell', 'id': f'{prefix}:aas', 'idShort': 'DigitalProductPassport',
                 'assetInformation': {'assetKind': 'Instance' if granularity == 'Item' else 'Type',
                                      'globalAssetId': digital_link, 'specificAssetIds': specific},
                 'submodels': [_ref(i) for i in created]}
        if spec.get('name'):
            shell['displayName'] = [{'language': 'en', 'text': spec['name']}]
        # 3.1 has no AssetKind.Batch: store 'Type' and keep the 3.2 value in the sidecar for 3.2 export
        sidecar = [{'path': ['assetInformation', 'assetKind'], 'value': 'Batch'}] if granularity == 'Batch' else []
        repository.save('shell', shell, actor=actor, must_exist=False, sidecar=sidecar)
    return {'aasId': shell['id'], 'passportId': passport_id, 'digitalLink': digital_link, 'submodels': created,
            'granularity': granularity}


def update_values(repository, shell_id: str, values: dict, actor=None) -> dict:
    """Apply {template-key: {path: value}}; refreshes DppMetadata.lastUpdate."""
    shell = repository.store.get('shell', shell_id)
    found, _ = submodels_of(repository.store, shell)
    changed = []
    with repository.store.transaction():
        for key, submodel_values in values.items():
            if key not in found:
                raise PassportError(f'passport has no {key} submodel')
            target = found[key][0]
            prototypes = _prototypes(catalog.template(key))

            def mutate(submodel, submodel_values=submodel_values, prototypes=prototypes):
                for path, value in submodel_values.items():
                    smt.set_value(submodel, path, value, prototypes)
                smt.tidy(submodel)
            repository.update_submodel(target['id'], mutate, actor=actor)
            changed.append(key)
        if 'dpp-metadata' in found and changed != ['dpp-metadata']:
            repository.update_submodel(found['dpp-metadata'][0]['id'],
                                       lambda s: smt.set_value(s, 'lastUpdate', _now()), actor=actor)
    return {'updated': changed}


def _prototypes(template_submodel):
    result = {}
    for path, element in elements.walk_elements(template_submodel):
        if path.endswith('[0]'):
            result[smt.generic(path[:-3]) + '[]'] = smt._strip_template_parts(copy.deepcopy(element), False)
    return result


# -- views -----------------------------------------------------------------------------------------
def _matches_where(submodel, concrete_path, where):
    if not where:
        return True
    segments = elements.parse_path(concrete_path)
    for key, expected in where.items():
        sibling = elements.format_path(segments[:-1] + [key])
        try:
            value = elements.value_only(elements.get(submodel, sibling))
        except elements.PathError:
            return False
        if str(value).lower() != str(expected).lower():
            return False
    return True


def attribute_values(found: dict, mapping: dict) -> list:
    hits = []
    for element_spec in mapping['elements']:
        for submodel in found.get(element_spec['template'], []):
            for path, element in elements.walk_elements(submodel):
                if smt.generic(path) == element_spec['path'] and \
                        _matches_where(submodel, path, element_spec.get('where')):
                    hits.append((submodel, path, element))
    return hits


def completeness(found: dict, category: str | None) -> dict:
    category = category if category in catalog.CATEGORIES else None
    rows, mandatory, filled = [], 0, 0
    by_tier = {}
    for attr in catalog.attributes():
        flag = attr['applicability'].get(category or 'ev', '') if category else (
            'x' if any(v == 'x' for v in attr['applicability'].values()) else 'o')
        if flag not in ('x', 'o'):
            continue
        mapping = catalog.attribute_map()[attr['id']]
        hits = attribute_values(found, mapping)
        present = any(smt._has_value(element) for _, _, element in hits)
        status = 'present' if present else ('unmapped' if not mapping['elements'] else 'missing')
        if flag == 'x':
            mandatory += 1
            filled += present
            tier = by_tier.setdefault(attr['access'], {'mandatory': 0, 'present': 0})
            tier['mandatory'] += 1
            tier['present'] += present
        rows.append({'attribute': attr['id'], 'name': attr['attribute'], 'mandatory': flag == 'x', 'access': attr['access'],
                     'status': status, 'mapping': mapping['method'], 'regulation': attr['regulation_reference'],
                     'paths': [f'{catalog.manifest()[e["template"]]["idShort"]}/{e["path"]}' for e in mapping['elements']]})
    return {'category': category, 'mandatory': mandatory, 'present': filled,
            'score': round(100 * filled / mandatory, 1) if mandatory else None, 'byTier': by_tier,
            'attributes': rows,
            'note': None if category else 'battery category not set; scored against attributes mandatory in any category'}


def _label(element):
    for name in element.get('displayName') or []:
        if name.get('language', '').startswith('en'):
            return name.get('text')
    id_short = element.get('idShort') or ''
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', id_short).strip() or id_short


def _fields(submodel, index):
    def walk(container, prefix, owner):
        fields = []
        for position, element in enumerate(container):
            path = f'{prefix}[{position}]' if owner == 'SubmodelElementList' else (
                f'{prefix}.{element.get("idShort")}' if prefix else element.get('idShort'))
            key = elements.CHILD_KEYS.get(element.get('modelType'))
            entry = {'path': path, 'label': _label(element) if owner != 'SubmodelElementList' else f'#{position + 1}',
                     'type': element.get('modelType'), 'access': index.get(path, 'public')}
            if key:
                entry['children'] = walk(element.get(key, []), path, element.get('modelType'))
                if not entry['children']:
                    continue
            else:
                value = elements.value_only(element)
                if value in (None, '', [], {}):
                    continue
                entry['value'] = value
                if element.get('valueType'):
                    entry['valueType'] = element['valueType']
                semantic = ((element.get('semanticId') or {}).get('keys') or [{}])[0].get('value')
                if semantic:
                    entry['semanticId'] = semantic
            fields.append(entry)
        return fields

    return walk(submodel.get('submodelElements') or [], '', 'Submodel')


def view(store, shell: dict, tier: str = 'public', base_url: str = '') -> dict:
    found_all, others = submodels_of(store, shell)
    category = detect_category(found_all)
    visible = {}   # template key -> [(original, filtered)]
    for key, submodels in found_all.items():
        for submodel in submodels:
            filtered = tiers.filter_for_tier(submodel, tier)
            if filtered is not None:
                visible.setdefault(key, []).append((submodel, filtered))
    meta = {}
    if 'dpp-metadata' in found_all:
        meta = elements.submodel_value_only(found_all['dpp-metadata'][0])
    info = shell.get('assetInformation') or {}
    sections = []
    for key in catalog.TEMPLATE_KEYS:
        for original, submodel in visible.get(key, []):
            if key == 'dpp-metadata':
                continue
            fields = _fields(submodel, tiers.element_tier_index(original))
            sections.append({'template': key, 'title': catalog.TITLES[key], 'submodelId': submodel['id'],
                             'semanticId': catalog.manifest()[key]['semanticId'], 'fields': fields})
    hidden = sum(1 for key, sms in found_all.items() for sm in sms
                 for p, t in tiers.element_tier_index(sm).items() if catalog.TIER_ORDER[t] > catalog.TIER_ORDER[tier])
    aas_b64 = canonical.b64url_encode(shell['id'])
    return {
        'passportId': meta.get('digitalProductPassportId'), 'uniqueProductIdentifier': meta.get('uniqueProductIdentifier'),
        'granularity': meta.get('granularity'), 'status': meta.get('dppStatus'), 'lastUpdate': meta.get('lastUpdate'),
        'schemaVersion': meta.get('dppSchemaVersion'), 'economicOperatorId': meta.get('economicOperatorId'),
        'facilityId': meta.get('facilityId'), 'aasId': shell['id'],
        'name': next((n['text'] for n in shell.get('displayName') or [] if n.get('language', '').startswith('en')),
                     shell.get('idShort')),
        'globalAssetId': info.get('globalAssetId'), 'specificAssetIds': info.get('specificAssetIds') or [],
        'category': category, 'viewerTier': tier, 'hiddenElements': hidden, 'sections': sections,
        'otherSubmodels': [{'id': s['id'], 'idShort': s.get('idShort')} for s in others],
        'completeness': {k: v for k, v in completeness(found_all, category).items() if k != 'attributes'},
        'links': {'aas': f'{base_url}/api/v3.1/shells/{aas_b64}', 'html': f'{base_url}/dpp/passports/{aas_b64}?format=html',
                  'json': f'{base_url}/dpp/passports/{aas_b64}',
                  'credential': f'{base_url}/dpp/passports/{aas_b64}/credential',
                  'signedShell': f'{base_url}/api/v3.1/shells/{aas_b64}/$signed'},
    }


# -- carbon footprint roll-up -------------------------------------------------------------------
def pcf_value(store, shell) -> float | None:
    found, _ = submodels_of(store, shell)
    for submodel in found.get('dbp-carbon-footprint', []):
        for path, element in elements.walk_elements(submodel):
            if path.endswith('PcfCO2eq') and element.get('value') not in (None, ''):
                try:
                    return float(element['value'])
                except ValueError:
                    return None
    return None


def carbon_rollup(store, shell, _seen=None) -> dict:
    """Sum product carbon footprints over an IDTA 02011 bill of material (Entities referencing
    component shells by globalAssetId, optional ``Quantity`` property per entity)."""
    seen = _seen or set()
    seen.add(shell['id'])
    own = pcf_value(store, shell)
    components = []
    _, others = submodels_of(store, shell)
    by_asset = {(s.get('assetInformation') or {}).get('globalAssetId'): s for s in store.iter_all('shell')}
    for submodel in others:
        if 'HierarchicalStructures' not in (submodel.get('idShort') or '') and 'BoM' not in (submodel.get('idShort') or ''):
            continue
        for path, element in elements.walk_elements(submodel):
            if element.get('modelType') != 'Entity' or not element.get('globalAssetId'):
                continue
            child = by_asset.get(element['globalAssetId'])
            if child is None or child['id'] in seen or child['id'] == shell['id']:
                continue
            quantity = 1.0
            for statement in element.get('statements') or []:
                if statement.get('idShort', '').lower() in ('quantity', 'bulkcount') and statement.get('value'):
                    try:
                        quantity = float(statement['value'])
                    except ValueError:
                        pass
            nested = carbon_rollup(store, child, seen)
            components.append({'aasId': child['id'], 'quantity': quantity, 'pcf': nested['total']})
    total_components = sum((c['pcf'] or 0) * c['quantity'] for c in components)
    total = (own or 0) + total_components if (own is not None or components) else None
    return {'aasId': shell['id'], 'own': own, 'components': components, 'total': total,
            'unit': 'kg CO2e per declared unit'}
