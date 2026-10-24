"""Independent numerical and counterexample checks for the original v3 model.

These are authored implementation fixtures. Sensitivity witnesses establish an
available information path, never trained semantic accuracy or injectivity.
No public corpus, previous study, vendor model or service is used here.
"""
from array import array
from collections import Counter
import copy
import dataclasses
import json
import math
import re
import unicodedata
import unittest
from unittest.mock import patch

from eidos import decision_contextual as contextual
from eidos.decision_contract import validate_request
from eidos.decision_neural import build_vocabulary


Network = getattr(contextual, 'ContextualNetwork', None) or contextual.DecisionNetwork
ModelError = contextual.ModelError


def request(context='Alfa is ochre. Beta is ivory.',
            prompt='What color is Alfa?', options=None, request_id='v3-authored'):
    return validate_request({'format': 'eidos-decision-request-v2',
        'request_id': request_id, 'context': context, 'prompt': prompt,
        'kind': 'CHOICE', 'options': options or [
            {'id': 'a', 'description': 'ochre'},
            {'id': 'b', 'description': 'ivory'}]})


def model_for(*cases, dimension=3, hidden=4, seed=113):
    return Network.initialize(build_vocabulary(cases), dimension=dimension,
                              hidden=hidden, seed=seed)


def scalar_softmax(values):
    peak = max(values)
    masses = [math.exp(value - peak) for value in values]
    total = math.fsum(masses)
    return [mass / total for mass in masses]


def scalar_loss(logits, target):
    peak = max(logits)
    return peak - logits[target] + math.log(math.fsum(
        math.exp(value - peak) for value in logits))


def independent_tokens(text):
    normalized = unicodedata.normalize('NFKC', text).casefold()
    return re.findall(r"\w+(?:['\u2019\u2010-\u2015-]\w+)*|[^\w\s]", normalized)


def independent_context(text):
    """Literal independent segmentation for short authored oracle fixtures."""
    normalized = unicodedata.normalize('NFKC', text).casefold()
    result, current, previous = [], [], 0
    for match in re.finditer(r"\w+(?:['\u2019\u2010-\u2015-]\w+)*|[^\w\s]", normalized):
        if current and '\n' in normalized[previous:match.start()]:
            result.append(current)
            current = []
        term = match.group()
        current.append(term)
        if term in '.!?;':
            result.append(current)
            current = []
        previous = match.end()
    if current:
        result.append(current)
    return result


def independent_layout(vocabulary_size, dimension, hidden):
    """Reconstruct the declared flat parameter contract, not model offsets."""
    offsets = {'embedding': 0}
    cursor = vocabulary_size * dimension
    offsets['conv'] = cursor
    cursor += 4 * dimension
    for name in ('position', 'key', 'value', 'read_fact', 'read_query',
                 'read_option', 'update'):
        offsets[name] = cursor
        cursor += dimension
    for name in ('key_position', 'value_position', 'copy_query', 'match',
                 'candidate', 'joint'):
        offsets[name] = cursor
        cursor += 1
    offsets['hidden_weight'] = cursor
    cursor += hidden * (5 * dimension + 12)
    offsets['hidden_bias'] = cursor
    cursor += hidden
    offsets['output'] = cursor
    cursor += hidden
    offsets['count'] = cursor
    return offsets


