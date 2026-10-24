"""Original v2 transport/typing fixtures; no model or corpus is evaluated."""
import copy
from dataclasses import FrozenInstanceError
import json
import unittest

from eidos.decision_contract import (
    FORMAT, MAX_CONTEXT_CHARACTERS, MAX_DESCRIPTION_CHARACTERS,
    MAX_PROMPT_CHARACTERS, MAX_REQUEST_BYTES, DecisionContractError,
    DecisionOption, DecisionRequest, canonical_request, parse_request,
    validate_request,
)


def choice():
    return {'format': FORMAT, 'request_id': 'original-request-01',
            'context': 'The lamp is amber. The folder is teal.',
            'prompt': 'Which colour describes the folder?', 'kind': 'CHOICE',
            'options': [{'id': 'opaque-B', 'description': 'amber'},
                        {'id': 'opaque-A', 'description': 'teal'}]}


def binary():
    value = choice()
    value.update(kind='BINARY', prompt='Is the folder teal?', options=[
        {'id': 'true', 'description': 'The proposition is supported by the supplied context.'},
        {'id': 'false', 'description': 'The proposition is contradicted by the supplied context.'}])
    return value


def score():
    value = choice()
    value.update(kind='SCORE', minimum=-2, maximum=2,
                 prompt='Rate the stated severity on the supplied scale.', options=[
                     {'id': 'low', 'description': 'Minor severity', 'value': -2},
                     {'id': 'high', 'description': 'Major severity', 'value': 2}])
    return value


