"""Independent paired arithmetic and full artifact checks; authored fixtures only."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from eidos.compare_owned import group_rows, load_comparison, plan, score, settings
from eidos.evaluate_owned import EvaluationError, canonical, digest, prepare, predict
from eidos.paired_metrics import aggregate, blocks, bootstrap, percentile
from eidos.train_linear_support import build
from test_owned_training import PROVENANCE, fixture, raw


def evaluation():
    return [{'id': 'case-01', 'text': 'please explain the total on my invoice today', 'label': 'billing_help'},
            {'id': 'case-02', 'text': 'this payment receipt needs a correction please', 'label': 'billing_help'},
            {'id': 'case-03', 'text': 'I would like a song for listening tonight', 'label': 'music_help'},
            {'id': 'case-04', 'text': 'can you find a relaxing tune to play', 'label': 'music_help'}]


def grouping():
    # Mixed-label group and unequal group sizes deliberately exercise cluster semantics.
    return [{'id': row['id'], 'group_id': 'group-01' if i < 3 else 'group-02'}
            for i, row in enumerate(evaluation())]


class PairedArithmetic(unittest.TestCase):
    def rows(self):
        gold = ['a', 'a', 'b', 'b']
        left = [{'id': str(i), 'label': y, 'predicted': p} for i, (y, p) in enumerate(zip(gold, ['a', 'b', 'b', 'a']))]
        right = [{'id': str(i), 'label': y, 'predicted': 'b'} for i, y in enumerate(gold)]
        return left, right

    def test_independent_hand_calculated_paired_counts_and_point_metrics(self):
        left, right = self.rows()
        items, paired = blocks(left, right, ['one', 'one', 'one', 'two'], ['a', 'b'])
        a, b = aggregate(items, {0: 1, 1: 1}, 2)
        self.assertEqual(paired, dict.fromkeys(['both_correct', 'left_only_correct', 'right_only_correct', 'both_wrong'], 1))
        self.assertEqual(a['candidate_accuracy'], .5)
        self.assertEqual(b['candidate_accuracy'], .5)
        self.assertEqual(a['macro_f1_fixed_offered_labels'], .5)
        self.assertAlmostEqual(b['macro_f1_fixed_offered_labels'], 1 / 3)

    def test_all_four_cluster_resamples_match_exact_enumeration(self):
        left, right = self.rows()
        sequence = iter([0, 0, 0, 1, 1, 0, 1, 1])
        rng = type('Draw', (), {'randrange': lambda self, n: next(sequence)})()
        with patch('eidos.paired_metrics.random.Random', return_value=rng):
            result = bootstrap(left, right, ['one', 'one', 'one', 'two'], ['a', 'b'], 4, 1, lambda: None)
        accuracy = result['differences']['candidate_accuracy']
        macro = result['differences']['macro_f1_fixed_offered_labels']
        self.assertEqual(accuracy['left_minus_right'], 0.)
        self.assertAlmostEqual(macro['left_minus_right'], 1 / 6)
        for actual, expected in zip(accuracy['individual_approximate_95_percent_interval'], [-.925, .925 / 3]):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(macro['individual_approximate_95_percent_interval'], [-.45, 191 / 480]):
            self.assertAlmostEqual(actual, expected)
        self.assertFalse(result['numeric_gates']['both_pass'])
        self.assertEqual((result['minimum_group_rows'], result['maximum_group_rows']), (1, 3))

    def test_absent_classes_count_and_multiplicity_preserves_whole_group(self):
        left, right = self.rows()
        items, _ = blocks(left, right, ['one', 'one', 'one', 'two'], ['a', 'b', 'c'])
        a, b = aggregate(items, {0: 2}, 3)
        self.assertEqual(a['n'], 6)
        self.assertAlmostEqual(a['candidate_accuracy'], 2 / 3)
        self.assertAlmostEqual(a['macro_f1_fixed_offered_labels'], 4 / 9)
        self.assertAlmostEqual(b['macro_f1_fixed_offered_labels'], 1 / 6)

    def test_seed_reproducibility_and_arm_reversal(self):
        left, right = self.rows()
        args = (['one', 'one', 'one', 'two'], ['a', 'b'], 200, 113, lambda: None)
        a = bootstrap(left, right, *args)
        self.assertEqual(a, bootstrap(left, right, *args))
        b = bootstrap(right, left, *args)
        for key in a['differences']:
            self.assertAlmostEqual(a['differences'][key]['left_minus_right'], -b['differences'][key]['left_minus_right'])
            low, high = a['differences'][key]['individual_approximate_95_percent_interval']
            rev = b['differences'][key]['individual_approximate_95_percent_interval']
            self.assertAlmostEqual(low, -rev[1])
            self.assertAlmostEqual(high, -rev[0])

    def test_identical_predictions_have_zero_intervals_and_fail_strict_gate(self):
        left, _ = self.rows()
        result = bootstrap(left, left, ['one', 'one', 'two', 'two'], ['a', 'b'], 200, 113, lambda: None)
        self.assertFalse(result['numeric_gates']['both_pass'])
        for value in result['differences'].values():
            self.assertEqual(value['left_minus_right'], 0)
            self.assertEqual(value['individual_approximate_95_percent_interval'], [0., 0.])
        self.assertEqual(percentile([3, 1, 2, 4], .5), 2.5)

    def test_positive_cohort_gate_never_requires_a_draw_to_drop_cases_or_classes(self):
        left, right = self.rows()
        for a, b in zip(left, right):
            a['predicted'] = a['label']
            b['predicted'] = 'a' if b['label'] == 'b' else 'b'
        result = bootstrap(left, right, ['one', 'one', 'two', 'two'], ['a', 'b'], 200, 113, lambda: None)
        self.assertTrue(result['numeric_gates']['both_pass'])
        self.assertEqual(result['differences']['candidate_accuracy']['individual_approximate_95_percent_interval'], [1., 1.])


class OwnedComparison(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.support = cls.root / 'support.jsonl'
        cls.provenance = cls.root / 'provenance.json'
        cls.evaluation = cls.root / 'evaluation.jsonl'
        cls.groups = cls.root / 'groups.jsonl'
        cls.support.write_bytes(raw(fixture()))
        cls.provenance.write_bytes(PROVENANCE)
        cls.evaluation.write_bytes(raw(evaluation()))
        cls.groups.write_bytes(raw(grouping()))
        for method in ('gradient_descent', 'adam'):
            build(cls.support, cls.provenance, cls.root / method, method, optimizer=method, epochs=3)
            prepare(cls.root / method, cls.support, cls.provenance, cls.evaluation, cls.provenance,
                    cls.root / (method + '-bundle'))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setup_plan(self, directory, **kwargs):
        out = Path(directory) / 'comparison'
        plan(self.root / 'adam-bundle/prediction-input/plan.json',
             self.root / 'gradient_descent-bundle/prediction-input/plan.json',
             self.groups, self.provenance, out, replicates=200, **kwargs)
        return out

    def pipeline(self, directory):
        root = Path(directory)
        comparison = self.setup_plan(directory)
        for method in ('adam', 'gradient_descent'):
            predict(self.root / method, self.root / (method + '-bundle/prediction-input'), root / method)
        result = score(comparison, self.root / 'adam-bundle/gold-private',
                       self.root / 'gradient_descent-bundle/gold-private', root / 'adam',
                       root / 'gradient_descent', root / 'result')
        return comparison, result

    def replace(self, path, value):
        path.chmod(0o600)
        path.write_bytes(canonical(value) + b'\n')

    def test_actual_models_complete_with_private_aggregate_only_output(self):
        with tempfile.TemporaryDirectory() as directory:
            comparison, result = self.pipeline(directory)
            self.assertTrue(result['complete'])
            self.assertEqual(result['n'], 4)
            self.assertEqual(sum(result['paired_correctness_counts'].values()), 4)
            self.assertEqual(result['groups'], 2)
            for name in ('model_weights_or_source_case_text_read', 'per_case_or_per_group_rows_written',
                         'rival_or_general_superiority_certified', 'production_certified',
                         'group_independence_or_exchangeability_verified', 'simultaneous_or_global_coverage_guarantee',
                         'action_authorized', 'inference_fit_calibration_or_promotion_performed'):
                self.assertIs(result[name], False)
            self.assertEqual(result['accepted_coverage'], 0.)
            self.assertEqual({p.name for p in (Path(directory) / 'result').iterdir()}, {'result.json'})
            for root in (comparison, Path(directory) / 'result'):
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o400 for p in root.iterdir()))
            data = (Path(directory) / 'result/result.json').read_text()
            self.assertNotIn('case-01', data)
            self.assertNotIn('group-01', data)
            self.assertNotIn('invoice', data)

    def test_identical_inputs_reproduce_all_bundle_and_result_bytes(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            a, ar = self.pipeline(one)
            b, br = self.pipeline(two)
            self.assertEqual(ar, br)
            for path in a.iterdir():
                self.assertEqual(path.read_bytes(), (b / path.name).read_bytes())
            self.assertEqual((Path(one) / 'result/result.json').read_bytes(), (Path(two) / 'result/result.json').read_bytes())

    def test_group_order_canonicalized_and_complete_membership_required(self):
        rows = grouping()
        ids = [row['id'] for row in rows]
        self.assertEqual(group_rows(raw(list(reversed(rows))), ids), rows)
        for bad in (rows[:3], rows + [{'id': 'extra', 'group_id': 'group-03'}], [rows[0], *rows[:3]],
                    [{**row, 'group_id': 'only'} for row in rows], [{**rows[0], 'group_id': True}, *rows[1:]],
                    [{**rows[0], 'unknown': 'x'}, *rows[1:]]):
            with self.assertRaises((EvaluationError, ValueError)):
                group_rows(raw(bad), ids)
        with self.assertRaises(EvaluationError):
            group_rows(raw(list(reversed(rows))), ids, ordered=True)

    def test_same_model_and_mismatched_case_candidate_or_support_scope_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.root / 'adam-bundle/prediction-input/plan.json'
            with self.assertRaisesRegex(EvaluationError, 'distinct_frozen'):
                plan(source, source, self.groups, self.provenance, root / 'bad')
            original = json.loads((self.root / 'gradient_descent-bundle/prediction-input/plan.json').read_bytes())
            mismatches = [(key, '0' * 64) for key in ('input_sha256', 'support_sha256', 'support_provenance_sha256')]
            mismatches += [('labels', ['different_class', 'other_class']),
                           ('case_ids', ['other-01', 'other-02', 'other-03', 'other-04'])]
            for key, change in mismatches:
                value = copy.deepcopy(original)
                value[key] = change
                altered = root / (key + '.json')
                altered.write_bytes(canonical(value))
                with self.assertRaisesRegex(EvaluationError, 'scope_mismatch'):
                    plan(source, altered, self.groups, self.provenance, root / 'bad')
            self.assertFalse((root / 'bad').exists())

    def test_protocol_group_code_and_plan_tampering_fail_closed(self):
        variants = [('seed', True), ('replicates', 199), ('work_units', 1), ('n', True),
                    ('groups', 999), ('numeric_gate', 'accuracy_only'),
                    ('group_independence_or_exchangeability_verified', True), ('code_files', {})]
        for key, change in variants:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                out = self.setup_plan(directory)
                protocol = json.loads((out / 'comparison.json').read_bytes())
                protocol[key] = change
                self.replace(out / 'comparison.json', protocol)
                with self.assertRaises(EvaluationError):
                    load_comparison(out)
        for filename in ('left-plan.json', 'groups.jsonl', 'group-provenance.json'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                out = self.setup_plan(directory)
                path = out / filename
                path.chmod(0o600)
                path.write_bytes(path.read_bytes() + b' ')
                with self.assertRaises(EvaluationError):
                    load_comparison(out)

    def test_invalid_settings_and_work_bound_refuse(self):
        for replicates, seed in ((199, 1), (2001, 1), (True, 1), (200, True), (200, -1), (200, 2**32)):
            with self.assertRaises(EvaluationError):
                settings(replicates, seed, 4, 2, 2)
        with tempfile.TemporaryDirectory() as directory:
            with patch('eidos.compare_owned.MAX_WORK', 1):
                with self.assertRaisesRegex(EvaluationError, 'work_limit'):
                    self.setup_plan(directory)
            self.assertFalse((Path(directory) / 'comparison').exists())

    def test_partial_predictions_refused_even_with_rehashed_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comparison = self.setup_plan(directory)
            for method in ('adam', 'gradient_descent'):
                predict(self.root / method, self.root / (method + '-bundle/prediction-input'), root / method)
            path = root / 'adam/predictions.jsonl'
            data = path.read_bytes().splitlines()[0] + b'\n'
            path.chmod(0o600)
            path.write_bytes(data)
            receipt = json.loads((root / 'adam/receipt.json').read_bytes())
            receipt['predictions_sha256'] = digest(data)
            self.replace(root / 'adam/receipt.json', receipt)
            with self.assertRaisesRegex(EvaluationError, 'complete_ordered'):
                score(comparison, self.root / 'adam-bundle/gold-private',
                      self.root / 'gradient_descent-bundle/gold-private', root / 'adam', root / 'gradient_descent', root / 'bad')
            self.assertFalse((root / 'bad').exists())

    def test_different_gold_or_declared_source_refused_even_if_rebound(self):
        for field in ('gold', 'source'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                comparison = self.setup_plan(directory)
                for method in ('adam', 'gradient_descent'):
                    predict(self.root / method, self.root / (method + '-bundle/prediction-input'), root / method)
                changed = root / 'changed-gold'
                shutil.copytree(self.root / 'adam-bundle/gold-private', changed)
                binding = json.loads((changed / 'binding.json').read_bytes())
                if field == 'gold':
                    gold = [json.loads(line) for line in (changed / 'gold.jsonl').read_bytes().splitlines()]
                    gold[0]['label'] = 'music_help'
                    data = raw(gold)
                    (changed / 'gold.jsonl').chmod(0o600)
                    (changed / 'gold.jsonl').write_bytes(data)
                    binding['gold_sha256'] = digest(data)
                else:
                    binding['evaluation_input_sha256'] = '0' * 64
                self.replace(changed / 'binding.json', binding)
                with self.assertRaisesRegex(EvaluationError, 'paired_gold_or_source'):
                    score(comparison, changed, self.root / 'gradient_descent-bundle/gold-private',
                          root / 'adam', root / 'gradient_descent', root / 'bad')
                self.assertFalse((root / 'bad').exists())

    def test_no_overwrite_symlink_and_budget_refusals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = self.setup_plan(directory)
            before = {p.name: p.read_bytes() for p in out.iterdir()}
            with self.assertRaisesRegex(EvaluationError, 'output_already_exists'):
                self.setup_plan(directory)
            self.assertEqual(before, {p.name: p.read_bytes() for p in out.iterdir()})
            original = root / 'original-groups'
            (out / 'groups.jsonl').rename(original)
            (out / 'groups.jsonl').symlink_to(original)
            with self.assertRaises(OSError):
                load_comparison(out)
        with tempfile.TemporaryDirectory() as directory:
            with patch('eidos.compare_owned.budget', side_effect=EvaluationError('budget')):
                with self.assertRaisesRegex(EvaluationError, 'budget'):
                    self.setup_plan(directory)
            self.assertFalse((Path(directory) / 'comparison').exists())


if __name__ == '__main__':
    unittest.main()
