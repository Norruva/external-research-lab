"""Original question-routed subword/pointer decision model, version 4.

This separately versioned model extends EIDOS's original contextual v3.1
encoder and scalar differentiation implementation; the previous source and
artifacts are unchanged. Learned bounded character features distinguish some
unknown spellings but do not provide their meanings. Exact spelling pointers
remain separate. Question/current-state controls select token keys/values at
each read, and a question-conditioned depth mixture feeds a direct copy feature
alongside the shared residual head. The mixture is not independently normalized
across candidates when routing depends on each candidate.

Only standard-library facilities and original EIDOS tokenization are used.
No model, corpus, network, subprocess, credentials or third-party package is
read. This is a bounded research model, not a semantic or production guarantee.
"""
from array import array
import collections
from dataclasses import dataclass
import hashlib
import json
import math
import random
import unicodedata

from .decision_contract import DecisionRequest
from .decision_neural import tokenize, build_vocabulary, _TOKEN

ARCHITECTURE = 'eidos-question-routed-subword-pointer-memory-v4'
MODEL_FORMAT = 'eidos-question-routed-neural-v4'
TOKENIZER = 'eidos-unicode-word-punctuation-v1'
SUBWORD_BUCKETS = 32
SUBWORD_ALGORITHM = 'eidos-sha256-boundary-char-1-3-v1'
SUBWORD_WIDTHS = (1, 2, 3)
SUBWORD_HASH_PREFIX = b'eidos-subword-v1\0'
COPY_FLOOR = 1e-8
ROUTING = 'diagonal-question-current-hop-key-value-v1'
DEPTH_MIX = 'question-softmax-read-depth-v1'
UNK = '<UNK>'
MEMORY_READS = 3
CONTEXT_WINDOW = 2
OFFSETS = (-2, -1, 1, 2)
SPAN_TOKENS = 48
MAX_VOCABULARY = 4096
MAX_PARAMETERS = 120_000
MAX_CONTEXT_TOKENS = 512
MAX_CONTEXT_SPANS = 64
MAX_PROMPT_TOKENS = 128
MAX_OPTION_TOKENS = 64
MAX_ALL_OPTION_TOKENS = 2048
MAX_TOKEN_CHARACTERS = 256
MAX_WORK_UNITS = 5_000_000
MAX_TAPE_NODES = 500_000
MAX_PARAMETER_MAGNITUDE = 1_000_000.
_BOUNDARIES = frozenset('.!?;')


class ModelError(ValueError):
    """Bounded refusal with text-free reasons."""


def _need(condition, reason):
    if not condition:
        raise ModelError(reason)


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


class _Tape:
    """Small original scalar reverse-mode tape; local partials are explicit.

    Nodes retain numbers and parent indices, not executable serialized objects.
    A hard node bound applies before allocation. Inference uses plain floats.
    """
    def __init__(self):
        self.values, self.parents = [], []

    def node(self, value, parents=()):
        _need(math.isfinite(value), 'nonfinite_routed_computation')
        _need(len(self.values) < MAX_TAPE_NODES, 'routed_tape_budget')
        index = len(self.values)
        self.values.append(float(value))
        self.parents.append(parents)
        return _Value(self, index)

    def backward(self, loss, parameters):
        derivative = array('d', [0.]) * len(self.values)
        derivative[loss.index] = 1.
        for index in range(len(self.values) - 1, -1, -1):
            upstream = derivative[index]
            if upstream:
                for parent, partial in self.parents[index]:
                    derivative[parent] += upstream * partial
        result = array('d', (derivative[v.index] for v in parameters))
        _need(all(math.isfinite(v) for v in result), 'nonfinite_routed_gradient')
        return result