class DecisionContractTests(unittest.TestCase):
    def refused(self, value):
        with self.assertRaises(DecisionContractError):
            validate_request(value)

    def test_choice_preserves_semantic_text_and_canonicalizes_only_opaque_ids(self):
        value = choice()
        request = validate_request(value)
        self.assertEqual(request.context, value['context'])
        self.assertEqual(request.prompt, value['prompt'])
        self.assertEqual([o.id for o in request.options], ['opaque-A', 'opaque-B'])
        self.assertEqual([o.description for o in request.options], ['teal', 'amber'])
        self.assertEqual([o.value for o in request.options], ['opaque-A', 'opaque-B'])
        self.assertIsNone(request.minimum)
        self.assertIsNone(request.maximum)
        self.assertEqual(value, choice())

    def test_unchanged_option_set_permutation_has_identical_canonical_document(self):
        first = choice()
        second = copy.deepcopy(first)
        second['options'].reverse()
        self.assertEqual(validate_request(first), validate_request(second))
        self.assertEqual(canonical_request(first), canonical_request(second))
        self.assertEqual(parse_request(canonical_request(first)), validate_request(first))

    def test_immutable_normalized_request_and_options(self):
        request = validate_request(choice())
        with self.assertRaises(FrozenInstanceError):
            request.prompt = 'changed'
        with self.assertRaises(FrozenInstanceError):
            request.options[0].description = 'changed'
        self.assertIsInstance(request.options, tuple)

    def test_binary_uses_real_boolean_values_and_explicit_option_meanings(self):
        request = validate_request(binary())
        self.assertEqual([o.id for o in request.options], ['false', 'true'])
        self.assertIs(request.options[0].value, False)
        self.assertIs(request.options[1].value, True)
        self.assertNotIn('value', request.as_dict()['options'][0])
        self.assertEqual(parse_request(canonical_request(request)), request)

    def test_binary_refuses_wrong_ids_missing_descriptions_and_extra_options(self):
        for mutate in (
                lambda r: r['options'][0].update(id='yes'),
                lambda r: r['options'][0].pop('description'),
                lambda r: r['options'].append({'id': 'unknown', 'description': 'Unknown'})):
            with self.subTest(mutate=mutate):
                value = binary()
                mutate(value)
                self.refused(value)

    def test_score_values_and_bounds_roundtrip_without_known_classes(self):
        request = validate_request(score())
        self.assertEqual((request.minimum, request.maximum), (-2.0, 2.0))
        self.assertEqual([(o.id, o.value) for o in request.options], [('high', 2.0), ('low', -2.0)])
        self.assertEqual(parse_request(canonical_request(request)), request)
        self.assertIn('value', request.as_dict()['options'][0])

    def test_score_refuses_boolean_nonfinite_excessive_and_non_numeric_values(self):
        for invalid in (True, False, None, '1', float('inf'), float('-inf'), float('nan'),
                        1_000_000_001, 10**400):
            for position in ('minimum', 'maximum', 'anchor'):
                with self.subTest(invalid=type(invalid).__name__, position=position):
                    value = score()
                    if position == 'anchor':
                        value['options'][0]['value'] = invalid
                    else:
                        value[position] = invalid
                    self.refused(value)

    def test_score_refuses_reversed_bounds_outside_and_duplicate_numeric_anchors(self):
        for mutate in (
                lambda r: r.update(minimum=2, maximum=-2),
                lambda r: r.update(minimum=2, maximum=2),
                lambda r: r['options'][0].update(value=-3),
                lambda r: r['options'][0].update(value=2.0),
                lambda r: r['options'][0].pop('value')):
            value = score()
            mutate(value)
            self.refused(value)
        value = score()
        value['options'][0]['value'] = 0
        value['options'][1]['value'] = -0.0
        self.refused(value)

    def test_unknown_missing_and_kind_specific_fields_are_refused(self):
        for mutate in (
                lambda r: r.update(extra=True),
                lambda r: r.pop('prompt'),
                lambda r: r.update(minimum=0, maximum=1),
                lambda r: r.update(format='legacy'),
                lambda r: r.update(kind='ROUTE'),
                lambda r: r.update(kind=['CHOICE']),
                lambda r: r['options'][0].update(class_id='trained-class'),
                lambda r: r['options'][0].update(value='invented')):
            value = choice()
            mutate(value)
            self.refused(value)
        self.refused({'kind': 'CHOICE'})
        self.refused([choice()])

    def test_opaque_ids_are_unique_bounded_ascii_identifiers(self):
        for bad in ('', ' ', 'contains space', 'two/parts', '\n', 'x'*129, 'é', 1, True):
            for position in ('request_id', 'option'):
                value = choice()
                if position == 'request_id':
                    value['request_id'] = bad
                else:
                    value['options'][0]['id'] = bad
                self.refused(value)
        value = choice()
        value['options'][1]['id'] = value['options'][0]['id']
        self.refused(value)

    def test_two_and_255_options_accepted_unsupported_counts_refused(self):
        value = choice()
        value['options'] = [{'id': f'o-{i:03d}', 'description': f'Original alternative {i}'}
                            for i in range(255)]
        self.assertEqual(len(validate_request(value).options), 255)
        value['options'].append({'id': 'extra', 'description': 'Extra alternative'})
        self.refused(value)
        for rows in ([], [choice()['options'][0]], tuple(choice()['options'])):
            value = choice()
            value['options'] = rows
            self.refused(value)

    def test_context_prompt_and_description_bounds_are_characters_not_tokens(self):
        for key, maximum in (('context', MAX_CONTEXT_CHARACTERS), ('prompt', MAX_PROMPT_CHARACTERS)):
            value = choice()
            value[key] = '界'*maximum
            self.assertEqual(getattr(validate_request(value), key), value[key])
            value[key] += 'x'
            self.refused(value)
            for invalid in ('', ' \n\t', None, 1):
                value[key] = invalid
                self.refused(value)
        value = choice()
        value['options'][0]['description'] = '界'*MAX_DESCRIPTION_CHARACTERS
        self.assertEqual(len(validate_request(value).options[1].description), MAX_DESCRIPTION_CHARACTERS)
        value['options'][0]['description'] += 'x'
        self.refused(value)

    def test_total_text_budget_is_independent_of_per_field_bounds(self):
        value = choice()
        value['context'] = 'x'*MAX_CONTEXT_CHARACTERS
        value['options'] = [{'id': f'o-{i:03d}', 'description': 'y'*1024} for i in range(100)]
        self.refused(value)

    def test_regular_unicode_and_document_whitespace_are_preserved(self):
        value = choice()
        value['context'] = 'Café\n世界\t🙂\r\nA combining accent: e\u0301'
        request = parse_request(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        self.assertEqual(request.context, value['context'])
        self.assertEqual(validate_request(request.as_dict()), request)

    def test_control_characters_and_orphan_surrogates_are_refused(self):
        for bad in ('\x00', '\x01', '\x7f', '\x85', '\ud800', '\udfff'):
            for position in ('context', 'prompt', 'description'):
                value = choice()
                if position == 'description':
                    value['options'][0][position] += bad
                else:
                    value[position] += bad
                self.refused(value)
                with self.assertRaises(DecisionContractError):
                    parse_request(json.dumps(value))
        with self.assertRaises(DecisionContractError):
            parse_request(b'\xff\xfe')
        with self.assertRaises(DecisionContractError):
            parse_request('\ud800')

    def test_duplicate_root_and_nested_json_keys_are_refused(self):
        raw = json.dumps(choice())
        for changed in (raw.replace('"request_id":', '"request_id":"duplicate","request_id":', 1),
                        raw.replace('"description":', '"description":"duplicate","description":', 1)):
            with self.assertRaises(DecisionContractError):
                parse_request(changed)

    def test_nonfinite_json_literals_and_overflow_exponents_are_refused(self):
        raw = json.dumps(score())
        for invalid in ('NaN', 'Infinity', '-Infinity', '1e9999'):
            with self.assertRaises(DecisionContractError):
                parse_request(raw.replace('"minimum": -2', '"minimum": '+invalid))

    def test_encoded_byte_budget_malformed_and_non_serialized_inputs_are_refused(self):
        for invalid in (b'', '', b'{', b'[]', b'{}', bytearray(b'{}'), choice(), None,
                        b' '*(MAX_REQUEST_BYTES+1), '界'*(MAX_REQUEST_BYTES//2)):
            with self.subTest(type=type(invalid).__name__):
                with self.assertRaises(DecisionContractError):
                    parse_request(invalid)

    def test_direct_dict_cycles_deep_nodes_and_non_json_types_are_refused(self):
        value = choice()
        value['extra'] = value
        self.refused(value)
        value = choice()
        nested = []
        value['extra'] = nested
        for _ in range(12):
            child = []
            nested.append(child)
            nested = child
        self.refused(value)
        value = choice()
        value['extra'] = [None]*5000
        self.refused(value)
        for extra in (set(), object(), (1, 2)):
            value = choice()
            value['extra'] = extra
            self.refused(value)
        value = choice()
        value[1] = 'not a string key'
        self.refused(value)

    def test_canonical_serialization_revalidates_directly_constructed_dataclasses(self):
        request = validate_request(choice())
        invalid = DecisionRequest(request.format, request.request_id, request.context,
                                  request.prompt, request.kind,
                                  (DecisionOption('same', 'First meaning', 'same'),
                                   DecisionOption('same', 'Second meaning', 'same')))
        with self.assertRaises(DecisionContractError):
            canonical_request(invalid)


if __name__ == '__main__':
    unittest.main()
