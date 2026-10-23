"""Train a new review-only linear registry with bounded sparse online SGD.

This original standard-library recipe accepts unequal class counts. It reads
only explicit labeled training input and a caller's rights declaration. Fixed
epochs do not consume development/test data or activate a serving registry.
"""
from array import array
import argparse
import collections
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import random
import sys
import time

from .contracts import ContractError, finite_json, parse_json
from .general_linear import FEATURES, terms, write_model
from .model import canonical, words
from .support_memory import read_regular
from .train_support import TrainingError, digest, identifier

MAX_INPUT = 32 * 1024**2
MAX_TEXT = 10_000_000
MAX_ROWS = 20_000
MAX_CLASSES = 64
MAX_FEATURES = 10_000
MAX_OBSERVED_TERMS = 1_000_000
MAX_NNZ = 10_000_000
MAX_WORK_UNITS = 1_000_000_000
DEFAULT_EPOCHS = 3
MAX_EPOCHS = 20
DEFAULT_SEED = 113
LEARNING_RATE = .5
L2 = .0001
CPU_SECONDS = 600
WALL_SECONDS = 900
REBASE_SCALE = .25
RECIPE = 'sparse-tfidf-online-softmax-sgd-v1'


@dataclass(frozen=True)
class SparseExamples:
    """CSR training rows; float64 values avoid a separate input quantization."""
    offsets: array
    indices: array
    values: array
    gold: array

    @property
    def nrows(self):
        return len(self.gold)

    @property
    def nnz(self):
        return len(self.indices)


def _check_budget(start_cpu, start_wall):
    if time.process_time() - start_cpu > CPU_SECONDS or time.monotonic() - start_wall > WALL_SECONDS:
        raise TrainingError('training_time_budget_exceeded')


def _tick(budget):
    if callable(budget):
        budget()
    elif budget is not None:
        _check_budget(*budget)


def parse_training(raw, provenance_raw, budget=None):
    """Strict training-only JSONL; class counts need not be equal or twenty."""
    _tick(budget)
    if not 1 <= len(raw) <= MAX_INPUT or not 1 <= len(provenance_raw) <= 16_000:
        raise TrainingError('input_size_limit')
    provenance = parse_json(provenance_raw.decode('utf-8'), maximum=16_000)
    if type(provenance) is not dict or set(provenance) != {'source', 'license', 'citation', 'rights_basis'}:
        raise TrainingError('provenance_fields_required')
    if any(type(value) is not str or not 1 <= len(value) <= 1000 or not value.strip()
           for value in provenance.values()):
        raise TrainingError('invalid_provenance')
    canonical(provenance)
    lines = raw.decode('utf-8').splitlines()
    if not 2 <= len(lines) <= MAX_ROWS or any(not line.strip() for line in lines):
        raise TrainingError('row_count_or_blank_line')
    rows, ids, normalized = [], set(), set()
    total = 0
    for line in lines:
        row = parse_json(line, maximum=64_000)
        finite_json(row, max_nodes=20, max_text=15_000)
        if type(row) is not dict or set(row) != {'id', 'text', 'label'}:
            raise TrainingError('row_fields_required')
        if not identifier(row['id']) or not identifier(row['label']):
            raise TrainingError('invalid_id_or_label')
        if type(row['text']) is not str or not 1 <= len(row['text']) <= 12_000:
            raise TrainingError('invalid_text')
        row['text'].encode('utf-8')  # Refuse escaped unpaired surrogates.
        text_key = ' '.join(words(row['text']))
        if not text_key:
            raise TrainingError('empty_word_sequence')
        if row['id'] in ids:
            raise TrainingError('duplicate_id')
        if text_key in normalized:
            raise TrainingError('duplicate_normalized_text')
        total += len(row['text'])
        if total > MAX_TEXT:
            raise TrainingError('aggregate_text_limit')
        ids.add(row['id'])
        normalized.add(text_key)
        rows.append(row)
        _tick(budget)
    counts = collections.Counter(row['label'] for row in rows)
    if not 2 <= len(counts) <= MAX_CLASSES:
        raise TrainingError('class_count_outside_2_to_64')
    result = sorted(rows, key=lambda row: (row['label'], row['id']))
    _tick(budget)
    return result, provenance


