"""Fit an original question/context/candidate scorer from explicit owned tasks.

Only standard-library modules and original EIDOS modules are used. This fixed
final-epoch trainer neither selects on development/test data nor activates a
model. Rights and group provenance are caller declarations, not verified facts.
"""
from array import array
import argparse
import collections
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
import time

from .decision_artifact import (ArtifactError, canonical, digest, identity_identifier,
                                regular_bytes, strict_json, write_artifact)
from .decision_contract import DecisionContractError, validate_request
from .decision_neural import DecisionNetwork, build_vocabulary

MAX_INPUT = 32 * 1024**2
MAX_TEXT = 8_000_000
MAX_ROWS = 2_000
MAX_VOCABULARY = 4_096
MAX_EPOCHS = 100
MAX_WORK_UNITS = 1_000_000_000
DEFAULT_EPOCHS = 20
DEFAULT_SEED = 113
DEFAULT_RATE = .005
DIMENSION = 12
HIDDEN = 16
CPU_SECONDS = 600
WALL_SECONDS = 900
L2 = .00001
CLIP_NORM = 5.
RECIPE = 'question-conditioned-attention-mlp-adam-v2'


class TrainingError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TrainingRow:
    request: object
    target_option_id: str
    source_group: str


def _tick(started):
    if callable(started):
        started()
    elif started is not None:
        if time.process_time() - started[0] > CPU_SECONDS or time.monotonic() - started[1] > WALL_SECONDS:
            raise TrainingError('training_time_budget_exceeded')


def _semantic_key(request):
    """Exclude administrative IDs from duplicate-task checks and model semantics."""
    wire = request.as_dict()
    candidates = sorted((option.description.casefold().strip(),
                         option.value if request.kind == 'SCORE' else None)
                        for option in request.options)
    return digest(canonical({'kind': request.kind, 'context': ' '.join(request.context.casefold().split()),
        'prompt': ' '.join(request.prompt.casefold().split()), 'options': candidates,
        'minimum': request.minimum, 'maximum': request.maximum}))


def parse_training(raw, provenance_raw, budget=None):
    _tick(budget)
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_INPUT:
        raise TrainingError('training_input_size_refused')
    provenance = strict_json(provenance_raw, 16_384)
    if type(provenance) is not dict or set(provenance) != {'source', 'license', 'citation', 'rights_basis'}:
        raise TrainingError('provenance_fields_required')
    if any(type(value) is not str or not 1 <= len(value) <= 1000 or not value.strip()
           for value in provenance.values()):
        raise TrainingError('invalid_provenance_declaration')
    canonical(provenance)
    lines = raw.decode('utf-8').splitlines()
    if not 2 <= len(lines) <= MAX_ROWS or any(not line.strip() for line in lines):
        raise TrainingError('training_row_count_or_blank_line_refused')
    rows, ids, tasks, text_total = [], set(), set(), 0
    for line in lines:
        _tick(budget)
        obj = strict_json(line.encode('utf-8'), 1024 * 1024)
        if type(obj) is not dict or set(obj) != {'request', 'target_option_id', 'source_group'}:
            raise TrainingError('training_row_fields_mismatch')
        request = validate_request(obj['request'])
        target = obj['target_option_id']
        group = obj['source_group']
        if type(target) is not str or target not in {option.id for option in request.options}:
            raise TrainingError('target_option_not_offered')
        if not identity_identifier(group):
            raise TrainingError('invalid_source_group')
        if request.request_id in ids:
            raise TrainingError('duplicate_training_request_id')
        key = _semantic_key(request)
        if key in tasks:
            raise TrainingError('duplicate_training_task')
        ids.add(request.request_id)
        tasks.add(key)
        text_total += len(request.context) + len(request.prompt) + sum(len(o.description) for o in request.options)
        if text_total > MAX_TEXT:
            raise TrainingError('training_text_budget_exceeded')
        rows.append(TrainingRow(request, target, group))
    # Stable ordering preserves fitting when records are reordered; input-byte
    # provenance correctly changes. Administrative IDs are not scorer features.
    rows.sort(key=lambda row: (row.source_group, row.request.request_id))
    return rows, provenance