def scalar_reference(model, case, *, recursive=True):
    """Evaluate the declared equations using ordinary floats and wire text.

    This does not call encode, forward, a private network helper, its autodiff
    tape or its parameter-offset attributes. In the nonrecursive control the
    head receives three static reads of the initial focus/query instead.
    """
    vocabulary = tuple(model.vocabulary)
    d, h, p = model.dimension, model.hidden, model.parameters
    offsets = independent_layout(len(vocabulary), d, h)
    if offsets['count'] != len(p):
        raise AssertionError('declared independent parameter layout differs')
    index = {word: i for i, word in enumerate(vocabulary)}
    facts = independent_context(case.context)
    question = independent_tokens(case.prompt)
    options = [independent_tokens(option.description) for option in case.options]

    def dot(a, b):
        return math.fsum(x * y for x, y in zip(a, b))

    def centered(length):
        return [0.] if length == 1 else [2 * i / (length - 1) - 1
                                         for i in range(length)]

    def embedding(word):
        start = index.get(word, 0) * d
        return [p[start + j] for j in range(d)]

    def vector(name):
        start = offsets[name]
        return [p[start + j] for j in range(d)]

    def contextual_states(words):
        raw = [embedding(word) for word in words]
        positions = centered(len(words))
        states = []
        for t, row in enumerate(raw):
            state = []
            for j in range(d):
                value = row[j] + p[offsets['position'] + j] * positions[t]
                for k, delta in enumerate((-2, -1, 1, 2)):
                    neighbor = t + delta
                    if 0 <= neighbor < len(words):
                        value += p[offsets['conv'] + k*d + j] * raw[neighbor][j]
                state.append(math.tanh(value))
            states.append(state)
        return states

    def mean(states):
        return [math.fsum(row[j] for row in states) / len(states)
                for j in range(d)]

    context_states = [contextual_states(words) for words in facts]
    fact_vectors = [mean(states) for states in context_states]
    q = mean(contextual_states(question))
    key, value = vector('key'), vector('value')
    read_fact, read_query = vector('read_fact'), vector('read_query')
    read_option, update = vector('read_option'), vector('update')
    key_weights = [scalar_softmax([
        dot(key, state) + p[offsets['key_position']] * position
        for state, position in zip(states, centered(len(states)))])
        for states in context_states]
    present = set(word for words in facts for word in words)
    focus_counts = Counter(word for word in question if word in present)
    normalizer = sum(focus_counts.values())
    initial_focus = ({word: count / normalizer for word, count in focus_counts.items()}
                     if normalizer else {})
    logits = []
    for candidate in options:
        o = mean(contextual_states(candidate))
        focus = dict(initial_focus)
        u = [q[j] + o[j] for j in range(d)]
        initial_u = list(u)
        candidate_set = set(candidate)
        overlaps = [sum(word in candidate_set for word in words) / len(words)
                    for words in facts]
        reads, supports, evidences, key_returns, value_returns = [], [], [], [], []
        for hop in range(3):
            matching = [math.fsum(weight * focus.get(word, 0.)
                         for weight, word in zip(weights, words))
                        for weights, words in zip(key_weights, facts)]
            energy = []
            for f, match, overlap in zip(fact_vectors, matching, overlaps):
                score = dot(read_fact, f)
                score += math.fsum(read_query[j] * f[j] * u[j] for j in range(d))
                score += math.fsum(read_option[j] * f[j] * o[j] for j in range(d))
                score += p[offsets['match']] * match
                score += p[offsets['candidate']] * overlap
                score += p[offsets['joint']] * match * overlap
                energy.append(score)
            attention = scalar_softmax(energy)
            r = [math.fsum(a * f[j] for a, f in zip(attention, fact_vectors))
                 for j in range(d)]
            reads.append(r)
            supports.append(math.fsum(a * match * overlap
                for a, match, overlap in zip(attention, matching, overlaps)))
            evidences.append(math.fsum(a * overlap for a, overlap in zip(attention, overlaps)))
            key_returns.append(math.fsum(a * math.fsum(weight for word, weight in zip(words, weights)
                if word in candidate_set) for a, words, weights in zip(attention, facts, key_weights)))
            next_focus = {}
            for a, states, words in zip(attention, context_states, facts):
                positions = centered(len(states))
                weights = scalar_softmax([
                    dot(value, state) + p[offsets['value_position']] * position +
                    p[offsets['copy_query']] * dot(state, u) / math.sqrt(d)
                    for state, position in zip(states, positions)])
                for weight, word in zip(weights, words):
                    next_focus[word] = next_focus.get(word, 0.) + a * weight
            value_returns.append(math.fsum(next_focus.get(word, 0.) for word in candidate_set))
            if hop < 2 and recursive:
                focus = next_focus
                u = [math.tanh(u[j] + update[j] * r[j]) for j in range(d)]
            elif not recursive:
                focus = dict(initial_focus)
                u = list(initial_u)
        features = q + o + [x for r in reads for x in r] + supports + evidences + key_returns + value_returns
        hidden = []
        for k in range(h):
            start = offsets['hidden_weight'] + k * (5*d + 12)
            activation = p[offsets['hidden_bias'] + k] + math.fsum(
                p[start + j] * feature for j, feature in enumerate(features))
            hidden.append(math.tanh(activation))
        logits.append(math.fsum(p[offsets['output'] + k] * hidden[k] for k in range(h)))
    return {'logits': logits, 'probabilities': scalar_softmax(logits)}


