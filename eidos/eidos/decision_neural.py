"""Original bounded question/context/candidate-conditioned neural scorer.

This is a compact train-from-scratch research model, not a pretrained language
model. Shared token embeddings, order-sensitive position moments in all three roles, conditioned attention
and a shared nonlinear candidate function are fitted from caller training data.
IDs and numeric anchor values never enter its semantic features. Softmax outputs
are conditional on the offered options and are not calibrated confidence.

No model, corpus, network, subprocess or third-party package is accessed here.
"""
from array import array
import collections
from dataclasses import dataclass
import hashlib
import json
import math
import random
import re
import unicodedata

from .decision_contract import DecisionRequest

ARCHITECTURE = 'eidos-question-conditioned-attention-mlp-v2'
TOKENIZER = 'eidos-unicode-word-punctuation-v1'
MODEL_FORMAT = 'eidos-decision-neural-model-v1'
UNK = '<UNK>'
MAX_VOCABULARY = 4096
MAX_TOKEN_CHARACTERS = 256
MAX_TOKEN_UTF8_BYTES = 1024
BYTE_VOCABULARY = 256
MAX_OBSERVED_TOKENS = 100_000
MAX_VOCABULARY_TOKENS = 1_000_000
MAX_PARAMETERS = 160_000
MAX_CONTEXT_TOKENS = 16_384
MAX_PROMPT_TOKENS = 1024
MAX_OPTION_TOKENS = 256
MAX_ALL_OPTION_TOKENS = 32_768
MAX_CONTEXT_SPANS = 1024
SPAN_TOKENS = 48
MAX_WORK_UNITS = 100_000_000
MAX_PARAMETER_MAGNITUDE = 1_000_000.0
_TOKEN = re.compile(r"\w+(?:['\u2019\u2010-\u2015-]\w+)*|[^\w\s]", re.UNICODE)
_BOUNDARIES = frozenset('.!?;')


class ModelError(ValueError):
    """Invalid model or explicitly bounded computation; messages contain no text."""


def _need(condition, reason):
    if not condition:
        raise ModelError(reason)


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def tokenize(text):
    """Own Unicode NFKC/casefold word-and-punctuation tokenizer, no learned IDs."""
    _need(type(text) is str, 'tokenizer_text_required')
    normalized = unicodedata.normalize('NFKC', text).casefold()
    result = []
    for match in _TOKEN.finditer(normalized):
        _need(len(result) < MAX_CONTEXT_TOKENS, 'tokenizer_token_budget')
        result.append(match.group())
    return result


def build_vocabulary(requests, max_vocabulary=MAX_VOCABULARY):
    """Fit token inventory on explicitly supplied training requests only.

    Frequency ties use token order. Neither request/option IDs, targets, anchor
    values nor gold information are read. OOV text maps to the learned UNK row.
    """
    _need(_integer(max_vocabulary, 2, MAX_VOCABULARY), 'vocabulary_bound')
    counts = collections.Counter()
    total = 0
    for request in requests:
        _need(type(request) is DecisionRequest, 'validated_request_required')
        texts = [request.context, request.prompt]
        texts.extend(option.description for option in request.options)
        for text in texts:
            terms = tokenize(text)
            _need(all(len(token) <= MAX_TOKEN_CHARACTERS for token in terms), 'training_token_length_bound')
            total += len(terms)
            _need(total <= MAX_VOCABULARY_TOKENS, 'vocabulary_token_budget')
            counts.update(terms)
            _need(len(counts) <= MAX_OBSERVED_TOKENS, 'observed_vocabulary_budget')
    _need(total > 0, 'nonempty_training_text_required')
    selected = sorted(counts, key=lambda token: (-counts[token], token))[:max_vocabulary - 1]
    return (UNK, *sorted(selected))


@dataclass(frozen=True)
class EncodedToken:
    word_id: int
    utf8: tuple[int, ...]


@dataclass(frozen=True)
class EncodedRequest:
    model_signature: str
    context: tuple[tuple[int, ...], ...]
    prompt: tuple[int, ...]
    candidates: tuple[tuple[int, ...], ...]
    option_ids: tuple[str, ...]
    accounting: dict
    tokens: tuple[EncodedToken, ...]