class _Value:
    __slots__ = ('tape', 'index')

    def __init__(self, tape, index):
        self.tape, self.index = tape, index

    @property
    def value(self):
        return self.tape.values[self.index]

    def __add__(self, other):
        if isinstance(other, _Value):
            _need(other.tape is self.tape, 'incompatible_gradient_tapes')
            return self.tape.node(self.value + other.value,
                                  ((self.index, 1.), (other.index, 1.)))
        return self.tape.node(self.value + other, ((self.index, 1.),))

    __radd__ = __add__

    def __neg__(self):
        return self.tape.node(-self.value, ((self.index, -1.),))

    def __sub__(self, other):
        return self + -other

    def __rsub__(self, other):
        return -self + other

    def __mul__(self, other):
        if isinstance(other, _Value):
            _need(other.tape is self.tape, 'incompatible_gradient_tapes')
            return self.tape.node(self.value * other.value,
                                  ((self.index, other.value), (other.index, self.value)))
        return self.tape.node(self.value * other, ((self.index, float(other)),))

    __rmul__ = __mul__

    def __truediv__(self, other):
        if isinstance(other, _Value):
            _need(other.tape is self.tape and other.value != 0, 'invalid_routed_division')
            return self.tape.node(self.value / other.value,
                ((self.index, 1. / other.value),
                 (other.index, -self.value / (other.value * other.value))))
        return self * (1. / other)


def _number(value):
    return value.value if isinstance(value, _Value) else value


def _tanh(value):
    output = math.tanh(_number(value))
    return value.tape.node(output, ((value.index, 1. - output * output),)) if isinstance(value, _Value) else output


def _exp(value):
    output = math.exp(_number(value))
    return value.tape.node(output, ((value.index, output),)) if isinstance(value, _Value) else output


def _log(value):
    output = math.log(_number(value))
    return value.tape.node(output, ((value.index, 1. / value.value),)) if isinstance(value, _Value) else output


def _softplus(value):
    """Stable log(1+exp(x)); selecting the stable branch detaches no derivative."""
    if _number(value) >= 0:
        return value + _log(1. + _exp(-value))
    return _log(1. + _exp(value))


def subword_features(spelling):
    """All boundary character 1/2/3-grams, SHA256 buckets, normalized counts.

    The normalized token length is bounded before hashing. Bucket collisions
    are possible; this is a spelling feature, not an injective semantic code.
    """
    _need(type(spelling) is str and 1 <= len(spelling) <= MAX_TOKEN_CHARACTERS and
          not any(0xD800 <= ord(c) <= 0xDFFF for c in spelling), 'routed_subword_token_bound')
    normalized = unicodedata.normalize('NFKC', spelling).casefold()
    _need(1 <= len(normalized) <= MAX_TOKEN_CHARACTERS, 'routed_subword_token_bound')
    padded = '\x02' + normalized + '\x03'
    counts = collections.Counter()
    total = 0
    for width in SUBWORD_WIDTHS:
        for start in range(len(padded) - width + 1):
            gram = padded[start:start+width]
            raw = SUBWORD_HASH_PREFIX + bytes((width,)) + gram.encode('utf-8')
            bucket = int.from_bytes(hashlib.sha256(raw).digest()[:8], 'big') % SUBWORD_BUCKETS
            counts[bucket] += 1
            total += 1
    return tuple((bucket, count / total) for bucket, count in sorted(counts.items()))


def _sum(values):
    """Fuse an ordered sum into one tape node, retaining repeated parents."""
    values = list(values)
    total, tape, parents = 0., None, []
    for value in values:
        total = total + _number(value)
        if isinstance(value, _Value):
            if tape is None:
                tape = value.tape
            _need(value.tape is tape, 'incompatible_gradient_tapes')
            parents.append((value.index, 1.))
    return tape.node(total, tuple(parents)) if tape is not None else total


