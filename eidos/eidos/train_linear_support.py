"""Fit a NEW review-only linear registry from explicit labeled support files.

Original stdlib batch softmax regression; no pretrained weights, evaluation
selection, network, calibration, registry activation or action execution.
"""
from array import array
import argparse
import collections
import json
import math
import os
from pathlib import Path
import sys
import time

from .contracts import ContractError
from .general_linear import FEATURES, terms, write_model
from .model import canonical
from .support_memory import read_regular
from .train_support import (MAX_INPUT, MAX_NNZ, MAX_OBSERVED_TERMS,
                            TrainingError, digest, identifier, parse_training)

DEFAULT_EPOCHS = 64
MAX_EPOCHS = 200
LEARNING_RATE = 1.0
L2 = 0.0001
MAX_WORK_UNITS = 250_000_000
CPU_SECONDS = 60
WALL_SECONDS = 90


def _check_budget(start_cpu, start_wall):
    if time.process_time() - start_cpu > CPU_SECONDS or time.monotonic() - start_wall > WALL_SECONDS:
        raise TrainingError('training_time_budget_exceeded')


def _prepare(rows):
    labels = sorted({row['label'] for row in rows})
    frequencies = collections.Counter()
    for row in rows:
        frequencies.update(set(terms(row['text'])))
        if len(frequencies) > MAX_OBSERVED_TERMS:
            raise TrainingError('observed_term_limit')
    vocabulary = sorted(sorted(frequencies, key=lambda t: (-frequencies[t], t))[:10_000])
    if any(not 1 <= len(term) <= 512 for term in vocabulary):
        raise TrainingError('selected_feature_string_limit')
    idf = [math.log((1 + len(rows)) / (1 + frequencies[t])) + 1 for t in vocabulary]
    index = {term: i for i, term in enumerate(vocabulary)}
    label_index = {label: i for i, label in enumerate(labels)}
    examples = []
    nnz = 0
    for row in rows:
        counts = collections.Counter(terms(row['text']))
        pairs = sorted((index[t], math.log1p(n) * idf[index[t]]) for t, n in counts.items() if t in index)
        norm = math.sqrt(math.fsum(value * value for _, value in pairs))
        if not norm:
            raise TrainingError('support_without_selected_features')
        pairs = [(i, value / norm) for i, value in pairs]
        nnz += len(pairs)
        if nnz > MAX_NNZ:
            raise TrainingError('sparse_vector_limit')
        examples.append((label_index[row['label']], pairs))
    return labels, vocabulary, idf, examples, nnz


def _objective_gradient(weights, examples, classes, width, budget=None):
    """Mean stable cross-entropy + L2/2 ||W||²; biases are unpenalized."""
    gradient = array('d', [0.0]) * len(weights)
    losses = []
    count = len(examples)
    bias = classes * width
    for gold, pairs in examples:
        logits = [weights[bias + c] + math.fsum(weights[c * width + i] * value for i, value in pairs)
                  for c in range(classes)]
        maximum = max(logits)
        exponentials = [math.exp(value - maximum) for value in logits]
        denominator = math.fsum(exponentials)
        losses.append(maximum - logits[gold] + math.log(denominator))
        for c, exponential in enumerate(exponentials):
            residual = (exponential / denominator - (c == gold)) / count
            gradient[bias + c] += residual
            offset = c * width
            for i, value in pairs:
                gradient[offset + i] += residual * value
        if budget is not None:
            _check_budget(*budget)
    penalty = math.fsum(value * value for value in weights[:bias]) * (L2 / 2)
    for i in range(bias):
        gradient[i] += L2 * weights[i]
    return math.fsum(losses) / count + penalty, gradient


def _fit(examples, classes, width, epochs, budget):
    weights = array('d', [0.0]) * (classes * width + classes)
    initial = None
    for _ in range(epochs):
        _check_budget(*budget)
        objective, gradient = _objective_gradient(weights, examples, classes, width, budget)
        if initial is None:
            initial = objective
        for i, value in enumerate(gradient):
            weights[i] -= LEARNING_RATE * value
        if not math.isfinite(objective):
            raise TrainingError('nonfinite_training_objective')
    final, _ = _objective_gradient(weights, examples, classes, width, budget)
    if not math.isfinite(final) or any(not math.isfinite(v) or abs(v) > 1e6 for v in weights):
        raise TrainingError('invalid_fitted_parameters')
    return weights, initial, final


