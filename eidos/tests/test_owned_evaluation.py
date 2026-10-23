"""Numerical and workflow integrity tests using authored fixtures only."""
import copy
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from eidos.evaluate_owned import EvaluationError, canonical, digest, prepare, predict, score
from eidos.owned_metrics import summarize
from eidos.train_linear_support import build as build_linear
from eidos.train_support import build as build_memory
from test_owned_training import PROVENANCE, fixture, raw


def cases():
    return [{'id': 'eval-01', 'text': 'please explain the details on my invoice today', 'label': 'billing_help'},
            {'id': 'eval-02', 'text': 'I would like a song for listening tonight', 'label': 'music_help'}]


class MetricArithmetic(unittest.TestCase):
    def test_hand_calculated_confusion_f1_nll_brier_and_ece(self):
        probabilities = [[.8, .2], [.4, .6], [.3, .7], [.9, .1]]
        gold = ['a', 'a', 'b', 'b']
        rows = [{'label': y, 'predicted': ['a', 'b'][max(range(2), key=lambda i: p[i])],
                 'probabilities': p, 'logits': [math.log(x) for x in p]} for p, y in zip(probabilities, gold)]
        result = summarize(rows, ['a', 'b'])
        self.assertEqual(result['confusion']['counts'], [[1, 1], [1, 1]])
        self.assertEqual(result['candidate_accuracy'], .5)
        self.assertEqual(result['macro_f1_fixed_offered_labels'], .5)
        self.assertAlmostEqual(result['negative_log_likelihood'], -math.log(.8 * .4 * .7 * .1) / 4)
        self.assertAlmostEqual(result['multiclass_brier_sum'], .65)
        self.assertAlmostEqual(result['ece_10_equal_width_bins'], .5)
        self.assertEqual(result['accepted_coverage'], 0)
        self.assertIsNone(result['selective_accuracy'])
        self.assertIsNone(result['confidence_intervals'])

    def test_absent_gold_class_counts_in_fixed_macro(self):
        rows = [{'label': 'a', 'predicted': 'a', 'probabilities': [.8, .1, .1],
                 'logits': [math.log(.8), math.log(.1), math.log(.1)]}]
        result = summarize(rows, ['a', 'b', 'c'])
        self.assertEqual(result['macro_f1_fixed_offered_labels'], 1 / 3)
        self.assertEqual([r['support'] for r in result['per_class']], [1, 0, 0])

    def test_zero_gold_probability_has_finite_logit_based_loss(self):
        result = summarize([{'label': 'b', 'predicted': 'a', 'probabilities': [1., 0.],
                             'logits': [1000., -1000.]}], ['a', 'b'])
        self.assertEqual(result['negative_log_likelihood'], 2000.)
        self.assertEqual(result['multiclass_brier_sum'], 2.)
        self.assertEqual(result['reliability'][-1]['n'], 1)


