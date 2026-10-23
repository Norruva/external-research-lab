"""Sparse-trainer lineage and complete owned workflows; authored cases only."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from eidos.compare_owned import plan as comparison_plan, score as comparison_score
from eidos.evaluate_owned import (EvaluationError, canonical, digest, prepare,
                                  predict, registry, score)
from eidos.train_sparse import build
from test_owned_training import PROVENANCE, raw


def authored_training():
    # Unequal counts deliberately cross the old exact-20 boundary.
    return [{'id': f'train-{label}-{i}',
             'text': f'{stem} original training example number {i}', 'label': label}
            for label, stem, count in [('alpha', 'alpha invoice billing help', 31),
                                       ('beta', 'beta melody music help', 23)]
            for i in range(count)]


def authored_cases():
    return [{'id': 'eval-alpha', 'text': 'please review this invoice amount', 'label': 'alpha'},
            {'id': 'eval-beta', 'text': 'please play a melody for me', 'label': 'beta'}]


class SparseOwnedEvaluation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.training = cls.root / 'training.jsonl'
        cls.provenance = cls.root / 'provenance.json'
        cls.evaluation = cls.root / 'evaluation.jsonl'
        cls.training.write_bytes(raw(authored_training()))
        cls.provenance.write_bytes(PROVENANCE)
        cls.evaluation.write_bytes(raw(authored_cases()))
        for name, seed in [('left', 113), ('right', 114)]:
            build(cls.training, cls.provenance, cls.root / name, 'owned-sparse', epochs=2, seed=seed)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def bundle(self, root, *, model=None, values=None, name='bundle'):
        evaluation = root / (name + '-cases.jsonl')
        evaluation.write_bytes(raw(authored_cases() if values is None else values))
        out = root / name
        prepare(self.root / 'left' if model is None else model, self.training, self.provenance,
                evaluation, self.provenance, out)
        return out

    def copied_model(self, root):
        destination = root / 'model'
        shutil.copytree(self.root / 'left', destination)
        return destination

    @staticmethod
    def replace(path, value):
        path.chmod(0o600)
        path.write_bytes(canonical(value))

    def rebind_model(self, model, *, training_change=None, receipt_change=None):
        """Update legitimate byte bindings so tests exercise semantic checks."""
        metadata = json.loads((model / 'model.json').read_bytes())
        receipt = json.loads((model / 'TRAINING-RECEIPT.json').read_bytes())
        if training_change is not None:
            training_change(metadata['training'])
        if receipt_change is not None:
            receipt_change(receipt)
        self.replace(model / 'model.json', metadata)
        model_sha = digest((model / 'model.json').read_bytes())
        inventory = json.loads((model / 'registry.json').read_bytes())
        inventory['models'][0]['sha256'] = model_sha
        self.replace(model / 'registry.json', inventory)
        receipt['model']['sha256'] = model_sha
        receipt['files'] = {name: {'bytes': (model / name).stat().st_size,
                                   'sha256': digest((model / name).read_bytes())}
                            for name in ('model.json', 'registry.json', 'weights.f32')}
        self.replace(model / 'TRAINING-RECEIPT.json', receipt)

    def test_arbitrary_count_registry_uses_actual_prepare_predict_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.bundle(root)
            receipt = predict(self.root / 'left', bundle / 'prediction-input', root / 'predictions')
            self.assertIs(receipt['gold_input_read'], False)
            result = score(bundle / 'prediction-input/plan.json', bundle / 'gold-private',
                           root / 'predictions', root / 'score')
            self.assertTrue(result['complete'])
            self.assertEqual(result['metrics']['n'], 2)
            self.assertEqual(result['metrics']['accepted_coverage'], 0.)
            self.assertFalse(result['fit_calibration_promotion_or_rival_comparison_performed'])
            self.assertFalse(result['per_case_rows_written'])
            self.assertEqual({p.name for p in (root / 'score').iterdir()}, {'result.json'})
            for folder in (bundle, root / 'predictions', root / 'score'):
                self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o400
                                    for p in folder.rglob('*') if p.is_file()))

    def test_prediction_needs_no_gold_and_score_needs_no_training_text_or_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.bundle(root)
            gold = root / 'separated-gold'
            (bundle / 'gold-private').rename(gold)
            predict(self.root / 'left', bundle / 'prediction-input', root / 'predictions')
            (bundle / 'prediction-input/inputs.jsonl').unlink()
            with patch('eidos.evaluate_owned.registry', side_effect=AssertionError('unexpected registry read')):
                result = score(bundle / 'prediction-input/plan.json', gold, root / 'predictions', root / 'score')
            self.assertTrue(result['complete'])

    def test_receipt_counts_recipe_dimensions_and_boolean_aliases_refused(self):
        mutations = [('training_rows', True), ('training_rows', 53),
                     ('class_counts', {'alpha': 30, 'beta': 24}),
                     ('class_counts', {'alpha': True, 'beta': 53}),
                     ('class_counts', {'alpha': 31, 'other': 23}),
                     ('features', True), ('features', 1), ('nnz', False),
                     ('nnz', 10_000_001), ('epochs', True), ('seed', False),
                     ('training_recipe', 'a-different-recipe'),
                     ('checkpoint_selection', 'best_test_checkpoint'),
                     ('final_training_objective', True), ('privacy_guarantee', True)]
        for field, value in mutations:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                model = self.copied_model(Path(directory))
                self.rebind_model(model, receipt_change=lambda receipt: receipt.update({field: value}))
                with self.assertRaises(EvaluationError):
                    registry(model)

    def test_rebound_sparse_metadata_still_requires_fixed_recipe_and_valid_counts(self):
        mutations = [('training_rows', True), ('training_rows', 20_001),
                     ('class_counts', {'alpha': 31, 'beta': 22}),
                     ('class_counts', {'alpha': True, 'beta': 53}),
                     ('learning_rate', True), ('learning_rate', .7), ('epochs', 21),
                     ('seed', -1), ('l2', 0.), ('raw_training_text_embedded', True),
                     ('work_units', 1_000_000_001), ('final_training_objective', -1.),
                     ('provenance_sha256', 'invalid')]
        for field, value in mutations:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                model = self.copied_model(Path(directory))
                self.rebind_model(model, training_change=lambda training: training.update({field: value}),
                                  receipt_change=lambda receipt: receipt.update({field: value}))
                with self.assertRaises(EvaluationError):
                    registry(model)

    def test_consistent_forged_class_counts_refused_against_actual_training_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = self.copied_model(root)
            counts = {'alpha': 30, 'beta': 24}
            self.rebind_model(model, training_change=lambda training: training.update(class_counts=counts),
                              receipt_change=lambda receipt: receipt.update(class_counts=counts))
            # It is internally consistent until the actual declared source is supplied.
            registry(model)
            with self.assertRaisesRegex(EvaluationError, 'sparse_training_counts_do_not_match_source'):
                self.bundle(root, model=model)
            self.assertFalse((root / 'bundle').exists())

    def test_rebound_impossible_sparse_dimensions_and_work_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.copied_model(Path(directory))
            receipt = json.loads((model / 'TRAINING-RECEIPT.json').read_bytes())
            impossible_nnz = receipt['training_rows'] * receipt['features'] + 1
            self.rebind_model(model, receipt_change=lambda value: value.update(nnz=impossible_nnz))
            with self.assertRaisesRegex(EvaluationError, 'sparse_training_counts_or_dimensions_mismatch'):
                registry(model)
        with tempfile.TemporaryDirectory() as directory:
            model = self.copied_model(Path(directory))
            self.rebind_model(model, training_change=lambda value: value.update(work_units=value['work_units'] + 1),
                              receipt_change=lambda value: value.update(work_units=value['work_units'] + 1))
            with self.assertRaisesRegex(EvaluationError, 'sparse_training_work_mismatch'):
                registry(model)

    def test_old_receipt_and_training_tags_do_not_bypass_new_contract(self):
        for training_tag, receipt_tag in [
                ('eidos-caller-sparse-training-v1', 'eidos-owned-linear-training-receipt-v1'),
                ('eidos-caller-linear-training-v1', 'eidos-owned-sparse-training-receipt-v1'),
                ('eidos-caller-support-training-v1', 'eidos-owned-sparse-training-receipt-v1')]:
            with self.subTest(training=training_tag, receipt=receipt_tag), tempfile.TemporaryDirectory() as directory:
                model = self.copied_model(Path(directory))
                self.rebind_model(model, training_change=lambda training: training.update(format=training_tag),
                                  receipt_change=lambda receipt: receipt.update(format=receipt_tag))
                with self.assertRaises(EvaluationError):
                    registry(model)

    def test_overlap_after_twentieth_training_row_is_rejected(self):
        source = authored_training()[25]
        for field in ('id', 'text'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                values = copy.deepcopy(authored_cases())
                values[0][field] = source[field] if field == 'id' else '  ' + source[field].upper() + '!!! '
                with self.assertRaisesRegex(EvaluationError, 'support_evaluation_id_or_normalized_text_overlap'):
                    self.bundle(root, values=values)
                self.assertFalse((root / 'bundle').exists())

    def test_changed_training_bytes_refused_even_with_unchanged_numerical_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reordered = root / 'reordered.jsonl'
            reordered.write_bytes(raw(list(reversed(authored_training()))))
            with self.assertRaisesRegex(EvaluationError, 'support_files_do_not_match_model_lineage'):
                prepare(self.root / 'left', reordered, self.provenance, self.evaluation,
                        self.provenance, root / 'bundle')
            self.assertFalse((root / 'bundle').exists())

    def test_new_training_does_not_relax_evaluation_work_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('eidos.evaluate_owned.MAX_WORK', 1):
                with self.assertRaisesRegex(EvaluationError, 'prediction_work_budget_exceeded'):
                    self.bundle(root)
            self.assertFalse((root / 'bundle').exists())

    def test_sparse_source_parser_uses_the_evaluation_phase_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('eidos.evaluate_owned.budget', side_effect=EvaluationError('phase_time_budget_exceeded')) as guard:
                with self.assertRaisesRegex(EvaluationError, 'phase_time_budget_exceeded'):
                    self.bundle(root)
            self.assertGreaterEqual(guard.call_count, 1)
            self.assertFalse((root / 'bundle').exists())

    def test_distinct_sparse_models_work_with_existing_paired_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundles = {name: self.bundle(root, model=self.root / name, name=name + '-bundle')
                       for name in ('left', 'right')}
            groups = root / 'groups.jsonl'
            groups.write_bytes(raw([{'id': row['id'], 'group_id': row['id']}
                                    for row in authored_cases()]))
            comparison_plan(bundles['left'] / 'prediction-input/plan.json',
                            bundles['right'] / 'prediction-input/plan.json', groups,
                            self.provenance, root / 'comparison', replicates=200)
            for name in ('left', 'right'):
                predict(self.root / name, bundles[name] / 'prediction-input', root / (name + '-predictions'))
            result = comparison_score(root / 'comparison', bundles['left'] / 'gold-private',
                                      bundles['right'] / 'gold-private', root / 'left-predictions',
                                      root / 'right-predictions', root / 'paired-result')
            self.assertTrue(result['complete'])
            self.assertEqual(result['n'], 2)
            self.assertEqual(result['accepted_coverage'], 0.)
            self.assertFalse(result['rival_or_general_superiority_certified'])


if __name__ == '__main__':
    unittest.main()
