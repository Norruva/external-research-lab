"""Original exact/hand-calculated fixtures for pure epistemic primitives.

These tests do not fit or load a classifier, evaluate a corpus, establish sampling
assumptions, authorize a real action or estimate comparative semantic quality.
"""
import copy
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import itertools
import json
import math
import unittest

from eidos.decision_epistemic import (ConformalProfile, EpistemicError,
    evidence_state, merge_evidence, fit_conformal, prediction_set,
    value_of_information, assess_forecast)


class FourValuedEvidenceTests(unittest.TestCase):
    def test_all_four_support_states_keep_missing_and_conflicting_information(self):
        expected = [(False, False, 'neither'), (True, False, 'true'),
                    (False, True, 'false'), (True, True, 'both')]
        for positive, negative, state in expected:
            with self.subTest(state=state):
                result = evidence_state(positive, negative)
                self.assertEqual(result['state'], state)
                self.assertIs(result['positive_support'], positive)
                self.assertIs(result['negative_support'], negative)
                self.assertFalse(result['support_truth_or_source_verified'])
                self.assertTrue(result['review_required'])
                self.assertFalse(result['action_authority'])

    def test_information_union_is_commutative_idempotent_and_not_majority_vote(self):
        first = merge_evidence([(True, False)] * 100 + [(False, True)])
        second = merge_evidence([(False, True), (True, False)])
        self.assertEqual(first, second)
        self.assertEqual(first['state'], 'both')
        self.assertEqual(merge_evidence([])['state'], 'neither')
        self.assertEqual(merge_evidence([(True, False)] * 10), evidence_state(True, False))

    def test_only_explicit_boolean_support_is_accepted(self):
        for bad in (0, 1, 'true', None, [], {}):
            with self.subTest(bad=bad):
                with self.assertRaises(EpistemicError): evidence_state(bad, False)
                with self.assertRaises(EpistemicError): merge_evidence([(False, bad)])
        with self.assertRaises(EpistemicError): merge_evidence([(True, False)] * 4097)


