"""Battery Regulation access tiers for passport data.

Tier of an element (most specific wins):
1. a marker on the element, an ancestor or the submodel: an ``Extension`` named ``TIER_EXTENSION``
   whose value is the tier, or a supplemental semanticId ``TIER_EXTENSION + '/' + tier``. The
   second form can be tested in the standard Access Rule grammar, so generated rules use it;
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
    # Only the claim the generated access rules test: the passport view and the raw API must agree.
    tier = claims.get(ROLE_CLAIM)
    return tier if isinstance(tier, str) and tier in TIER_ORDER else 'public'


def tier_marker(tier: str) -> str:
    """Supplemental semanticId that marks an element or submodel as belonging to ``tier``."""
    return f'{TIER_EXTENSION}/{tier}'


def _extension_tier(node: dict):
    for extension in node.get('extensions') or []:
        if extension.get('name') == TIER_EXTENSION and extension.get('value') in TIER_ORDER:
            return extension['value']
    for reference in node.get('supplementalSemanticIds') or []:
        value = ((reference.get('keys') or [{}])[0]).get('value') or ''
        if value.startswith(TIER_EXTENSION + '/') and value[len(TIER_EXTENSION) + 1:] in TIER_ORDER:
            return value[len(TIER_EXTENSION) + 1:]
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

    Element-level tiers from the attribute map are expressed with semanticId conditions in FILTER
    fragments, and marked elements with conditions on ``$sme#supplementalSemanticIds[]``; marked
    submodels are excluded in FORMULAs. Every tier reads nothing above itself; economic operators
    get full access. Elements marked only by an Extension are hidden on this server by the exact
    tier filter (see ``service.DppService``) but need the supplemental-semanticId marker to be
    hidden by other servers that load these rules.
    """
    above = {t: [u for u in TIER_ORDER if TIER_ORDER[u] > TIER_ORDER[t]] for t in TIER_ORDER}
    restricted = {t: set() for t in TIER_ORDER if t != 'public'}
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
    hidden_submodels = {t: [] for t in restricted}
    for submodel in submodels:
        tier = _extension_tier(submodel)
        if tier in hidden_submodels:
            hidden_submodels[tier].append(submodel['id'])

    def combine(operator, items):
        """The schema requires at least two operands for $and/$or."""
        if not items:
            return {'$boolean': operator == '$and'}
        return items[0] if len(items) == 1 else {operator: items}

    def not_marked(tier):
        return {'$not': {'$eq': [{'$field': '$sme#supplementalSemanticIds[]'}, {'$strVal': tier_marker(tier)}]}}

    def tier_is(*tiers):
        return combine('$or', [{'$eq': [{'$attribute': {'CLAIM': ROLE_CLAIM}}, {'$strVal': t}]} for t in tiers])

    def reading_rule(tier, attributes, objects, subject_formula):
        """READ for ``tier``: hides submodels and elements of every higher tier."""
        hidden_ids = sorted(i for t in above[tier] for i in hidden_submodels[t])
        semantics = sorted(set().union(*(restricted[t] for t in above[tier])))
        condition = combine('$and', [{'$ne': [{'$field': '$sme#semanticId'}, {'$strVal': x}]} for x in semantics]
                            + [not_marked(t) for t in above[tier]])
        return {'ACL': {'ATTRIBUTES': attributes, 'RIGHTS': ['READ'], 'ACCESS': 'ALLOW'}, 'OBJECTS': objects,
                'FORMULA': combine('$and', [subject_formula] + [{'$ne': [{'$field': '$sm#id'}, {'$strVal': i}]}
                                                               for i in hidden_ids]),
                'FILTER': {'FRAGMENT': '$sme', 'CONDITION': condition}}

    public_objects = [{'ROUTE': r} for r in ('/shells', '/submodels', '/concept-descriptions', '/lookup', '/shell-descriptors',
                                             '/submodel-descriptors', '/description', '/dpp', '/01', '/id', '/query', '/mcp',
                                             '/.well-known')]
    claim = [{'CLAIM': ROLE_CLAIM}]
    rules = [
        {'ACL': {'ATTRIBUTES': claim, 'RIGHTS': ['ALL'], 'ACCESS': 'ALLOW'},
         'OBJECTS': [{'ROUTE': '*'}], 'FORMULA': tier_is('economic-operator')},
        reading_rule('authority', claim, [{'ROUTE': '*'}], tier_is('authority')),
        reading_rule('legitimate-interest', claim, public_objects, tier_is('legitimate-interest')),
        reading_rule('public', [{'GLOBAL': 'ANONYMOUS'}], public_objects, {'$boolean': True}),
    ]
    return {'AllAccessPermissionRules': {'rules': rules}}