def build(input_path, provenance_path, output_path, model_id, version='1', *, epochs=DEFAULT_EPOCHS):
    start = (time.process_time(), time.monotonic())
    if not identifier(model_id) or not identifier(version):
        raise TrainingError('invalid_model_id_or_version')
    if type(epochs) is not int or not 1 <= epochs <= MAX_EPOCHS:
        raise TrainingError('epochs_outside_1_to_200')
    output = Path(output_path).absolute()
    if output.exists() or output.is_symlink():
        raise TrainingError('output_already_exists')
    if not output.parent.is_dir() or any(p.is_symlink() for p in (output.parent, *output.parent.parents)):
        raise TrainingError('output_parent_must_be_existing_regular_directory')
    raw = read_regular(input_path, MAX_INPUT)
    provenance_raw = read_regular(provenance_path, 16_000)
    rows, provenance = parse_training(raw, provenance_raw)
    labels, vocabulary, idf, examples, nnz = _prepare(rows)
    k, width = len(labels), len(vocabulary)
    # Conservative arithmetic work bound, not a CPU-time/cgroup guarantee.
    work = (epochs + 1) * (2 * k * nnz + 4 * k * width + k * len(rows))
    if work > MAX_WORK_UNITS:
        raise TrainingError('training_work_budget_exceeded')
    weights, initial, final = _fit(examples, k, width, epochs, start)
    _check_budget(*start)
    training = {
        'format': 'eidos-caller-linear-training-v1',
        'input_sha256': digest(raw), 'provenance_sha256': digest(provenance_raw),
        'declaration': provenance, 'declaration_status': 'caller_supplied_not_independently_verified',
        'support_rows': len(rows), 'per_class': 20,
        'training_recipe': 'support-only-tfidf-batch-softmax-gradient-descent-v1',
        'optimizer': 'full_batch_gradient_descent_from_zero_float64',
        'epochs': epochs, 'learning_rate': LEARNING_RATE, 'l2': L2,
        'objective': 'mean_cross_entropy_plus_l2_half_weight_norm_squared_bias_unpenalized',
        'initial_training_objective': initial, 'final_training_objective': final,
        'objective_scope': 'support_fit_diagnostic_not_evaluation_or_calibration',
        'work_units': work, 'work_limit': MAX_WORK_UNITS,
        'cpu_seconds_limit': CPU_SECONDS, 'wall_seconds_limit': WALL_SECONDS,
        'input_schema': 'id,text,label JSONL', 'raw_training_text_embedded': False,
        'privacy_guarantee': False, 'benchmark_or_calibration_evidence_created': False,
        'same_recipe_or_artifact_as_historical_laya_comparison': False,
    }
    document = {'format': 'eidos-linear-general-v1', 'id': model_id, 'version': version,
                'feature_version': FEATURES, 'labels': labels, 'vocabulary': vocabulary, 'idf': idf,
                'temperatures': {str(n): 1.0 for n in range(2, k + 1)}, 'training': training,
                'calibration': None, 'languages': ['en'],
                'domain': 'Caller-supplied linear support; English research profile; human review required',
                'consequential': True, 'thresholds': {'probability': 1.0, 'margin': 1.0, 'coverage': .35}}
    output.mkdir(mode=0o700, exist_ok=False)
    model = write_model(output, document, weights)
    files = {name: {'bytes': (output / name).stat().st_size,
                    'sha256': digest((output / name).read_bytes())}
             for name in ('model.json', 'weights.f32', 'registry.json')}
    receipt = {'format': 'eidos-owned-linear-training-receipt-v1', 'complete': True,
               'model': model.identity, 'classes': k, 'support_rows': len(rows),
               'features': width, 'nnz': nnz, 'files': files,
               'input_sha256': digest(raw), 'provenance_sha256': digest(provenance_raw),
               'training_recipe': training['training_recipe'], 'epochs': epochs,
               'work_units': work, 'initial_training_objective': initial, 'final_training_objective': final,
               'objective_scope': training['objective_scope'],
               'export_parameters': 'little_endian_float32',
               'third_party_training_or_inference_packages': False,
               'operational_acceptance_enabled': False, 'model_promoted': False,
               'evaluation_or_calibration_performed': False, 'privacy_guarantee': False,
               'same_recipe_or_artifact_as_historical_laya_comparison': False}
    with (output / 'TRAINING-RECEIPT.json').open('xb') as stream:
        stream.write(canonical(receipt) + b'\n')
    for name in (*files, 'TRAINING-RECEIPT.json'):
        (output / name).chmod(0o400, follow_symlinks=False)
    return receipt


def main():
    parser = argparse.ArgumentParser(description='Train a NEW local review-only linear registry using standard-library Python')
    parser.add_argument('--input', required=True, help='UTF-8 JSONL: id,text,label; exactly20distinct rows/class')
    parser.add_argument('--provenance', required=True, help='JSON: source,license,citation,rights_basis')
    parser.add_argument('--output', required=True, help='NEW registry directory; never overwrite an existing output')
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--version', default='1')
    parser.add_argument('--epochs', type=int, default=DEFAULT_EPOCHS, help='Fixed1–200passes, default64; no evaluation-based selection')
    args = parser.parse_args()
    mask = os.umask(0o077)
    try:
        receipt = build(args.input, args.provenance, args.output, args.model_id, args.version, epochs=args.epochs)
        print(json.dumps(receipt, sort_keys=True, allow_nan=False))
        return 0
    except TrainingError as error:
        print(json.dumps({'error': 'linear_training_refused', 'reason': error.code}), file=sys.stderr)
        return 2
    except (ContractError, ValueError, TypeError, OSError, UnicodeError, MemoryError, OverflowError):
        print(json.dumps({'error': 'linear_training_refused', 'reason': 'invalid_input_or_artifact'}), file=sys.stderr)
        return 2
    finally:
        os.umask(mask)


if __name__ == '__main__':
    raise SystemExit(main())