class SplitConformalTests(unittest.TestCase):
    def test_hand_rank_quantile_and_conservative_boundary_ties(self):
        # n=3, alpha=1/2 -> rank2, sorted score threshold1/4.
        profile = fit_conformal([.5, .25, 0.], .5)
        self.assertEqual(profile.rank, 2)
        self.assertEqual(profile.threshold, .25)
        result = prediction_set(profile, {'b': .25, 'a': .75})
        self.assertEqual(result['set'], ['a'])
        self.assertEqual(result['set_size'], 1)
        self.assertFalse(result['per_example_or_subgroup_coverage_guarantee'])
        self.assertTrue(result['review_required'])
        self.assertFalse(result['action_authority'])

    def test_small_calibration_requires_all_offered_options(self):
        profile = fit_conformal([.01, .02], .05)
        self.assertEqual(profile.rank, 3)
        self.assertIsNone(profile.threshold)
        result = prediction_set(profile, {'b': .1, 'a': .9})
        self.assertEqual(result['set'], ['a', 'b'])
        self.assertTrue(result['threshold_is_positive_infinity'])
        # Infinity is represented explicitly without nonstandard JSON numbers.
        json.dumps(profile.as_dict(), allow_nan=False)
        json.dumps(result, allow_nan=False)

    def test_empty_and_multi_candidate_sets_are_not_automatic_decisions(self):
        empty = prediction_set(fit_conformal([0., 0., 0.], .5), {'a': .5, 'b': .5})
        both = prediction_set(fit_conformal([.5, .5, .5], .5), {'a': .5, 'b': .5})
        self.assertEqual(empty['set'], [])
        self.assertEqual(both['set'], ['a', 'b'])
        for result in (empty, both):
            self.assertTrue(result['review_required'])
            self.assertFalse(result['action_authority'])

    def test_profile_is_immutable_bound_and_no_sampling_proof_is_fabricated(self):
        profile = fit_conformal([0., .25, .5], .5)
        with self.assertRaises(FrozenInstanceError): profile.alpha = .1
        with self.assertRaises(EpistemicError): prediction_set(replace(profile, rank=1), {'a': .5, 'b': .5})
        with self.assertRaises(EpistemicError): prediction_set(replace(profile, threshold=None), {'a': .5, 'b': .5})
        with self.assertRaises(EpistemicError): prediction_set(replace(profile, calibration_scores_sha256='fake'), {'a': .5, 'b': .5})
        self.assertFalse(profile.as_dict()['sampling_assumptions_verified'])
        self.assertFalse(profile.as_dict()['model_population_and_score_rule_binding_verified'])

    def test_input_order_does_not_change_quantile_or_mapped_membership(self):
        first = fit_conformal([.25, .5, 0.], .5)
        second = fit_conformal([0., .5, .25], .5)
        self.assertEqual(first.threshold, second.threshold)
        self.assertNotEqual(first.calibration_scores_sha256, second.calibration_scores_sha256)
        self.assertEqual(prediction_set(first, {'b': .25, 'a': .75})['set'],
                         prediction_set(second, {'a': .75, 'b': .25})['set'])

    def test_complete_probability_vector_and_score_bounds_are_enforced(self):
        profile = fit_conformal([0., .25, .5], .5)
        for probabilities in ({'a': 1.}, {'a': .4, 'b': .4}, {'a': -1., 'b': 2.},
                              {'a': True, 'b': 0.}, {'a': float('nan'), 'b': .5},
                              {'a': float('inf'), 'b': 0.}, {'bad id': .5, 'b': .5}):
            with self.subTest(probabilities=probabilities):
                with self.assertRaises(EpistemicError): prediction_set(profile, probabilities)
        for scores, alpha in (([], .1), ([0.] * 100001, .1), ([True], .1), ([-.1], .1),
                              ([1.1], .1), ([float('nan')], .1), ([.5], True),
                              ([.5], 0.), ([.5], 1.), ([.5], float('inf'))):
            with self.subTest(alpha=alpha):
                with self.assertRaises(EpistemicError): fit_conformal(scores, alpha)

    def test_all_255_candidates_are_retained_at_infinite_threshold(self):
        p = {'id' + str(i): 1 / 255 for i in range(255)}
        self.assertEqual(prediction_set(fit_conformal([0.], .05), p)['set_size'], 255)
        p['overflow'] = 0.
        with self.assertRaises(EpistemicError): prediction_set(fit_conformal([0.], .05), p)


