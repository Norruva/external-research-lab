"""Independent original authored fixtures only; no corpus/native/model import."""
import copy
import collections
import hashlib
import json
import unittest
from unittest.mock import patch

from cohort import (CohortError, SALT, canonical, check_cohort, digest, exposure_rows,
                    fingerprints, historical_texts, normalized, scan_sources,
                    select_sources, unique_json, validate_exposures)

LABELS = ('A', 'B')
QUOTAS = {'training': 1, 'development': 1, 'test': 1}
EXPECTED = {'train': 6, 'test': 4}


def source():
    return {split: [{'label': label, 'title': f'Original {split} article {label} number {i}',
                     'content': f'Authored {split} explanation category {label} example {i}.'}
                    for label in range(2) for i in range(count)]
            for split, count in EXPECTED.items()}


def factory(values):
    return lambda split: iter(values[split])


def identifier(split, index):
    return f'dbp-{split}-{index:08}'


def feature(value):
    return value['title'] + '\n' + value['content']


def prepare(values=None, exposures=None, historical=None):
    values = source() if values is None else values
    exposures = {} if exposures is None else exposures
    historical = set() if historical is None else historical
    census = scan_sources(factory(values), exposures, LABELS, EXPECTED)
    roles, stats = select_sources(factory(values), census, historical, QUOTAS, LABELS)
    return census, roles, stats


