"""Independent authored mathematics/capacity checks for original routed v4.

The scalar oracle uses raw wire text and declared equations, not runtime
encoding, offsets, helpers or tape. Capacity witnesses use explicit parameters;
they do not claim learned language, benchmark quality or injectivity.
"""
from array import array
from collections import Counter
import copy
import dataclasses
import hashlib
import json
import math
import re
import unicodedata
import unittest
from unittest.mock import patch

from eidos import decision_routed as routed
from eidos.decision_contract import validate_request
from eidos.decision_neural import build_vocabulary

Network = routed.DecisionNetwork
ModelError = routed.ModelError


def request(context='a b. b c. c a.', prompt='a b', options=None, request_id='v4-authored'):
    return validate_request({'format': 'eidos-decision-request-v2',
        'request_id': request_id, 'context': context, 'prompt': prompt,
        'kind': 'CHOICE', 'options': options or [
            {'id': 'a', 'description': 'b a'}, {'id': 'b', 'description': 'c b'}]})


def model_for(*cases, dimension=2, hidden=2, seed=113):
    return Network.initialize(build_vocabulary(cases), dimension=dimension,
                              hidden=hidden, seed=seed)


def softmax(values):
    peak = max(values)
    masses = [math.exp(value - peak) for value in values]
    total = math.fsum(masses)
    return [mass / total for mass in masses]


def loss_from_logits(logits, target):
    peak = max(logits)
    return peak - logits[target] + math.log(math.fsum(math.exp(x - peak) for x in logits))


