"""AAS formula engine: the IDTA-01002 Query Language and the IDTA-01004 Access Rule formulas share
one grammar, so one evaluator serves ``/query/*`` endpoints and authorization (FORMULA/FILTER).

Semantics follow the specification text:
* a FieldIdentifier resolves to *all* matching atomic values (``[]`` = any list entry, no
  IdShortPath under ``$sme`` = recursive search); comparisons are existential over those values;
* comparing two empty fields is ``$eq`` true;
* comparison errors (e.g. a non-numeric value under ``$numVal``) are three-valued: the object is
  not returned rather than treated as false-and-continue;
* ``$match`` requires all comparisons to hold for the *same* list entry (or the same recursively
  found submodel element);
* ``$filters`` / FILTER fragments prune submodel elements (row-wise with ``[]``) from results.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from functools import lru_cache
from importlib import resources

from . import elements

NUMERIC_TYPES = elements.NUMERIC
FIELD = re.compile(r'^\$(aas|sm|sme|cd|aasdesc|smdesc)((?:\.[^#]+)?)#(.+)$')
ROOT_ONLY = re.compile(r'^\$(aas|sm|sme|cd|aasdesc|smdesc)((?:\.[^#]+)?)$')
TOKEN = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)((?:\[\d*\])*)')


class FormulaError(ValueError):
    pass


@dataclass(frozen=True)
class Value:
    value: object
    vtype: str | None = None
    binding: tuple = ()
    literal: bool = False


@dataclass
class Context:
    """What a formula is evaluated against."""

    kind: str                      # shell | submodel | concept-description | aas-descriptor | sm-descriptor
    obj: dict
    submodels_of: object = None    # callable(shell) -> list[submodel]; used for $sm/$sme in shell queries
    shells_of: object = None       # callable(submodel) -> list[shell]; used for $aas in submodel queries
    claims: dict = field(default_factory=dict)
    now: datetime | None = None
    bound: dict = field(default_factory=dict)   # '$sme' -> (submodel, path) when evaluating per element


@lru_cache(maxsize=1)
def _schema_validator():
    import jsonschema
    schema = json.loads(resources.files('aasforge.data').joinpath('query-json-schema.json').read_text())
    return schema, jsonschema


def validate(document: dict, definition: str = 'Query') -> list[str]:
    """Validate a query or access-rule document against the normative JSON schema."""
    schema, jsonschema = _schema_validator()
    sub = dict(schema)
    sub['$ref'] = f'#/definitions/{definition}'
    validator = jsonschema.Draft7Validator(sub)
    return [f'{"/".join(map(str, e.absolute_path))}: {e.message}'[:300] for e in validator.iter_errors(document)]


# -- field resolution -----------------------------------------------------------------------------
def _child(node, name):
    if not isinstance(node, dict):
        return None
    if name in node:
        return node[name]
    lowered = {k.lower(): k for k in node}
    return node.get(lowered.get(name.lower()))


def _walk_attribute(node, attribute: str, binding: tuple):
    """Resolve a dotted attribute path with [] / [i] against a JSON node."""
    results = [(node, binding)]
    for name, brackets in TOKEN.findall(attribute):
        nxt = []
        for current, bind in results:
            value = _child(current, name)
            if value is None:
                continue
            selections = [(value, bind)]
            for bracket in re.findall(r'\[(\d*)\]', brackets):
                expanded = []
                for item, b in selections:
                    if not isinstance(item, list):
                        continue
                    if bracket == '':
                        expanded += [(entry, b + (i,)) for i, entry in enumerate(item)]
                    elif int(bracket) < len(item):
                        expanded.append((item[int(bracket)], b))
                selections = expanded
            nxt += selections
        results = nxt
    out = []
    for value, bind in results:
        if isinstance(value, dict) and 'keys' in value:      # Reference shortcut -> first key value
            keys = value.get('keys') or []
            out.append((keys[0].get('value') if keys else None, bind))
        elif isinstance(value, list) and value and isinstance(value[0], dict) and 'keys' in value[0]:
            out += [((r.get('keys') or [{}])[0].get('value'), bind + (i,)) for i, r in enumerate(value)]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out.append((value, bind))
    return out


def _sme_attribute(element: dict, attribute: str, binding: tuple):
    model_type = element.get('modelType')
    if attribute == 'value':
        if model_type == 'MultiLanguageProperty':
            return [Value(s.get('text'), 'xs:string', binding) for s in element.get('value', [])]
        if model_type == 'ReferenceElement':
            keys = (element.get('value') or {}).get('keys') or []
            return [Value(keys[0]['value'] if keys else None, 'xs:string', binding)]
        if model_type in ('File', 'Blob'):
            return [Value(element.get('value'), 'xs:string', binding)]
        if model_type == 'Range':
            return [Value(element.get(b), element.get('valueType'), binding) for b in ('min', 'max')]
        return [Value(element.get('value'), element.get('valueType'), binding)]
    if attribute == 'language':
        return [Value(s.get('language'), 'xs:string', binding) for s in element.get('value', [])
                if model_type == 'MultiLanguageProperty']
    if attribute == 'valueType':
        return [Value(element.get('valueType'), 'xs:string', binding)]
    return [Value(v, 'xs:string', b) for v, b in _walk_attribute(element, attribute, binding)]


def _submodels(ctx: Context):
    if ctx.kind == 'submodel':
        return [ctx.obj]
    if ctx.kind == 'shell' and ctx.submodels_of:
        return ctx.submodels_of(ctx.obj)
    return []


def resolve(ctx: Context, identifier: str) -> list[Value]:
    match = FIELD.match(identifier)
    if not match:
        raise FormulaError(f'invalid FieldIdentifier {identifier!r}')
    root, path, attribute = match.group(1), match.group(2).lstrip('.'), match.group(3)
    if root == 'sme':
        return _resolve_sme(ctx, path, attribute)
    roots = []
    if root == 'aas':
        roots = [ctx.obj] if ctx.kind == 'shell' else (ctx.shells_of(ctx.obj) if ctx.shells_of else [])
    elif root == 'sm':
        roots = _submodels(ctx)
    elif root == 'cd':
        roots = [ctx.obj] if ctx.kind == 'concept-description' else []
    elif root == 'aasdesc':
        roots = [ctx.obj] if ctx.kind == 'aas-descriptor' else []
    elif root == 'smdesc':
        roots = [ctx.obj] if ctx.kind == 'sm-descriptor' else (
            ctx.obj.get('submodelDescriptors', []) if ctx.kind == 'aas-descriptor' else [])
    values = []
    for index, node in enumerate(roots):
        values += [Value(v, 'xs:string', b) for v, b in _walk_attribute(node, attribute, (index,) if len(roots) > 1 else ())]
    return values


def _resolve_sme(ctx: Context, path: str, attribute: str) -> list[Value]:
    values = []
    if not path and '$sme' in ctx.bound:
        submodel, element_path = ctx.bound['$sme']
        return _sme_attribute(elements.get(submodel, element_path), attribute, (element_path,))
    for submodel in _submodels(ctx):
        if not path:
            for element_path, element in elements.walk_elements(submodel):
                values += _sme_attribute(element, attribute, (element_path,))
            continue
        for element, binding in _elements_at(submodel, path):
            values += _sme_attribute(element, attribute, binding)
    return values


def _elements_at(submodel: dict, path: str):
    """Elements addressed by an IdShortPath that may contain [] wildcards."""
    current = [(submodel.get('submodelElements', []), 'Submodel', ())]
    found = []
    tokens = TOKEN.findall(path)
    for depth, (name, brackets) in enumerate(tokens):
        nxt = []
        for container, _owner, binding in current:
            element = next((e for e in container if e.get('idShort') == name), None)
            if element is None:
                continue
            selections = [(element, binding)]
            for bracket in re.findall(r'\[(\d*)\]', brackets):
                expanded = []
                for item, b in selections:
                    items = item.get('value', []) if item.get('modelType') == 'SubmodelElementList' else []
                    if bracket == '':
                        expanded += [(entry, b + (i,)) for i, entry in enumerate(items)]
                    elif int(bracket) < len(items):
                        expanded.append((items[int(bracket)], b))
                selections = expanded
            if depth == len(tokens) - 1:
                found += selections
            else:
                for item, b in selections:
                    kids = elements.children(dict(item)) if elements.CHILD_KEYS.get(item.get('modelType')) else None
                    if kids is not None:
                        nxt.append((item.get(elements.CHILD_KEYS[item['modelType']], []), item.get('modelType'), b))
        current = nxt
    return found


# -- operands ---------------------------------------------------------------------------------
def _parse_datetime(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _attribute(ctx: Context, item: dict):
    if 'CLAIM' in item:
        claim = ctx.claims.get(item['CLAIM'])
        if isinstance(claim, list):
            return [Value(str(c), 'xs:string') for c in claim] or [Value(None)]
        return [Value(None if claim is None else str(claim), 'xs:string')]
    if 'GLOBAL' in item:
        if item['GLOBAL'] in ('UTCNOW', 'LOCALNOW', 'CLIENTNOW'):
            return [Value(ctx.now or datetime.now(timezone.utc), 'xs:dateTime')]
        return [Value('ANONYMOUS' if ctx.claims.get('anonymous', True) else None, 'xs:string')]
    return [Value(None)]


def operand(ctx: Context, spec: dict) -> list[Value]:
    if not isinstance(spec, dict) or len(spec) != 1:
        raise FormulaError(f'invalid operand {spec!r}')
    (op, arg), = spec.items()
    if op == '$field':
        values = resolve(ctx, arg)
        return values or [Value(None)]
    if op == '$strVal':
        return [Value(arg, 'xs:string', literal=True)]
    if op == '$numVal':
        return [Value(float(arg), 'xs:double', literal=True)]
    if op == '$hexVal':
        return [Value(int(str(arg).split('#')[-1], 16), 'hex', literal=True)]
    if op == '$boolean':
        return [Value(bool(arg), 'xs:boolean', literal=True)]
    if op == '$dateTimeVal':
        return [Value(_parse_datetime(arg), 'xs:dateTime', literal=True)]
    if op == '$timeVal':
        return [Value(time.fromisoformat(arg.replace('Z', '+00:00')), 'xs:time', literal=True)]
    if op == '$attribute':
        return _attribute(ctx, arg)
    inner = operand(ctx, arg) if isinstance(arg, dict) else [Value(arg)]
    return [Value(c.value, c.vtype, c.binding, True) for c in (_cast(op, v) for v in inner)]


def _cast(op: str, value: Value) -> Value:
    raw = value.value
    try:
        if raw is None:
            return Value(None, None, value.binding)
        if op == '$strCast':
            return Value(raw.isoformat() if isinstance(raw, (datetime, time)) else str(raw), 'xs:string', value.binding)
        if op == '$numCast':
            return Value(float(raw), 'xs:double', value.binding)
        if op == '$boolCast':
            return Value(str(raw).strip().lower() in ('true', '1'), 'xs:boolean', value.binding)
        if op == '$hexCast':
            return Value(int(str(raw).split('#')[-1], 16) if isinstance(raw, str) else int(raw), 'hex', value.binding)
        if op == '$dateTimeCast':
            return Value(raw if isinstance(raw, datetime) else _parse_datetime(str(raw)), 'xs:dateTime', value.binding)
        if op == '$timeCast':
            return Value(raw.timetz() if isinstance(raw, datetime) else time.fromisoformat(str(raw)), 'xs:time',
                         value.binding)
        dt = raw if isinstance(raw, datetime) else _parse_datetime(str(raw))
        part = {'$dayOfWeek': dt.isoweekday() % 7, '$dayOfMonth': dt.day, '$month': dt.month, '$year': dt.year}[op]
        return Value(float(part), 'xs:double', value.binding)
    except (ValueError, KeyError, TypeError):
        return Value(_INVALID, None, value.binding)


_INVALID = object()


# -- comparisons --------------------------------------------------------------------------------
LITERAL_KIND = {'xs:string': 'str', 'xs:double': 'num', 'xs:boolean': 'bool', 'xs:dateTime': 'datetime',
                'xs:time': 'time', 'hex': 'hex'}


def _kind_of(values: list[Value], other: list[Value]) -> str:
    """Comparison domain: the literal operand's type wins; two field operands compare numerically
    only when both carry numeric valueTypes. Two literals of different types do not compare."""
    literal_kinds = {LITERAL_KIND.get(v.vtype, 'str') for v in values + other if v.literal}
    if len(literal_kinds) > 1:
        return 'mismatch'
    if literal_kinds:
        return literal_kinds.pop()
    field_types = {v.vtype for v in values + other if v.value is not None}
    if field_types and field_types <= NUMERIC_TYPES:
        return 'num'
    return 'str'


def _coerce(value: Value, kind: str):
    raw = value.value
    if raw is None or raw is _INVALID:
        return raw
    if kind == 'num':
        return raw if isinstance(raw, float) else float(raw)
    if kind == 'bool':
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ('true', '1')
    if kind == 'datetime':
        return raw if isinstance(raw, datetime) else _parse_datetime(str(raw))
    if kind == 'time':
        return raw if isinstance(raw, time) else time.fromisoformat(str(raw))
    if kind == 'hex':
        return raw if isinstance(raw, int) else int(str(raw).split('#')[-1], 16)
    return raw if isinstance(raw, str) else str(raw)


OPS = {
    '$eq': lambda a, b: a == b, '$ne': lambda a, b: a != b, '$gt': lambda a, b: a > b, '$lt': lambda a, b: a < b,
    '$ge': lambda a, b: a >= b, '$le': lambda a, b: a <= b,
    '$contains': lambda a, b: b in a, '$starts-with': lambda a, b: a.startswith(b),
    '$ends-with': lambda a, b: a.endswith(b), '$regex': lambda a, b: re.search(b, a) is not None,
}
STRING_OPS = {'$contains', '$starts-with', '$ends-with', '$regex'}


def _compare(op: str, left: list[Value], right: list[Value]):
    """Return (result, bindings-that-satisfy) with result True/False/None (error)."""
    kind = 'str' if op in STRING_OPS else _kind_of(left, right)
    if kind == 'mismatch':
        return op == '$ne', set()
    satisfied, valid = set(), False
    for a in left:
        for b in right:
            try:
                x, y = _coerce(a, kind), _coerce(b, kind)
            except (ValueError, TypeError):
                continue
            if x is _INVALID or y is _INVALID:
                continue
            valid = True
            if x is None or y is None:
                both_empty = x is None and y is None
                ok = (op in ('$eq', '$le', '$ge') and both_empty) or (op == '$ne' and not both_empty)
            else:
                try:
                    ok = OPS[op](x, y)
                except (TypeError, re.error):
                    continue
            if ok:
                satisfied.add(a.binding or b.binding)
    if not valid:
        return None, set()
    return bool(satisfied), satisfied


def evaluate(ctx: Context, expression: dict):
    """Evaluate a logical expression to True / False / None (invalid)."""
    if not isinstance(expression, dict) or len(expression) != 1:
        raise FormulaError(f'invalid logical expression {expression!r}')
    (op, arg), = expression.items()
    if op == '$boolean':
        return bool(arg)
    if op == '$and':
        results = [evaluate(ctx, e) for e in arg]
        return False if False in results else None if None in results else True
    if op == '$or':
        results = [evaluate(ctx, e) for e in arg]
        return True if True in results else None if None in results else False
    if op == '$not':
        result = evaluate(ctx, arg)
        return None if result is None else not result
    if op == '$match':
        return _match(ctx, arg)
    if op in OPS:
        if not isinstance(arg, list) or len(arg) != 2:
            raise FormulaError(f'{op} needs two operands')
        return _compare(op, operand(ctx, arg[0]), operand(ctx, arg[1]))[0]
    raise FormulaError(f'unknown operator {op}')


def _match(ctx: Context, items: list):
    common = None
    for item in items:
        (op, arg), = item.items()
        if op == '$match':
            result = _match(ctx, arg)
            if result is not True:
                return result
            continue
        result, bindings = _compare(op, operand(ctx, arg[0]), operand(ctx, arg[1]))
        if result is None:
            return None
        heads = {b[:1] for b in bindings if b}
        if not heads and result:
            continue   # comparison not on a list (e.g. literal-only); does not constrain the match
        common = heads if common is None else common & heads
        if not common:
            return False
    return True if common is None or common else False


def matches(ctx: Context, expression: dict) -> bool:
    return evaluate(ctx, expression) is True


# -- filters --------------------------------------------------------------------------------
def _substitute(expression, old: str, new: str):
    if isinstance(expression, dict):
        return {k: (v.replace(old, new, 1) if k == '$field' and isinstance(v, str) and v.startswith(old)
                    else _substitute(v, old, new)) for k, v in expression.items()}
    if isinstance(expression, list):
        return [_substitute(v, old, new) for v in expression]
    return expression


def apply_filter(ctx: Context, fragment: str, condition: dict) -> None:
    """Prune submodel elements of ``ctx.obj`` (a submodel) that fail ``condition``.

    ``$sme`` alone evaluates per element (``$sme#...`` fields bound to it); ``$sme.p`` keeps or
    removes the element at ``p``; ``$sme.p[]`` filters the entries of list ``p`` row-wise.
    """
    match = ROOT_ONLY.match(fragment)
    if not match or match.group(1) != 'sme':
        return   # fragments on identifiable attributes are not pruned (mandatory data)
    submodel = ctx.obj
    path = match.group(2).lstrip('.')
    if not path:
        failing = []
        for element_path in elements.all_paths(submodel):
            bound = Context(**{**ctx.__dict__, 'bound': {'$sme': (submodel, element_path)}})
            if evaluate(bound, condition) is not True:
                failing.append(element_path)
        elements.remove_many(submodel, failing)
        return
    row_wise = path.endswith('[]')
    base = path[:-2] if row_wise else path
    try:
        target = elements.get(submodel, base)
    except elements.PathError:
        return
    if row_wise:
        items = target.get('value', []) if target.get('modelType') == 'SubmodelElementList' else []
        keep = []
        for index, item in enumerate(items):
            concrete = _substitute(condition, f'$sme.{base}[]', f'$sme.{base}[{index}]')
            if evaluate(ctx, concrete) is True:
                keep.append(item)
        target['value'] = keep
    elif evaluate(ctx, condition) is not True:
        elements.remove(submodel, base)


# -- queries ------------------------------------------------------------------------------------
def run_query(query: dict, kind: str, candidates, *, submodels_of=None, shells_of=None, claims=None):
    """Evaluate a Query document over candidate objects; returns matching objects or ids."""
    errors = validate(query)
    if errors:
        raise FormulaError('; '.join(errors[:5]))
    condition = query['$condition']
    results = []
    for obj in candidates:
        ctx = Context(kind, obj, submodels_of, shells_of, claims or {})
        if not matches(ctx, condition):
            continue
        if query.get('$select') == 'id':
            results.append(obj.get('id'))
            continue
        if query.get('$filters') and kind == 'submodel':
            obj = copy.deepcopy(obj)
            for spec in query['$filters']:
                apply_filter(Context(kind, obj, submodels_of, shells_of, claims or {}), spec['$fragment'],
                             spec['$condition'])
        results.append(obj)
    return results