class InformationValueTests(unittest.TestCase):
    def setUp(self):
        self.prior = {'a': .5, 'b': .5}
        self.losses = {'choose-a': {'a': 0., 'b': 1.}, 'choose-b': {'a': 1., 'b': 0.},
                       'review': {'a': .25, 'b': .25}}

    def test_perfect_information_example_counts_review_as_costly(self):
        joint = {'see-a': {'a': .5, 'b': 0.}, 'see-b': {'a': 0., 'b': .5}}
        result = value_of_information(self.prior, self.losses, joint, .1)
        self.assertEqual(result['minimum_prior_loss'], .25)
        self.assertEqual(result['best_prior_actions'], ['review'])
        self.assertEqual(result['expected_minimum_posterior_loss'], 0.)
        self.assertEqual(result['gross_information_value'], .25)
        self.assertAlmostEqual(result['net_information_value'], .15)
        self.assertTrue(result['positive_declared_net_value'])
        self.assertTrue(result['review_required'])
        self.assertFalse(result['action_authority'])

    def test_uninformative_observation_only_adds_investigation_cost(self):
        joint = {'left': {'a': .25, 'b': .25}, 'right': {'a': .25, 'b': .25}}
        result = value_of_information(self.prior, self.losses, joint, .125)
        self.assertEqual(result['expected_minimum_posterior_loss'], .25)
        self.assertEqual(result['gross_information_value'], 0.)
        self.assertEqual(result['net_information_value'], -.125)
        self.assertFalse(result['positive_declared_net_value'])

    def test_zero_probability_observation_has_no_undefined_posterior(self):
        joint = {'nothing': {'a': 0., 'b': 0.}, 'regular': {'a': .5, 'b': .5}}
        result = value_of_information(self.prior, self.losses, joint, 0.)
        zero = next(row for row in result['observations'] if row['id'] == 'nothing')
        self.assertEqual(zero['probability'], 0.)
        self.assertIsNone(zero['posterior'])
        self.assertIsNone(zero['minimum_posterior_loss'])
        json.dumps(result, allow_nan=False)

    def test_partial_information_matches_independent_fraction_arithmetic(self):
        # Experiment is correct3/4 of the time. Review cost3/8, so before3/8;
        # after1/4; gross1/8. Cost1/16 leaves net1/16.
        losses = {'choose-a': {'a': 0., 'b': 1.}, 'choose-b': {'a': 1., 'b': 0.},
                  'review': {'a': .375, 'b': .375}}
        joint = {'see-a': {'a': .375, 'b': .125}, 'see-b': {'a': .125, 'b': .375}}
        result = value_of_information(self.prior, losses, joint, .0625)
        self.assertEqual(Fraction(result['minimum_prior_loss']), Fraction(3, 8))
        self.assertEqual(Fraction(result['expected_minimum_posterior_loss']), Fraction(1, 4))
        self.assertEqual(Fraction(result['net_information_value']), Fraction(1, 16))
        self.assertEqual([r['best_actions'] for r in result['observations']], [['choose-a'], ['choose-b']])

    def test_result_and_inputs_keep_declared_assumptions_and_no_causal_claim(self):
        joint = {'regular': {'a': .5, 'b': .5}}
        before = copy.deepcopy((self.prior, self.losses, joint))
        result = value_of_information(self.prior, self.losses, joint, 0.)
        self.assertEqual((self.prior, self.losses, joint), before)
        self.assertFalse(result['probability_loss_and_cost_models_verified'])
        self.assertFalse(result['causal_or_sequential_planning_claimed'])
        self.assertFalse(result['action_authority'])

    def test_joint_is_not_mistaken_for_conditional_or_inconsistent_marginal(self):
        for joint in ({'left': {'a': 1., 'b': 0.}, 'right': {'a': 0., 'b': 1.}},
                      {'left': {'a': .75, 'b': .25}}, {'left': {'a': .5}},
                      {'left': {'a': True, 'b': .5}}):
            with self.subTest(joint=joint):
                with self.assertRaises(EpistemicError): value_of_information(self.prior, self.losses, joint, 0.)

    def test_malformed_loss_actions_and_nonfinite_cost_are_refused(self):
        joint = {'regular': {'a': .5, 'b': .5}}
        for loss in ({}, {'bad id': {'a': 0., 'b': 0.}}, {'choose-a': {'a': 0.}},
                     {'choose-a': {'a': 0., 'b': -1.}}, {'choose-a': {'a': 0., 'b': float('nan')}}):
            with self.subTest(loss=loss):
                with self.assertRaises(EpistemicError): value_of_information(self.prior, loss, joint, 0.)
        for cost in (-1., True, float('nan'), float('inf'), 10**1000):
            with self.subTest(cost=cost):
                with self.assertRaises(EpistemicError): value_of_information(self.prior, self.losses, joint, cost)

    def test_all_action_ties_are_reported_without_hidden_execution_choice(self):
        losses = {'one': {'a': .25, 'b': .25}, 'two': {'a': .25, 'b': .25}}
        result = value_of_information(self.prior, losses, {'only': self.prior}, 0.)
        self.assertEqual(result['best_prior_actions'], ['one', 'two'])
        self.assertEqual(result['observations'][0]['best_actions'], ['one', 'two'])


