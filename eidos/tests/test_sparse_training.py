"""Original authored fixtures and independent dense checks for sparse training.

These tests verify numerical and application mechanics, not corpus quality or a
comparison with another classifier. No benchmark data or pretrained model is read.
"""
from array import array
import collections
import hashlib
import json
import math
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from eidos.contracts import ContractError
from eidos.engine import Engine
from eidos.model import canonical
from eidos import train_sparse as sparse
from eidos.train_support import TrainingError


PROVENANCE = canonical({
    'source': 'Original authored sparse training mechanics fixtures',
    'license': 'CC0-1.0',
    'citation': 'EIDOS independent numerical and application tests',
    'rights_basis': 'Original authored examples; no benchmark, private or rival data',
})


def fixture():
    """Unequal class counts, including a class larger than the old twenty-row quota."""
    result = []
    for label, stem, count in (
            ('invoice_help', 'help with invoice payment reference', 23),
            ('music_help', 'play my song music selection', 7),
            ('plant_help', 'describe a flowering plant leaf', 2)):
        for number in range(count):
            result.append({'id': f'{label}-{number:03d}',
                           'text': f'{stem} original example number {number}',
                           'label': label})
    return result


def raw(rows):
    return b''.join(canonical(row) + b'\n' for row in rows)


def dense_step(parameters, vector, gold, classes, width, rate, l2):
    """Independent reference: directly decay every dense weight each example."""
    bias = classes * width
    logits = [parameters[bias + c] + sum(parameters[c * width + j] * vector[j]
                                        for j in range(width)) for c in range(classes)]
    peak = max(logits)
    masses = [math.exp(z - peak) for z in logits]
    probabilities = [mass / sum(masses) for mass in masses]
    result = list(parameters)
    for c in range(classes):
        residual = probabilities[c] - int(c == gold)
        for j in range(width):
            i = c * width + j
            result[i] = parameters[i] * (1 - rate * l2) - rate * residual * vector[j]
        result[bias + c] = parameters[bias + c] - rate * residual
    return result


def dense_objective(parameters, examples, classes, width, l2):
    losses = []
    bias = classes * width
    for vector, gold in examples:
        logits = [parameters[bias + c] + sum(parameters[c * width + j] * vector[j]
                                            for j in range(width)) for c in range(classes)]
        peak = max(logits)
        losses.append(peak - logits[gold] + math.log(sum(math.exp(z - peak) for z in logits)))
    return sum(losses) / len(losses) + l2 / 2 * sum(p * p for p in parameters[:bias])


