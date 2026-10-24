"""Fixed-final-checkpoint training of the original contextual memory model.

Only the supplied training rows and caller provenance are used. Neither held-out
quality nor model activation is part of this bounded standard-library trainer.
"""
from array import array
import argparse
import collections
import json
import math
import os
from pathlib import Path
import random
import sys
import time

from .contextual_artifact import (ArtifactError, LINEAGE_FORMAT, canonical,
    digest, identity_identifier, regular_bytes, write_artifact)
from .decision_contract import DecisionContractError
from .decision_contextual import DecisionNetwork
from .decision_neural import build_vocabulary
from .train_decision import (MAX_INPUT, MAX_VOCABULARY, TrainingError,
                             _validate_settings, parse_training)

MAX_WORK_UNITS = 1_000_000_000
CPU_SECONDS = 600
WALL_SECONDS = 900
DEFAULT_EPOCHS = 6
DEFAULT_SEED = 113
DEFAULT_RATE = .005
DIMENSION = 8
HIDDEN = 12
L2 = .00001
CLIP_NORM = 5.
RECIPE = 'original-contextual-memory-adam-float64-v3'


def _tick(started):
    if time.process_time() - started[0] > CPU_SECONDS or time.monotonic() - started[1] > WALL_SECONDS:
        raise TrainingError('contextual_training_time_budget_exceeded')


def _prepare(input_path, provenance_path, epochs, seed, learning_rate, started):
    _validate_settings(epochs, seed, learning_rate)
    raw = regular_bytes(input_path, MAX_INPUT)
    provenance_raw = regular_bytes(provenance_path, 16_384)
    budget = lambda: _tick(started)
    rows, provenance = parse_training(raw, provenance_raw, budget)
    vocabulary = build_vocabulary([row.request for row in rows], max_vocabulary=MAX_VOCABULARY)
    model = DecisionNetwork.initialize(vocabulary, dimension=DIMENSION, hidden=HIDDEN, seed=seed)
    encoded, targets, estimated_pass = [], [], 0
    for row in rows:
        _tick(started)
        item = model.encode(row.request)
        encoded.append(item)
        targets.append([option.id for option in row.request.options].index(row.target_option_id))
        estimated_pass += model.estimate_work(item)
    work = (epochs + 2) * estimated_pass + epochs * len(rows) * model.parameter_count * 40
    preflight = {'format': 'eidos-contextual-training-preflight-v3', 'complete': True,
        'training_rows': len(rows), 'source_groups': len({row.source_group for row in rows}),
        'parameter_count': model.parameter_count, 'vocabulary_size': len(vocabulary),
        'epochs': epochs, 'seed': seed, 'learning_rate': learning_rate,
        'estimated_forward_backward_pass': estimated_pass,
        'work_units_estimate': work, 'work_units_limit': MAX_WORK_UNITS,
        'within_work_budget': work <= MAX_WORK_UNITS,
        'model_forward_loss_fit_or_quality_scoring_performed': False,
        'heldout_or_gold_inputs_requested': False,
        'estimate_is_aggregate_FLOP_or_latency_guarantee': False}
    return rows, provenance, raw, provenance_raw, model, encoded, targets, preflight


def training_preflight(input_path, provenance_path, *, epochs=DEFAULT_EPOCHS,
                       seed=DEFAULT_SEED, learning_rate=DEFAULT_RATE):
    """Read training, initialize and encode only; return work without fitting."""
    started = (time.process_time(), time.monotonic())
    prepared = _prepare(input_path, provenance_path, epochs, seed, learning_rate, started)
    _tick(started)
    return prepared[-1]


def _objective(model, encoded, targets, started):
    losses = []
    for item, target in zip(encoded, targets):
        _tick(started)
        logits = model.forward(item)['logits']
        peak = max(logits)
        losses.append(peak - logits[target] + math.log(math.fsum(math.exp(z - peak) for z in logits)))
    return math.fsum(losses) / len(losses)


def _source_pins():
    parent = Path(__file__).parent
    names = ('decision_contextual.py', 'contextual_artifact.py', 'train_contextual.py',
             'contextual.py', 'decision.py', 'decision_contract.py',
             'decision_neural.py', 'decision_artifact.py', 'train_decision.py')
    return {name: digest((parent / name).read_bytes()) for name in names}