class SelectionTests(unittest.TestCase):
    def test_independent_hash_ranking_exact_roles_and_complete_counts(self):
        census, roles, stats = prepare()
        for split, count in EXPECTED.items():
            for label_index, label in enumerate(LABELS):
                ids = [identifier(split, index) for index in range(label_index * count, (label_index + 1) * count)]
                ordered = sorted(ids, key=lambda item: hashlib.sha256((SALT + '\n' + item).encode()).hexdigest())
                for role, offset in ([('training', 0), ('development', 1)] if split == 'train' else [('test', 0)]):
                    self.assertEqual([row['id'] for row in roles[role] if row['label'] == label], [ordered[offset]])
            self.assertEqual(stats[split]['source_rows'], 2 * count)
            self.assertEqual(stats[split]['excluded_rows'], 0)
            self.assertEqual(census.source_stats[split]['class_counts'], {label: count for label in LABELS})
        again = prepare()
        self.assertEqual(roles, again[1])
        self.assertEqual(stats, again[2])

    def test_prior_selected_case_is_removed_before_ranking(self):
        values = source()
        _, baseline, _ = prepare(values)
        old = baseline['training'][0]
        exposures = {old['id']: hashlib.sha256(old['text'].encode()).hexdigest()}
        census, roles, stats = prepare(values, exposures)
        self.assertNotIn(old['id'], {row['id'] for rows in roles.values() for row in rows})
        self.assertEqual(stats['train']['overlapping_exclusion_reason_counts']['previously_selected_id_or_component'], 1)
        self.assertIn(digest(normalized(old['title'])), census.exposure_keys[0])
        self.assertTrue(fingerprints(old['content']) <= census.exposure_fingerprints)
        groups, lineage = check_cohort(roles, census, set(), QUOTAS, LABELS)
        self.assertEqual(lineage['resolved_previous_exposure_cases'], 1)
        self.assertEqual(len(groups), 6)

    def test_global_title_full_feature_and_content_singletons_across_splits(self):
        for mode, reason in [('title', 'non_singleton_title'), ('full', 'non_singleton_full_feature'),
                             ('content', 'non_singleton_content')]:
            values = source()
            if mode in ('title', 'full'):
                values['test'][0]['title'] = values['train'][0]['title']
            if mode in ('content', 'full'):
                values['test'][0]['content'] = values['train'][0]['content']
            with self.subTest(mode=mode):
                _, roles, stats = prepare(values)
                selected = {row['id'] for rows in roles.values() for row in rows}
                self.assertNotIn(identifier('train', 0), selected)
                self.assertNotIn(identifier('test', 0), selected)
                self.assertEqual(stats['train']['overlapping_exclusion_reason_counts'][reason], 1)
                self.assertEqual(stats['test']['overlapping_exclusion_reason_counts'][reason], 1)

    def test_transitive_duplicate_bridge_cannot_enter_any_role(self):
        values = source()
        # A--title--B--content--C: excluding endpoints of every duplicate key
        # excludes the whole non-singleton component without partial retention.
        values['train'][1]['title'] = values['train'][0]['title']
        values['test'][0]['content'] = values['train'][1]['content']
        _, roles, stats = prepare(values)
        selected = {row['id'] for rows in roles.values() for row in rows}
        self.assertTrue({identifier('train', 0), identifier('train', 1), identifier('test', 0)}.isdisjoint(selected))
        self.assertEqual(stats['train']['excluded_rows'], 2)
        self.assertEqual(stats['test']['excluded_rows'], 1)

    def test_historical_full_or_body_match_is_excluded_before_hashing(self):
        values = source()
        for text in (feature(values['test'][0]), values['test'][0]['content']):
            history = fingerprints(text.upper().replace(' ', '_'))
            census, roles, stats = prepare(values, historical=history)
            self.assertNotIn(identifier('test', 0), {row['id'] for row in roles['test']})
            self.assertEqual(stats['test']['overlapping_exclusion_reason_counts']['historical_full_or_content_fingerprint'], 1)
            check_cohort(roles, census, history, QUOTAS, LABELS)

    def test_exposure_id_text_mismatch_and_missing_identifier_reject_first_pass(self):
        values = source()
        for exposures, reason in [({identifier('train', 0): '0' * 64}, 'exposure_source_text_mismatch'),
                                  ({identifier('test', 99): '0' * 64}, 'unresolved_exposure_id')]:
            with self.subTest(reason=reason), self.assertRaisesRegex(CohortError, reason):
                scan_sources(factory(values), exposures, LABELS, EXPECTED)

    def test_prior_newline_title_uses_actual_source_boundaries(self):
        values = source()
        values['train'][0]['title'] = 'Original title\nwith a second line'
        exposures = {identifier('train', 0): digest(feature(values['train'][0]))}
        census, _, _ = prepare(values, exposures)
        self.assertIn(digest(normalized(values['train'][0]['title'])), census.exposure_keys[0])
        self.assertTrue(fingerprints(values['train'][0]['content']) <= census.exposure_fingerprints)

    def test_complete_second_pass_is_bound_even_if_changed_row_not_selected(self):
        values = source()
        census = scan_sources(factory(values), {}, LABELS, EXPECTED)
        changed = copy.deepcopy(values)
        changed['train'][-1]['content'] += ' a changed unselected sentence'
        with self.assertRaisesRegex(CohortError, 'source_changed_between_passes'):
            select_sources(factory(changed), census, set(), QUOTAS, LABELS)

    def test_every_source_row_schema_and_counts_are_validated(self):
        changes = ({'label': True}, {'label': 2}, {'title': ''}, {'title': 'x' * 2049},
                   {'content': None}, {'content': 'x' * 64001}, {'content': '\ud800'}, {'extra': 1})
        for change in changes:
            values = source()
            values['test'][-1].update(change)
            with self.subTest(fields=list(change)), self.assertRaises(CohortError):
                scan_sources(factory(values), {}, LABELS, EXPECTED)
        with self.assertRaisesRegex(CohortError, 'unexpected_source_class_counts'):
            scan_sources(factory(source()), {}, LABELS, {'train': 7, 'test': 4})

    def test_insufficient_eligible_records_abort_without_replacement(self):
        values = source()
        for row in values['train']:
            row['title'] = 'One globally duplicated title'
        census = scan_sources(factory(values), {}, LABELS, EXPECTED)
        with self.assertRaisesRegex(CohortError, 'insufficient_eligible_class_records'):
            select_sources(factory(values), census, set(), QUOTAS, LABELS)

    def test_selected_oversize_article_rejects_whole_cohort_without_resampling(self):
        values = source()
        _, baseline, _ = prepare(values)
        selected_id = baseline['test'][0]['id']
        index = int(selected_id.rsplit('-', 1)[1])
        values['test'][index]['content'] = 'authored ' + 'x' * 12_001
        census, roles, _ = prepare(values)
        self.assertIn(selected_id, {row['id'] for row in roles['test']})
        with self.assertRaisesRegex(CohortError, 'selected_text_limit'):
            check_cohort(roles, census, set(), QUOTAS, LABELS)
        self.assertEqual(sum(map(len, roles.values())), 6)