class ForecastAssessmentTests(unittest.TestCase):
    def forecast(self):
        return {'format': 'eidos-decision-response-v2', 'kind': 'CHOICE',
                'request_id': 'authored-forecast', 'status': 'review_required',
                'review_required': True, 'value': None,
                'authorization': 'recommendation_only_no_action_authorized',
                'model': {'sha256': 'a' * 64},
                'distribution': [{'id': 'a', 'value': 'a', 'probability': .75, 'logit': .5},
                                 {'id': 'b', 'value': 'b', 'probability': .25, 'logit': -.5}]}

    def test_valid_forecast_keeps_review_and_unsupported_evidence_is_not_false(self):
        forecast = self.forecast()
        before = copy.deepcopy(forecast)
        result = assess_forecast(forecast)
        self.assertEqual(forecast, before)
        self.assertEqual(result['four_valued_evidence']['state'], 'neither')
        self.assertIsNone(result['prediction_set'])
        self.assertTrue(result['review_required'])
        self.assertFalse(result['action_authority'])
        self.assertFalse(result['forecast_model_and_evidence_verified'])
        self.assertFalse(result['evidence_changes_model_probabilities'])

    def test_contradiction_and_singleton_set_never_authorize_an_action(self):
        profile = fit_conformal([0., .25, .5], .5)
        result = assess_forecast(self.forecast(), evidence_positive=True,
                                 evidence_negative=True, conformal_profile=profile)
        self.assertEqual(result['four_valued_evidence']['state'], 'both')
        self.assertEqual(result['prediction_set']['set'], ['a'])
        self.assertTrue(result['review_required'])
        self.assertFalse(result['action_authority'])
        self.assertFalse(result['calibration_regime_applies_to_this_forecast_verified'])
        json.dumps(result, allow_nan=False)

    def test_reordered_probability_stream_has_same_binding(self):
        left = self.forecast()
        right = copy.deepcopy(left)
        right['distribution'].reverse()
        self.assertEqual(assess_forecast(left)['forecast_probability_binding_sha256'],
                         assess_forecast(right)['forecast_probability_binding_sha256'])

    def test_malformed_or_nonreview_forecast_is_refused(self):
        for key, value in (('format', 'legacy'), ('review_required', False),
                           ('value', 'execute'), ('status', 'answered'),
                           ('authorization', 'execute-now'), ('request_id', 'bad id')):
            with self.subTest(key=key):
                forecast = self.forecast(); forecast[key] = value
                with self.assertRaises(EpistemicError): assess_forecast(forecast)
        for alter in ('duplicate', 'nonfinite', 'incomplete', 'wrong-value', 'bad-model'):
            with self.subTest(alter=alter):
                forecast = self.forecast()
                if alter == 'duplicate': forecast['distribution'][1]['id'] = 'a'
                elif alter == 'nonfinite': forecast['distribution'][0]['logit'] = float('nan')
                elif alter == 'incomplete': forecast['distribution'][0].pop('probability')
                elif alter == 'wrong-value': forecast['distribution'][0]['value'] = 'b'
                else: forecast['model']['sha256'] = 'unverified'
                with self.assertRaises(EpistemicError): assess_forecast(forecast)

    def test_binary_and_numeric_value_types_are_checked(self):
        binary = self.forecast(); binary['kind'] = 'BINARY'
        binary['distribution'] = [
            {'id': 'false', 'value': False, 'probability': .25, 'logit': -.5},
            {'id': 'true', 'value': True, 'probability': .75, 'logit': .5}]
        self.assertTrue(assess_forecast(binary)['review_required'])
        binary['distribution'][0]['value'] = 0
        with self.assertRaises(EpistemicError): assess_forecast(binary)
        numeric = self.forecast(); numeric['kind'] = 'SCORE'
        numeric['distribution'][0]['value'] = 0
        numeric['distribution'][1]['value'] = 2
        self.assertTrue(assess_forecast(numeric)['review_required'])
        numeric['distribution'][1]['value'] = True
        with self.assertRaises(EpistemicError): assess_forecast(numeric)


if __name__ == '__main__':
    unittest.main()