def _prepare(rows, budget=None):
    """Fit training-only DF/IDF, then retain normalized vectors in compact CSR."""
    _tick(budget)
    if array('I').itemsize != 4 or array('d').itemsize != 8:
        raise TrainingError('unsupported_array_layout')
    labels = sorted({row['label'] for row in rows})
    frequencies = collections.Counter()
    for row in rows:
        frequencies.update(set(terms(row['text'])))
        if len(frequencies) > MAX_OBSERVED_TERMS:
            raise TrainingError('observed_term_limit')
        _tick(budget)
    vocabulary = sorted(sorted(frequencies, key=lambda term: (-frequencies[term], term))[:MAX_FEATURES])
    if not vocabulary or any(not 1 <= len(term) <= 512 for term in vocabulary):
        raise TrainingError('selected_feature_string_limit')
    idf = [math.log((1 + len(rows)) / (1 + frequencies[term])) + 1 for term in vocabulary]
    index = {term: i for i, term in enumerate(vocabulary)}
    label_index = {label: i for i, label in enumerate(labels)}
    # Discard the large observed vocabulary before constructing the sparse table.
    del frequencies
    offsets, indices, values, gold = array('I', [0]), array('I'), array('d'), array('I')
    for row in rows:
        counts = collections.Counter(terms(row['text']))
        pairs = sorted((index[term], math.log1p(count) * idf[index[term]])
                       for term, count in counts.items() if term in index)
        norm = math.sqrt(math.fsum(value * value for _, value in pairs))
        if not norm or not math.isfinite(norm):
            raise TrainingError('training_row_without_selected_features')
        if len(indices) + len(pairs) > MAX_NNZ:
            raise TrainingError('sparse_vector_limit')
        indices.extend(i for i, _ in pairs)
        values.extend(value / norm for _, value in pairs)
        gold.append(label_index[row['label']])
        offsets.append(len(indices))
        _tick(budget)
    return labels, vocabulary, idf, SparseExamples(offsets, indices, values, gold)


def _logits(weights, indices, values, classes, width, scale, start, stop):
    bias = classes * width
    return [weights[bias + c] + scale * math.fsum(
        weights[c * width + indices[j]] * values[j] for j in range(start, stop))
            for c in range(classes)]


def _sgd_step(weights, indices, values, gold, classes, width, scale, rate, *,
              start=0, stop=None, l2=L2, budget=None):
    """One exact lazy-L2 softmax SGD update; return the new global W scale.

    Effective weight parameters are ``scale * weights[:classes * width]``;
    the trailing biases remain unscaled and unpenalized. Gradients are computed
    from pre-update logits, before applying weight-only L2 shrinkage.
    """
    _tick(budget)
    if (not math.isfinite(scale) or not 0 < scale <= 1 or not math.isfinite(rate) or
            rate <= 0 or not math.isfinite(l2) or l2 < 0 or rate * l2 >= 1):
        raise TrainingError('invalid_sgd_state_or_rate')
    if stop is None:
        stop = len(indices)
    logits = _logits(weights, indices, values, classes, width, scale, start, stop)
    if any(not math.isfinite(value) for value in logits):
        raise TrainingError('nonfinite_training_logits')
    maximum = max(logits)
    exponentials = [math.exp(value - maximum) for value in logits]
    denominator = math.fsum(exponentials)
    new_scale = scale * (1 - rate * l2)
    if not math.isfinite(new_scale) or new_scale <= 0:
        raise TrainingError('invalid_sgd_scale')
    factor = rate / new_scale
    bias = classes * width
    for c, exponential in enumerate(exponentials):
        residual = exponential / denominator - (c == gold)
        weights[bias + c] -= rate * residual
        offset = c * width
        for j in range(start, stop):
            weights[offset + indices[j]] -= factor * residual * values[j]
        _tick(budget)
    if new_scale < REBASE_SCALE:
        for i in range(bias):
            weights[i] *= new_scale
            if i % 4096 == 0:
                _tick(budget)
        new_scale = 1.0
    return new_scale


def _objective(weights, examples, classes, width, budget=None):
    losses = []
    for row, gold in enumerate(examples.gold):
        logits = _logits(weights, examples.indices, examples.values, classes, width,
                         1.0, examples.offsets[row], examples.offsets[row + 1])
        maximum = max(logits)
        losses.append(maximum - logits[gold] + math.log(math.fsum(
            math.exp(value - maximum) for value in logits)))
        _tick(budget)
    penalty = math.fsum(weights[i] * weights[i] for i in range(classes * width)) * (L2 / 2)
    result = math.fsum(losses) / examples.nrows + penalty
    if not math.isfinite(result):
        raise TrainingError('nonfinite_training_objective')
    _tick(budget)
    return result


