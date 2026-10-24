"""Original bounded request contract for question-conditioned decision models.

This module validates transport and typed decoding inputs. It does not establish
semantic correctness, data rights, probability calibration or model quality.
Opaque IDs are transport identities; scorer features must use meaning-bearing
context, prompt and option descriptions instead.
"""
from dataclasses import dataclass
import json
import math
import re

FORMAT = 'eidos-decision-request-v2'
MAX_REQUEST_BYTES = 1024 * 1024
MAX_CONTEXT_CHARACTERS = 65_536
MAX_PROMPT_CHARACTERS = 4_096
MAX_DESCRIPTION_CHARACTERS = 1_024
MAX_TOTAL_TEXT_CHARACTERS = 131_072
MAX_OPTIONS = 255
MAX_SCORE_MAGNITUDE = 1_000_000_000.0
MAX_JSON_NODES = 4_096
MAX_JSON_DEPTH = 8
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z', re.ASCII)


class DecisionContractError(ValueError):
    """A request violates the explicit v2 contract or bounded input policy."""


@dataclass(frozen=True)
class DecisionOption:
    id: str
    description: str
    value: str | bool | float


@dataclass(frozen=True)
class DecisionRequest:
    format: str
    request_id: str
    context: str
    prompt: str
    kind: str
    options: tuple[DecisionOption, ...]
    minimum: float | None = None
    maximum: float | None = None

    def as_dict(self):
        """Return the public wire form; internal derived values are not accepted fields."""
        rows = []
        for option in self.options:
            row = {'id': option.id, 'description': option.description}
            if self.kind == 'SCORE':
                row['value'] = option.value
            rows.append(row)
        result = {'format': self.format, 'request_id': self.request_id,
                  'context': self.context, 'prompt': self.prompt,
                  'kind': self.kind, 'options': rows}
        if self.kind == 'SCORE':
            result.update(minimum=self.minimum, maximum=self.maximum)
        return result

    def to_dict(self):
        return self.as_dict()


def _fail(reason):
    # Reasons deliberately contain no caller text or values.
    raise DecisionContractError(reason)


def _safe_unicode(text):
    for character in text:
        point = ord(character)
        if 0xD800 <= point <= 0xDFFF:
            _fail('invalid_unicode')
        if (point < 0x20 and character not in '\t\r\n') or 0x7F <= point <= 0x9F:
            _fail('control_character_refused')


def _json_budget(value):
    """Bound direct dictionaries as well as serialized input; reject cycles/types."""
    active = set()
    nodes = 0
    characters = 0

    def visit(item, depth):
        nonlocal nodes, characters
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            _fail('json_structure_budget_exceeded')
        if item is None or type(item) is bool:
            return
        if type(item) in (int, float):
            try:
                finite = math.isfinite(item)
            except (OverflowError, ValueError):
                finite = False
            if not finite:
                _fail('nonfinite_number')
            return
        if type(item) is str:
            characters += len(item)
            if characters > MAX_TOTAL_TEXT_CHARACTERS:
                _fail('total_text_budget_exceeded')
            _safe_unicode(item)
            return
        if type(item) not in (dict, list):
            _fail('json_value_type_refused')
        identity = id(item)
        if identity in active:
            _fail('cyclic_json_refused')
        active.add(identity)
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    _fail('json_key_type_refused')
                visit(key, depth + 1)
                visit(child, depth + 1)
        else:
            for child in item:
                visit(child, depth + 1)
        active.remove(identity)

    visit(value, 0)


def _text(value, maximum):
    if type(value) is not str or not 1 <= len(value) <= maximum or not value.strip():
        _fail('text_type_blank_or_length_refused')
    _safe_unicode(value)
    return value


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail('opaque_identifier_refused')
    return value


def _number(value):
    if type(value) not in (int, float):
        _fail('score_number_type_refused')
    try:
        result = float(value)
    except (OverflowError, ValueError):
        _fail('score_number_outside_bounds')
    if not math.isfinite(result) or abs(result) > MAX_SCORE_MAGNITUDE:
        _fail('score_number_outside_bounds')
    return result


