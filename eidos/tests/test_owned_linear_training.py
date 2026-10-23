"""Original numerical and ownership checks; authored fixtures only."""
from array import array
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eidos.engine import Engine
from eidos.general_linear import LinearModel
from eidos.model import canonical
from eidos.train_linear_support import L2, _objective_gradient, build
from eidos.train_support import TrainingError, build as build_memory
from test_owned_training import PROVENANCE, fixture, raw


class OwnedLinearTraining(unittest.TestCase):
    def files(self, directory, values=None):
        root = Path(directory)
        support, provenance = root / 'input.jsonl', root / 'provenance.json'
        support.write_bytes(raw(fixture() if values is None else values))
        provenance.write_bytes(PROVENANCE)
        return support, provenance

    def test_all_nine_gradient_coordinates_match_central_differences(self):
        examples = [(0, [(0, 1.0)]), (1, [(1, 1.0)]), (2, [(0, .6), (1, .8)])]
        weights = array('d', [.03, -.2, .4, .1, -.1, .25, .2, -.15, .01])
        objective, gradient = _objective_gradient(weights, examples, 3, 2)
        self.assertTrue(math.isfinite(objective))
        for i in range(9):
            high, low = array('d', weights), array('d', weights)
            high[i] += 1e-6
            low[i] -= 1e-6
            numerical = (_objective_gradient(high, examples, 3, 2)[0] -
                         _objective_gradient(low, examples, 3, 2)[0]) / 2e-6
            self.assertAlmostEqual(gradient[i], numerical, delta=2e-9)
        self.assertAlmostEqual(sum(gradient[6:]), 0.0, delta=1e-14)

    def test_stable_logsumexp_for_extreme_wrong_logit(self):
        value, gradient = _objective_gradient(array('d', [900.0, -900.0, 0.0, 0.0]),
                                               [(1, [(0, 1.0)])], 2, 1)
        self.assertAlmostEqual(value, 1800 + L2 * 900**2)
        self.assertTrue(all(math.isfinite(x) for x in gradient))
        self.assertEqual(list(gradient[2:]), [1.0, -1.0])

    def test_actual_engine_loads_private_review_only_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            out = Path(directory) / 'linear'
            receipt = build(support, provenance, out, 'owned-demo', epochs=8)
            self.assertLess(receipt['final_training_objective'], receipt['initial_training_objective'])
            self.assertEqual(receipt['input_sha256'], hashlib.sha256(support.read_bytes()).hexdigest())
            engine = Engine.from_registry(out, tenants=('demo',))
            labels = ['billing_help', 'music_help']
            request = {'schema_version': '1.0', 'input_version': 'authored-only', 'request_id': 'numerical-mechanics',
                       'tenant_id': 'demo', 'language': 'en', 'state': {'utterance': 'help with an invoice'}, 'sources': [],
                       'questions': [{'id': 'q', 'kind': 'CHOICE', 'profile': 'owned-demo', 'prompt': 'Choose intent',
                                      'state_path': ['utterance'], 'choices': [{'id': x, 'class_id': x} for x in labels]}]}
            response = engine.decide(request)
            decision = response['decisions'][0]
            self.assertEqual(decision['status'], 'abstained')
            self.assertIsNone(decision['value'])
            self.assertIsNone(decision['calibrated_confidence'])
            self.assertTrue(decision['review_required'])
            self.assertIn('acceptance_gate_unavailable', decision['reasons'])
            self.assertEqual(response['authorization'], 'recommendation_only_no_action_authorized')
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)
            self.assertEqual({x.name for x in out.iterdir()}, {'model.json', 'weights.f32', 'registry.json', 'TRAINING-RECEIPT.json'})
            self.assertTrue(all(x.stat().st_mode & 0o777 == 0o400 for x in out.iterdir()))
            self.assertFalse(receipt['evaluation_or_calibration_performed'])
            self.assertFalse(receipt['same_recipe_or_artifact_as_historical_laya_comparison'])

    def test_repeated_build_has_identical_artifact_and_receipt_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            root = Path(directory)
            left = build(support, provenance, root / 'left', 'owned-demo', epochs=4)
            right = build(support, provenance, root / 'right', 'owned-demo', epochs=4)
            self.assertEqual(left, right)
            for path in (root / 'left').iterdir():
                self.assertEqual(path.read_bytes(), (root / 'right' / path.name).read_bytes())

    def test_input_order_preserves_weights_and_tracks_input_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            root = Path(directory)
            build(support, provenance, root / 'left', 'owned-demo', epochs=4)
            support.write_bytes(raw(list(reversed(fixture()))))
            build(support, provenance, root / 'right', 'owned-demo', epochs=4)
            self.assertEqual((root / 'left/weights.f32').read_bytes(), (root / 'right/weights.f32').read_bytes())
            left = json.loads((root / 'left/model.json').read_bytes())
            right = json.loads((root / 'right/model.json').read_bytes())
            self.assertEqual(left['vocabulary'], right['vocabulary'])
            self.assertEqual(left['idf'], right['idf'])
            self.assertNotEqual(left['training']['input_sha256'], right['training']['input_sha256'])

    def test_same_support_transform_as_existing_memory_method(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            root = Path(directory)
            build(support, provenance, root / 'linear', 'linear', epochs=2)
            build_memory(support, provenance, root / 'memory', 'memory')
            linear = Engine.from_registry(root / 'linear').models['linear']
            memory = Engine.from_registry(root / 'memory').models['memory']
            self.assertEqual(linear.data['vocabulary'], memory.data['vocabulary'])
            self.assertEqual(linear.data['idf'], memory.data['idf'])
            for text in ['help invoice reference', 'play music', 'unobserved particles']:
                self.assertEqual(linear.vector(text), memory.vector(text))

    def test_candidate_subsets_preserve_raw_logits_and_export_parity(self):
        values = fixture() + [{'id': 'alarm-' + str(i), 'label': 'alarm_help',
                               'text': 'set my morning alarm timer ' + str(i)} for i in range(20)]
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory, values)
            root = Path(directory)
            build(support, provenance, root / 'out', 'linear', epochs=4)
            model = Engine.from_registry(root / 'out').models['linear']
            full = model.logits('please play music')
            labels = model.data['labels']
            subset = [labels[2], labels[0]]
            self.assertEqual(model.logits('please play music', subset), [full[2], full[0]])
            z, p = model.predict_subset('please play music', subset)
            self.assertAlmostEqual(sum(p), 1.0)
            metadata = root / 'out/model.json'
            loaded = LinearModel.load(metadata, hashlib.sha256(metadata.read_bytes()).hexdigest())
            self.assertEqual(z, loaded.logits('please play music', subset))

    def test_work_budget_refuses_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            out = Path(directory) / 'out'
            with patch('eidos.train_linear_support.MAX_WORK_UNITS', 1):
                with self.assertRaisesRegex(TrainingError, 'training_work_budget_exceeded'):
                    build(support, provenance, out, 'linear')
            self.assertFalse(out.exists())

    def test_time_budget_refuses_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            out = Path(directory) / 'out'
            with patch('eidos.train_linear_support.CPU_SECONDS', -1):
                with self.assertRaisesRegex(TrainingError, 'training_time_budget_exceeded'):
                    build(support, provenance, out, 'linear')
            self.assertFalse(out.exists())

    def test_epoch_values_are_bounded_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            out = Path(directory) / 'out'
            for value in (0, 201, True, 1.5):
                with self.assertRaisesRegex(TrainingError, 'epochs_outside'):
                    build(support, provenance, out, 'linear', epochs=value)
            self.assertFalse(out.exists())

    def test_existing_outputs_and_symlink_inputs_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            root = Path(directory)
            out = root / 'out'
            out.mkdir()
            (out / 'sentinel').write_bytes(b'preserved')
            with self.assertRaisesRegex(TrainingError, 'output_already_exists'):
                build(support, provenance, out, 'linear')
            self.assertEqual((out / 'sentinel').read_bytes(), b'preserved')
            alias = root / 'alias'
            alias.symlink_to(support)
            with self.assertRaises(OSError):
                build(alias, provenance, root / 'new', 'linear')
            self.assertFalse((root / 'new').exists())

    def test_oversized_selected_term_refused_before_publication(self):
        values = fixture()
        values[0]['text'] += ' ' + 'q' * 513
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory, values)
            out = Path(directory) / 'out'
            with self.assertRaisesRegex(TrainingError, 'selected_feature_string_limit'):
                build(support, provenance, out, 'linear')
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
