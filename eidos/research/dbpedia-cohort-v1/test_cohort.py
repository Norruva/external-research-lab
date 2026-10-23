"""Authored fixtures only; no PyArrow, network, model or real corpus imports."""
import copy
import hashlib
import json
import unittest

from cohort import (CohortError, SALT, check_cohort, fingerprints, historical_texts,
                    normalized, reject_training_overlap, select_records, unique_json)


def source(count=6):
    return [{'label': label, 'title': f'Entity {label} number {i}',
             'content': f'Original explanation category {label} example {i}.'}
            for label in range(2) for i in range(count)]


def row(identifier, title, text, label='A'):
    return {'id': identifier, 'title': title, 'content': text,
            'text': title + '\n' + text, 'label': label}


def roles():
    return {'support': [row('s', 'Support entity', 'Authored training words')],
            'development': [row('d', 'Dev entity', 'Authored dev words')],
            'test': [row('t', 'Test entity', 'Authored final words')]}


class SelectionTests(unittest.TestCase):
    def test_independent_hash_ranking_and_exact_partition(self):
        records = source()
        actual, stats = select_records(iter(records), 'train', ('A', 'B'), 2, 6)
        for index, label in enumerate(('A', 'B')):
            ids = [f'dbp-train-{i:08}' for i in range(index * 6, (index + 1) * 6)]
            expected = sorted(ids, key=lambda x: hashlib.sha256((SALT+'\n'+x).encode()).hexdigest())
            self.assertEqual([r['id'] for r in actual['support'] if r['label'] == label], expected[:2])
            self.assertEqual([r['id'] for r in actual['development'] if r['label'] == label], expected[2:4])
        self.assertEqual(stats['rows'], 12)
        self.assertEqual(select_records(records, 'train', ('A', 'B'), 2, 6), (actual, stats))

    def test_features_cannot_change_selected_ids(self):
        first, _ = select_records(source(), 'test', ('A', 'B'), 2, 6)
        changed = source()
        for r in changed:
            r['title'] = 'Different title'; r['content'] = 'Different content'
        second, _ = select_records(changed, 'test', ('A', 'B'), 2, 6)
        self.assertEqual([r['id'] for r in first['test']], [r['id'] for r in second['test']])
        self.assertTrue(all(r['text'] == 'Different title\nDifferent content' for r in second['test']))

    def test_validates_every_row_not_only_retained_rows(self):
        values = source(); values[-1]['content'] = None
        with self.assertRaisesRegex(CohortError, 'source_content'):
            select_records(values, 'test', ('A', 'B'), 1)

    def test_strict_source_schema_types_and_counts(self):
        changes = ({'label': True}, {'label': 2}, {'title': ''}, {'title': 'x'*2049},
                   {'content': 'x'*64001}, {'content': '\ud800'}, {'extra': 'bad'})
        for change in changes:
            with self.subTest(change=list(change)):
                values = source(); values[0].update(change)
                with self.assertRaises(CohortError): select_records(values, 'test', ('A', 'B'), 2)
        with self.assertRaisesRegex(CohortError, 'unexpected_source_class_counts'):
            select_records(source(), 'train', ('A', 'B'), 2, 7)
        with self.assertRaisesRegex(CohortError, 'insufficient_class_records'):
            select_records(source(1), 'train', ('A', 'B'), 2)

    def test_full_training_overlap_checks_unselected_rows(self):
        selected_test = [row('t', 'TEST Entity', 'Completely unseen content')]
        check = reject_training_overlap(selected_test)
        for offending in (row('s', 'test_entity!', 'Unrelated prose'),
                          row('s', 'TEST Entity', 'Completely unseen content')):
            with self.assertRaises(CohortError): check(offending)
        check(row('s', 'Unrelated entity', 'Different words'))