def tokens(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    return re.findall(r"\w+(?:['\u2019\u2010-\u2015-]\w+)*|[^\w\s]", text)


def context_spans(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    spans, active, previous = [], [], 0
    for match in re.finditer(r"\w+(?:['\u2019\u2010-\u2015-]\w+)*|[^\w\s]", text):
        if active and '\n' in text[previous:match.start()]:
            spans.append(active)
            active = []
        word = match.group()
        active.append(word)
        if word in '.!?;' or len(active) == 48:
            spans.append(active)
            active = []
        previous = match.end()
    if active:
        spans.append(active)
    return spans


def subword_reference(word):
    word = unicodedata.normalize('NFKC', word).casefold()
    padded = '\x02' + word + '\x03'
    counts = Counter()
    for width in (1, 2, 3):
        for position in range(len(padded) - width + 1):
            payload = b'eidos-subword-v1\x00' + bytes((width,)) + padded[position:position+width].encode('utf-8')
            bucket = int.from_bytes(hashlib.sha256(payload).digest()[:8], 'big') % 32
            counts[bucket] += 1
    total = sum(counts.values())
    return tuple((bucket, counts[bucket] / total) for bucket in sorted(counts))


def layout(vocabulary_size, dimension, hidden):
    """Independent written shape contract; no runtime offset attributes."""
    result = {'embedding': 0}
    cursor = vocabulary_size * dimension
    result['conv'] = cursor
    cursor += 4 * dimension
    for name in ('position', 'key', 'value', 'read_fact', 'read_query', 'read_option', 'update'):
        result[name] = cursor
        cursor += dimension
    for name in ('key_position', 'value_position', 'copy_query', 'match', 'candidate', 'joint'):
        result[name] = cursor
        cursor += 1
    for name, count in (('subword', 32*dimension), ('key_query', dimension),
                        ('key_pos_query', dimension), ('value_query', dimension),
                        ('value_pos_query', dimension), ('hop', 3*dimension),
                        ('depth_weight', 3*dimension), ('depth_bias', 3), ('copy_scale', 1),
                        ('hidden_weight', hidden*(5*dimension+12)),
                        ('hidden_bias', hidden), ('output', hidden)):
        result[name] = cursor
        cursor += count
    result['count'] = cursor
    return result


def scalar_reference(model, case, *, recursive=True):
    """Raw-text float equations; no model helper/encode/forward/tape calls."""
    vocabulary, d, h = tuple(model.vocabulary), model.dimension, model.hidden
    p = model.parameters
    off = layout(len(vocabulary), d, h)
    if len(p) != off['count']:
        raise AssertionError('independent written layout mismatch')
    index = {word: i for i, word in enumerate(vocabulary)}
    facts, question = context_spans(case.context), tokens(case.prompt)
    candidates = [tokens(option.description) for option in case.options]

    def dot(a, b):
        return math.fsum(x*y for x, y in zip(a, b))

    def vector(name):
        return [p[off[name]+j] for j in range(d)]

    def centered(length):
        return [0.] if length == 1 else [2*i/(length-1)-1 for i in range(length)]

    def embedding(word):
        base = index.get(word, 0) * d
        features = subword_reference(word)
        return [p[base+j] + math.fsum(weight*p[off['subword']+bucket*d+j]
            for bucket, weight in features) for j in range(d)]

    def contextual(words):
        raw = [embedding(word) for word in words]
        positions = centered(len(words))
        states = []
        for t, row in enumerate(raw):
            state = []
            for j in range(d):
                value = row[j] + p[off['position']+j]*positions[t]
                for k, delta in enumerate((-2, -1, 1, 2)):
                    neighbor = t + delta
                    if 0 <= neighbor < len(words):
                        value += p[off['conv']+k*d+j]*raw[neighbor][j]
                state.append(math.tanh(value))
            states.append(state)
        return states

    def mean(states):
        return [math.fsum(row[j] for row in states)/len(states) for j in range(d)]

    fact_states = [contextual(words) for words in facts]
    fact_vectors = [mean(states) for states in fact_states]
    q = mean(contextual(question))
    depth_weights = softmax([dot(p[off['depth_weight']+hop*d:off['depth_weight']+(hop+1)*d], q)
        + p[off['depth_bias']+hop] for hop in range(3)])
    scale = p[off['copy_scale']]
    scale = scale + math.log1p(math.exp(-scale)) if scale >= 0 else math.log1p(math.exp(scale))
    present = set(word for fact in facts for word in fact)
    focus_counts = Counter(word for word in question if word in present)
    total = sum(focus_counts.values())
    initial_focus = {word: count/total for word, count in focus_counts.items()} if total else {}
    logits = []
    for candidate in candidates:
        o = mean(contextual(candidate))
        u = [a+b for a, b in zip(q, o)]
        initial_u, focus = list(u), dict(initial_focus)
        members = set(candidate)
        overlaps = [sum(word in members for word in fact)/len(fact) for fact in facts]
        reads, supports, evidence, key_returns, value_returns = [], [], [], [], []
        for hop in range(3):
            control = [math.tanh(q[j]+u[j]+p[off['hop']+hop*d+j]) for j in range(d)]
            key_position = p[off['key_position']] + dot(vector('key_pos_query'), control)
            value_position = p[off['value_position']] + dot(vector('value_pos_query'), control)
            keys = [softmax([dot(vector('key'), state) +
                math.fsum(p[off['key_query']+j]*state[j]*control[j] for j in range(d)) +
                key_position*position for state, position in zip(states, centered(len(states)))])
                for states in fact_states]
            matches = [math.fsum(weight*focus.get(word, 0.) for weight, word in zip(key, fact))
                       for key, fact in zip(keys, facts)]
            energies = []
            for fact, match, overlap in zip(fact_vectors, matches, overlaps):
                energy = dot(vector('read_fact'), fact)
                energy += math.fsum(p[off['read_query']+j]*fact[j]*u[j] for j in range(d))
                energy += math.fsum(p[off['read_option']+j]*fact[j]*o[j] for j in range(d))
                energy += p[off['match']]*match + p[off['candidate']]*overlap
                energy += p[off['joint']]*match*overlap
                energies.append(energy)
            attention = softmax(energies)
            r = [math.fsum(a*fact[j] for a, fact in zip(attention, fact_vectors)) for j in range(d)]
            reads.extend(r)
            supports.append(math.fsum(a*m*c for a, m, c in zip(attention, matches, overlaps)))
            evidence.append(math.fsum(a*c for a, c in zip(attention, overlaps)))
            key_returns.append(math.fsum(a*math.fsum(weight for word, weight in zip(fact, key)
                if word in members) for a, fact, key in zip(attention, facts, keys)))
            copied = {}
            for a, states, fact in zip(attention, fact_states, facts):
                values = softmax([dot(vector('value'), state) +
                    math.fsum(p[off['value_query']+j]*state[j]*control[j] for j in range(d)) +
                    value_position*position + p[off['copy_query']]*dot(state, u)/math.sqrt(d)
                    for state, position in zip(states, centered(len(states)))])
                for word, probability in zip(fact, values):
                    copied[word] = copied.get(word, 0.) + a*probability
            value_returns.append(math.fsum(copied.get(word, 0.) for word in members))
            if hop < 2 and recursive:
                focus = copied
                u = [math.tanh(u[j]+p[off['update']+j]*r[j]) for j in range(d)]
            elif not recursive:
                focus = dict(initial_focus)
                u = list(initial_u)
        features = q + o + reads + supports + evidence + key_returns + value_returns
        hidden = [math.tanh(p[off['hidden_bias']+k] + dot(
            p[off['hidden_weight']+k*(5*d+12):off['hidden_weight']+(k+1)*(5*d+12)], features))
            for k in range(h)]
        logits.append(dot(p[off['output']:off['output']+h], hidden) +
            scale*math.log(1e-8+dot(depth_weights, value_returns)))
    return {'logits': logits, 'probabilities': softmax(logits)}


class DecisionRoutedTests(unittest.TestCase):
    def modest_model(self, *cases):
        model = model_for(*cases)
        model.parameters = array('d', (.12*math.sin(i*.73)+.04*math.cos(i*.19)
                                      for i in range(model.parameter_count)))
        return model

    def test_fused_contractions_keep_ordered_values_duplicate_gradients_and_reduce_tape_nodes(self):
        # Closed polynomial derivatives are independent of both the fused
        # contractions and the scalar AD expansion used for node accounting.
        def operands(name, x, y):
            if name == 'sum':
                return ([x, 2., y, x, -.25],)
            if name == 'dot':
                return ([x, 2., y, x], [y, x, -3., x])
            return ([x, 2., y, x, 1.], [y, x, -3., x, 2.],
                    [x, y, 2., x, 3.])

        helpers = {'sum': routed._sum, 'dot': routed._dot, 'triple': routed._triple_dot}
        expected = {'sum': (3.5, (2., 1.)),
                    'dot': (5.375, (3.75, -1.75)),
                    'triple': (9.40625, (1.3125, -1.9375))}
        for name, helper in helpers.items():
            with self.subTest(contraction=name):
                tape = routed._Tape()
                x, y = tape.node(1.25), tape.node(-.75)
                before = len(tape.values)
                # Generators exercise the accepted one-pass operand contract.
                fused = helper(*(iter(items) for items in operands(name, x, y)))
                fused_nodes = len(tape.values)-before
                self.assertEqual(fused_nodes, 1)
                value, derivatives = expected[name]
                self.assertEqual(fused.value, value)
                for observed, wanted in zip(tape.backward(fused, [x, y]), derivatives):
                    self.assertAlmostEqual(observed, wanted, places=14)
                expanded_tape = routed._Tape()
                ex, ey = expanded_tape.node(1.25), expanded_tape.node(-.75)
                parts = operands(name, ex, ey)
                expanded = 0.
                if name == 'sum':
                    for term in parts[0]:
                        expanded = expanded + term
                elif name == 'dot':
                    for a, b in zip(*parts):
                        expanded = expanded + a*b
                else:
                    for a, b, c in zip(*parts):
                        expanded = expanded + (a*b)*c
                self.assertEqual(fused.value, expanded.value)
                self.assertLess(fused_nodes, len(expanded_tape.values)-2)
                self.assertEqual(tape.backward(fused, [x, y]),
                                 expanded_tape.backward(expanded, [ex, ey]))
                # The float path must retain precisely the left-associated
                # product/addition order, including cancellation-sensitive sums.
                float_parts = operands(name, 1.25, -.75)
                self.assertEqual(helper(*(iter(items) for items in float_parts)), value)
                self.assertEqual(helper(*(() for _ in float_parts)), 0.)
                foreign = routed._Tape().node(.5)
                bad = ([x, foreign],) if name == 'sum' else (
                    ([x], [foreign]) if name == 'dot' else ([x], [2.], [foreign]))
                with self.assertRaisesRegex(ModelError, 'incompatible_gradient_tapes'):
                    helper(*bad)
        ordered = 0.
        cancellation = (1e16, 1., -1e16, 3.)
        for term in cancellation:
            ordered = ordered + term
        self.assertEqual(routed._sum(iter(cancellation)), ordered)
        self.assertEqual(routed._dot(cancellation, (1.,)*4), ordered)
        self.assertEqual(routed._triple_dot(cancellation, (1.,)*4, (1.,)*4), ordered)

    def test_all_coordinates_match_independent_central_differences_with_live_routing_blocks(self):
        cases = [request(), request(context='a a b\nb c c\nc a b', prompt='apart', options=[
            {'id': 'a', 'description': 'a c'}, {'id': 'b', 'description': 'b b'},
            {'id': 'c', 'description': 'c a'}])]
        model = self.modest_model(*cases)
        off = layout(len(model.vocabulary), model.dimension, model.hidden)
        live = Counter()
        ranges = [('subword', 'key_query'), ('key_query', 'key_pos_query'),
            ('key_pos_query', 'value_query'), ('value_query', 'value_pos_query'),
            ('value_pos_query', 'hop'), ('hop', 'depth_weight'),
            ('depth_weight', 'depth_bias'), ('depth_bias', 'copy_scale'),
            ('copy_scale', 'hidden_weight')]
        for case, target in zip(cases, (1, 2)):
            encoded = model.encode(case)
            before = array('d', model.parameters)
            loss, gradient = model.loss_and_grad(encoded, target)
            self.assertEqual(model.parameters, before)
            expected = scalar_reference(model, case)
            self.assertAlmostEqual(loss, loss_from_logits(expected['logits'], target), places=11)
            self.assertEqual(len(gradient), off['count'])
            for start, end in ranges:
                live[start] += math.fsum(abs(x) for x in gradient[off[start]:off[end]])
            for index, analytic in enumerate(gradient):
                self.assertTrue(math.isfinite(analytic))
                original = model.parameters[index]
                epsilon = 1e-5*max(1., abs(original))
                try:
                    model.parameters[index] = original + epsilon
                    plus = loss_from_logits(scalar_reference(model, case)['logits'], target)
                    model.parameters[index] = original - epsilon
                    minus = loss_from_logits(scalar_reference(model, case)['logits'], target)
                finally:
                    model.parameters[index] = original
                numeric = (plus-minus)/(2*epsilon)
                self.assertAlmostEqual(analytic, numeric, delta=3e-7+3e-6*abs(numeric),
                    msg=f'coordinate {index}: analytic={analytic}, independent={numeric}')
            self.assertEqual(model.parameters, before)
        for name, magnitude in live.items():
            self.assertGreater(magnitude, 1e-13, f'new block {name} was dead in both fixtures')

    def test_forward_matches_independent_scalar_subwords_routing_and_recursive_depth_mix(self):
        case = request()
        model = self.modest_model(case)
        actual, expected = model.forward(model.encode(case)), scalar_reference(model, case)
        for key in ('logits', 'probabilities'):
            for a, b in zip(actual[key], expected[key]):
                self.assertAlmostEqual(a, b, places=12)
        static = scalar_reference(model, case, recursive=False)
        self.assertGreater(max(abs(a-b) for a, b in zip(expected['logits'], static['logits'])), 1e-12)

    def test_full_boundary_ngram_hash_contract_and_tail_without_prefix_cutoff(self):
        for word in ('once', 'twice', 'abba', 'baab', 'Élin', 'E\u0301LIN', 'a'*255+'z'):
            with self.subTest(word=word[:18]):
                actual = routed.subword_features(word)
                self.assertEqual(actual, subword_reference(word))
                self.assertAlmostEqual(math.fsum(weight for _, weight in actual), 1., places=14)
                self.assertTrue(all(0 <= bucket < 32 and weight > 0 for bucket, weight in actual))
        self.assertNotEqual(routed.subword_features('a'*128+'b'), routed.subword_features('a'*128+'c'))
        self.assertEqual(routed.subword_features('Élin'), routed.subword_features('E\u0301LIN'))
        for bad in ('', 'x'*257, '\ufb03'*100):
            with self.subTest(bad=bad[:12]), self.assertRaises(ModelError):
                routed.subword_features(bad)

    def test_unknown_noncontext_instruction_markers_have_a_distinct_information_path(self):
        fit = request(context='red blue', prompt='select', options=[
            {'id': 'a', 'description': 'red'}, {'id': 'b', 'description': 'blue'}])
        first = request(context='abba has red. baab has blue.', prompt='From abba move once',
                        options=fit.as_dict()['options'])
        second = request(context=first.context, prompt='From abba move twice', options=first.as_dict()['options'])
        model = model_for(fit, dimension=3, hidden=4)
        self.assertNotIn('once', model.vocabulary)
        self.assertNotIn('twice', model.vocabulary)
        a, b = model.forward(model.encode(first)), model.forward(model.encode(second))
        self.assertGreater(max(abs(x-y) for x, y in zip(a['probabilities'], b['probabilities'])), 1e-12)
        for actual, expected in zip(a['logits'], scalar_reference(model, first)['logits']):
            self.assertAlmostEqual(actual, expected, places=12)
        # Remove only the learned spelling channel: this restores the exact
        # unknown-instruction collision, proving the path without semantics.
        off = layout(len(model.vocabulary), model.dimension, model.hidden)
        for i in range(off['subword'], off['key_query']):
            model.parameters[i] = 0.
        a, b = model.forward(model.encode(first)), model.forward(model.encode(second))
        self.assertEqual(a['logits'], b['logits'])

    def test_question_alone_can_reverse_unknown_token_key_and_value_selection(self):
        options = [{'id': 'a', 'description': 'abba'}, {'id': 'b', 'description': 'baab'}]
        left = request(context='abba baab', prompt='left', options=options)
        right = request(context=left.context, prompt='right', options=options)
        for channel in ('key', 'value'):
            with self.subTest(channel=channel):
                model = Network.initialize(('<UNK>', 'left', 'right'), dimension=2, hidden=2)
                model.parameters = array('d', [0.])*model.parameter_count
                off = layout(3, 2, 2)
                model.parameters[2] = 1.  # left embedding[0]
                model.parameters[4] = -1.  # right embedding[0]
                model.parameters[off['position']+1] = 1.
                model.parameters[off[channel+'_pos_query']] = -4.
                if channel == 'key':
                    model.parameters[off['hidden_weight']+5*2+6] = 1.
                    model.parameters[off['output']] = 1.
                a, b = model.forward(model.encode(left)), model.forward(model.encode(right))
                self.assertGreater(a['logits'][0], a['logits'][1])
                self.assertGreater(b['logits'][1], b['logits'][0])
                for case, actual in ((left, a), (right, b)):
                    expected = scalar_reference(model, case)
                    for x, y in zip(actual['logits'], expected['logits']):
                        self.assertAlmostEqual(x, y, places=12)
                if channel == 'key':
                    gain = -4*math.tanh(2*math.tanh(1.))
                    masses = softmax([-gain, gain])
                    self.assertAlmostEqual(a['logits'][0]-a['logits'][1],
                        math.tanh(masses[0])-math.tanh(masses[1]), places=12)
                model.parameters[off[channel+'_pos_query']] = 0.
                uniform = model.forward(model.encode(left))
                self.assertAlmostEqual(uniform['logits'][0], uniform['logits'][1], places=12)

    def test_question_alone_can_select_first_or_second_copied_answer(self):
        options = [{'id': 'a', 'description': 'bb'}, {'id': 'b', 'description': 'cc'},
                   {'id': 'c', 'description': 'dd'}]
        first = request(context='aa bb\nbb cc\ncc dd', prompt='aa one', options=options)
        second = request(context=first.context, prompt='aa two', options=options)
        model = Network.initialize(('<UNK>', 'one', 'two'), dimension=2, hidden=2)
        model.parameters = array('d', [0.])*model.parameter_count
        off = layout(3, 2, 2)
        model.parameters[2] = 2.
        model.parameters[4] = -2.
        model.parameters[off['position']+1] = 1.
        model.parameters[off['key_position']] = -8.
        model.parameters[off['value_position']] = 8.
        model.parameters[off['match']] = 40.
        model.parameters[off['depth_weight']] = 20.
        model.parameters[off['depth_weight']+2] = -20.
        model.parameters[off['depth_bias']+2] = -20.
        model.parameters[off['copy_scale']] = 4.
        predictions = [model.forward(model.encode(case)) for case in (first, second)]
        self.assertEqual(max(range(3), key=predictions[0]['logits'].__getitem__), 0)
        self.assertEqual(max(range(3), key=predictions[1]['logits'].__getitem__), 1)
        # Independently compute a three-fact pointer transition matrix, then
        # the declared question depth mixture; no contextual helper involved.
        key_left, key_right = softmax([8., -8.])
        value_left, value_right = softmax([-8., 8.])
        focus = {'aa': 1.}
        returns = []
        edges = (('aa', 'bb'), ('bb', 'cc'), ('cc', 'dd'))
        for _ in range(3):
            match = [key_left*focus.get(src, 0.)+key_right*focus.get(dst, 0.) for src, dst in edges]
            attention = softmax([40*x for x in match])
            copied = Counter()
            for a, (src, dst) in zip(attention, edges):
                copied[src] += a*value_left
                copied[dst] += a*value_right
            returns.append([copied[word] for word in ('bb', 'cc', 'dd')])
            focus = copied
        scale = 4+math.log1p(math.exp(-4))
        for sign, observed in zip((1., -1.), predictions):
            q = sign*math.tanh(2.)/2
            weights = softmax([20*q, -20*q, -20.])
            expected = [scale*math.log(1e-8+math.fsum(weights[h]*returns[h][i] for h in range(3)))
                        for i in range(3)]
            for x, y in zip(observed['logits'], expected):
                self.assertAlmostEqual(x, y, places=10)
        for i in range(off['depth_weight'], off['depth_bias']+3):
            model.parameters[i] = 0.
        a, b = model.forward(model.encode(first)), model.forward(model.encode(second))
        self.assertEqual(a['logits'], b['logits'])

    def test_positive_copy_floor_and_extreme_softplus_remain_finite_when_options_absent(self):
        case = request(context='aa bb', prompt='read', options=[
            {'id': 'a', 'description': 'xx'}, {'id': 'b', 'description': 'yy'}])
        model = model_for(case)
        model.parameters = array('d', [0.])*model.parameter_count
        off = layout(len(model.vocabulary), model.dimension, model.hidden)
        for raw_scale in (-1000., 0., 1000.):
            with self.subTest(raw_scale=raw_scale):
                model.parameters[off['copy_scale']] = raw_scale
                result = model.forward(model.encode(case))
                self.assertTrue(all(math.isfinite(x) for x in result['logits']))
                self.assertEqual(result['probabilities'], [.5, .5])
                loss, gradient = model.loss_and_grad(model.encode(case), 1)
                self.assertAlmostEqual(loss, math.log(2.), places=10)
                self.assertTrue(all(math.isfinite(x) for x in gradient))

    def test_candidate_duplicates_are_equal_and_new_option_preserves_old_log_odds(self):
        base = request(context='a b. b c.', prompt='a', options=[
            {'id': 'a', 'description': 'b'}, {'id': 'b', 'description': 'c'}])
        extended = request(context=base.context, prompt=base.prompt, options=[
            {'id': 'a', 'description': 'b'}, {'id': 'b', 'description': 'c'},
            {'id': 'c', 'description': 'b'}])
        model = model_for(base, extended)
        a, b = model.forward(model.encode(base)), model.forward(model.encode(extended))
        self.assertEqual(a['logits'], b['logits'][:2])
        self.assertEqual(b['logits'][0], b['logits'][2])
        self.assertAlmostEqual(math.log(a['probabilities'][0]/a['probabilities'][1]),
                               math.log(b['probabilities'][0]/b['probabilities'][1]), places=12)

    def test_option_order_opaque_identifiers_and_request_id_do_not_change_corresponding_logits(self):
        a = request(options=[{'id': 'a', 'description': 'b'}, {'id': 'b', 'description': 'c'}])
        b = request(context=a.context, prompt=a.prompt, request_id='arbitrary', options=[
            {'id': 'x', 'description': 'c'}, {'id': 'z', 'description': 'b'}])
        model = model_for(a)
        left, right = model.forward(model.encode(a)), model.forward(model.encode(b))
        self.assertEqual(left['logits'], list(reversed(right['logits'])))
        self.assertEqual(left['probabilities'], list(reversed(right['probabilities'])))

    def test_unicode_and_complete_fact_order_invariance_without_global_fact_positions(self):
        a = request(context='Élin has red. Beta has blue.', prompt='What has Élin?')
        b = request(context='BETA HAS BLUE. E\u0301LIN HAS RED.', prompt='WHAT HAS E\u0301LIN?')
        model = model_for(a)
        left, right = model.forward(model.encode(a)), model.forward(model.encode(b))
        for x, y in zip(left['logits'], right['logits']):
            self.assertAlmostEqual(x, y, places=12)

    def test_pointer_isolation_has_unknown_name_bijection_equivariance(self):
        fit = request(context='red blue', prompt='select', options=[
            {'id': 'a', 'description': 'red'}, {'id': 'b', 'description': 'blue'}])
        a = request(context='abba has red. baab has blue.', prompt='abba', options=fit.as_dict()['options'])
        b = request(context='cddc has red. dccd has blue.', prompt='cddc', options=fit.as_dict()['options'])
        model = model_for(fit)
        off = layout(len(model.vocabulary), model.dimension, model.hidden)
        for i in range(off['subword'], off['key_query']):
            model.parameters[i] = 0.
        left, right = model.forward(model.encode(a)), model.forward(model.encode(b))
        self.assertEqual(left['logits'], right['logits'])

    def test_score_anchors_are_only_decoding_metadata(self):
        raw = {'format': 'eidos-decision-request-v2', 'request_id': 'anchor-v4',
            'context': 'The band is low.', 'prompt': 'Select its band.', 'kind': 'SCORE',
            'minimum': 0, 'maximum': 1, 'options': [
                {'id': 'a', 'description': 'low', 'value': 0},
                {'id': 'b', 'description': 'high', 'value': 1}]}
        a = validate_request(raw)
        raw['maximum'] = 10
        raw['options'][1]['value'] = 10
        b = validate_request(raw)
        model = model_for(a)
        self.assertEqual(model.forward(model.encode(a))['logits'], model.forward(model.encode(b))['logits'])

    def test_distinct_metadata_strict_roundtrip_and_old_contextual_format_refusal(self):
        case = request()
        model = model_for(case)
        wire = json.loads(json.dumps(model.to_document(), allow_nan=False))
        restored = Network.from_document(wire)
        self.assertEqual(restored.parameters, model.parameters)
        self.assertEqual(restored.metadata(), model.metadata())
        self.assertEqual(restored.forward(restored.encode(case))['logits'], model.forward(model.encode(case))['logits'])
        for field, replacement in (('architecture', 'eidos-contextual-token-pointer-readout-memory-v3p1'),
                ('subword_buckets', 31), ('copy_log_floor', 0.), ('routing', 'unknown')):
            changed = copy.deepcopy(wire)
            changed['metadata'][field] = replacement
            with self.subTest(field=field), self.assertRaises(ModelError):
                Network.from_document(changed)
        changed = copy.deepcopy(wire)
        changed['format'] = 'eidos-contextual-memory-neural-v3p1'
        with self.assertRaises(ModelError):
            Network.from_document(changed)
        for replacement in (float('nan'), float('inf'), True, '__import__("os")'):
            changed = copy.deepcopy(wire)
            changed['parameters'][0] = replacement
            with self.subTest(parameter=repr(replacement)), self.assertRaises(ModelError):
                Network.from_document(changed)

    def test_serialized_resource_revision_pins_5m_work_500k_tape_and_refuses_old_metadata(self):
        model = model_for(request())
        wire = json.loads(json.dumps(model.to_document(), allow_nan=False))
        self.assertEqual(wire['metadata']['request_work_limit'], 5_000_000)
        self.assertEqual(wire['metadata']['gradient_tape_node_limit'], 500_000)
        self.assertEqual(Network.from_document(wire).metadata(), model.metadata())
        for missing in (('request_work_limit',), ('gradient_tape_node_limit',),
                        ('request_work_limit', 'gradient_tape_node_limit')):
            changed = copy.deepcopy(wire)
            for name in missing:
                del changed['metadata'][name]
            with self.subTest(missing=missing), self.assertRaises(ModelError):
                Network.from_document(changed)
        for field, old_limit in (('request_work_limit', 4_000_000),
                                 ('gradient_tape_node_limit', 250_000)):
            changed = copy.deepcopy(wire)
            changed['metadata'][field] = old_limit
            with self.subTest(old_field=field), self.assertRaises(ModelError):
                Network.from_document(changed)

    def test_complete_255_distribution_or_explicit_budget_refusal_and_no_truncation(self):
        case = request(context='a b.', prompt='a', options=[
            {'id': f'x{i:03d}', 'description': f'word{i}'} for i in range(255)])
        model = model_for(case)
        try:
            result = model.forward(model.encode(case))
        except ModelError as error:
            self.assertRegex(str(error), 'budget|bound|limit')
            return
        self.assertEqual(len(result['probabilities']), 255)
        self.assertAlmostEqual(math.fsum(result['probabilities']), 1., places=12)
        self.assertFalse(result['accounting']['truncated'])

    def test_bounds_wrong_model_signature_invalid_targets_and_preflight_work_refusal(self):
        case = request()
        model = model_for(case)
        encoded = model.encode(case)
        with self.assertRaises(ModelError):
            model.forward(dataclasses.replace(encoded, model_signature='0'*64))
        for target in (-1, 2, True, '0'):
            with self.subTest(target=target), self.assertRaises(ModelError):
                model.loss_and_grad(encoded, target)
        for invalid in (request(context='x'*257), request(context='a '*(routed.MAX_CONTEXT_TOKENS+1))):
            with self.assertRaises(ModelError):
                model.encode(invalid)
        with patch.object(routed, 'MAX_WORK_UNITS', 1), self.assertRaises(ModelError):
            model.encode(case)

    def test_initialization_is_deterministic_and_boolean_settings_are_refused(self):
        case = request()
        a, b = model_for(case), model_for(case)
        self.assertEqual(a.parameters, b.parameters)
        self.assertNotEqual(a.parameters, model_for(case, seed=114).parameters)
        for settings in ({'dimension': True}, {'hidden': False}, {'seed': True},
                         {'dimension': 0}, {'hidden': 1000}, {'seed': -1}):
            with self.subTest(settings=settings), self.assertRaises(ModelError):
                Network.initialize(build_vocabulary([case]), **settings)


if __name__ == '__main__':
    unittest.main()
