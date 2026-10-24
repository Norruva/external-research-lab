"""Authored independent decoding checks; no reference models or real corpus."""
import copy
import math
import unittest
from dataclasses import replace

from eidos.decision import DecisionEngine
from eidos.decision_contract import DecisionContractError, validate_request


def request(kind='CHOICE'):
    r = {'format': 'eidos-decision-request-v2', 'request_id': 'authored-check',
         'context': 'The packet is red.', 'prompt': 'Which color describes the packet?',
         'kind': kind, 'options': [{'id': 'a', 'description': 'red'},
                                  {'id': 'b', 'description': 'blue'}]}
    if kind == 'BINARY':
        r['options'] = [{'id': 'true', 'description': 'The packet is red.'},
                        {'id': 'false', 'description': 'The packet is not red.'}]
    if kind == 'SCORE':
        r.update(minimum=0, maximum=10)
        r['options'][0]['value'] = 0
        r['options'][1]['value'] = 10
    return r


class AuthoredScorer:
    def encode(self, value):
        self.seen = value
        return value

    def forward(self, value):
        return {'logits': [math.log(0.25), math.log(0.75)],
                'probabilities': [0.25, 0.75], 'accounting': {'options_scored': 2}}


class DecisionEngineTests(unittest.TestCase):
    def setUp(self):
        self.scorer = AuthoredScorer()
        self.engine = DecisionEngine(self.scorer, {'id': 'authored-decoding', 'calibration_status': 'unfitted'})

    def test_choice_mapping_and_review_are_explicit(self):
        r = self.engine.decide(request())
        self.assertEqual(r['candidate_value'], 'b')
        self.assertIsNone(r['value'])
        self.assertTrue(r['review_required'])
        self.assertIsNone(r['calibrated_confidence'])
        self.assertEqual(r['authorization'], 'recommendation_only_no_action_authorized')
        self.assertEqual(r['processing']['options_scored'], 2)

    def test_binary_true_probability_uses_id_not_input_order(self):
        r = self.engine.decide(request('BINARY'))
        self.assertIs(r['candidate_value'], True)
        self.assertEqual(r['probability_true'], .75)
        self.assertEqual([p['id'] for p in r['distribution']], ['false', 'true'])

    def test_score_is_weighted_anchor_expectation(self):
        r = self.engine.decide(request('SCORE'))
        self.assertEqual(r['candidate_value'], 7.5)
        self.assertAlmostEqual(r['uncertainty']['score_stddev'], math.sqrt(18.75))

    def test_complete_request_reaches_scorer(self):
        original = request()
        self.engine.decide(original)
        self.assertEqual(self.scorer.seen.prompt, original['prompt'])
        self.assertEqual(self.scorer.seen.context, original['context'])
        self.assertEqual(self.scorer.seen.options[0].description, 'red')

    def test_direct_dataclass_cannot_bypass_validation(self):
        malformed = replace(validate_request(request()), context='')
        with self.assertRaises(DecisionContractError):
            self.engine.decide(malformed)

    def test_request_and_identity_not_mutated(self):
        q = request(); before = copy.deepcopy(q)
        r = self.engine.decide(q)
        r['model']['id'] = 'changed'
        self.assertEqual(q, before)
        self.assertEqual(self.engine.identity['id'], 'authored-decoding')

    def test_rejects_bad_model_distribution(self):
        for probabilities in ([.3, .3], [True, 0], [float('nan'), .5], [-.5, 1.5], [1.]):
            with self.subTest(probabilities=probabilities):
                self.scorer.forward = lambda _: {'logits': [0., 0.], 'probabilities': probabilities}
                with self.assertRaises(DecisionContractError):
                    self.engine.decide(request())


if __name__ == '__main__':
    unittest.main()