def _validate_settings(epochs, seed, learning_rate):
    if type(epochs) is not int or not 1 <= epochs <= MAX_EPOCHS:
        raise TrainingError('epochs_outside_bounds')
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise TrainingError('seed_outside_bounds')
    if type(learning_rate) not in (int, float) or not math.isfinite(learning_rate) or not .000001 <= learning_rate <= .2:
        raise TrainingError('learning_rate_outside_bounds')


def _objective(model, encoded, targets, started):
    losses = []
    for example, target in zip(encoded, targets):
        _tick(started)
        logits = model.forward(example)['logits']
        peak = max(logits)
        losses.append(peak - logits[target] + math.log(math.fsum(math.exp(z - peak) for z in logits)))
    return math.fsum(losses) / len(losses)


def _source_pins():
    parent = Path(__file__).parent
    return {name: digest((parent / name).read_bytes()) for name in
            ('decision_contract.py', 'decision_neural.py', 'decision_artifact.py', 'train_decision.py')}


def build(input_path, provenance_path, output_path, model_id, version='1', *,
          epochs=DEFAULT_EPOCHS, seed=DEFAULT_SEED, learning_rate=DEFAULT_RATE):
    started = (time.process_time(), time.monotonic())
    _validate_settings(epochs, seed, learning_rate)
    if not identity_identifier(model_id) or not identity_identifier(version):
        raise TrainingError('invalid_model_identity')
    output = Path(output_path).absolute()
    if output.exists() or output.is_symlink():
        raise TrainingError('output_already_exists')
    if not output.parent.is_dir() or any(p.is_symlink() for p in (output.parent, *output.parent.parents)):
        raise TrainingError('output_parent_refused')
    raw = regular_bytes(input_path, MAX_INPUT)
    provenance_raw = regular_bytes(provenance_path, 16_384)
    rows, provenance = parse_training(raw, provenance_raw, started)
    _tick(started)
    vocabulary = build_vocabulary([r.request for r in rows], max_vocabulary=MAX_VOCABULARY)
    model = DecisionNetwork.initialize(vocabulary, dimension=DIMENSION, hidden=HIDDEN, seed=seed)
    encoded, targets = [], []
    estimated_pass = 0
    for row in rows:
        _tick(started)
        item = model.encode(row.request)
        encoded.append(item)
        targets.append([o.id for o in row.request.options].index(row.target_option_id))
        estimated_pass += model.estimate_work(item)
    # Include two objectives, complete analytical gradients, dense Adam moments
    # and updates. This is a conservative refusal estimate, not a FLOP promise.
    work = (epochs + 2) * estimated_pass + epochs * len(rows) * model.parameter_count * 40
    if work > MAX_WORK_UNITS:
        raise TrainingError('training_work_budget_exceeded')
    initial = _objective(model, encoded, targets, started)
    moments = array('d', [0.]) * model.parameter_count
    velocities = array('d', [0.]) * model.parameter_count
    rng = random.Random(seed)
    order = list(range(len(rows)))
    step = 0
    for _epoch in range(epochs):
        rng.shuffle(order)
        for index in order:
            _tick(started)
            _loss, gradients = model.loss_and_grad(encoded[index], targets[index])
            if len(gradients) != model.parameter_count:
                raise TrainingError('gradient_dimension_mismatch')
            norm = math.sqrt(math.fsum(g * g for g in gradients))
            if not math.isfinite(norm):
                raise TrainingError('nonfinite_training_gradient')
            multiplier = min(1., CLIP_NORM / norm) if norm else 1.
            step += 1
            correction_m, correction_v = 1 - .9**step, 1 - .999**step
            for j in range(model.parameter_count):
                gradient = gradients[j] * multiplier + L2 * model.parameters[j]
                moments[j] = .9 * moments[j] + .1 * gradient
                velocities[j] = .999 * velocities[j] + .001 * gradient * gradient
                update = learning_rate * (moments[j] / correction_m) / (math.sqrt(velocities[j] / correction_v) + 1e-8)
                value = model.parameters[j] - update
                if not math.isfinite(value) or abs(value) > 1_000_000:
                    raise TrainingError('nonfinite_or_unbounded_training_parameters')
                model.parameters[j] = value
    final = _objective(model, encoded, targets, started)
    _tick(started)
    group_counts = collections.Counter(row.source_group for row in rows)
    groups = sorted(group_counts)
    training = {'format': 'eidos-owned-decision-training-lineage-v1',
        'input_sha256': digest(raw), 'provenance_sha256': digest(provenance_raw),
        'declaration': provenance, 'rights_verified': False,
        'declaration_status': 'caller_supplied_not_independently_verified',
        'training_rows': len(rows), 'source_groups': len(groups),
        'source_group_ids_sha256': digest(canonical(groups)),
        'source_group_counts_sha256': digest(canonical(dict(group_counts))),
        'source_group_independence_verified': False,
        'kind_counts': dict(sorted(collections.Counter(row.request.kind for row in rows).items())),
        'recipe': RECIPE, 'epochs': epochs, 'seed': seed, 'learning_rate': learning_rate,
        'l2': L2, 'l2_scope': 'all_parameters_including_biases',
        'gradient_clip_norm': CLIP_NORM, 'optimizer': 'adam_float64',
        'checkpoint_selection': 'fixed_final_epoch_no_development_or_test_input',
        'pretrained_or_vendor_model_used': False, 'third_party_training_or_inference_packages': False,
        'tokenizer_and_vocabulary_fitted_on': 'training_only',
        'raw_training_rows_embedded': False, 'privacy_guarantee': False,
        'work_units_estimate': work, 'work_units_limit': MAX_WORK_UNITS,
        'cpu_seconds_limit': CPU_SECONDS, 'wall_seconds_limit': WALL_SECONDS,
        'source_module_sha256': _source_pins()}
    diagnostics = {'initial_training_cross_entropy': initial, 'final_training_cross_entropy': final,
        'scope': 'training_fit_diagnostic_not_quality_evaluation_or_calibration',
        'optimizer_steps': step, 'parameter_count': model.parameter_count,
        'vocabulary_size': len(vocabulary), 'process_cpu_seconds': time.process_time() - started[0],
        'elapsed_seconds': time.monotonic() - started[1]}
    return write_artifact(output, model, model_id, version, training, diagnostics)


def main():
    parser = argparse.ArgumentParser(description='Fit a NEW original local question-conditioned decision model')
    parser.add_argument('--input', required=True, help='JSONL request,target_option_id,source_group; training only')
    parser.add_argument('--provenance', required=True, help='Caller declaration: source,license,citation,rights_basis')
    parser.add_argument('--output', required=True, help='NEW private artifact directory; no activation')
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--version', default='1')
    parser.add_argument('--epochs', type=int, default=DEFAULT_EPOCHS)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--learning-rate', type=float, default=DEFAULT_RATE)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        result = build(args.input, args.provenance, args.output, args.model_id, args.version,
                       epochs=args.epochs, seed=args.seed, learning_rate=args.learning_rate)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except TrainingError as error:
        print(json.dumps({'error': 'decision_training_refused', 'reason': error.code}), file=sys.stderr)
        return 2
    except (ArtifactError, DecisionContractError, ValueError, TypeError, OSError,
            UnicodeError, MemoryError, OverflowError, RecursionError):
        print(json.dumps({'error': 'decision_training_refused', 'reason': 'invalid_input_or_artifact'}), file=sys.stderr)
        return 2
    finally:
        os.umask(previous)


if __name__ == '__main__':
    raise SystemExit(main())
