"""Authored numerical/conditioning mechanics, not broad semantic evaluation."""
from array import array
import copy
import math
import unittest

from eidos.decision_contract import validate_request
from eidos.decision_neural import (ARCHITECTURE, DecisionNetwork, EncodedRequest, EncodedToken,
                                    ModelError, build_vocabulary, tokenize)


def request(context='Ada has red fabric. Bo has blue fabric.', prompt='Which fabric does Ada have?',
            options=None, request_id='authored'):
    return validate_request({'format': 'eidos-decision-request-v2', 'request_id': request_id,
        'context': context, 'prompt': prompt, 'kind': 'CHOICE',
        'options': options or [{'id': 'a', 'description': 'red fabric'},
                               {'id': 'b', 'description': 'blue fabric'}]})


def model_for(*requests, dimension=3, hidden=4, seed=113):
    return DecisionNetwork.initialize(build_vocabulary(requests), dimension, hidden, seed)


class DecisionNeural(unittest.TestCase):
    def test_all_parameter_gradients_match_independent_central_differences(self):
        # Two spans force differentiation through attention. Repeated terms
        # independently exercise accumulated shared embedding derivatives.
        case = request(context='Ada red. Bo blue.', prompt='Does Ada prefer blue?',
                       options=[{'id': 'a', 'description': 'red fabric Ada'},
                                {'id': 'b', 'description': 'blue fabric Bo'}])
        network = model_for(case, dimension=2, hidden=2)
        encoded = network.encode(case)
        loss, gradient = network.loss_and_grad(encoded, 1)
        self.assertTrue(math.isfinite(loss))
        epsilon = 1e-5
        for index, analytic in enumerate(gradient):
            original = network.parameters[index]
            network.parameters[index] = original + epsilon
            plus = network.forward(encoded)['logits']
            network.parameters[index] = original - epsilon
            minus = network.forward(encoded)['logits']
            network.parameters[index] = original
            def independent_ce(logits):
                peak = max(logits)
                return peak - logits[1] + math.log(sum(math.exp(value - peak) for value in logits))
            numeric = (independent_ce(plus) - independent_ce(minus)) / (2 * epsilon)
            self.assertAlmostEqual(analytic, numeric, delta=2e-7,
                                   msg=f'parameter {index}')

    def test_forward_matches_independent_two_span_scalar_equations(self):
        case = request(context='red\nblue', prompt='who red blue', options=[
            {'id': 'a', 'description': 'red blue who'},
            {'id': 'b', 'description': 'blue who red'}])
        network = DecisionNetwork.initialize(('<UNK>', 'blue', 'red', 'who'), 2, 2, 113)
        network.parameters = array('d', [0.]) * network.parameter_count
        embeddings = {'blue': [-.2, .4], 'red': [.6, -.3], 'who': [.1, .2]}
        for token, vector in embeddings.items():
            for j, value in enumerate(vector):
                network.parameters[network.index[token] * 2 + j] = value
        network.parameters[network.position_offset] = .15
        network.parameters[network.position_offset + 1] = -.25
        # Hidden0 reads R*O; hidden1 reads Q*O. Independent scalar
        # evaluation below includes complete context attention for each option.
        network.parameters[network.hidden_weight_offset + 8] = .7
        network.parameters[network.hidden_weight_offset + 9] = -.4
        network.parameters[network.hidden_weight_offset + 12 + 10] = -.2
        network.parameters[network.hidden_weight_offset + 12 + 11] = .5
        network.parameters[network.hidden_bias_offset] = .1
        network.parameters[network.hidden_bias_offset + 1] = -.05
        network.parameters[network.output_offset] = .8
        network.parameters[network.output_offset + 1] = -.6
        # Three-token first moments can be computed independently from their
        # endpoints. Each sequence has the same token bag but different order.
        def ordered_mean(tokens):
            return [sum(embeddings[token][j] for token in tokens) / 3 +
                    (.15, -.25)[j] * (embeddings[tokens[2]][j] - embeddings[tokens[0]][j]) / 3
                    for j in range(2)]
        q = ordered_mean(('who', 'red', 'blue'))
        expected = []
        for tokens in (('red', 'blue', 'who'), ('blue', 'who', 'red')):
            option = ordered_mean(tokens)
            energies = [sum(state[j] * (q[j] + option[j]) for j in range(2)) /
                        math.sqrt(2) for state in (embeddings['red'], embeddings['blue'])]
            masses = [math.exp(e - max(energies)) for e in energies]
            weights = [mass / sum(masses) for mass in masses]
            r = [sum(weight * state[j] for weight, state in zip(weights,
                 (embeddings['red'], embeddings['blue']))) for j in range(2)]
            hidden0 = math.tanh(.1 + .7 * r[0] * option[0] - .4 * r[1] * option[1])
            hidden1 = math.tanh(-.05 - .2 * q[0] * option[0] + .5 * q[1] * option[1])
            expected.append(.8 * hidden0 - .6 * hidden1)
        result = network.forward(network.encode(case))
        for actual, wanted in zip(result['logits'], expected):
            self.assertAlmostEqual(actual, wanted, places=14)

    def test_gradient_includes_position_embedding_attention_and_hidden_groups(self):
        case = request()
        network = model_for(case)
        _, gradient = network.loss_and_grad(network.encode(case), 0)
        boundaries = [0, network.byte_embedding_offset, network.byte_position_offset,
                      network.position_offset, network.context_position_offset,
                      network.hidden_weight_offset, network.hidden_bias_offset,
                      network.output_offset, network.parameter_count]
        for lo, hi in zip(boundaries, boundaries[1:]):
            self.assertGreater(sum(abs(value) for value in gradient[lo:hi]), 1e-10)

    def test_question_changes_semantic_features_and_scores(self):
        ada, bo = request(), request(prompt='Which fabric does Bo have?')
        network = model_for(ada, bo)
        left, right = network.encode(ada), network.encode(bo)
        self.assertNotEqual(left.prompt, right.prompt)
        self.assertNotEqual(network.forward(left)['logits'], network.forward(right)['logits'])

    def test_option_meaning_changes_scores_without_trained_class_ids(self):
        original = request()
        changed = request(options=[{'id': 'a', 'description': 'Ada wears blue'},
                                   {'id': 'b', 'description': 'Bo wears red'}])
        network = model_for(original, changed)
        self.assertNotEqual(network.forward(network.encode(original))['logits'],
                            network.forward(network.encode(changed))['logits'])

    def test_order_moment_distinguishes_same_bag_context_permutation(self):
        left = request(context='Ada red Bo blue')
        right = request(context='Bo red Ada blue')
        network = model_for(left, right)
        one, two = network.forward(network.encode(left)), network.forward(network.encode(right))
        self.assertNotEqual(one['logits'], two['logits'])

    def test_reversed_same_token_question_is_not_bag_invariant(self):
        left = request(prompt='Does Ada help Bo?')
        right = request(prompt='Does Bo help Ada?')
        network = model_for(left, right)
        self.assertEqual(sorted(tokenize(left.prompt)), sorted(tokenize(right.prompt)))
        one = network.forward(network.encode(left))
        two = network.forward(network.encode(right))
        self.assertNotEqual(one['logits'], two['logits'])
        self.assertNotEqual(one['probabilities'], two['probabilities'])

    def test_reversed_same_token_option_changes_scores_without_ids(self):
        options = [{'id': 'a', 'description': 'Ada helps Bo'},
                   {'id': 'b', 'description': 'Bo helps Ada'}]
        left = request(prompt='Who helps whom?', options=options)
        right = request(prompt='Who helps whom?', options=[
            {'id': 'a', 'description': options[1]['description']},
            {'id': 'b', 'description': options[0]['description']}])
        network = model_for(left, right)
        self.assertEqual(sorted(tokenize(options[0]['description'])),
                         sorted(tokenize(options[1]['description'])))
        one = network.forward(network.encode(left))
        two = network.forward(network.encode(right))
        self.assertNotEqual(one['logits'][0], one['logits'][1])
        self.assertEqual(one['logits'], list(reversed(two['logits'])))
        self.assertEqual(one['probabilities'], list(reversed(two['probabilities'])))

    def test_old_bag_question_option_architecture_is_not_reinterpreted(self):
        network = model_for(request())
        document = network.to_document()
        document['metadata']['architecture'] = 'eidos-question-conditioned-attention-mlp-v1'
        with self.assertRaises(ModelError):
            DecisionNetwork.from_document(document)

    def test_composed_byte_vector_matches_independent_scalar_values(self):
        network = DecisionNetwork.initialize(('<UNK>', 'ab'), 2, 2, 113)
        network.parameters = array('d', [0.]) * network.parameter_count
        # Give the known word and UNK the same base to isolate composition.
        for word in (0, 1):
            network.parameters[word * 2] = .1
            network.parameters[word * 2 + 1] = -.1
        for byte, values in ((ord('a'), (.2, -.4)), (ord('b'), (.6, .8))):
            for j, value in enumerate(values):
                network.parameters[network.byte_embedding_offset + byte * 2 + j] = value
        network.parameters[network.byte_position_offset] = .3
        network.parameters[network.byte_position_offset + 1] = -.2
        known = network._token_vector(EncodedToken(1, tuple(b'ab')))[0]
        unknown = network._token_vector(EncodedToken(0, tuple(b'ba')))[0]
        for actual, expected in zip(known, (.56, -.02)):
            self.assertAlmostEqual(actual, expected, places=14)
        for actual, expected in zip(unknown, (.44, .22)):
            self.assertAlmostEqual(actual, expected, places=14)

    def test_novel_names_retain_distinct_spelling_after_word_oov(self):
        fit = request()
        novel = request(context='Zerin helps Vomar.', prompt='Who is the helper?', options=[
            {'id': 'a', 'description': 'Zerin'}, {'id': 'b', 'description': 'Vomar'}])
        network = model_for(fit)
        encoded = network.encode(novel)
        a = encoded.tokens[encoded.candidates[0][0]]
        b = encoded.tokens[encoded.candidates[1][0]]
        self.assertEqual(a.word_id, 0)
        self.assertEqual(b.word_id, 0)
        self.assertNotEqual(a.utf8, b.utf8)
        result = network.forward(encoded)
        self.assertNotEqual(result['logits'][0], result['logits'][1])
        self.assertTrue(result['accounting']['byte_composition_applied_to_all_tokens'])

    def test_same_byte_bag_novel_anagrams_have_order_sensitive_features(self):
        fit = request()
        novel = request(options=[{'id': 'a', 'description': 'mora'},
                                 {'id': 'b', 'description': 'roma'}])
        network = model_for(fit)
        encoded = network.encode(novel)
        first, second = (encoded.tokens[candidate[0]] for candidate in encoded.candidates)
        self.assertEqual(first.word_id, second.word_id)
        self.assertEqual(sorted(first.utf8), sorted(second.utf8))
        self.assertNotEqual(first.utf8, second.utf8)
        result = network.forward(encoded)
        self.assertNotEqual(result['logits'][0], result['logits'][1])

    def test_global_context_span_order_is_not_permutation_invariant(self):
        first = request(context='Ada has red fabric. Bo has blue fabric.')
        second = request(context='Bo has blue fabric. Ada has red fabric.')
        network = model_for(first, second)
        self.assertEqual(sorted(tokenize(first.context)), sorted(tokenize(second.context)))
        one = network.forward(network.encode(first))
        two = network.forward(network.encode(second))
        self.assertNotEqual(one['logits'], two['logits'])
        self.assertNotEqual(one['probabilities'], two['probabilities'])
        self.assertTrue(one['accounting']['global_span_position_features'])

    def test_long_single_token_is_refused_without_byte_truncation(self):
        network = model_for(request())
        case = request(context='x' * 257)
        with self.assertRaisesRegex(ModelError, 'encoded_token_length_bound'):
            network.encode(case)

    def test_ids_and_input_option_order_do_not_supply_semantic_features(self):
        original = request()
        renamed = request(request_id='totally-different', options=[
            {'id': 'x', 'description': 'blue fabric'}, {'id': 'z', 'description': 'red fabric'}])
        network = model_for(original, renamed)
        a = network.forward(network.encode(original))
        b = network.forward(network.encode(renamed))
        self.assertEqual(a['logits'], list(reversed(b['logits'])))
        self.assertEqual(a['probabilities'], list(reversed(b['probabilities'])))
        raw = original.as_dict()
        raw['options'].reverse()
        self.assertEqual(network.forward(network.encode(validate_request(raw))), a)

    def test_vocabulary_reads_only_training_text_not_transport_ids_or_anchor_values(self):
        case = request(request_id='never-a-feature', options=[
            {'id': 'secretidentifier', 'description': 'red fabric'},
            {'id': 'otheridentifier', 'description': 'blue fabric'}])
        vocabulary = build_vocabulary([case])
        self.assertNotIn('secretidentifier', vocabulary)
        self.assertNotIn('never-a-feature', vocabulary)
        self.assertEqual(vocabulary[0], '<UNK>')
        self.assertEqual(list(vocabulary[1:]), sorted(vocabulary[1:]))

    def test_all_255_candidates_and_complete_context_are_accounted(self):
        case = request(context=' '.join(['Ada has red fabric.'] * 40), options=[
            {'id': f'x{i:03}', 'description': f'fabric candidate number {i}'} for i in range(255)])
        network = model_for(case, dimension=2, hidden=2)
        result = network.forward(network.encode(case))
        self.assertEqual(len(result['probabilities']), 255)
        self.assertAlmostEqual(sum(result['probabilities']), 1)
        self.assertEqual(result['accounting']['context_tokens'],
                         result['accounting']['context_tokens_processed'])
        self.assertEqual(result['accounting']['context_tokens'], len(tokenize(case.context)))
        self.assertFalse(result['accounting']['truncated'])

    def test_oov_descriptions_are_encoded_and_not_rejected_as_unknown_classes(self):
        fit, new = request(), request(options=[{'id': 'x', 'description': 'previously unseen nebula'},
                                              {'id': 'y', 'description': 'another new phrase'}])
        network = model_for(fit)
        result = network.forward(network.encode(new))
        self.assertEqual(len(result['probabilities']), 2)
        self.assertLess(result['accounting']['known_token_fraction'], 1)

    def test_artifact_round_trip_and_initialization_are_deterministic(self):
        case = request()
        one, two = model_for(case), model_for(case)
        self.assertEqual(one.parameters, two.parameters)
        restored = DecisionNetwork.from_document(one.to_document())
        self.assertEqual(restored.metadata()['architecture'], ARCHITECTURE)
        self.assertEqual(restored.parameters, one.parameters)
        self.assertEqual(restored.forward(restored.encode(case)), one.forward(one.encode(case)))

    def test_invalid_parameter_and_metadata_documents_refused(self):
        network = model_for(request())
        for value in (math.nan, math.inf, True, 1e10):
            document = copy.deepcopy(network.to_document())
            document['parameters'][0] = value
            with self.assertRaises(ModelError):
                DecisionNetwork.from_document(document)
        document = network.to_document()
        document['metadata']['tokenizer'] = 'unrecognized'
        with self.assertRaises(ModelError):
            DecisionNetwork.from_document(document)

    def test_model_binding_and_explicit_token_refusals(self):
        one, other = request(), request(context='An unseen separate vocabulary')
        network, wrong = model_for(one), model_for(other)
        with self.assertRaises(ModelError):
            wrong.forward(network.encode(one))
        oversize = request(prompt=' '.join(['a'] * 1025))
        with self.assertRaises(ModelError):
            network.encode(oversize)
        too_many_spans = request(context='a. ' * 1025)
        with self.assertRaises(ModelError):
            network.encode(too_many_spans)

    def test_bad_targets_and_parameter_length_changes_refused(self):
        case = request()
        network = model_for(case)
        encoded = network.encode(case)
        for target in (-1, True, 2, 1.0):
            with self.assertRaises(ModelError):
                network.loss_and_grad(encoded, target)
        network.parameters.append(0.)
        with self.assertRaises(ModelError):
            network.forward(encoded)

    def test_one_small_gradient_step_lowers_authored_example_loss(self):
        case = request()
        network = model_for(case)
        encoded = network.encode(case)
        before, gradient = network.loss_and_grad(encoded, 0)
        for index, value in enumerate(gradient):
            network.parameters[index] -= .01 * value
        after, _ = network.loss_and_grad(encoded, 0)
        self.assertLess(after, before)


if __name__ == '__main__':
    unittest.main()