def _dot(left, right):
    """Fuse ordered products/sum without changing float forward order."""
    pairs = list(zip(left, right))
    total, tape, parents = 0., None, []
    for a, b in pairs:
        av, bv = _number(a), _number(b)
        total = total + av * bv
        if isinstance(a, _Value):
            if tape is None:
                tape = a.tape
            _need(a.tape is tape, 'incompatible_gradient_tapes')
            _need(math.isfinite(bv), 'nonfinite_routed_gradient')
            parents.append((a.index, bv))
        if isinstance(b, _Value):
            if tape is None:
                tape = b.tape
            _need(b.tape is tape, 'incompatible_gradient_tapes')
            _need(math.isfinite(av), 'nonfinite_routed_gradient')
            parents.append((b.index, av))
    return tape.node(total, tuple(parents)) if tape is not None else total


def _triple_dot(left, middle, right):
    """Fuse sum((a*b)*c); explicit local derivatives retain duplicate refs."""
    triples = list(zip(left, middle, right))
    total, tape, parents = 0., None, []
    for a, b, c in triples:
        av, bv, cv = _number(a), _number(b), _number(c)
        total = total + (av * bv) * cv
        for value, partial in ((a, bv * cv), (b, av * cv), (c, av * bv)):
            if isinstance(value, _Value):
                if tape is None:
                    tape = value.tape
                _need(value.tape is tape, 'incompatible_gradient_tapes')
                _need(math.isfinite(partial), 'nonfinite_routed_gradient')
                parents.append((value.index, partial))
    return tape.node(total, tuple(parents)) if tape is not None else total


def _softmax(values):
    _need(values and all(math.isfinite(_number(v)) for v in values), 'nonfinite_routed_logits')
    peak = max(_number(v) for v in values)
    masses = [_exp(v - peak) for v in values]
    total = _sum(masses)
    return [v / total for v in masses]


def _positions(length):
    return [0.] if length == 1 else [2 * i / (length - 1) - 1 for i in range(length)]


@dataclass(frozen=True)
class EncodedToken:
    word_id: int
    spelling: str


@dataclass(frozen=True)
class EncodedRequest:
    model_signature: str
    context: tuple[tuple[int, ...], ...]
    prompt: tuple[int, ...]
    candidates: tuple[tuple[int, ...], ...]
    option_ids: tuple[str, ...]
    accounting: dict
    tokens: tuple[EncodedToken, ...]