def _softmax(values):
    _need(values and all(math.isfinite(value) for value in values), 'nonfinite_logits')
    peak = max(values)
    masses = [math.exp(value - peak) for value in values]
    total = math.fsum(masses)
    _need(math.isfinite(total) and total > 0, 'invalid_probability_mass')
    return [mass / total for mass in masses]


def _dot(left, right):
    return math.fsum(a * b for a, b in zip(left, right))


class DecisionNetwork:
    """A shared trainable candidate scorer with one flat float64 parameter array.

    Layout is word_embeddings[V,D], byte_embeddings[256,D], byte_position[D],
    role_position[D], global_span_position[D], hidden_weights[H,6D],
    hidden_bias[H], output_weights[H]. A shared output bias is omitted because
    adding it to every logit cancels in the complete-set categorical softmax.
    """
    def __init__(self, vocabulary, dimension, hidden, parameters):
        _need(type(vocabulary) in (list, tuple) and 1 <= len(vocabulary) <= MAX_VOCABULARY,
              'vocabulary_size_bound')
        _need(all(type(token) is str and 1 <= len(token) <= MAX_TOKEN_CHARACTERS and
                  not any(0xD800 <= ord(c) <= 0xDFFF for c in token) for token in vocabulary),
              'vocabulary_token_bound')
        _need(vocabulary[0] == UNK and len(set(vocabulary)) == len(vocabulary) and
              list(vocabulary[1:]) == sorted(vocabulary[1:]) and
              all(tokenize(token) == [token] for token in vocabulary[1:]), 'canonical_vocabulary_required')
        _need(_integer(dimension, 2, 32) and _integer(hidden, 2, 64), 'architecture_dimension_bound')
        self.vocabulary = tuple(vocabulary)
        self.dimension = dimension
        self.hidden = hidden
        self.index = {token: i for i, token in enumerate(vocabulary)}
        self.embedding_offset = 0
        self.byte_embedding_offset = len(vocabulary) * dimension
        self.byte_position_offset = self.byte_embedding_offset + BYTE_VOCABULARY * dimension
        self.position_offset = self.byte_position_offset + dimension
        self.context_position_offset = self.position_offset + dimension
        self.hidden_weight_offset = self.context_position_offset + dimension
        self.hidden_bias_offset = self.hidden_weight_offset + hidden * 6 * dimension
        self.output_offset = self.hidden_bias_offset + hidden
        self.parameter_count = self.output_offset + hidden
        _need(self.parameter_count <= MAX_PARAMETERS, 'parameter_count_bound')
        _need(type(parameters) in (array, list, tuple), 'parameter_array_required')
        if type(parameters) is array:
            _need(parameters.typecode == 'd', 'float64_parameters_required')
        _need(len(parameters) == self.parameter_count and
              all(_finite(value) and abs(value) <= MAX_PARAMETER_MAGNITUDE for value in parameters),
              'parameter_count_or_finiteness')
        self.parameters = array('d', parameters)
        self.signature = hashlib.sha256(json.dumps(self.metadata(), sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

    @classmethod
    def initialize(cls, vocabulary, dimension=12, hidden=16, seed=113):
        _need(_integer(seed, 0, 2**32 - 1), 'seed_bound')
        # Constructor handles exact vocabulary/architecture validation before
        # this bounded initialization is accepted as a model.
        _need(type(vocabulary) in (list, tuple) and 1 <= len(vocabulary) <= MAX_VOCABULARY,
              'vocabulary_size_bound')
        _need(_integer(dimension, 2, 32) and _integer(hidden, 2, 64), 'architecture_dimension_bound')
        count = (len(vocabulary) + BYTE_VOCABULARY) * dimension + 3 * dimension + hidden * 6 * dimension + 2 * hidden
        _need(count <= MAX_PARAMETERS, 'parameter_count_bound')
        generator = random.Random(seed)
        values = array('d', (generator.uniform(-.35, .35)
                            for _ in range(len(vocabulary) * dimension)))
        values.extend(generator.uniform(-.1, .1) for _ in range(BYTE_VOCABULARY * dimension))
        values.extend(generator.uniform(-.2, .2) for _ in range(3 * dimension))
        bound = math.sqrt(6 / (6 * dimension + hidden))
        values.extend(generator.uniform(-bound, bound) for _ in range(hidden * 6 * dimension))
        values.extend([0.] * hidden)
        bound = math.sqrt(6 / (hidden + 1))
        values.extend(generator.uniform(-bound, bound) for _ in range(hidden))
        return cls(vocabulary, dimension, hidden, values)

    def metadata(self):
        return {'architecture': ARCHITECTURE, 'dimension': self.dimension,
                'hidden': self.hidden, 'vocabulary': list(self.vocabulary),
                'parameter_count': self.parameter_count, 'tokenizer': TOKENIZER,
                'span_tokens': SPAN_TOKENS}

    def to_document(self):
        return {'format': MODEL_FORMAT, 'metadata': self.metadata(),
                'parameters': list(self.parameters)}

    @classmethod
    def from_document(cls, document):
        _need(type(document) is dict and set(document) == {'format', 'metadata', 'parameters'} and
              document['format'] == MODEL_FORMAT, 'model_document_format')
        metadata = document['metadata']
        _need(type(metadata) is dict and set(metadata) == {'architecture', 'dimension', 'hidden',
              'vocabulary', 'parameter_count', 'tokenizer', 'span_tokens'}, 'metadata_fields')
        model = cls(metadata['vocabulary'], metadata['dimension'], metadata['hidden'],
                    document['parameters'])
        _need(model.metadata() == metadata, 'metadata_architecture_mismatch')
        return model

    def encode(self, request):
        _need(type(request) is DecisionRequest, 'validated_request_required')
        lexicon, records = {}, []

        def local_token(token):
            _need(1 <= len(token) <= MAX_TOKEN_CHARACTERS, 'encoded_token_length_bound')
            if token not in lexicon:
                raw = token.encode('utf-8')
                _need(1 <= len(raw) <= MAX_TOKEN_UTF8_BYTES, 'encoded_token_byte_bound')
                lexicon[token] = len(records)
                records.append(EncodedToken(self.index.get(token, 0), tuple(raw)))
            return lexicon[token]

        context_text = unicodedata.normalize('NFKC', request.context).casefold()
        spans, active = [], []
        previous_end, context_count = 0, 0
        for match in _TOKEN.finditer(context_text):
            context_count += 1
            _need(context_count <= MAX_CONTEXT_TOKENS, 'context_token_budget')
            if active and '\n' in context_text[previous_end:match.start()]:
                spans.append(tuple(active))
                active = []
            token = match.group()
            active.append(local_token(token))
            if token in _BOUNDARIES or len(active) == SPAN_TOKENS:
                spans.append(tuple(active))
                active = []
            previous_end = match.end()
            _need(len(spans) <= MAX_CONTEXT_SPANS, 'context_span_budget')
        if active:
            spans.append(tuple(active))
        _need(1 <= len(spans) <= MAX_CONTEXT_SPANS, 'context_span_budget')
        prompt = tuple(local_token(token) for token in tokenize(request.prompt))
        _need(1 <= len(prompt) <= MAX_PROMPT_TOKENS, 'prompt_token_budget')
        candidates = []
        for option in request.options:
            tokens = tuple(local_token(token) for token in tokenize(option.description))
            _need(1 <= len(tokens) <= MAX_OPTION_TOKENS, 'option_token_budget')
            candidates.append(tokens)
        option_tokens = sum(map(len, candidates))
        _need(2 <= len(candidates) <= 255 and option_tokens <= MAX_ALL_OPTION_TOKENS,
              'candidate_token_or_count_budget')
        all_ids = [i for span in spans for i in span] + list(prompt)
        all_ids.extend(i for candidate in candidates for i in candidate)
        accounting = {'context_characters': len(request.context), 'context_tokens': context_count,
            'context_spans': len(spans), 'prompt_tokens': len(prompt), 'option_tokens': option_tokens,
            'options': len(candidates), 'context_tokens_processed': sum(map(len, spans)),
            'truncated': False,
            'known_token_fraction': sum(records[i].word_id != 0 for i in all_ids) / len(all_ids),
            'distinct_token_spellings': len(records),
            'compositional_utf8_bytes': sum(len(record.utf8) for record in records),
            'byte_composition_applied_to_all_tokens': True,
            'global_span_position_features': True}
        encoded = EncodedRequest(self.signature, tuple(spans), prompt, tuple(candidates),
                                 tuple(option.id for option in request.options), accounting, tuple(records))
        _need(self.estimate_work(encoded) <= MAX_WORK_UNITS, 'network_work_budget')
        return encoded

    encode_request = encode

    def estimate_work(self, encoded):
        """Conservative scalar-arithmetic estimate for one forward/backward pass.

        Does not include an optimizer's full-array updates or data preparation;
        callers must budget those separately. This is not a latency guarantee.
        """
        _need(type(encoded) is EncodedRequest, 'encoded_request_required')
        d, h = self.dimension, self.hidden
        tokens = sum(map(len, encoded.context)) + len(encoded.prompt) + sum(map(len, encoded.candidates))
        byte_count = sum(len(token.utf8) for token in encoded.tokens)
        return 8 * (2 * tokens * d + 4 * byte_count * d + len(encoded.candidates) *
                    (len(encoded.context) * d * 6 + 6 * d * h * 4))

    def _validate_encoded(self, encoded):
        _need(type(encoded) is EncodedRequest and encoded.model_signature == self.signature,
              'encoded_model_mismatch')
        _need(type(encoded.context) is tuple and type(encoded.prompt) is tuple and
              type(encoded.candidates) is tuple and type(encoded.option_ids) is tuple and
              type(encoded.tokens) is tuple and 1 <= len(encoded.tokens) <=
              MAX_CONTEXT_TOKENS + MAX_PROMPT_TOKENS + MAX_ALL_OPTION_TOKENS and
              all(type(span) is tuple for span in encoded.context) and
              all(type(tokens) is tuple for tokens in encoded.candidates), 'encoded_sequence_types')
        _need(1 <= len(encoded.context) <= MAX_CONTEXT_SPANS and
              1 <= len(encoded.prompt) <= MAX_PROMPT_TOKENS and
              2 <= len(encoded.candidates) <= 255, 'encoded_shape_bound')
        _need(len(encoded.option_ids) == len(encoded.candidates), 'encoded_identity_count')
        _need(all(1 <= len(span) <= SPAN_TOKENS for span in encoded.context) and
              all(1 <= len(tokens) <= MAX_OPTION_TOKENS for tokens in encoded.candidates),
              'encoded_sequence_bound')
        tokens = tuple(i for span in encoded.context for i in span) + encoded.prompt
        tokens += tuple(i for candidate in encoded.candidates for i in candidate)
        _need(all(_integer(i, 0, len(encoded.tokens) - 1) for i in tokens), 'encoded_token_identity')
        _need(all(type(token) is EncodedToken and
                  _integer(token.word_id, 0, len(self.vocabulary) - 1) and
                  type(token.utf8) is tuple and 1 <= len(token.utf8) <= MAX_TOKEN_UTF8_BYTES and
                  all(_integer(byte, 0, 255) for byte in token.utf8) for token in encoded.tokens),
              'encoded_composition_record')
        _need(sum(map(len, encoded.context)) <= MAX_CONTEXT_TOKENS and
              sum(map(len, encoded.candidates)) <= MAX_ALL_OPTION_TOKENS,
              'encoded_total_token_bound')
        _need(self.estimate_work(encoded) <= MAX_WORK_UNITS, 'network_work_budget')
        _need(len(self.parameters) == self.parameter_count, 'parameter_count_changed')

    def _token_vector(self, token):
        d, p, length = self.dimension, self.parameters, len(token.utf8)
        factors = [0.] if length == 1 else [2 * t / (length - 1) - 1 for t in range(length)]
        average = [math.fsum(p[self.byte_embedding_offset + byte * d + j]
                            for byte in token.utf8) / length for j in range(d)]
        moment = [math.fsum(factors[t] * p[self.byte_embedding_offset + byte * d + j]
                           for t, byte in enumerate(token.utf8)) / length for j in range(d)]
        vector = [p[token.word_id * d + j] + average[j] +
                  p[self.byte_position_offset + j] * moment[j] for j in range(d)]
        return vector, moment, factors

    def _mean(self, token_ids, token_vectors):
        inv = 1 / len(token_ids)
        return [math.fsum(token_vectors[token][j] for token in token_ids) * inv
                for j in range(self.dimension)]

    def _span(self, token_ids, token_vectors):
        """Shared ordered sequence encoding for context, questions and options.

        Learned byte/word representations and centered first moments carry
        spelling and order information. This is not an injective representation
        or language-understanding guarantee; moment collisions remain possible.
        """
        d, p, length = self.dimension, self.parameters, len(token_ids)
        average = self._mean(token_ids, token_vectors)
        factors = [0.] if length == 1 else [2 * t / (length - 1) - 1 for t in range(length)]
        moment = [math.fsum(factors[t] * token_vectors[token][j]
                           for t, token in enumerate(token_ids)) / length for j in range(d)]
        vector = [average[j] + p[self.position_offset + j] * moment[j] for j in range(d)]
        return vector, moment, factors

    def _sequence_gradient(self, token_ids, encoding, derivative, token_derivatives, gradient):
        """Accumulate role-order and composed-token derivatives for one role."""
        _, moment, factors = encoding
        d, p, inv = self.dimension, self.parameters, 1 / len(token_ids)
        for j in range(d):
            gradient[self.position_offset + j] += derivative[j] * moment[j]
        for t, token in enumerate(token_ids):
            for j in range(d):
                token_derivatives[token][j] += derivative[j] * inv * (1 + p[self.position_offset + j] * factors[t])

    def _token_gradient(self, token, encoding, derivative, gradient):
        _, moment, factors = encoding
        d, p, inv = self.dimension, self.parameters, 1 / len(token.utf8)
        for j in range(d):
            gradient[token.word_id * d + j] += derivative[j]
            gradient[self.byte_position_offset + j] += derivative[j] * moment[j]
        for t, byte in enumerate(token.utf8):
            offset = self.byte_embedding_offset + byte * d
            for j in range(d):
                gradient[offset + j] += derivative[j] * inv * (1 + p[self.byte_position_offset + j] * factors[t])

    def _forward(self, encoded, caches=False):
        self._validate_encoded(encoded)
        d, h, p = self.dimension, self.hidden, self.parameters
        token_encodings = [self._token_vector(token) for token in encoded.tokens]
        token_vectors = [entry[0] for entry in token_encodings]
        context = [self._span(tokens, token_vectors) for tokens in encoded.context]
        span_factors = [0.] if len(context) == 1 else [2 * s / (len(context) - 1) - 1
                                                      for s in range(len(context))]
        states = [[entry[0][j] * (1 + p[self.context_position_offset + j] * span_factors[s])
                   for j in range(d)] for s, entry in enumerate(context)]
        question_encoding = self._span(encoded.prompt, token_vectors)
        q = question_encoding[0]
        inv_root = 1 / math.sqrt(d)
        candidates, logits = [], []
        for token_ids in encoded.candidates:
            option_encoding = self._span(token_ids, token_vectors)
            o = option_encoding[0]
            query = [q[j] + o[j] for j in range(d)]
            energies = [_dot(state, query) * inv_root for state in states]
            attention = _softmax(energies)
            r = [math.fsum(a * state[j] for a, state in zip(attention, states)) for j in range(d)]
            features = r + q + o + [r[j] * q[j] for j in range(d)] + \
                       [r[j] * o[j] for j in range(d)] + [q[j] * o[j] for j in range(d)]
            hidden = []
            for k in range(h):
                offset = self.hidden_weight_offset + k * 6 * d
                pre = p[self.hidden_bias_offset + k] + math.fsum(
                    p[offset + j] * value for j, value in enumerate(features))
                _need(math.isfinite(pre), 'nonfinite_hidden_value')
                hidden.append(math.tanh(pre))
            logit = math.fsum(p[self.output_offset + k] * hidden[k] for k in range(h))
            logits.append(logit)
            if caches:
                candidates.append((option_encoding, query, attention, r, features, hidden))
        probabilities = _softmax(logits)
        output = {'logits': logits, 'probabilities': probabilities,
                  'accounting': dict(encoded.accounting)}
        return (output, (context, states, span_factors, question_encoding, candidates, token_encodings)) if caches else output

    def forward(self, encoded):
        """Return finite logits/mass for every offered option, without decoding."""
        try:
            return self._forward(encoded)
        except (OverflowError, ZeroDivisionError) as error:
            raise ModelError('nonfinite_network_computation') from error

    predict = forward

    def loss_and_grad(self, encoded, target_index):
        """Listwise cross-entropy and full analytical derivative of every weight."""
        self._validate_encoded(encoded)
        _need(_integer(target_index, 0, len(encoded.candidates) - 1), 'target_index_bound')
        output, (context, states, span_factors, question_encoding, candidates, token_encodings) = self._forward(encoded, caches=True)
        q = question_encoding[0]
        logits = output['logits']
        peak = max(logits)
        loss = peak - logits[target_index] + math.log(math.fsum(math.exp(z - peak) for z in logits))
        d, h, p = self.dimension, self.hidden, self.parameters
        gradient = array('d', [0.]) * self.parameter_count
        d_q = [0.] * d
        d_states = [[0.] * d for _ in context]
        token_derivatives = [[0.] * d for _ in encoded.tokens]
        inv_root = 1 / math.sqrt(d)
        for choice, (option_encoding, query, attention, r, features, hidden) in enumerate(candidates):
            o = option_encoding[0]
            dz = output['probabilities'][choice] - int(choice == target_index)
            d_features = [0.] * (6 * d)
            for k in range(h):
                gradient[self.output_offset + k] += dz * hidden[k]
                d_pre = dz * p[self.output_offset + k] * (1 - hidden[k] * hidden[k])
                gradient[self.hidden_bias_offset + k] += d_pre
                offset = self.hidden_weight_offset + k * 6 * d
                for j, value in enumerate(features):
                    gradient[offset + j] += d_pre * value
                    d_features[j] += d_pre * p[offset + j]
            d_r, d_o = [0.] * d, [0.] * d
            for j in range(d):
                d_r[j] = d_features[j] + d_features[3*d + j] * q[j] + d_features[4*d + j] * o[j]
                d_q[j] += d_features[d + j] + d_features[3*d + j] * r[j] + d_features[5*d + j] * o[j]
                d_o[j] += d_features[2*d + j] + d_features[4*d + j] * r[j] + d_features[5*d + j] * q[j]
            # Derivative through both the value path and the softmax attention.
            for s, state in enumerate(states):
                d_energy = attention[s] * math.fsum(d_r[j] * (state[j] - r[j]) for j in range(d))
                for j in range(d):
                    d_states[s][j] += attention[s] * d_r[j] + d_energy * query[j] * inv_root
                    added = d_energy * state[j] * inv_root
                    d_q[j] += added
                    d_o[j] += added
            self._sequence_gradient(encoded.candidates[choice], option_encoding, d_o, token_derivatives, gradient)
        self._sequence_gradient(encoded.prompt, question_encoding, d_q, token_derivatives, gradient)
        for s, encoding in enumerate(context):
            derivative = [d_states[s][j] * (1 + p[self.context_position_offset + j] * span_factors[s])
                          for j in range(d)]
            for j in range(d):
                gradient[self.context_position_offset + j] += d_states[s][j] * encoding[0][j] * span_factors[s]
            self._sequence_gradient(encoded.context[s], encoding, derivative, token_derivatives, gradient)
        for t, token in enumerate(encoded.tokens):
            self._token_gradient(token, token_encodings[t], token_derivatives[t], gradient)
        _need(math.isfinite(loss) and all(math.isfinite(value) for value in gradient),
              'nonfinite_loss_or_gradient')
        return loss, gradient


Model = DecisionNetwork