class OwnedEvaluation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.support = cls.root / 'support.jsonl'
        cls.provenance = cls.root / 'provenance.json'
        cls.support.write_bytes(raw(fixture()))
        cls.provenance.write_bytes(PROVENANCE)
        build_memory(cls.support, cls.provenance, cls.root / 'memory', 'owned-demo')
        build_linear(cls.support, cls.provenance, cls.root / 'linear', 'owned-demo', epochs=4)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setup_bundle(self, directory, values=None, method='memory'):
        root = Path(directory)
        evaluation = root / 'evaluation.jsonl'
        evaluation.write_bytes(raw(cases() if values is None else values))
        out = root / 'bundle'
        prepare(self.root / method, self.support, self.provenance, evaluation, self.provenance, out)
        return out

    def pipeline(self, directory, method='memory'):
        root = Path(directory)
        bundle = self.setup_bundle(root, method=method)
        predictions = root / 'predictions'
        predict(self.root / method, bundle / 'prediction-input', predictions)
        result = score(bundle / 'prediction-input/plan.json', bundle / 'gold-private', predictions, root / 'score')
        return bundle, predictions, result

    def test_both_owned_methods_complete_with_aggregate_only_private_output(self):
        for method in ('memory', 'linear'):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                bundle, predictions, result = self.pipeline(directory, method)
                self.assertTrue(result['complete'])
                self.assertEqual(result['metrics']['n'], 2)
                self.assertEqual(sum(map(sum, result['metrics']['confusion']['counts'])), 2)
                self.assertFalse(result['per_case_rows_written'])
                self.assertFalse(result['model_weights_or_source_text_read_for_scoring'])
                self.assertFalse(result['source_speaker_paraphrase_or_fresh_holdout_independence_verified'])
                self.assertEqual({p.name for p in (Path(directory) / 'score').iterdir()}, {'result.json'})
                for folder in (bundle, predictions, Path(directory) / 'score'):
                    self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
                    self.assertTrue(all(p.stat().st_mode & 0o777 == 0o400 for p in folder.rglob('*') if p.is_file()))
                self.assertNotIn('utterance', (Path(directory) / 'score/result.json').read_text())

    def test_identical_inputs_reproduce_plan_predictions_and_results(self):
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            a = self.pipeline(left)
            b = self.pipeline(right)
            for name in ('prediction-input/plan.json', 'prediction-input/inputs.jsonl', 'gold-private/gold.jsonl', 'gold-private/binding.json'):
                self.assertEqual((a[0] / name).read_bytes(), (b[0] / name).read_bytes())
            self.assertEqual((a[1] / 'predictions.jsonl').read_bytes(), (b[1] / 'predictions.jsonl').read_bytes())
            self.assertEqual((Path(left) / 'score/result.json').read_bytes(), (Path(right) / 'score/result.json').read_bytes())

    def test_prediction_needs_no_gold_and_scoring_needs_no_text_or_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.setup_bundle(root)
            gold = root / 'hidden-gold'
            (bundle / 'gold-private').rename(gold)
            predict(self.root / 'memory', bundle / 'prediction-input', root / 'predictions')
            (bundle / 'prediction-input/inputs.jsonl').unlink()
            with patch('eidos.evaluate_owned.registry', side_effect=AssertionError('scoring opened registry')):
                result = score(bundle / 'prediction-input/plan.json', gold, root / 'predictions', root / 'score')
            self.assertTrue(result['complete'])

    def test_support_id_or_normalized_text_overlap_refused(self):
        for replacement in [{'id': fixture()[0]['id']}, {'text': '  ' + fixture()[0]['text'].upper() + '!!! '}]:
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as directory:
                values = cases()
                values[0].update(replacement)
                with self.assertRaisesRegex(EvaluationError, 'support_evaluation'):
                    self.setup_bundle(directory, values)
                self.assertFalse((Path(directory) / 'bundle').exists())

    def test_duplicate_or_unknown_cases_refused_without_output(self):
        variants = []
        for field in ('id', 'text'):
            values = cases()
            values[1][field] = values[0][field]
            variants.append(values)
        values = cases()
        values[0]['label'] = 'untrained-label'
        variants.append(values)
        values = cases()
        values[0]['extra'] = 'unknown'
        variants.append(values)
        for values in variants:
            with self.subTest(values=values), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises((EvaluationError, ValueError)):
                    self.setup_bundle(directory, values)
                self.assertFalse((Path(directory) / 'bundle').exists())

    def test_support_source_hash_must_match_training_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed = root / 'changed-support.jsonl'
            changed.write_bytes(raw(list(reversed(fixture()))))
            evaluation = root / 'evaluation.jsonl'
            evaluation.write_bytes(raw(cases()))
            with self.assertRaisesRegex(EvaluationError, 'support_files_do_not_match'):
                prepare(self.root / 'memory', changed, self.provenance, evaluation, self.provenance, root / 'bundle')
            self.assertFalse((root / 'bundle').exists())

    def test_incomplete_or_wrong_training_receipt_flags_refused(self):
        for field, value in [('complete', False), ('format', 'wrong-receipt'), ('classes', True),
                             ('support_rows', 39), ('operational_acceptance_enabled', True)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                model = root / 'model'
                shutil.copytree(self.root / 'memory', model)
                path = model / 'TRAINING-RECEIPT.json'
                receipt = json.loads(path.read_bytes())
                receipt[field] = value
                path.chmod(0o600)
                path.write_bytes(canonical(receipt))
                evaluation = root / 'evaluation.jsonl'
                evaluation.write_bytes(raw(cases()))
                with self.assertRaisesRegex(EvaluationError, 'completed_owned_training_receipt_required'):
                    prepare(model, self.support, self.provenance, evaluation, self.provenance, root / 'bundle')
                self.assertFalse((root / 'bundle').exists())

    def test_changed_prediction_input_model_or_code_refused(self):
        for change in ('text', 'model', 'code'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle = self.setup_bundle(root)
                if change == 'text':
                    p = bundle / 'prediction-input/inputs.jsonl'
                    p.chmod(0o600)
                    p.write_bytes(p.read_bytes() + b' ')
                    context = patch.dict({}, {})
                elif change == 'code':
                    context = patch('eidos.evaluate_owned.code_bindings', return_value={})
                else:
                    context = patch.dict({}, {})
                with context, self.assertRaises(EvaluationError):
                    predict(self.root / ('linear' if change == 'model' else 'memory'), bundle / 'prediction-input', root / 'predictions')
                self.assertFalse((root / 'predictions').exists())

    def test_partial_duplicate_or_reordered_predictions_refused_even_if_rehashed(self):
        for variant in ('partial', 'duplicate', 'reordered'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle = self.setup_bundle(root)
                pred = root / 'predictions'
                predict(self.root / 'memory', bundle / 'prediction-input', pred)
                rows = [json.loads(line) for line in (pred / 'predictions.jsonl').read_bytes().splitlines()]
                rows = rows[:1] if variant == 'partial' else ([rows[0], rows[0]] if variant == 'duplicate' else list(reversed(rows)))
                raw_predictions = b''.join(canonical(row) + b'\n' for row in rows)
                (pred / 'predictions.jsonl').chmod(0o600)
                (pred / 'predictions.jsonl').write_bytes(raw_predictions)
                receipt = json.loads((pred / 'receipt.json').read_bytes())
                receipt['predictions_sha256'] = digest(raw_predictions)
                (pred / 'receipt.json').chmod(0o600)
                (pred / 'receipt.json').write_bytes(canonical(receipt))
                with self.assertRaises(EvaluationError):
                    score(bundle / 'prediction-input/plan.json', bundle / 'gold-private', pred, root / 'score')
                self.assertFalse((root / 'score').exists())

    def test_inconsistent_numeric_probabilities_and_review_policy_refused(self):
        for mutation in ('probabilities', 'prediction', 'policy', 'bool-logit'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle = self.setup_bundle(root)
                pred = root / 'predictions'
                predict(self.root / 'memory', bundle / 'prediction-input', pred)
                rows = [json.loads(line) for line in (pred / 'predictions.jsonl').read_bytes().splitlines()]
                if mutation == 'probabilities': rows[0]['probabilities'] = [.2, .2]
                if mutation == 'prediction': rows[0]['predicted'] = next(x for x in ['billing_help', 'music_help'] if x != rows[0]['predicted'])
                if mutation == 'policy': rows[0]['status'] = 'answered'
                if mutation == 'bool-logit': rows[0]['logits'][0] = True
                raw_predictions = b''.join(canonical(row) + b'\n' for row in rows)
                (pred / 'predictions.jsonl').chmod(0o600)
                (pred / 'predictions.jsonl').write_bytes(raw_predictions)
                receipt = json.loads((pred / 'receipt.json').read_bytes())
                receipt['predictions_sha256'] = digest(raw_predictions)
                (pred / 'receipt.json').chmod(0o600)
                (pred / 'receipt.json').write_bytes(canonical(receipt))
                with self.assertRaises(EvaluationError):
                    score(bundle / 'prediction-input/plan.json', bundle / 'gold-private', pred, root / 'score')

    def test_gold_and_prediction_receipt_tampering_refused(self):
        for item in ('gold', 'receipt'):
            with self.subTest(item=item), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle = self.setup_bundle(root)
                pred = root / 'predictions'
                predict(self.root / 'memory', bundle / 'prediction-input', pred)
                path = bundle / 'gold-private/gold.jsonl' if item == 'gold' else pred / 'receipt.json'
                path.chmod(0o600)
                path.write_bytes(path.read_bytes() + b' ')
                if item == 'receipt':
                    value = json.loads(path.read_bytes())
                    value['plan_sha256'] = '0' * 64
                    path.write_bytes(canonical(value))
                with self.assertRaises(EvaluationError):
                    score(bundle / 'prediction-input/plan.json', bundle / 'gold-private', pred, root / 'score')

    def test_existing_output_and_input_symlink_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.setup_bundle(root)
            out = root / 'preserve'
            out.mkdir()
            (out / 'sentinel').write_bytes(b'keep')
            with self.assertRaisesRegex(EvaluationError, 'output_already_exists'):
                predict(self.root / 'memory', bundle / 'prediction-input', out)
            self.assertEqual((out / 'sentinel').read_bytes(), b'keep')
            path = bundle / 'prediction-input/inputs.jsonl'
            original = root / 'original-inputs'
            path.rename(original)
            path.symlink_to(original)
            with self.assertRaises(OSError):
                predict(self.root / 'memory', bundle / 'prediction-input', root / 'predictions')

    def test_work_limit_refuses_before_preparation_output(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('eidos.evaluate_owned.MAX_WORK', 1):
                with self.assertRaisesRegex(EvaluationError, 'prediction_work_budget_exceeded'):
                    self.setup_bundle(directory)
            self.assertFalse((Path(directory) / 'bundle').exists())


if __name__ == '__main__':
    unittest.main()