def build(input_path, provenance_path, output_path, model_id, version='1', *,
          epochs=DEFAULT_EPOCHS, seed=DEFAULT_SEED, learning_rate=DEFAULT_RATE):
    started = (time.process_time(), time.monotonic())
    _validate_settings(epochs, seed, learning_rate)
    if not identity_identifier(model_id) or not identity_identifier(version):
        raise TrainingError('invalid_contextual_model_identity')
    output = Path(output_path).absolute()
    if output.exists() or output.is_symlink():
        raise TrainingError('contextual_output_already_exists')
    if not output.parent.is_dir() or any(p.is_symlink() for p in (output.parent, *output.parent.parents)):
        raise TrainingError('contextual_output_parent_refused')
    rows, provenance, raw, provenance_raw, model, encoded, targets, preflight = _prepare(
        input_path, provenance_path, epochs, seed, learning_rate, started)
    if not preflight['within_work_budget']:
        raise TrainingError('contextual_training_work_budget_exceeded')
    initial = _objective(model, encoded, targets, started)
    moments = array('d', [0.]) * model.parameter_count
    velocities = array('d', [0.]) * model.parameter_count
    order = list(range(len(rows)))
    rng = random.Random(seed)
    step = 0
    for _epoch in range(epochs):
        rng.shuffle(order)
        for index in order:
            _tick(started)
            _loss, gradient = model.loss_and_grad(encoded[index], targets[index])
            if len(gradient) != model.parameter_count:
                raise TrainingError('contextual_gradient_dimension_mismatch')
            norm = math.sqrt(math.fsum(g * g for g in gradient))
            if not math.isfinite(norm):
                raise TrainingError('nonfinite_contextual_gradient')
            multiplier = min(1., CLIP_NORM / norm) if norm else 1.
            step += 1
            correction_m, correction_v = 1 - .9**step, 1 - .999**step
            for j in range(model.parameter_count):
                g = gradient[j] * multiplier + L2 * model.parameters[j]
                moments[j] = .9 * moments[j] + .1 * g
                velocities[j] = .999 * velocities[j] + .001 * g * g
                update = learning_rate * (moments[j] / correction_m) / (
                    math.sqrt(velocities[j] / correction_v) + 1e-8)
                value = model.parameters[j] - update
                if not math.isfinite(value) or abs(value) > 1_000_000:
                    raise TrainingError('nonfinite_contextual_update')
                model.parameters[j] = value
    final = _objective(model, encoded, targets, started)
    _tick(started)
    groups = collections.Counter(row.source_group for row in rows)
    lineage = {'format': LINEAGE_FORMAT, 'input_sha256': digest(raw),
        'provenance_sha256': digest(provenance_raw), 'declaration': provenance,
        'rights_verified': False, 'declaration_status': 'caller_supplied_not_independently_verified',
        'training_rows': len(rows), 'source_groups': len(groups),
        'source_group_ids_sha256': digest(canonical(sorted(groups))),
        'source_group_counts_sha256': digest(canonical(dict(groups))),
        'source_group_independence_verified': False,
        'kind_counts': dict(sorted(collections.Counter(row.request.kind for row in rows).items())),
        'recipe': RECIPE, 'epochs': epochs, 'seed': seed, 'learning_rate': learning_rate,
        'l2': L2, 'l2_scope': 'all_parameters_including_biases',
        'gradient_clip_norm': CLIP_NORM, 'optimizer': 'adam_float64',
        'checkpoint_selection': 'fixed_final_epoch_no_development_or_test_input',
        'pretrained_or_vendor_model_used': False, 'third_party_training_or_inference_packages': False,
        'tokenizer_and_vocabulary_fitted_on': 'training_only',
        'raw_training_rows_embedded': False, 'privacy_guarantee': False,
        'work_units_estimate': preflight['work_units_estimate'], 'work_units_limit': MAX_WORK_UNITS,
        'cpu_seconds_limit': CPU_SECONDS, 'wall_seconds_limit': WALL_SECONDS,
        'source_module_sha256': _source_pins()}
    diagnostics = {'initial_training_cross_entropy': initial, 'final_training_cross_entropy': final,
        'scope': 'training_fit_diagnostic_not_quality_evaluation_or_calibration',
        'optimizer_steps': step, 'parameter_count': model.parameter_count,
        'vocabulary_size': len(model.metadata()['vocabulary']),
        'process_cpu_seconds': time.process_time() - started[0],
        'elapsed_seconds': time.monotonic() - started[1]}
    return write_artifact(output, model, model_id, version, lineage, diagnostics)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Train one NEW original contextual memory model')
    parser.add_argument('--input', required=True)
    parser.add_argument('--provenance', required=True)
    parser.add_argument('--output')
    parser.add_argument('--model-id')
    parser.add_argument('--version', default='1')
    parser.add_argument('--epochs', type=int, default=DEFAULT_EPOCHS)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--learning-rate', type=float, default=DEFAULT_RATE)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args(argv)
    if not args.preflight_only and (args.output is None or args.model_id is None):
        parser.error('--output and --model-id are required for fitting')
    if args.preflight_only and (args.output is not None or args.model_id is not None):
        parser.error('--preflight-only does not create an output/model')
    prior_umask = os.umask(0o077)
    try:
        settings = dict(epochs=args.epochs, seed=args.seed, learning_rate=args.learning_rate)
        result = (training_preflight(args.input, args.provenance, **settings) if args.preflight_only
                  else build(args.input, args.provenance, args.output, args.model_id,
                             args.version, **settings))
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except TrainingError as error:
        print(json.dumps({'error': 'contextual_training_refused', 'reason': error.code}), file=sys.stderr)
        return 2
    except (ArtifactError, DecisionContractError, ValueError, TypeError, OSError,
            UnicodeError, MemoryError, OverflowError, RecursionError):
        print('{"error":"contextual_training_refused","reason":"invalid_input_or_artifact"}',
              file=sys.stderr)
        return 2
    finally:
        os.umask(prior_umask)


if __name__ == '__main__':
    raise SystemExit(main())
