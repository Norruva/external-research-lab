"""Bundled DPP reference data: IDTA templates, Battery Regulation attribute catalogue and the
attribute-to-element map (see tools/build_dpp_data.py for provenance)."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from importlib import resources

TEMPLATE_KEYS = ('dpp-metadata', 'dbp-nameplate', 'dbp-handover', 'dbp-carbon-footprint', 'dbp-technical-data',
                 'dbp-product-condition', 'dbp-material-composition', 'dbp-circularity')
TITLES = {'dpp-metadata': 'Passport', 'dbp-nameplate': 'Identity and nameplate',
          'dbp-handover': 'Documents and declarations', 'dbp-carbon-footprint': 'Carbon footprint',
          'dbp-technical-data': 'Performance and durability', 'dbp-product-condition': 'State of health',
          'dbp-material-composition': 'Materials and chemistry', 'dbp-circularity': 'Circularity and end of life'}
TIER_ORDER = {'public': 0, 'legitimate-interest': 1, 'authority': 2, 'economic-operator': 3}
CATEGORIES = ('ev', 'lmt', 'industrial', 'stationary')
TIER_EXTENSION = 'https://norruva.com/aasforge/dpp/accessTier'


def _data(name):
    return json.loads(resources.files('aasforge.data').joinpath(name).read_text(encoding='utf-8'))


@lru_cache(maxsize=None)
def _template_env(key):
    return _data(f'templates/{key}.json')


def template_env(key: str) -> dict:
    return copy.deepcopy(_template_env(key))


def template(key: str) -> dict:
    return template_env(key)['submodels'][0]


@lru_cache(maxsize=None)
def manifest() -> dict:
    return _data('templates/manifest.json')


@lru_cache(maxsize=None)
def semantic_ids() -> dict:
    """submodel semanticId -> template key"""
    return {v['semanticId']: k for k, v in manifest().items() if not k.startswith('_')}


def template_key_for(submodel: dict) -> str | None:
    semantic = ((submodel.get('semanticId') or {}).get('keys') or [{}])[0].get('value')
    if semantic in semantic_ids():
        return semantic_ids()[semantic]
    for known, key in semantic_ids().items():   # tolerate minor version suffix differences
        if semantic and known and semantic.rsplit('/', 2)[0] == known.rsplit('/', 2)[0] and '/idta/' in known:
            return key
    return None


@lru_cache(maxsize=None)
def attributes() -> list:
    return _data('battery-attributes.json')['attributes']


@lru_cache(maxsize=None)
def attribute_map() -> dict:
    return {m['attribute']: m for m in _data('attribute-map.json')['map']}


def attribute(attribute_id: int) -> dict:
    return next(a for a in attributes() if a['id'] == attribute_id)


@lru_cache(maxsize=None)
def element_tiers() -> dict:
    """(template key, generic idShortPath) -> most restrictive access tier of mapped attributes."""
    result = {}
    for mapping in attribute_map().values():
        for element in mapping['elements']:
            key = (element['template'], element['path'])
            current = result.get(key)
            if current is None or TIER_ORDER[mapping['access']] > TIER_ORDER[current]:
                result[key] = mapping['access']
    for key in TEMPLATE_KEYS:     # passport metadata and documents are public by default
        if key in ('dpp-metadata', 'dbp-handover'):
            for (template_key, path) in list(result):
                if template_key == key:
                    result[(template_key, path)] = 'public'
    return result
