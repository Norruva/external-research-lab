"""Compile Query Language / Access Rule formulas to SQL *prefilters*.

Exact semantics stay in ``formula.py``. This module produces a SQL condition that is guaranteed
to select every object for which the Python evaluator returns ``True``, and usually few others.
Callers run the exact evaluator on what the database returns, so results are identical, but a
query over a large repository reads only the candidates (the approach AASX Server takes when it
compiles formulas to SQL, without depending on SQL for correctness).

A sub-expression that cannot be compiled safely becomes *unconstrained* (``None``, meaning
"true"). That covers ``$not``, ``$regex``, casts, field-to-field comparisons, ``$ne``, and
date/time and boolean literals. Unconstrained parts are dropped from ``$and`` and make an
``$or`` unconstrained. ``$match`` compiles as the ``$and`` of its comparisons. A comparison
without fields is evaluated with Python at compile time.

Compiled conditions reference the ``identifiables`` table and the ``element_index``,
``shell_submodels`` and ``shell_asset_ids`` tables kept by ``store.py``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import formula

FALSE = ('1=0', [])
CMP = {'$eq': '=', '$gt': '>', '$lt': '<', '$ge': '>=', '$le': '<='}
FLIP = {'$eq': '$eq', '$gt': '$lt', '$lt': '$gt', '$ge': '$le', '$le': '$ge'}
STRING = ('$contains', '$starts-with', '$ends-with')

# identifiable attribute -> column of ``identifiables``, per root
COLUMNS = {
    'aas': {'id': 'id', 'idshort': 'id_short', 'assetinformation.globalassetid': 'global_asset_id'},
    'sm': {'id': 'id', 'idshort': 'id_short', 'semanticid': 'semantic_id'},
    'cd': {'id': 'id', 'idshort': 'id_short'},
}
ROOT_KIND = {'aas': 'shell', 'sm': 'submodel', 'cd': 'concept-description'}
ELEMENT_COLUMNS = {'value': None, 'idshort': 'id_short', 'semanticid': 'semantic_id', 'valuetype': 'value_type'}
INDEX = re.compile(r'\[\d*\]')


class _Compiler:
    def __init__(self, db, kind: str, claims: dict | None):
        self.db, self.kind, self.claims = db, kind, claims or {}

    # -- logical -----------------------------------------------------------------------------
    def logical(self, expression):
        if not isinstance(expression, dict) or len(expression) != 1:
            return None
        (op, arg), = expression.items()
        if op == '$boolean':
            return None if arg else FALSE
        if op in ('$and', '$match'):
            parts = [self.logical(e) for e in arg if isinstance(e, dict)]
            parts = [p for p in parts if p is not None]
            if FALSE in parts:
                return FALSE
            return _join(' AND ', parts) if parts else None
        if op == '$or':
            parts = [self.logical(e) for e in arg]
            if any(p is None for p in parts):
                return None
            parts = [p for p in parts if p != FALSE]
            return _join(' OR ', parts) if parts else FALSE
        if op in CMP or op in STRING:
            if not isinstance(arg, list) or len(arg) != 2:
                return None
            return self.comparison(op, arg[0], arg[1])
        return None     # $not, $regex, $ne and anything unknown

    # -- comparisons ----------------------------------------------------------------------------
    def comparison(self, op, left, right):
        left_field, right_field = _is_field(left), _is_field(right)
        if not _has_field(left) and not _has_field(right):
            return self.constant({op: [left, right]})
        if (_has_field(left) and not left_field) or (_has_field(right) and not right_field):
            return None                 # a cast or function around a field
        if left_field and right_field:
            return None
        if right_field:
            if op in STRING:
                return None             # "literal contains field" is not index-friendly
            op, left, right = FLIP[op], right, left
        constant = self.constant_operand(right)
        if constant is None:
            return None
        kind, values = constant
        clauses = [c for c in (self.field_condition(left['$field'], op, kind, v) for v in values)]
        if any(c is None for c in clauses):
            return None
        return _join(' OR ', clauses) if len(clauses) > 1 else clauses[0]

    def constant(self, expression):
        context = formula.Context(self.kind, {}, claims=self.claims, now=datetime.now(timezone.utc))
        try:
            return None if formula.evaluate(context, expression) is True else FALSE
        except formula.FormulaError:
            return None

    def constant_operand(self, spec):
        """(comparison kind, [python values]) for a literal or attribute operand, else None."""
        if not isinstance(spec, dict) or len(spec) != 1:
            return None
        (op, arg), = spec.items()
        if op == '$strVal' and isinstance(arg, str):
            return 'str', [arg]
        if op == '$numVal' and isinstance(arg, (int, float)) and not isinstance(arg, bool):
            return 'num', [float(arg)]
        if op == '$attribute' and isinstance(arg, dict) and 'CLAIM' in arg:
            claim = self.claims.get(arg['CLAIM'])
            values = claim if isinstance(claim, list) else [claim]
            if not values or any(v is None for v in values):
                return None             # absent claims have both-empty semantics; leave to Python
            return 'attr', [str(v) for v in values]
        return None

    # -- fields ------------------------------------------------------------------------------
    def field_condition(self, identifier, op, kind, value):
        match = formula.FIELD.match(identifier) if isinstance(identifier, str) else None
        if not match:
            return None
        root, path, attribute = match.group(1), match.group(2).lstrip('.'), match.group(3)
        attribute_key = attribute.lower()
        if root == 'sme':
            return self.element_condition(path, attribute_key, op, kind, value)
        if root not in COLUMNS or kind == 'num':
            return None
        if root == 'aas' and attribute_key in ('assetinformation.specificassetids[].name',
                                                'assetinformation.specificassetids[].value'):
            column = attribute_key.rsplit('.', 1)[1]
            test, params = self.text_test(f'a.{column}', op, value)
            return self.scoped('aas', f'EXISTS (SELECT 1 FROM shell_asset_ids a WHERE a.aas_id={{aas}} AND {test})',
                               params)
        column = COLUMNS[root].get(attribute_key)
        if column is None:
            return None
        test, params = self.text_test(f'x.{column}', op, value)
        return self.scoped(root, f'EXISTS (SELECT 1 FROM identifiables x WHERE x.kind=? AND x.id={{{root}}} AND {test})',
                           [ROOT_KIND[root]] + params)

    def element_condition(self, path, attribute_key, op, kind, value):
        if attribute_key not in ELEMENT_COLUMNS:
            return None
        clauses, params = [], []
        if path:
            clauses.append('e.pattern=?')
            params.append(INDEX.sub('[]', path))
        column = ELEMENT_COLUMNS[attribute_key]
        if column is None:          # the element value
            if kind == 'num':
                if op not in CMP:
                    return None
                clauses.append(f'e.value_num {CMP[op]} ?')
                params.append(value)
            else:
                test, more = self.text_test('e.value_text', op, value)
                clauses.append(test)
                params += more
        else:
            if kind == 'num':
                return None
            test, more = self.text_test(f'e.{column}', op, value)
            clauses.append(test)
            params += more
        return self.scoped('sm', f'EXISTS (SELECT 1 FROM element_index e WHERE e.owner_id={{sm}} AND '
                                 f'{" AND ".join(clauses)})', params)

    def text_test(self, column, op, value):
        if op in CMP:
            return f'{column} {CMP[op]} ?', [value]
        if op == '$contains':
            return self.db.contains(column), [value]
        if op == '$starts-with':
            return f'substr({column}, 1, length(?)) = ?', [value, value]
        return f'(length({column}) >= length(?) AND substr({column}, length({column}) - length(?) + 1) = ?)', \
            [value, value, value]

    def scoped(self, root, template, params):
        """Bind ``{aas}``/``{sm}``/``{cd}`` to the queried object or to the objects related to it."""
        if root == 'aas':
            if self.kind == 'shell':
                return template.format(aas='identifiables.id'), params
            if self.kind == 'submodel':
                inner = template.format(aas='l.aas_id')
                return f'EXISTS (SELECT 1 FROM shell_submodels l WHERE l.sm_id=identifiables.id AND {inner})', params
            return None                 # other kinds may still resolve $aas through shells_of
        if root == 'sm':
            if self.kind == 'submodel':
                return template.format(sm='identifiables.id'), params
            if self.kind == 'shell':
                inner = template.format(sm='l.sm_id')
                return f'EXISTS (SELECT 1 FROM shell_submodels l WHERE l.aas_id=identifiables.id AND {inner})', params
            return FALSE
        if root == 'cd':
            return (template.format(cd='identifiables.id'), params) if self.kind == 'concept-description' else FALSE
        return None


def _is_field(spec) -> bool:
    return isinstance(spec, dict) and set(spec) == {'$field'}


def _has_field(spec) -> bool:
    if isinstance(spec, dict):
        return '$field' in spec or any(_has_field(v) for v in spec.values())
    if isinstance(spec, list):
        return any(_has_field(v) for v in spec)
    return False


def _join(separator, parts):
    return separator.join(f'({sql})' for sql, _ in parts), [p for _, params in parts for p in params]


def compile_condition(expression: dict, kind: str, db, claims: dict | None = None):
    """``(sql, params)`` selecting a superset of the matches of ``expression``, or ``None`` when the
    expression puts no usable constraint on the database (every object is a candidate)."""
    if kind not in ('shell', 'submodel', 'concept-description'):
        return None
    return _Compiler(db, kind, claims).logical(expression)


def combine(*conditions):
    """AND of compiled conditions; ``None`` entries are unconstrained."""
    parts = [c for c in conditions if c is not None]
    if FALSE in parts:
        return FALSE
    return _join(' AND ', parts) if parts else None


def any_of(conditions):
    """OR of compiled conditions; any unconstrained entry makes the result unconstrained."""
    conditions = list(conditions)
    if not conditions:
        return FALSE
    if any(c is None for c in conditions):
        return None
    parts = [c for c in conditions if c != FALSE]
    return _join(' OR ', parts) if parts else FALSE