def _fit(examples, classes, width, epochs, seed, budget=None):
    if budget is None:
        budget = (time.process_time(), time.monotonic())
    _tick(budget)
    weights = array('d', [0.0]) * (classes * width + classes)
    initial = _objective(weights, examples, classes, width, budget)
    order, rng = list(range(examples.nrows)), random.Random(seed)
    scale, step = 1.0, 0
    for _ in range(epochs):
        rng.shuffle(order)
        _tick(budget)
        for row in order:
            rate = LEARNING_RATE / math.sqrt(1 + step / examples.nrows)
            scale = _sgd_step(weights, examples.indices, examples.values,
                              examples.gold[row], classes, width, scale, rate,
                              start=examples.offsets[row], stop=examples.offsets[row + 1], budget=budget)
            step += 1
    for i in range(classes * width):
        weights[i] *= scale
        if i % 4096 == 0:
            _tick(budget)
    if any(not math.isfinite(value) or abs(value) > 1e6 for value in weights):
        raise TrainingError('invalid_fitted_parameters')
    final = _objective(weights, examples, classes, width, budget)
    return weights, initial, final


def _work_units(examples, classes, width, epochs):
    # Weighted class-feature visits, class residuals and dense scaling passes.
    # This is a deterministic refusal bound, not a FLOP, CPU or cgroup guarantee.
    # eta never exceeds LEARNING_RATE, so this also bounds lazy rebasing passes.
    minimum_rebase_steps = math.floor(math.log(REBASE_SCALE) / math.log1p(-LEARNING_RATE * L2)) + 1
    rebases = math.ceil(epochs * examples.nrows / minimum_rebase_steps)
    return ((2 * epochs + 2) * classes * examples.nnz +
            (4 * epochs + 4) * classes * examples.nrows +
            (rebases + 3) * classes * width)