class SparseTraining(unittest.TestCase):
    def files(self, directory, rows=None):
        root = Path(directory)
        data, provenance = root / 'train.jsonl', root / 'provenance.json'
        data.write_bytes(raw(fixture() if rows is None else rows))
        provenance.write_bytes(PROVENANCE)
        return data, provenance

    def test_lazy_updates_match_dense_updates_including_rebase_and_untouched_columns(self):
        classes, width = 3, 4
        weights = array('d', [.2, -.4, .7, .1, -.3, .6, .8, -.9, .5, .4, -.2, .3,
                              .11, -.12, .13])
        scale = .3
        expected = [value * scale for value in weights[:classes * width]] + list(weights[classes * width:])
        for indices, values, gold, rate in (
                ([0, 2], [.6, .8], 2, .8),
                ([1], [1.], 0, .4),
                ([2], [.5], 1, .3)):
            vector = [0.] * width
            for index, value in zip(indices, values):
                vector[index] = value
            expected = dense_step(expected, vector, gold, classes, width, rate, .7)
            scale = sparse._sgd_step(weights, indices, values, gold, classes, width,
                                     scale, rate, l2=.7)
            actual = [value * scale for value in weights[:classes * width]] + list(weights[classes * width:])
            for left, right in zip(actual, expected):
                self.assertAlmostEqual(left, right, delta=3e-15)
            if rate == .8:
                self.assertEqual(scale, 1., 'first update must exercise the lazy-scale rebase')

    def test_unpenalized_biases_and_extreme_logits_remain_finite(self):
        weights = array('d', [900., -900., 7., -3.])
        scale = sparse._sgd_step(weights, [0], [1.], 1, 2, 1, 1., .5, l2=.2)
        self.assertTrue(all(math.isfinite(value) for value in weights))
        self.assertAlmostEqual(weights[0] * scale, 809.5)
        self.assertAlmostEqual(weights[1] * scale, -809.5)
        self.assertEqual(list(weights[2:]), [6.5, -2.5])

    def test_complete_fit_matches_independent_dense_schedule_and_objective(self):
        examples = sparse.SparseExamples(array('I', [0, 1, 2, 4]), array('I', [0, 1, 0, 1]),
                                         array('d', [1., 1., .6, .8]), array('I', [0, 1, 0]))
        dense = [([1., 0.], 0), ([0., 1.], 1), ([.6, .8], 0)]
        expected = [0.] * 6
        order = list(range(3))
        rng = random.Random(19)
        step = 0
        for _ in range(3):
            rng.shuffle(order)
            for row in order:
                rate = .5 / math.sqrt(1 + step / 3)
                expected = dense_step(expected, *dense[row], 2, 2, rate, .0001)
                step += 1
        fitted, initial, final = sparse._fit(examples, 2, 2, 3, 19)
        for value, independent in zip(fitted, expected):
            self.assertAlmostEqual(value, independent, delta=3e-15)
        self.assertAlmostEqual(initial, math.log(2), delta=1e-15)
        self.assertAlmostEqual(final, dense_objective(expected, dense, 2, 2, .0001), delta=1e-14)

    def test_arbitrary_unequal_counts_and_canonical_row_order(self):
        rows, provenance = sparse.parse_training(raw(list(reversed(fixture()))), PROVENANCE)
        self.assertEqual(rows, sorted(fixture(), key=lambda row: (row['label'], row['id'])))
        self.assertEqual(collections.Counter(row['label'] for row in rows),
                         {'invoice_help': 23, 'music_help': 7, 'plant_help': 2})
        self.assertEqual(provenance, json.loads(PROVENANCE))
        minimum = [{'id': 'a', 'text': 'original alpha document', 'label': 'A'},
                   {'id': 'b', 'text': 'original beta document', 'label': 'B'}]
        self.assertEqual(len(sparse.parse_training(raw(minimum), PROVENANCE)[0]), 2)

    def test_authored_text_above_old_aggregate_limit_is_accepted(self):
        rows = fixture()
        for row in rows:
            row['text'] += ' original authored phrase' * 300
        self.assertGreater(sum(len(row['text']) for row in rows), 200_000)
        self.assertEqual(len(sparse.parse_training(raw(rows), PROVENANCE)[0]), len(rows))

    def test_csr_vectors_match_exported_inference_transform(self):
        rows, _ = sparse.parse_training(raw(fixture()), PROVENANCE)
        labels, vocabulary, idf, examples = sparse._prepare(rows)
        self.assertEqual(examples.nrows, len(rows))
        self.assertEqual(examples.nnz, len(examples.indices))
        self.assertEqual(examples.offsets[-1], examples.nnz)
        self.assertEqual(examples.values.typecode, 'd')
        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            output = Path(directory) / 'model'
            sparse.build(data, provenance, output, 'owned-sparse', epochs=1)
            model = Engine.from_registry(output).models['owned-sparse']
            self.assertEqual(model.data['labels'], labels)
            self.assertEqual(model.data['vocabulary'], vocabulary)
            self.assertEqual(model.data['idf'], idf)
            for i, row in enumerate(rows):
                begin, end = examples.offsets[i:i + 2]
                actual = dict(zip(examples.indices[begin:end], examples.values[begin:end]))
                self.assertEqual(actual, model.vector(row['text']))
                self.assertEqual(examples.gold[i], labels.index(row['label']))
                self.assertAlmostEqual(sum(value * value for value in actual.values()), 1., delta=1e-14)

    def test_actual_registry_and_engine_preserve_human_review(self):
        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            output = Path(directory) / 'model'
            receipt = sparse.build(data, provenance, output, 'owned-sparse', epochs=2)
            engine = Engine.from_registry(output, tenants=('demo',))
            model = engine.models['owned-sparse']
            self.assertEqual(receipt['format'], 'eidos-owned-sparse-training-receipt-v1')
            self.assertEqual(receipt['training_rows'], 32)
            self.assertEqual(receipt['class_counts'], {'invoice_help': 23, 'music_help': 7, 'plant_help': 2})
            self.assertNotIn('per_class', model.data['training'])
            self.assertNotIn('support_rows', receipt)
            self.assertEqual(model.data['training']['format'], 'eidos-caller-sparse-training-v1')
            self.assertEqual(receipt['input_sha256'], hashlib.sha256(data.read_bytes()).hexdigest())
            self.assertEqual(receipt['provenance_sha256'], hashlib.sha256(PROVENANCE).hexdigest())
            self.assertIsNone(model.data['calibration'])
            self.assertTrue(model.data['consequential'])
            self.assertEqual(model.data['thresholds']['probability'], 1.)
            self.assertEqual(model.data['thresholds']['margin'], 1.)
            request = {'schema_version': '1.0', 'input_version': 'authored-only', 'request_id': 'sparse-mechanics',
                       'tenant_id': 'demo', 'language': 'en', 'state': {'utterance': 'play a song'}, 'sources': [],
                       'questions': [{'id': 'q', 'kind': 'CHOICE', 'profile': 'owned-sparse', 'prompt': 'Choose intent',
                                      'state_path': ['utterance'], 'choices': [{'id': x, 'class_id': x}
                                                                           for x in model.data['labels']]}]}
            response = engine.decide(request)
            decision = response['decisions'][0]
            self.assertEqual(decision['status'], 'abstained')
            self.assertIsNone(decision['value'])
            self.assertIsNone(decision['calibrated_confidence'])
            self.assertTrue(decision['review_required'])
            self.assertEqual(response['authorization'], 'recommendation_only_no_action_authorized')
            for key in ('evaluation_or_calibration_performed', 'operational_acceptance_enabled',
                        'model_promoted', 'third_party_training_or_inference_packages'):
                self.assertIs(receipt[key], False)

    def test_repeat_build_and_input_reordering_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            root = Path(directory)
            sparse.build(data, provenance, root / 'a', 'owned-sparse', epochs=2, seed=71)
            sparse.build(data, provenance, root / 'b', 'owned-sparse', epochs=2, seed=71)
            for path in (root / 'a').iterdir():
                self.assertEqual(path.read_bytes(), (root / 'b' / path.name).read_bytes())
            data.write_bytes(raw(list(reversed(fixture()))))
            sparse.build(data, provenance, root / 'c', 'owned-sparse', epochs=2, seed=71)
            self.assertEqual((root / 'a/weights.f32').read_bytes(), (root / 'c/weights.f32').read_bytes())
            left = json.loads((root / 'a/model.json').read_bytes())
            right = json.loads((root / 'c/model.json').read_bytes())
            self.assertNotEqual(left['training']['input_sha256'], right['training']['input_sha256'])
            self.assertEqual(left['vocabulary'], right['vocabulary'])
            self.assertEqual(left['idf'], right['idf'])
            sparse.build(data, provenance, root / 'd', 'owned-sparse', epochs=2, seed=72)
            self.assertNotEqual((root / 'c/weights.f32').read_bytes(), (root / 'd/weights.f32').read_bytes())

    def test_private_intermediate_files_even_with_permissive_caller_umask(self):
        original_writer = sparse.write_model
        checked = []

        def checked_writer(directory, *args, **kwargs):
            model = original_writer(directory, *args, **kwargs)
            for path in Path(directory).iterdir():
                self.assertEqual(path.stat().st_mode & 0o077, 0, path.name)
            checked.append(True)
            return model

        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            output = Path(directory) / 'model'
            old_mask = os.umask(0)
            try:
                with patch('eidos.train_sparse.write_model', checked_writer):
                    sparse.build(data, provenance, output, 'owned-sparse', epochs=1)
            finally:
                os.umask(old_mask)
            self.assertEqual(checked, [True])
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertEqual({p.name for p in output.iterdir()},
                             {'model.json', 'weights.f32', 'registry.json', 'TRAINING-RECEIPT.json'})
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o400 for path in output.iterdir()))

    def test_duplicate_ids_and_normalized_text_are_refused(self):
        for key in ('id', 'text'):
            values = fixture()
            values[1][key] = values[0][key]
            if key == 'text':
                values[1][key] = '  ' + values[0][key].upper() + '!!! '
            with self.assertRaises(TrainingError):
                sparse.parse_training(raw(values), PROVENANCE)

    def test_strict_schema_json_and_provenance(self):
        values = fixture()
        values[0]['unknown'] = True
        with self.assertRaises(TrainingError):
            sparse.parse_training(raw(values), PROVENANCE)
        duplicate = b'{"id":"a","id":"b","text":"alpha words","label":"A"}\n' + raw(fixture())
        with self.assertRaises(ContractError):
            sparse.parse_training(duplicate, PROVENANCE)
        with self.assertRaises(TrainingError):
            sparse.parse_training(raw(fixture()) + b'\n', PROVENANCE)
        for declaration in (b'{}', PROVENANCE.replace(b'"source":', b'"source":"x","source":'),
                            canonical({'source': ' ', 'license': 'CC0-1.0', 'citation': 'a', 'rights_basis': 'b'})):
            with self.assertRaises((TrainingError, ContractError)):
                sparse.parse_training(raw(fixture()), declaration)

    def test_invalid_unicode_nonfinite_json_and_empty_words_are_refused(self):
        values = fixture()
        values[0]['text'] = '🧬🛰🫧'
        with self.assertRaises(TrainingError):
            sparse.parse_training(raw(values), PROVENANCE)
        malformed = raw(fixture()).replace(b'help with invoice', b'help\xffwith invoice', 1)
        with self.assertRaises(UnicodeError):
            sparse.parse_training(malformed, PROVENANCE)
        escaped = raw(fixture()).replace(b'help with invoice', b'help\\ud800with invoice', 1)
        with self.assertRaises(UnicodeError):
            sparse.parse_training(escaped, PROVENANCE)
        invalid = b'{"id":"a","text":NaN,"label":"A"}\n' + raw(fixture())
        with self.assertRaises(ContractError):
            sparse.parse_training(invalid, PROVENANCE)

    def test_row_class_input_and_aggregate_bounds_are_enforced(self):
        with self.assertRaises(TrainingError):
            sparse.parse_training(raw(fixture()[:1]), PROVENANCE)
        one_class = [row for row in fixture() if row['label'] == 'invoice_help']
        with self.assertRaises(TrainingError):
            sparse.parse_training(raw(one_class), PROVENANCE)
        with patch('eidos.train_sparse.MAX_ROWS', 31):
            with self.assertRaises(TrainingError):
                sparse.parse_training(raw(fixture()), PROVENANCE)
        with patch('eidos.train_sparse.MAX_CLASSES', 2):
            with self.assertRaises(TrainingError):
                sparse.parse_training(raw(fixture()), PROVENANCE)
        with patch('eidos.train_sparse.MAX_INPUT', 10):
            with self.assertRaises(TrainingError):
                sparse.parse_training(raw(fixture()), PROVENANCE)
        with patch('eidos.train_sparse.MAX_TEXT', 100):
            with self.assertRaises(TrainingError):
                sparse.parse_training(raw(fixture()), PROVENANCE)

    def test_feature_nnz_work_and_time_limits_refuse_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            output = Path(directory) / 'model'
            for constant in ('MAX_OBSERVED_TERMS', 'MAX_NNZ', 'MAX_WORK_UNITS', 'CPU_SECONDS', 'WALL_SECONDS'):
                with self.subTest(bound=constant), patch.object(sparse, constant, 0):
                    with self.assertRaises(TrainingError):
                        sparse.build(data, provenance, output, 'owned-sparse', epochs=1)
                    self.assertFalse(output.exists())

    def test_existing_output_and_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            root = Path(directory)
            output = root / 'keep'
            output.mkdir()
            (output / 'sentinel').write_bytes(b'preserve caller material')
            with self.assertRaises(TrainingError):
                sparse.build(data, provenance, output, 'owned-sparse')
            self.assertEqual((output / 'sentinel').read_bytes(), b'preserve caller material')
            self.assertEqual(len(list(output.iterdir())), 1)
            alias = root / 'input-alias'
            alias.symlink_to(data)
            with self.assertRaises((OSError, TrainingError, ContractError)):
                sparse.build(alias, provenance, root / 'out', 'owned-sparse')
            missing = root / 'output-alias'
            missing.symlink_to(root / 'missing')
            with self.assertRaises(TrainingError):
                sparse.build(data, provenance, missing, 'owned-sparse')
            parent_alias = root / 'parent-alias'
            parent_alias.symlink_to(root, target_is_directory=True)
            with self.assertRaises(TrainingError):
                sparse.build(data, provenance, parent_alias / 'out', 'owned-sparse')
            self.assertFalse((root / 'out').exists())

    def test_invalid_identity_epochs_and_seed_refuse_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            output = Path(directory) / 'out'
            for identifier in ('', '../escape', 'bad name', 'a' * 129):
                with self.assertRaises(TrainingError):
                    sparse.build(data, provenance, output, identifier)
            for epochs in (0, 21, True, 1.5):
                with self.assertRaises(TrainingError):
                    sparse.build(data, provenance, output, 'owned-sparse', epochs=epochs)
            for seed in (-1, 2 ** 32, True, .5):
                with self.assertRaises(TrainingError):
                    sparse.build(data, provenance, output, 'owned-sparse', seed=seed)
            self.assertFalse(output.exists())

    def test_nonfinite_fitted_parameters_are_refused_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            output = Path(directory) / 'model'
            real_fit = sparse._fit

            def poisoned_fit(*args, **kwargs):
                weights, initial, final = real_fit(*args, **kwargs)
                weights[0] = float('nan')
                return weights, initial, final

            with patch('eidos.train_sparse._fit', poisoned_fit):
                with self.assertRaises((TrainingError, ContractError, ValueError)):
                    sparse.build(data, provenance, output, 'owned-sparse', epochs=1)
            self.assertFalse(output.exists())

    def test_artifact_permission_failure_does_not_publish_completion_receipt(self):
        original_chmod = Path.chmod

        def fail_artifact_chmod(path, mode, *args, **kwargs):
            if path.name == 'weights.f32':
                raise OSError('Injected artifact permission failure')
            return original_chmod(path, mode, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            output = Path(directory) / 'model'
            with patch.object(Path, 'chmod', autospec=True, side_effect=fail_artifact_chmod):
                with self.assertRaises(OSError):
                    sparse.build(data, provenance, output, 'owned-sparse', epochs=1)
            self.assertTrue(output.is_dir())
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertFalse((output / 'TRAINING-RECEIPT.json').exists())


if __name__ == '__main__':
    unittest.main()