class LineageTests(unittest.TestCase):
    def test_distinct_roles_and_deterministic_groups(self):
        group, stats = check_cohort(roles(), set())
        self.assertEqual(stats['selected_rows'], 3)
        self.assertEqual(stats['components'], 3)
        self.assertEqual(check_cohort(roles(), set()), (group, stats))

    def test_cross_role_title_component_rejects_whole_cohort(self):
        values = roles(); values['test'][0]['title'] = 'SUPPORT_entity!'
        with self.assertRaisesRegex(CohortError, 'cross_role_title_or_text_component'):
            check_cohort(values, set())
        self.assertEqual(sum(map(len, values.values())), 3)

    def test_same_role_title_components_can_have_mixed_labels(self):
        values = roles(); values['test'].append(row('t2', 'TEST Entity', 'Distinct final prose', 'B'))
        groups, stats = check_cohort(values, set())
        self.assertEqual(groups['t'], groups['t2'])
        self.assertEqual(stats['max_component_rows'], 2)

    def test_historical_full_and_content_only_overlap(self):
        for feature in ('text', 'content'):
            values = roles()
            historical = fingerprints(values['test'][0][feature].upper().replace(' ', '_'))
            with self.assertRaisesRegex(CohortError, 'historical_exact_text_overlap'):
                check_cohort(values, historical)

    def test_no_silent_truncation_or_duplicate_acceptance(self):
        for kind in ('long', 'duplicate_text', 'duplicate_id'):
            values = roles()
            if kind == 'long': values['test'][0]['text'] = 'x'*12001
            if kind == 'duplicate_text':
                added = copy.deepcopy(values['test'][0]); added['id'] = 't2'; values['test'].append(added)
            if kind == 'duplicate_id': values['test'][0]['id'] = 's'
            with self.subTest(kind=kind), self.assertRaises(CohortError): check_cohort(values, set())

    def test_normalization_matches_expected_application_tokens(self):
        self.assertEqual(normalized('Ａ_B, Straße!  42'), 'a b strasse 42')
        self.assertTrue(fingerprints('Ａ B Straße') & fingerprints('a_b, STRASSE'))

    def test_transitive_component_and_cross_role_join(self):
        values = roles()
        a = row('a', 'One title', 'First content')
        b = row('b', 'One title', 'Second content')
        c = row('c', 'Another title', 'Other prose')
        c['text'] = b['text']
        values['test'] = [a, b]; values['development'] = [c]
        with self.assertRaisesRegex(CohortError, 'cross_role_title_or_text_component'):
            check_cohort(values, set())


class HistoryTests(unittest.TestCase):
    def test_jsonl_extracts_only_text(self):
        raw = json.dumps({'id': 'authored', 'text': 'Example', 'label': 'unused'}).encode()
        self.assertEqual(list(historical_texts(raw, 'jsonl')), ['Example'])

    def test_duplicate_keys_nonfinite_and_invalid_text_refused(self):
        for value in ('{"text":"one","text":"two"}', '{"text":NaN}', '{"text":false}'):
            with self.subTest(value=value), self.assertRaises(CohortError):
                list(historical_texts(value.encode(), 'jsonl'))

    def test_lossless_paired_cesu8_and_unpaired_rejection(self):
        raw = b'{"Demo":[{"data":[{"text":"authored \xed\xa0\xbd\xed\xb8\x80"}]}]}'
        self.assertEqual(list(historical_texts(raw, 'snips')), ['authored \U0001f600'])
        with self.assertRaises(UnicodeError):
            list(historical_texts(raw.replace(b'\xed\xb8\x80', b''), 'snips'))

    def test_snips_schema_and_unapproved_kind(self):
        for raw in (b'{"Demo":[]}', b'{"Demo":[{"data":[{"text":1}]}]}', b'{"Demo":[],"Other":[]}'):
            with self.assertRaises(CohortError): list(historical_texts(raw, 'snips'))
        with self.assertRaises(CohortError): list(historical_texts(b'{}', 'pickle'))


if __name__ == '__main__':
    unittest.main()