def build(input_path, provenance_path, output_path, model_id, version='1', *,
          epochs=DEFAULT_EPOCHS, seed=DEFAULT_SEED):
    """Fit once into a NEW private registry; never overwrite or activate one.

    The checked legacy writer uses ordinary file creation, so the short export
    section temporarily restricts the process umask and restores it in finally.
    Prefer this module's CLI rather than concurrent threaded calls to build().
    A publication error can leave an incomplete private directory; it is never
    removed or automatically retried.
    """
    start = (time.process_time(), time.monotonic())
    if not identifier(model_id) or not identifier(version):
        raise TrainingError('invalid_model_id_or_version')
    if type(epochs) is not int or not 1 <= epochs <= MAX_EPOCHS:
        raise TrainingError('epochs_outside_1_to_20')
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise TrainingError('seed_outside_0_to_2pow32_minus1')
    output = Path(output_path).absolute()
    if output.exists() or output.is_symlink():
        raise TrainingError('output_already_exists')
    if not output.parent.is_dir() or any(path.is_symlink() for path in (output.parent, *output.parent.parents)):
        raise TrainingError('output_parent_must_be_existing_regular_directory')
    raw = read_regular(input_path, MAX_INPUT)
    provenance_raw = read_regular(provenance_path, 16_000)
    rows, provenance = parse_training(raw, provenance_raw, start)
    class_counts = dict(sorted(collections.Counter(row['label'] for row in rows).items()))
    labels, vocabulary, idf, examples = _prepare(rows, start)
    k, width, n = len(labels), len(vocabulary), len(rows)
    work = _work_units(examples, k, width, epochs)
    if work > MAX_WORK_UNITS:
        raise TrainingError('training_work_budget_exceeded')
    weights, initial, final = _fit(examples, k, width, epochs, seed, start)
    # Validate again at the export boundary rather than depending on the fitter.
    if (len(weights) != k * width + k or
            any(not math.isfinite(value) or abs(value) > 1e6 for value in weights) or
            not math.isfinite(initial) or initial < 0 or not math.isfinite(final) or final < 0):
        raise TrainingError('invalid_fitted_parameters')
    _tick(start)
    training = {
        'format': 'eidos-caller-sparse-training-v1',
        'input_sha256': digest(raw), 'provenance_sha256': digest(provenance_raw),
        'declaration': provenance, 'declaration_status': 'caller_supplied_not_independently_verified',
        'training_rows': n, 'class_counts': class_counts, 'training_recipe': RECIPE,
        'optimizer': 'online_softmax_sgd_from_zero_float64',
        'epochs': epochs, 'seed': seed, 'learning_rate': LEARNING_RATE, 'l2': L2,
        'learning_rate_schedule': 'inverse_sqrt_one_plus_zero_based_step_over_training_rows',
        'regularization': 'lazy_exact_l2_weights_only_bias_unpenalized',
        'checkpoint_selection': 'fixed_final_epoch_no_development_or_test_input',
        'objective': 'mean_cross_entropy_plus_l2_half_weight_norm_squared_bias_unpenalized',
        'initial_training_objective': initial, 'final_training_objective': final,
        'objective_scope': 'training_fit_diagnostic_not_evaluation_or_calibration',
        'normalization': 'training_document_frequency_log1p_tf_idf_l2_normalized_float64',
        'storage': 'csr_uint32_offsets_indices_labels_float64_values',
        'work_units': work, 'work_limit': MAX_WORK_UNITS,
        'work_unit_scope': 'weighted_class_feature_visits_not_flops_or_cpu_guarantee',
        'cpu_seconds_limit': CPU_SECONDS, 'wall_seconds_limit': WALL_SECONDS,
        'time_control': 'cooperative_checks_not_operating_system_limits',
        'limits': {'input_bytes': MAX_INPUT, 'text_characters': MAX_TEXT,
                   'rows': MAX_ROWS, 'classes': MAX_CLASSES,
                   'observed_terms': MAX_OBSERVED_TERMS, 'selected_features': MAX_FEATURES, 'nnz': MAX_NNZ},
        'input_schema': 'id,text,label JSONL', 'raw_training_text_embedded': False,
        'privacy_guarantee': False, 'benchmark_or_calibration_evidence_created': False,
        'same_recipe_or_artifact_as_historical_laya_comparison': False,
    }
    document = {
        'format': 'eidos-linear-general-v1', 'id': model_id, 'version': version,
        'feature_version': FEATURES, 'labels': labels, 'vocabulary': vocabulary, 'idf': idf,
        'temperatures': {str(count): 1.0 for count in range(2, k + 1)},
        'training': training, 'calibration': None, 'languages': ['en'],
        'domain': 'Caller-supplied sparse linear training; English research profile; human review required',
        'consequential': True, 'thresholds': {'probability': 1.0, 'margin': 1.0, 'coverage': .35},
    }
    _tick(start)
    mask = os.umask(0o077)
    try:
        output.mkdir(mode=0o700, exist_ok=False)
        model = write_model(output, document, weights)
        _tick(start)
        files = {name: {'bytes': (output / name).stat().st_size,
                        'sha256': digest((output / name).read_bytes())}
                 for name in ('model.json', 'weights.f32', 'registry.json')}
        receipt = {
            'format': 'eidos-owned-sparse-training-receipt-v1', 'complete': True,
            'model': model.identity, 'classes': k, 'training_rows': n, 'class_counts': class_counts,
            'features': width, 'nnz': examples.nnz, 'files': files,
            'input_sha256': digest(raw), 'provenance_sha256': digest(provenance_raw),
            'training_recipe': RECIPE, 'optimizer': training['optimizer'],
            'epochs': epochs, 'seed': seed, 'checkpoint_selection': training['checkpoint_selection'],
            'work_units': work, 'initial_training_objective': initial, 'final_training_objective': final,
            'objective_scope': training['objective_scope'], 'export_parameters': 'little_endian_float32',
            'third_party_training_or_inference_packages': False,
            'operational_acceptance_enabled': False, 'model_promoted': False,
            'evaluation_or_calibration_performed': False, 'privacy_guarantee': False,
            'same_recipe_or_artifact_as_historical_laya_comparison': False,
        }
        # Complete all fallible permission changes before creating the marker.
        # Existing evaluators refuse a registry with no valid completed receipt.
        for name in files:
            (output / name).chmod(0o400, follow_symlinks=False)
        _tick(start)
        fd = os.open(output / 'TRAINING-RECEIPT.json',
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(receipt) + b'\n')
        return receipt
    finally:
        os.umask(mask)


def main():
    parser = argparse.ArgumentParser(description='Train a NEW review-only sparse linear registry with standard-library Python')
    parser.add_argument('--input', required=True, help='UTF-8 JSONL: id,text,label; unequal class counts allowed')
    parser.add_argument('--provenance', required=True, help='JSON: source,license,citation,rights_basis; caller declaration')
    parser.add_argument('--output', required=True, help='NEW private registry directory; never overwrite or activate')
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--version', default='1')
    parser.add_argument('--epochs', type=int, default=DEFAULT_EPOCHS, help='Fixed 1–20 epochs; default 3; no evaluation selection')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED, help='Local deterministic shuffle seed, 0–2^32−1; default 113')
    args = parser.parse_args()
    try:
        result = build(args.input, args.provenance, args.output, args.model_id,
                       args.version, epochs=args.epochs, seed=args.seed)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except TrainingError as error:
        print(json.dumps({'error': 'sparse_training_refused', 'reason': error.code}), file=sys.stderr)
        return 2
    except (ContractError, ValueError, TypeError, OSError, UnicodeError, MemoryError, OverflowError):
        print(json.dumps({'error': 'sparse_training_refused', 'reason': 'invalid_input_or_artifact'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