class DecisionContextualTests(unittest.TestCase):
    def numeric_case(self):
        return request(context='a b. b c.', prompt='a b', options=[
            {'id': 'a', 'description': 'b a'},
            {'id': 'b', 'description': 'c b'}])

    def test_every_parameter_gradient_matches_independent_finite_differences(self):
        case = self.numeric_case()
        model = model_for(case, dimension=2, hidden=2)
        encoded = model.encode(case)
        before = array('d', model.parameters)
        loss, gradient = model.loss_and_grad(encoded, 1)
        self.assertEqual(len(gradient), model.parameter_count)
        self.assertEqual(model.parameters, before)
        self.assertAlmostEqual(loss, scalar_loss(scalar_reference(model, case)['logits'], 1), places=12)
        epsilon = 1e-5
        for index, analytic in enumerate(gradient):
            self.assertTrue(math.isfinite(analytic), f'gradient {index}')
            original = model.parameters[index]
            try:
                model.parameters[index] = original + epsilon
                plus = scalar_loss(scalar_reference(model, case)['logits'], 1)
                model.parameters[index] = original - epsilon
                minus = scalar_loss(scalar_reference(model, case)['logits'], 1)
            finally:
                model.parameters[index] = original
            numeric = (plus - minus) / (2 * epsilon)
            self.assertAlmostEqual(analytic, numeric, delta=3e-7,
                                   msg=f'parameter {index}: analytic={analytic}, numeric={numeric}')

    def test_forward_matches_independent_context_copy_and_three_read_equations(self):
        case = self.numeric_case()
        model = model_for(case, dimension=2, hidden=3)
        actual = model.forward(model.encode(case))
        expected = scalar_reference(model, case)
        for key in ('logits', 'probabilities'):
            self.assertEqual(len(actual[key]), len(expected[key]))
            for observed, wanted in zip(actual[key], expected[key]):
                self.assertAlmostEqual(observed, wanted, places=12)
        static = scalar_reference(model, case, recursive=False)
        self.assertGreater(max(abs(a-b) for a, b in zip(expected['logits'], static['logits'])), 1e-12)

    def test_empty_question_context_intersection_has_exact_zero_initial_focus(self):
        case = request(context='a b. b c.', prompt='entirely separate words',
            options=[{'id': 'a', 'description': 'a'}, {'id': 'b', 'description': 'c'}])
        model = model_for(case, dimension=2, hidden=2)
        expected = scalar_reference(model, case)
        actual = model.forward(model.encode(case))
        for a, b in zip(actual['logits'], expected['logits']):
            self.assertAlmostEqual(a, b, places=12)
        self.assertTrue(all(math.isfinite(p) for p in actual['probabilities']))

    def test_old_abba_baab_parameter_independent_collision_has_a_v3_witness(self):
        options = [{'id': 'a', 'description': 'a'}, {'id': 'b', 'description': 'b'}]
        first = request(context='a b b a .', prompt='Which symbol appears first?', options=options)
        second = request(context='b a a b .', prompt=first.prompt, options=options)
        self.assertEqual(Counter(independent_tokens(first.context)), Counter(independent_tokens(second.context)))
        model = model_for(first, second, dimension=3, hidden=4, seed=113)
        left = model.forward(model.encode(first))['logits']
        right = model.forward(model.encode(second))['logits']
        self.assertGreater(max(abs(a-b) for a, b in zip(left, right)), 1e-12)
        # This is one fixed-parameter sensitivity witness, not correct answers.
        for actual, expected in zip(left, scalar_reference(model, first)['logits']):
            self.assertAlmostEqual(actual, expected, places=12)

    def test_entity_property_assignment_swap_is_detectable_without_changed_word_counts(self):
        first = request(context='Alfa is ochre. Beta is ivory.')
        second = request(context='Alfa is ivory. Beta is ochre.')
        self.assertEqual(Counter(independent_tokens(first.context)), Counter(independent_tokens(second.context)))
        model = model_for(first, second)
        left = model.forward(model.encode(first))['logits']
        right = model.forward(model.encode(second))['logits']
        self.assertGreater(max(abs(a-b) for a, b in zip(left, right)), 1e-12)

    def test_novel_anagram_name_alignment_survives_shared_unk_embeddings(self):
        # No training vocabulary contains either spelling. Their exact token
        # identity, rather than an assumed pretrained name meaning, is needed.
        fit = request(context='red and blue.', prompt='Which color?', options=[
            {'id': 'a', 'description': 'red'}, {'id': 'b', 'description': 'blue'}])
        first = request(context='abba has red. baab has blue.', prompt='What has abba?', options=[
            {'id': 'a', 'description': 'red'}, {'id': 'b', 'description': 'blue'}])
        second = request(context=first.context, prompt='What has baab?', options=first.as_dict()['options'])
        model = model_for(fit)
        self.assertNotIn('abba', model.vocabulary)
        self.assertNotIn('baab', model.vocabulary)
        left = model.forward(model.encode(first))['logits']
        right = model.forward(model.encode(second))['logits']
        self.assertGreater(max(abs(a-b) for a, b in zip(left, right)), 1e-12)

    def test_candidate_readout_preserves_unknown_equal_count_symbol_identity(self):
        fit=request(context='red and blue.',prompt='Which color?',options=[
            {'id':'a','description':'red'},{'id':'b','description':'blue'}])
        options=[{'id':'a','description':'abba'},{'id':'b','description':'baab'}]
        first=request(context='abba baab baab abba .',prompt='Which symbol appears first?',options=options)
        second=request(context='baab abba abba baab .',prompt=first.prompt,options=options)
        model=model_for(fit,dimension=2,hidden=2)
        self.assertNotIn('abba',model.vocabulary);self.assertNotIn('baab',model.vocabulary)
        model.parameters=array('d',[0.])*model.parameter_count
        model.parameters[model.key_position_offset]=-4.
        # One explicit head neuron reads the first key-return probability.
        model.parameters[model.hidden_weight_offset+5*model.dimension+6]=1.
        model.parameters[model.output_offset]=1.
        left=model.forward(model.encode(first))['logits']
        right=model.forward(model.encode(second))['logits']
        self.assertGreater(left[0],left[1]);self.assertGreater(right[1],right[0])
        weights=scalar_softmax([-4*x for x in (-1.,-.5,0.,.5,1.)])
        self.assertAlmostEqual(left[0],math.tanh(weights[0]+weights[3]),places=12)
        self.assertAlmostEqual(left[1],math.tanh(weights[1]+weights[2]),places=12)

    def test_unicode_normalization_and_case_preserve_the_same_semantic_input(self):
        first = request(context='Élin has ochre. Beta has ivory.', prompt='What has Élin?')
        second = request(context='E\u0301LIN HAS OCHRE. BETA HAS IVORY.', prompt='WHAT HAS E\u0301LIN?')
        model = model_for(first)
        a = model.forward(model.encode(first))
        b = model.forward(model.encode(second))
        self.assertEqual(a['logits'], b['logits'])
        self.assertEqual(a['probabilities'], b['probabilities'])

    def test_opaque_ids_and_input_candidate_order_do_not_supply_semantics(self):
        first = request()
        renamed = request(request_id='unrelated-id', options=[
            {'id': 'x', 'description': 'ivory'}, {'id': 'z', 'description': 'ochre'}])
        model = model_for(first)
        a = model.forward(model.encode(first))
        b = model.forward(model.encode(renamed))
        self.assertEqual(a['logits'], list(reversed(b['logits'])))
        self.assertEqual(a['probabilities'], list(reversed(b['probabilities'])))
        raw = first.as_dict()
        raw['options'].reverse()
        c = model.forward(model.encode(validate_request(raw)))
        self.assertEqual(a['logits'], c['logits'])

    def test_numeric_anchor_values_are_decoding_metadata_not_neural_features(self):
        raw = {'format': 'eidos-decision-request-v2', 'request_id': 'anchors',
               'context': 'The band is low.', 'prompt': 'Select its band.',
               'kind': 'SCORE', 'minimum': 0, 'maximum': 1,
               'options': [{'id': 'a', 'description': 'low', 'value': 0},
                           {'id': 'b', 'description': 'high', 'value': 1}]}
        first = validate_request(raw)
        raw['maximum'] = 100
        raw['options'][1]['value'] = 100
        second = validate_request(raw)
        model = model_for(first)
        a = model.forward(model.encode(first))
        b = model.forward(model.encode(second))
        self.assertEqual(a['logits'], b['logits'])
        self.assertEqual(a['probabilities'], b['probabilities'])

    def test_complete_255_candidate_distribution_or_explicit_resource_refusal(self):
        case = request(context='a b.', prompt='a', options=[
            {'id': f'c{i:03d}', 'description': f'answer{i}'} for i in range(255)])
        model = model_for(case, dimension=2, hidden=2)
        try:
            encoded = model.encode(case)
            result = model.forward(encoded)
        except ModelError as error:
            self.assertRegex(str(error), 'budget|bound|limit')
            return
        self.assertEqual(len(result['logits']), 255)
        self.assertEqual(len(result['probabilities']), 255)
        self.assertTrue(all(math.isfinite(p) and 0 <= p <= 1 for p in result['probabilities']))
        self.assertAlmostEqual(math.fsum(result['probabilities']), 1., places=12)
        self.assertFalse(result['accounting']['truncated'])

    def test_complete_context_accounting_and_explicit_token_refusal(self):
        case = request(context='Alfa has ochre.\nBeta has ivory.')
        model = model_for(case)
        result = model.forward(model.encode(case))
        count = len(independent_tokens(case.context))
        self.assertEqual(result['accounting']['context_tokens'], count)
        self.assertEqual(result['accounting']['context_tokens_processed'], count)
        self.assertFalse(result['accounting']['truncated'])
        with self.assertRaises(ModelError):
            model.encode(request(context='x' * 257))
        too_many = request(context='a ' * (contextual.MAX_CONTEXT_TOKENS + 1))
        with self.assertRaises(ModelError):
            model.encode(too_many)

    def test_work_refusal_is_enforced_before_scoring_partial_candidates(self):
        case = request()
        model = model_for(case)
        with patch.object(contextual, 'MAX_WORK_UNITS', 1):
            with self.assertRaises(ModelError):
                model.encode(case)

    def test_initialization_is_deterministic_and_settings_are_strict(self):
        case = request()
        a = model_for(case)
        b = model_for(case)
        c = model_for(case, seed=114)
        self.assertEqual(a.parameters, b.parameters)
        self.assertEqual(a.metadata(), b.metadata())
        self.assertNotEqual(a.parameters, c.parameters)
        vocabulary = build_vocabulary([case])
        for settings in ({'dimension': True}, {'hidden': False}, {'seed': True},
                         {'seed': -1}, {'dimension': 0}, {'hidden': 10000}):
            with self.subTest(settings=settings), self.assertRaises(ModelError):
                Network.initialize(vocabulary, **settings)

    def test_data_only_document_round_trip_and_legacy_architecture_refusal(self):
        case = request()
        model = model_for(case)
        wire = json.loads(json.dumps(model.to_document(), allow_nan=False))
        restored = Network.from_document(wire)
        self.assertEqual(restored.metadata(), model.metadata())
        self.assertEqual(restored.parameters, model.parameters)
        self.assertEqual(restored.forward(restored.encode(case))['logits'],
                         model.forward(model.encode(case))['logits'])
        bad = copy.deepcopy(wire)
        bad['metadata']['architecture'] = 'eidos-question-conditioned-attention-mlp-v2'
        with self.assertRaises(ModelError):
            Network.from_document(bad)
        for replacement in (float('nan'), float('inf'), True, '__import__("os")'):
            bad = copy.deepcopy(wire)
            bad['parameters'][0] = replacement
            with self.subTest(replacement=repr(replacement)), self.assertRaises(ModelError):
                Network.from_document(bad)
        bad = copy.deepcopy(wire)
        bad['metadata']['unrecognized_executable_extension'] = 'refuse'
        with self.assertRaises(ModelError):
            Network.from_document(bad)

    def test_wrong_encoded_model_and_invalid_targets_are_refused(self):
        case = request()
        model = model_for(case)
        encoded = model.encode(case)
        self.assertTrue(dataclasses.is_dataclass(encoded))
        wrong = dataclasses.replace(encoded, model_signature='0' * 64)
        with self.assertRaises(ModelError):
            model.forward(wrong)
        for target in (-1, 2, True, '0'):
            with self.subTest(target=target), self.assertRaises(ModelError):
                model.loss_and_grad(encoded, target)
        with self.assertRaises(ModelError):
            model.forward(None)


if __name__ == '__main__':
    unittest.main()