class RoutedNetwork:
    """Question/current-state routed three-read exact-identity pointer memory.

    Layout: E[V,D], conv[4,D], position[D], key[D], value[D], read_fact[D],
    read_query[D], read_option[D], update[D], six original scalars (key
    position, value position, copy query, match, candidate, joint),
    subword[32,D], key_query[D], key_pos_query[D], value_query[D],
    value_pos_query[D], hop[3,D], depth_weight[3,D], depth_bias[3],
    copy_scale[1], W[H,5D+12], bias[H], out[H]. All candidate parameters are
    shared. Hop vectors and a question-controlled mixture distinguish reads.
    """
    def __init__(self, vocabulary, dimension, hidden, parameters):
        _need(type(vocabulary) in (list, tuple) and 1 <= len(vocabulary) <= MAX_VOCABULARY,
              'routed_vocabulary_bound')
        _need(all(type(t) is str and 1 <= len(t) <= MAX_TOKEN_CHARACTERS and
                  not any(0xD800 <= ord(c) <= 0xDFFF for c in t) for t in vocabulary),
              'routed_vocabulary_token_bound')
        _need(vocabulary[0] == UNK and len(set(vocabulary)) == len(vocabulary) and
              list(vocabulary[1:]) == sorted(vocabulary[1:]) and
              all(tokenize(t) == [t] for t in vocabulary[1:]), 'canonical_routed_vocabulary_required')
        _need(_integer(dimension, 2, 16) and _integer(hidden, 2, 32), 'routed_dimension_bound')
        self.vocabulary, self.dimension, self.hidden = tuple(vocabulary), dimension, hidden
        self.index = {token: i for i, token in enumerate(vocabulary)}
        self.embedding_offset = 0
        self.conv_offset = len(vocabulary) * dimension
        self.position_offset = self.conv_offset + 4 * dimension
        self.key_offset = self.position_offset + dimension
        self.value_offset = self.key_offset + dimension
        self.read_fact_offset = self.value_offset + dimension
        self.read_query_offset = self.read_fact_offset + dimension
        self.read_option_offset = self.read_query_offset + dimension
        self.update_offset = self.read_option_offset + dimension
        self.scalar_offset = self.update_offset + dimension
        (self.key_position_offset, self.value_position_offset, self.copy_query_offset,
         self.match_offset, self.candidate_offset, self.joint_offset) = range(self.scalar_offset, self.scalar_offset + 6)
        self.subword_offset = self.scalar_offset + 6
        self.key_query_offset = self.subword_offset + SUBWORD_BUCKETS * dimension
        self.key_pos_query_offset = self.key_query_offset + dimension
        self.value_query_offset = self.key_pos_query_offset + dimension
        self.value_pos_query_offset = self.value_query_offset + dimension
        self.hop_offset = self.value_pos_query_offset + dimension
        self.depth_weight_offset = self.hop_offset + MEMORY_READS * dimension
        self.depth_bias_offset = self.depth_weight_offset + MEMORY_READS * dimension
        self.copy_scale_offset = self.depth_bias_offset + MEMORY_READS
        self.feature_count = 5 * dimension + 12
        self.hidden_weight_offset = self.copy_scale_offset + 1
        self.hidden_bias_offset = self.hidden_weight_offset + hidden * self.feature_count
        self.output_offset = self.hidden_bias_offset + hidden
        self.parameter_count = self.output_offset + hidden
        _need(self.parameter_count <= MAX_PARAMETERS, 'routed_parameter_bound')
        _need(type(parameters) in (array, list, tuple) and len(parameters) == self.parameter_count,
              'routed_parameter_count')
        _need(not isinstance(parameters, array) or parameters.typecode == 'd', 'routed_float64_required')
        _need(all(_finite(v) and abs(v) <= MAX_PARAMETER_MAGNITUDE for v in parameters),
              'routed_parameter_finiteness')
        self.parameters = array('d', parameters)
        self.signature = hashlib.sha256(json.dumps(self.metadata(), sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

    @classmethod
    def initialize(cls, vocabulary, dimension=8, hidden=12, seed=113):
        _need(_integer(seed, 0, 2**32 - 1), 'routed_seed_bound')
        _need(type(vocabulary) in (list, tuple) and 1 <= len(vocabulary) <= MAX_VOCABULARY,
              'routed_vocabulary_bound')
        _need(_integer(dimension, 2, 16) and _integer(hidden, 2, 32), 'routed_dimension_bound')
        generator = random.Random(seed)
        count = len(vocabulary) * dimension + 53 * dimension + 10 + hidden * (5 * dimension + 12) + 2 * hidden
        _need(count <= MAX_PARAMETERS, 'routed_parameter_bound')
        values = array('d', (generator.uniform(-.35, .35) for _ in range(len(vocabulary) * dimension)))
        values.extend(generator.uniform(-.35, .35) for _ in range(11 * dimension))
        # Generic boundary/alignment inductive biases, not encoded task rules.
        values.extend((-2., 1., .2, 8., 0., 0.))
        # Character features provide a learned spelling path on every token.
        # Query interactions start near zero; hop vectors break read symmetry.
        values.extend(generator.uniform(-.1, .1) for _ in range(SUBWORD_BUCKETS * dimension))
        values.extend(generator.uniform(-.02, .02) for _ in range(4 * dimension))
        values.extend(generator.uniform(-.1, .1) for _ in range(MEMORY_READS * dimension))
        values.extend([0.] * (MEMORY_READS * dimension + MEMORY_READS + 1))
        bound = math.sqrt(6 / (5 * dimension + 12 + hidden))
        values.extend(generator.uniform(-bound, bound) for _ in range(hidden * (5 * dimension + 12)))
        values.extend([0.] * hidden)
        bound = math.sqrt(6 / (hidden + 1))
        values.extend(generator.uniform(-bound, bound) for _ in range(hidden))
        return cls(vocabulary, dimension, hidden, values)

    def metadata(self):
        return {'architecture': ARCHITECTURE, 'dimension': self.dimension, 'hidden': self.hidden,
                'vocabulary': list(self.vocabulary), 'parameter_count': self.parameter_count,
                'tokenizer': TOKENIZER, 'span_tokens': SPAN_TOKENS,
                'memory_reads': MEMORY_READS, 'context_window': CONTEXT_WINDOW,
                'subword_buckets': SUBWORD_BUCKETS, 'subword_algorithm': SUBWORD_ALGORITHM,
                'subword_widths': list(SUBWORD_WIDTHS), 'routing': ROUTING,
                'depth_mix': DEPTH_MIX, 'copy_log_floor': COPY_FLOOR,
                'copy_scale_transform': 'stable_softplus_v1',
                'request_work_limit': MAX_WORK_UNITS,
                'gradient_tape_node_limit': MAX_TAPE_NODES}

    def to_document(self):
        return {'format': MODEL_FORMAT, 'metadata': self.metadata(), 'parameters': list(self.parameters)}

    @classmethod
    def from_document(cls, document):
        _need(type(document) is dict and set(document) == {'format', 'metadata', 'parameters'} and
              document['format'] == MODEL_FORMAT, 'routed_document_format')
        spec = document['metadata']
        _need(type(spec) is dict and set(spec) == {'architecture','dimension','hidden','vocabulary',
              'parameter_count','tokenizer','span_tokens','memory_reads','context_window',
              'subword_buckets','subword_algorithm','subword_widths','routing','depth_mix',
              'copy_log_floor','copy_scale_transform','request_work_limit',
              'gradient_tape_node_limit'},
              'routed_metadata_fields')
        model = cls(spec['vocabulary'], spec['dimension'], spec['hidden'], document['parameters'])
        _need(model.metadata() == spec, 'routed_architecture_mismatch')
        return model

    def encode(self, request):
        _need(type(request) is DecisionRequest, 'validated_routed_request_required')
        records, spellings = [], {}
        def local(token):
            _need(1 <= len(token) <= MAX_TOKEN_CHARACTERS, 'routed_token_length_bound')
            if token not in spellings:
                spellings[token] = len(records)
                records.append(EncodedToken(self.index.get(token, 0), token))
            return spellings[token]
        normalized = unicodedata.normalize('NFKC', request.context).casefold()
        spans, active, total, previous_end = [], [], 0, 0
        for match in _TOKEN.finditer(normalized):
            total += 1
            _need(total <= MAX_CONTEXT_TOKENS, 'routed_context_token_budget')
            if active and '\n' in normalized[previous_end:match.start()]:
                spans.append(tuple(active));active = []
            token = match.group();active.append(local(token))
            if token in _BOUNDARIES or len(active) == SPAN_TOKENS:
                spans.append(tuple(active));active = []
            _need(len(spans) <= MAX_CONTEXT_SPANS, 'routed_context_span_budget')
            previous_end = match.end()
        if active:spans.append(tuple(active))
        _need(1 <= len(spans) <= MAX_CONTEXT_SPANS, 'routed_context_span_budget')
        prompt = tuple(local(t) for t in tokenize(request.prompt))
        _need(1 <= len(prompt) <= MAX_PROMPT_TOKENS, 'routed_prompt_token_budget')
        candidates = tuple(tuple(local(t) for t in tokenize(o.description)) for o in request.options)
        _need(all(1 <= len(t) <= MAX_OPTION_TOKENS for t in candidates), 'routed_option_token_budget')
        option_tokens = sum(map(len, candidates))
        _need(2 <= len(candidates) <= 255 and option_tokens <= MAX_ALL_OPTION_TOKENS,
              'routed_all_options_token_budget')
        accounting = {'context_characters': len(request.context), 'context_tokens': total,
            'context_spans': len(spans), 'context_tokens_processed': sum(map(len, spans)),
            'prompt_tokens': len(prompt), 'option_tokens': option_tokens, 'options': len(candidates),
            'truncated': False, 'memory_reads': MEMORY_READS,
            'all_text_roles_nonlinear_local_context_before_pooling': True,
            'exact_normalized_spelling_pointer_channel': True,
            'distinct_token_spellings': len(records),
            'oov_spellings': sum(t.word_id == 0 for t in records),
            'unknown_spelling_semantic_knowledge_claimed': False,
            'hashed_subword_features_on_all_tokens': True,
            'subword_bucket_count': SUBWORD_BUCKETS,
            'query_conditioned_key_value_attention': True,
            'question_conditioned_depth_mixture': True,
            'candidate_copy_mixture_is_independent_probability_distribution': False}
        encoded = EncodedRequest(self.signature, tuple(spans), prompt, candidates,
            tuple(o.id for o in request.options), accounting, tuple(records))
        _need(self.estimate_work(encoded) <= MAX_WORK_UNITS, 'routed_network_work_budget')
        return encoded

    encode_request = encode

    def estimate_work(self, encoded):
        _need(type(encoded) is EncodedRequest, 'encoded_routed_request_required')
        d, h = self.dimension, self.hidden
        context = sum(map(len, encoded.context))
        tokens = context + len(encoded.prompt) + sum(map(len, encoded.candidates))
        k, s = len(encoded.candidates), len(encoded.context)
        # Conservative scalar forward/backward work estimate; it is neither a
        # FLOP/latency promise nor an aggregate resource guarantee.
        # All ngrams are hashed; no character/gram truncation is permitted.
        # This conservatively budgets per-hash work and dense bucket mixing,
        # and recomputed per-hop key/value controls in addition to v3.1 work.
        grams = [sum(max(0, len(t.spelling) + 3 - width) for width in SUBWORD_WIDTHS)
                 for t in encoded.tokens]
        subwords = sum(64 * count + 2 * d * min(SUBWORD_BUCKETS, count) for count in grams)
        return 8 * (2 * self.parameter_count + 18 * d * tokens + subwords +
            MEMORY_READS * (4 * d + 2) +
            k * MEMORY_READS * (20 * d * context + s * (10 * d + 48) + 12 * d + 12) +
            k * h * self.feature_count * 4 + 32 * k)

    def _validate(self, encoded):
        _need(type(encoded) is EncodedRequest and encoded.model_signature == self.signature,
              'routed_encoded_signature')
        _need(type(encoded.context) is tuple and 1 <= len(encoded.context) <= MAX_CONTEXT_SPANS and
              all(type(span) is tuple and 1 <= len(span) <= SPAN_TOKENS for span in encoded.context),
              'routed_encoded_context_shape')
        _need(type(encoded.prompt) is tuple and 1 <= len(encoded.prompt) <= MAX_PROMPT_TOKENS and
              type(encoded.candidates) is tuple and 2 <= len(encoded.candidates) <= 255 and
              all(type(span) is tuple and 1 <= len(span) <= MAX_OPTION_TOKENS for span in encoded.candidates),
              'routed_encoded_role_shape')
        _need(type(encoded.option_ids) is tuple and len(encoded.option_ids) == len(encoded.candidates) and
              type(encoded.tokens) is tuple and 1 <= len(encoded.tokens) <=
              MAX_CONTEXT_TOKENS + MAX_PROMPT_TOKENS + MAX_ALL_OPTION_TOKENS,
              'routed_encoded_identity_shape')
        _need(all(type(t) is EncodedToken and _integer(t.word_id, 0, len(self.vocabulary)-1) and
                  type(t.spelling) is str and 1 <= len(t.spelling) <= MAX_TOKEN_CHARACTERS and
                  tokenize(t.spelling) == [t.spelling] and self.index.get(t.spelling, 0) == t.word_id
                  for t in encoded.tokens), 'routed_encoded_token_records')
        _need(len({t.spelling for t in encoded.tokens}) == len(encoded.tokens), 'routed_unique_spelling_records')
        sequence = tuple(t for span in encoded.context for t in span) + encoded.prompt + tuple(t for span in encoded.candidates for t in span)
        _need(all(_integer(i, 0, len(encoded.tokens)-1) for i in sequence) and
              sum(map(len, encoded.context)) <= MAX_CONTEXT_TOKENS and
              sum(map(len, encoded.candidates)) <= MAX_ALL_OPTION_TOKENS,
              'routed_encoded_token_bounds')
        _need(self.estimate_work(encoded) <= MAX_WORK_UNITS, 'routed_network_work_budget')
        _need(len(self.parameters) == self.parameter_count and
              all(_finite(v) and abs(v) <= MAX_PARAMETER_MAGNITUDE for v in self.parameters),
              'routed_parameters_changed_or_nonfinite')

    def _run(self, encoded, gradients=False, target_index=None):
        self._validate(encoded)
        d, h = self.dimension, self.hidden
        tape = _Tape() if gradients else None
        p = [tape.node(v) for v in self.parameters] if gradients else self.parameters
        subwords = [subword_features(t.spelling) for t in encoded.tokens]
        token_vectors = [[p[t.word_id * d + j] + _dot(
            [weight for _, weight in features],
            [p[self.subword_offset + bucket * d + j] for bucket, _ in features])
            for j in range(d)] for t, features in zip(encoded.tokens, subwords)]
        def contextual(ids):
            positions = _positions(len(ids));states = []
            for t, token in enumerate(ids):
                row = []
                for j in range(d):
                    value = token_vectors[token][j] + p[self.position_offset+j] * positions[t]
                    for r, delta in enumerate(OFFSETS):
                        if 0 <= t+delta < len(ids):
                            value = value + p[self.conv_offset+r*d+j] * token_vectors[ids[t+delta]][j]
                    row.append(_tanh(value))
                states.append(row)
            pooled = [_sum(row[j] for row in states) / len(states) for j in range(d)]
            return states, pooled, positions
        facts = [contextual(ids) for ids in encoded.context]
        q = contextual(encoded.prompt)[1]
        depth_weights = _softmax([_dot(
            p[self.depth_weight_offset+hop*d:self.depth_weight_offset+(hop+1)*d], q) +
            p[self.depth_bias_offset+hop] for hop in range(MEMORY_READS)])
        copy_scale = _softplus(p[self.copy_scale_offset])
        question_counts = collections.Counter(encoded.prompt)
        present = {i for ids in encoded.context for i in ids}
        total = sum(n for i, n in question_counts.items() if i in present)
        initial = [question_counts[i] / total if total and i in present else 0.
                   for i in range(len(encoded.tokens))]
        logits = []
        inv_root = 1 / math.sqrt(d)
        for ids in encoded.candidates:
            o = contextual(ids)[1]
            members = set(ids)
            candidate = [sum(i in members for i in span) / len(span) for span in encoded.context]
            u = [q[j] + o[j] for j in range(d)]
            mu = list(initial)
            reads, supports, evidence, key_returns, value_returns = [], [], [], [], []
            for hop in range(MEMORY_READS):
                control = [_tanh(q[j] + u[j] + p[self.hop_offset+hop*d+j]) for j in range(d)]
                key_position = p[self.key_position_offset] + _dot(
                    p[self.key_pos_query_offset:self.key_pos_query_offset+d], control)
                value_position = p[self.value_position_offset] + _dot(
                    p[self.value_pos_query_offset:self.value_pos_query_offset+d], control)
                keys = [_softmax([_dot(p[self.key_offset:self.key_offset+d], row) +
                    _triple_dot(p[self.key_query_offset:self.key_query_offset+d], row, control) +
                    key_position * positions[t] for t, row in enumerate(states)])
                    for states, _, positions in facts]
                match = [_dot(key, [mu[token] for token in span])
                         for key, span in zip(keys, encoded.context)]
                energies = []
                for s, (_, fact, _) in enumerate(facts):
                    energy = _dot(p[self.read_fact_offset:self.read_fact_offset+d], fact)
                    energy = energy + _triple_dot(p[self.read_query_offset:self.read_query_offset+d], fact, u)
                    energy = energy + _triple_dot(p[self.read_option_offset:self.read_option_offset+d], fact, o)
                    energy = energy + p[self.match_offset] * match[s] + p[self.candidate_offset] * candidate[s]
                    energy = energy + p[self.joint_offset] * match[s] * candidate[s]
                    energies.append(energy)
                attention = _softmax(energies)
                r = [_dot(attention, [fact[j] for _, fact, _ in facts]) for j in range(d)]
                reads.extend(r)
                supports.append(_sum(a * m * c for a, m, c in zip(attention, match, candidate)))
                evidence.append(_sum(a * c for a, c in zip(attention, candidate)))
                key_returns.append(_sum(a * _sum(weight for token, weight in zip(span, key)
                    if token in members) for a, span, key in zip(attention, encoded.context, keys)))
                copied = [0.] * len(encoded.tokens)
                for s, (states, _, positions) in enumerate(facts):
                    values = _softmax([_dot(p[self.value_offset:self.value_offset+d], row) +
                        _triple_dot(p[self.value_query_offset:self.value_query_offset+d], row, control) +
                        value_position * positions[t] +
                        p[self.copy_query_offset] * _dot(row, u) * inv_root
                        for t, row in enumerate(states)])
                    for token, probability in zip(encoded.context[s], values):
                        copied[token] = copied[token] + attention[s] * probability
                value_returns.append(_sum(copied[token] for token in members))
                if hop + 1 < MEMORY_READS:
                    mu = copied
                    u = [_tanh(u[j] + p[self.update_offset+j] * r[j]) for j in range(d)]
            features = q + o + reads + supports + evidence + key_returns + value_returns
            hidden = [_tanh(p[self.hidden_bias_offset+k] +
                _dot(p[self.hidden_weight_offset+k*self.feature_count:self.hidden_weight_offset+(k+1)*self.feature_count], features))
                for k in range(h)]
            copied_mass_feature = _dot(depth_weights, value_returns)
            logits.append(_dot(p[self.output_offset:self.output_offset+h], hidden) +
                          copy_scale * _log(COPY_FLOOR + copied_mass_feature))
        probabilities = _softmax(logits)
        if gradients:
            peak = max(_number(v) for v in logits)
            loss = peak - logits[target_index] + _log(_sum(_exp(v-peak) for v in logits))
            return _number(loss), tape.backward(loss, p)
        return {'logits': [_number(v) for v in logits],
                'probabilities': [_number(v) for v in probabilities],
                'accounting': dict(encoded.accounting)}

    def forward(self, encoded):
        try:
            return self._run(encoded)
        except (OverflowError, ZeroDivisionError) as error:
            raise ModelError('nonfinite_routed_computation') from error

    predict = forward

    def loss_and_grad(self, encoded, target_index):
        _need(type(encoded) is EncodedRequest and
              _integer(target_index, 0, len(encoded.candidates)-1), 'routed_target_bound')
        try:
            return self._run(encoded, True, target_index)
        except (OverflowError, ZeroDivisionError) as error:
            raise ModelError('nonfinite_routed_computation') from error


DecisionNetwork = RoutedNetwork
Model = RoutedNetwork