def validate_request(value):
    """Validate one wire dictionary and return a canonical immutable request.

    Limits are Unicode characters and encoded bytes, not tokenizer tokens.
    Candidate order is canonicalized by opaque ID so unchanged-set inference can
    be equivariant. ID spelling and numeric score values must not enter semantic
    scorer features. BINARY IDs are exactly ``false`` and ``true``; callers must
    still provide their explicit meaning-bearing descriptions.
    """
    _json_budget(value)
    if type(value) is not dict:
        _fail('request_object_required')
    common = {'format', 'request_id', 'context', 'prompt', 'kind', 'options'}
    kind = value.get('kind')
    if type(kind) is not str or kind not in ('CHOICE', 'BINARY', 'SCORE'):
        _fail('unsupported_decision_kind')
    expected = common | ({'minimum', 'maximum'} if kind == 'SCORE' else set())
    if set(value) != expected:
        _fail('request_fields_mismatch')
    if value['format'] != FORMAT or type(value['format']) is not str:
        _fail('unsupported_request_format')
    request_id = _identifier(value['request_id'])
    context = _text(value['context'], MAX_CONTEXT_CHARACTERS)
    prompt = _text(value['prompt'], MAX_PROMPT_CHARACTERS)
    rows = value['options']
    if type(rows) is not list or not 2 <= len(rows) <= MAX_OPTIONS:
        _fail('option_count_or_type_refused')
    if kind == 'BINARY' and len(rows) != 2:
        _fail('binary_requires_false_and_true')
    minimum = _number(value['minimum']) if kind == 'SCORE' else None
    maximum = _number(value['maximum']) if kind == 'SCORE' else None
    if kind == 'SCORE' and not minimum < maximum:
        _fail('score_bounds_must_increase')
    options = []
    identities = set()
    anchor_values = set()
    for row in rows:
        expected_row = {'id', 'description', 'value'} if kind == 'SCORE' else {'id', 'description'}
        if type(row) is not dict or set(row) != expected_row:
            _fail('option_fields_mismatch')
        identity = _identifier(row['id'])
        if identity in identities:
            _fail('duplicate_option_identifier')
        identities.add(identity)
        description = _text(row['description'], MAX_DESCRIPTION_CHARACTERS)
        if kind == 'SCORE':
            output_value = _number(row['value'])
            if not minimum <= output_value <= maximum:
                _fail('score_anchor_outside_bounds')
            if output_value in anchor_values:
                _fail('duplicate_score_anchor_value')
            anchor_values.add(output_value)
        elif kind == 'BINARY':
            if identity not in ('false', 'true'):
                _fail('binary_requires_false_and_true')
            output_value = identity == 'true'
        else:
            output_value = identity
        options.append(DecisionOption(identity, description, output_value))
    if kind == 'BINARY' and identities != {'false', 'true'}:
        _fail('binary_requires_false_and_true')
    return DecisionRequest(FORMAT, request_id, context, prompt, kind,
                           tuple(sorted(options, key=lambda option: option.id)),
                           minimum, maximum)


def parse_request(raw):
    """Decode bounded strict UTF-8 JSON, refusing duplicate/nonfinite fields."""
    if type(raw) is str:
        if len(raw) > MAX_REQUEST_BYTES:
            _fail('request_byte_budget_exceeded')
        try:
            encoded = raw.encode('utf-8', errors='strict')
        except UnicodeError:
            _fail('invalid_unicode')
    elif type(raw) is bytes:
        encoded = raw
    else:
        _fail('serialized_request_type_refused')
    if not 1 <= len(encoded) <= MAX_REQUEST_BYTES:
        _fail('request_byte_budget_exceeded')
    try:
        text = encoded.decode('utf-8', errors='strict')
    except UnicodeError:
        _fail('invalid_unicode')

    def pairs(items):
        result = {}
        for key, child in items:
            if key in result:
                _fail('duplicate_json_key')
            result[key] = child
        return result

    def invalid_constant(_value):
        _fail('nonfinite_number')

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except DecisionContractError:
        raise
    except (ValueError, TypeError, RecursionError, OverflowError):
        _fail('malformed_json')
    return validate_request(value)


def canonical_request(value):
    """Serialize a validated request deterministically without changing meaning."""
    if type(value) is DecisionRequest:
        request = validate_request(value.as_dict())
    else:
        request = validate_request(value)
    raw = json.dumps(request.as_dict(), ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > MAX_REQUEST_BYTES:
        _fail('request_byte_budget_exceeded')
    return raw