class ContractTests(unittest.TestCase):
    def test_groups_are_deterministic_singletons_and_declared_population_is_restricted(self):
        census, roles, _ = prepare()
        groups, summary = check_cohort(roles, census, set(), QUOTAS, LABELS)
        self.assertEqual(len(groups), 6)
        self.assertEqual(len(set(groups.values())), 6)
        for identifier_value, group in groups.items():
            self.assertEqual(group, 'dbp-scaled-group-' + hashlib.sha256(identifier_value.encode()).hexdigest())
        self.assertEqual(summary['max_component_rows'], 1)
        self.assertIn('globally_singleton', summary['selection_population'])
        self.assertFalse(summary['semantic_template_speaker_or_pretraining_independence_verified'])

    def test_role_character_and_file_size_caps_are_refusals(self):
        census, roles, _ = prepare()
        for constant in ('ROLE_CHARACTERS', 'ROLE_BYTES'):
            with patch('cohort.' + constant, {'training': 1, 'development': 1, 'test': 1}):
                with self.assertRaises(CohortError):
                    check_cohort(roles, census, set(), QUOTAS, LABELS)

    def test_prior_labeled_text_extraction_never_uses_label_value(self):
        expected = [('dbp-train-00000001', digest('Original prior text'))]
        for label in (None, True, 42, {'unused': ['a', 'b']}):
            raw = canonical({'id': expected[0][0], 'text': 'Original prior text', 'label': label})
            self.assertEqual(list(exposure_rows(raw, 'labeled_training_text')), expected)
        self.assertEqual(list(exposure_rows(canonical({'id': expected[0][0], 'text': 'Original prior text'}),
                                           'unlabeled_test_text')), expected)

    def test_prior_text_schema_json_unicode_and_ids_are_strict(self):
        invalid = [b'{"id":"dbp-test-00000001","text":"a","text":"b"}',
                   b'{"id":"dbp-test-00000001","text":NaN}',
                   b'{"id":"bad","text":"original"}',
                   b'{"id":"dbp-test-00000001","text":"escaped\\ud800"}',
                   b'{"id":"dbp-test-00000001","text":"original","gold":"A"}']
        for raw in invalid:
            with self.subTest(raw=raw[:40]), self.assertRaises((CohortError, UnicodeError)):
                list(exposure_rows(raw, 'unlabeled_test_text'))
        for exposure in ({'bad': '0' * 64}, {'dbp-test-00000001': True}):
            with self.assertRaises(CohortError):
                validate_exposures(exposure)

    def test_normalization_and_public_history_text_helpers(self):
        self.assertEqual(normalized('Ａ_B, Straße!  42'), 'a b strasse 42')
        self.assertTrue(fingerprints('Ａ B Straße') & fingerprints('a_b, STRASSE'))
        self.assertEqual(list(historical_texts(b'{"text":"authored words","label":"unused"}', 'jsonl')),
                         ['authored words'])
        cesu = b'{"Demo":[{"data":[{"text":"authored \xed\xa0\xbd\xed\xb8\x80"}]}]}'
        self.assertEqual(list(historical_texts(cesu, 'snips')), ['authored \U0001f600'])
        for raw in (b'{"text":"one","text":"two"}', b'{"text":false}'):
            with self.assertRaises(CohortError):
                list(historical_texts(raw, 'jsonl'))
        with self.assertRaises(CohortError):
            list(historical_texts(b'{}', 'pickle'))


if __name__ == '__main__':
    unittest.main()
