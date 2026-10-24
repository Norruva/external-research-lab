"""Battery Regulation access tiers for passport data.

Tier of an element (most specific wins):
1. an ``Extension`` named ``TIER_EXTENSION`` on the element, an ancestor or the submodel;
2. the attribute map (Battery Regulation Art. 77 / Annex XIII tier of the attributes mapped to the
   element's generic idShortPath), inherited by descendants;
3. ``public``.

The same model is exported as IDTA-01004 access rules (``access_rules``) so that any server
implementing the Access Rule Language enforces the same tiers.
"""
from __future__ import annotations

import copy

from .. import elements
from ..smt import generic
from .catalog import TIER_EXTENSION, TIER_ORDER, element_tiers, template_key_for

ROLE_CLAIM = 'dpp_tier'


def subject_tier(claims: dict, anonymous: bool) -> str:
    if anonymous:
        return 'public'
    tier = claims.get(ROLE_CLAIM)
    roles = claims.get('roles') or []
    if isinstance(roles, str):
        roles = [roles]
    candidates = [tier] + [r for r in roles if r in TIER_ORDER]
    best = max((c for c in candidates if c in TIER_ORDER), key=TIER_ORDER.get, default=None)
    return best or 'public'


def _extension_tier(node: dict):
    for extension in node.get('extensions') or []:
        if extension.get('name') == TIER_EXTENSION and extension.get('value') in TIER_ORDER:
            return extension['value']
    return None


def element_tier_index(submodel: dict) -> dict:
    """idShortPath -> effective tier for every element of a submodel."""
    key = template_key_for(submodel)
    mapped = element_tiers()
    submodel_tier = _extension_tier(submodel)
    result = {}
    for path, element in elements.walk_elements(submodel):
        own = _extension_tier(element)
        segments = elements.parse_path(path)
        inherited = result.get(elements.format_path(segments[:-1])) if len(segments) > 1 else None
        from_map = mapped.get((key, generic(path))) if key else None
        tier = own or from_map or inherited or submodel_tier or 'public'
        if inherited and TIER_ORDER[inherited] > TIER_ORDER[tier] and not own:
            tier = inherited
        result[path] = tier
    return result


def submodel_tier(submodel: dict) -> str:
    return _extension_tier(submodel) or 'public'


def filter_for_tier(submodel: dict, tier: str) -> dict:
    """Copy of ``submodel`` without elements above ``tier``."""
    if TIER_ORDER[submodel_tier(submodel)] > TIER_ORDER[tier]:
        return None
    index = element_tier_index(submodel)
    result = copy.deepcopy(submodel)
    hidden = [p for p, t in index.items() if TIER_ORDER[t] > TIER_ORDER[tier]]
    elements.remove_many(result, hidden)
    return result


def access_rules(submodels=()) -> dict:
    """Generate an IDTA-01004 AllAccessPermissionRules document enforcing the tiers.

    Element-level tiers are expressed with semanticId conditions in FILTER fragments; submodels
    marked with the tier extension get IDENTIFIABLE rules. Economic operators get full access.
    """
    restricted = {'legitimate-interest': set(), 'authority': set()}
    for (template_key, path), tier in element_tiers().items():
        if tier in restricted:
            from .catalog import template
            sm = template(template_key)
            try:
                element = elements.get(sm, path.replace('[]', '[0]'))
            except elements.PathError:
                continue
            semantic = ((element.get('semanticId') or {}).get('keys') or [{}])[0].get('value')
            if semantic:
                restricted[tier].add(semantic)
    hidden_submodels = {'legitimate-interest': [], 'authority': []}
    for submodel in submodels:
        tier = _extension_tier(submodel)
        if tier in hidden_submodels:
            hidden_submodels[tier].append(submodel['id'])

    def combine(operator, items):
        """The schema requires at least two operands for $and/$or."""
        if not items:
            return {'$boolean': operator == '$and'}
        return items[0] if len(items) == 1 else {operator: items}

    def not_in(semantics):
        return combine('$and', [{'$ne': [{'$field': '$sme#semanticId'}, {'$strVal': s}]} for s in sorted(semantics)])

    def tier_is(*tiers):
        return combine('$or', [{'$eq': [{'$attribute': {'CLAIM': ROLE_CLAIM}}, {'$strVal': t}]} for t in tiers])

    def not_submodels(ids):
        return [{'$ne': [{'$field': '$sm#id'}, {'$strVal': i}]} for i in sorted(ids)]

    def formula(base, ids):
        return combine('$and', [base] + not_submodels(ids))

    public_objects = [{'ROUTE': r} for r in ('/shells', '/submodels', '/concept-descriptions', '/lookup', '/shell-descriptors',
                                             '/submodel-descriptors', '/description', '/dpp', '/01', '/id', '/query', '/mcp',
                                             '/.well-known')]
    rules = [
        {'ACL': {'ATTRIBUTES': [{'CLAIM': ROLE_CLAIM}], 'RIGHTS': ['ALL'], 'ACCESS': 'ALLOW'},
         'OBJECTS': [{'ROUTE': '*'}], 'FORMULA': tier_is('economic-operator')},
        {'ACL': {'ATTRIBUTES': [{'CLAIM': ROLE_CLAIM}], 'RIGHTS': ['READ'], 'ACCESS': 'ALLOW'},
         'OBJECTS': [{'ROUTE': '*'}], 'FORMULA': tier_is('authority')},
        {'ACL': {'ATTRIBUTES': [{'CLAIM': ROLE_CLAIM}], 'RIGHTS': ['READ'], 'ACCESS': 'ALLOW'},
         'OBJECTS': public_objects,
         'FORMULA': formula(tier_is('legitimate-interest'), hidden_submodels['authority']),
         'FILTER': {'FRAGMENT': '$sme', 'CONDITION': not_in(restricted['authority'])}},
        {'ACL': {'ATTRIBUTES': [{'GLOBAL': 'ANONYMOUS'}], 'RIGHTS': ['READ'], 'ACCESS': 'ALLOW'},
         'OBJECTS': public_objects,
         'FORMULA': formula({'$boolean': True}, hidden_submodels['authority'] + hidden_submodels['legitimate-interest']),
         'FILTER': {'FRAGMENT': '$sme', 'CONDITION': not_in(restricted['authority'] | restricted['legitimate-interest'])}},
    ]
    return {'AllAccessPermissionRules': {'rules': rules}}
