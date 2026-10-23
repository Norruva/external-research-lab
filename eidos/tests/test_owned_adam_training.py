"""Numerical Adam checks and actual owned-registry workflows; authored data only."""
from array import array
from decimal import Decimal, localcontext
import math
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from eidos.engine import Engine
from eidos.evaluate_owned import prepare, predict, score
from eidos.train_linear_support import (ADAM_EPOCHS, ADAM_L2, L2, _adam_update,
                                        _fit_adam, _objective_gradient, build)
from eidos.train_support import TrainingError
from test_owned_evaluation import cases
import test_owned_linear_training as linear_tests
from test_owned_training import raw


class OwnedAdamTraining(unittest.TestCase):
    files = linear_tests.OwnedLinearTraining.files

    def test_first_step_bias_correction_epsilon_and_zero_gradient(self):
        weights = array('d', [0., 0., 3.])
        first, second = array('d', [0., 0., 0.]), array('d', [0., 0., 0.])
        _adam_update(weights, first, second, [2., -4., 0.], 1)
        self.assertAlmostEqual(weights[0], -.12 * 2 / (2 + 1e-8), delta=1e-15)
        self.assertAlmostEqual(weights[1], .12 * 4 / (4 + 1e-8), delta=1e-15)
        self.assertEqual(weights[2], 3.)
        self.assertAlmostEqual(first[0], .2, delta=1e-15)
        self.assertAlmostEqual(second[1], .016, delta=1e-15)

    def test_second_step_matches_independent_decimal_closed_form(self):
        weights, first, second = (array('d', [0.]) for _ in range(3))
        _adam_update(weights, first, second, [2.], 1)
        _adam_update(weights, first, second, [-1.], 2)
        with localcontext() as context:
            context.prec = 60
            d = Decimal
            initial = -d('.12') * 2 / (2 + d('1e-8'))
            expected = initial - d('.12') * (d('.08') / d('.19')) / (
                (d('.004996') / d('.001999')).sqrt() + d('1e-8'))
        self.assertAlmostEqual(weights[0], float(expected), delta=1e-14)
        self.assertAlmostEqual(first[0], .08, delta=1e-15)
        self.assertAlmostEqual(second[0], .004996, delta=1e-15)

    def test_adam_l2_is_coupled_weights_only_and_bias_unpenalized(self):
        weights = array('d', [.2, -.3, .7, -.8])
        examples = [(0, [(0, 1.)]), (1, [(0, .5)])]
        low, low_gradient = _objective_gradient(weights, examples, 2, 1, l2=ADAM_L2)
        high, high_gradient = _objective_gradient(weights, examples, 2, 1)
        self.assertAlmostEqual(high - low, (L2 - ADAM_L2) / 2 * (.2**2 + .3**2))
        for i in range(2):
            self.assertAlmostEqual(high_gradient[i] - low_gradient[i], (L2 - ADAM_L2) * weights[i])
        self.assertEqual(list(high_gradient[2:]), list(low_gradient[2:]))

    def test_actual_fitter_one_step_matches_independent_softmax_gradient(self):
        examples = [(0, [(0, 1.)]), (1, [(0, .5)])]
        fitted, initial, final = _fit_adam(examples, 2, 1, 1, (time.process_time(), time.monotonic()))
        self.assertAlmostEqual(initial, math.log(2))
        # Zero logits yield uniform probabilities: mean weight gradients = ±1/8.
        expected = .12 * .125 / (.125 + 1e-8)
        self.assertAlmostEqual(fitted[0], expected)
        self.assertAlmostEqual(fitted[1], -expected)
        self.assertEqual(list(fitted[2:]), [0., 0.])
        self.assertLess(final, initial)

    def test_actual_registry_records_optimizer_and_preserves_review_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            root = Path(directory)
            receipt = build(support, provenance, root / 'adam', 'owned-demo', optimizer='adam')
            model = Engine.from_registry(root / 'adam').models['owned-demo']
            self.assertEqual(receipt['epochs'], ADAM_EPOCHS)
            self.assertEqual(model.data['training']['learning_rate'], .12)
            self.assertEqual(model.data['training']['l2'], .00002)
            self.assertEqual(model.data['training']['beta1'], .9)
            self.assertEqual(model.data['training']['epsilon'], 1e-8)
            self.assertEqual(model.data['training']['regularization'], 'coupled_l2_weights_only_not_adamw')
            self.assertEqual(receipt['checkpoint_selection'], 'fixed_final_epoch_no_development_or_test_input')
            self.assertLess(receipt['final_training_objective'], receipt['initial_training_objective'])
            self.assertIsNone(model.data['calibration'])
            self.assertEqual(model.data['thresholds']['probability'], 1.)
            self.assertFalse(receipt['evaluation_or_calibration_performed'])
            self.assertFalse(receipt['same_recipe_or_artifact_as_historical_laya_comparison'])
            self.assertEqual((root / 'adam').stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o400 for p in (root / 'adam').iterdir()))

    def test_repeat_bytes_and_reordered_support_weights_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            root = Path(directory)
            for name in ('a', 'b'):
                build(support, provenance, root / name, 'owned-demo', epochs=3, optimizer='adam')
            for path in (root / 'a').iterdir():
                self.assertEqual(path.read_bytes(), (root / 'b' / path.name).read_bytes())
            support.write_bytes(b'\n'.join(reversed(support.read_bytes().splitlines())) + b'\n')
            build(support, provenance, root / 'c', 'owned-demo', epochs=3, optimizer='adam')
            self.assertEqual((root / 'a/weights.f32').read_bytes(), (root / 'c/weights.f32').read_bytes())

    def test_omitted_optimizer_and_explicit_gd_keep_identical_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            root = Path(directory)
            build(support, provenance, root / 'a', 'owned-demo')
            build(support, provenance, root / 'b', 'owned-demo', optimizer='gradient_descent', epochs=64)
            for path in (root / 'a').iterdir():
                self.assertEqual(path.read_bytes(), (root / 'b' / path.name).read_bytes())

    def test_invalid_optimizer_epochs_and_extra_work_refuse_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            root = Path(directory)
            for optimizer in ('adamw', 'Adam', True, None, []):
                with self.assertRaisesRegex(TrainingError, 'unsupported_optimizer'):
                    build(support, provenance, root / 'invalid', 'owned-demo', optimizer=optimizer)
            for epochs in (0, 201, True, 1.5):
                with self.assertRaisesRegex(TrainingError, 'epochs_outside'):
                    build(support, provenance, root / 'invalid', 'owned-demo', optimizer='adam', epochs=epochs)
            gd = build(support, provenance, root / 'gd', 'owned-demo', epochs=2)
            with patch('eidos.train_linear_support.MAX_WORK_UNITS', gd['work_units']):
                with self.assertRaisesRegex(TrainingError, 'training_work_budget_exceeded'):
                    build(support, provenance, root / 'invalid', 'owned-demo', optimizer='adam', epochs=2)
            self.assertFalse((root / 'invalid').exists())

    def test_budget_and_nonfinite_gradient_refuse(self):
        with self.assertRaisesRegex(TrainingError, 'nonfinite_training_gradient'):
            _adam_update(array('d', [0.]), array('d', [0.]), array('d', [0.]), [float('nan')], 1)
        with tempfile.TemporaryDirectory() as directory:
            support, provenance = self.files(directory)
            root = Path(directory)
            with patch('eidos.train_linear_support.CPU_SECONDS', -1):
                with self.assertRaisesRegex(TrainingError, 'training_time_budget_exceeded'):
                    build(support, provenance, root / 'adam', 'owned-demo', optimizer='adam')
            self.assertFalse((root / 'adam').exists())

    def test_adam_registry_uses_real_prepare_predict_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            support, provenance = self.files(directory)
            build(support, provenance, root / 'adam', 'owned-demo', optimizer='adam', epochs=3)
            evaluation = root / 'evaluation.jsonl'
            evaluation.write_bytes(raw(cases()))
            prepare(root / 'adam', support, provenance, evaluation, provenance, root / 'split')
            predict(root / 'adam', root / 'split/prediction-input', root / 'predictions')
            result = score(root / 'split/prediction-input/plan.json', root / 'split/gold-private',
                           root / 'predictions', root / 'score')
            self.assertEqual(result['metrics']['n'], 2)
            self.assertEqual(result['metrics']['accepted_coverage'], 0)
            self.assertFalse(result['fit_calibration_promotion_or_rival_comparison_performed'])


if __name__ == '__main__':
    unittest.main()
