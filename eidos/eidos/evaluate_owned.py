"""Prepare, predict and score caller-supplied owned-model evaluation artifacts.

Prediction needs no gold input; scoring needs no source text or model weights.
Use external namespaces/access controls for an operating-system boundary.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

from .contracts import ContractError, finite_json, parse_json, validate_request
from .owned_metrics import summarize

MAX_INPUT = 2 * 1024**2
MAX_CASES = 10_000
MAX_TEXT = 200_000
MAX_VALUES = 200_000
MAX_PREDICTIONS = 16 * 1024**2
MAX_WORK = 250_000_000
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z', re.ASCII)
SHA = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
POLICY = 'review_only_acceptance_disabled'


class EvaluationError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
            raise EvaluationError('invalid_regular_file_or_size')
        data = stream.read(maximum + 1)
        if len(data) != info.st_size:
            raise EvaluationError('file_changed_or_size_limit')
        return data


def parsed(data, maximum):
    value = parse_json(data.decode('utf-8'), maximum=maximum)
    finite_json(value, max_nodes=100_000, max_text=maximum)
    canonical(value)
    return value


def declaration(raw):
    value = parsed(raw, 16_000)
    if type(value) is not dict or set(value) != {'source', 'license', 'citation', 'rights_basis'}:
        raise EvaluationError('provenance_fields_required')
    if any(type(v) is not str or not 1 <= len(v) <= 1000 or not v.strip() for v in value.values()):
        raise EvaluationError('invalid_provenance')
    return value


def lines(raw, fields, maximum=MAX_INPUT):
    result = []
    identifiers = set()
    for line in raw.decode('utf-8').splitlines():
        value = parsed(line.encode('utf-8'), min(maximum, 64_000))
        if type(value) is not dict or set(value) != fields:
            raise EvaluationError('row_fields_required')
        if type(value['id']) is not str or not ID.fullmatch(value['id']):
            raise EvaluationError('invalid_case_id')
        if value['id'] in identifiers:
            raise EvaluationError('duplicate_case_id')
        identifiers.add(value['id'])
        result.append(value)
        if len(result) > MAX_CASES:
            raise EvaluationError('case_count_limit')
    if not result:
        raise EvaluationError('empty_cases')
    return result


def new_output(path):
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        raise EvaluationError('output_already_exists')
    if not path.parent.is_dir() or any(p.is_symlink() for p in (path.parent, *path.parent.parents)):
        raise EvaluationError('output_parent_must_be_existing_regular_directory')
    return path


def publish(output, files):
    output.mkdir(mode=0o700, exist_ok=False)
    for name, data in files.items():
        destination = output / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)


def budget(start):
    if time.process_time() - start[0] > 60 or time.monotonic() - start[1] > 90:
        raise EvaluationError('phase_time_budget_exceeded')


def code_bindings():
    root = Path(__file__).parent
    paths = sorted([*root.glob('*.py'), root / 'decision-api.schema.json'])
    if any(p.is_symlink() for p in paths):
        raise EvaluationError('symlink_code_refused')
    return {p.name: digest(read(p, MAX_INPUT)) for p in paths}


def registry(directory):
    from .engine import Engine
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise EvaluationError('invalid_registry_directory')
    names = {p.name for p in directory.iterdir()}
    expected = {'model.json', 'registry.json', 'TRAINING-RECEIPT.json'}
    if names not in (expected | {'weights.f32'}, expected | {'support.sparse'}):
        raise EvaluationError('owned_single_model_registry_required')
    files = {name: {'bytes': len(data), 'sha256': digest(data)}
             for name in sorted(names) for data in [read(directory / name, 16 * 1024**2)]}
    engine = Engine.from_registry(directory, tenants=('owned-evaluation',))
    if len(engine.models) != 1:
        raise EvaluationError('owned_single_model_registry_required')
    model = next(iter(engine.models.values()))
    if model.data['training'].get('format') not in ('eidos-caller-support-training-v1', 'eidos-caller-linear-training-v1'):
        raise EvaluationError('caller_trained_registry_required')
    if model.data['calibration'] is not None or not model.data['consequential'] or any(
            model.data['thresholds'][key] != 1 for key in ('probability', 'margin')):
        raise EvaluationError('review_only_uncalibrated_registry_required')
    if model.data['languages'] != ['en'] or any(value != 1 for value in model.data['temperatures'].values()):
        raise EvaluationError('english_unit_temperature_registry_required')
    receipt = parsed(read(directory / 'TRAINING-RECEIPT.json', 16_000), 16_000)
    receipt_format = ('eidos-owned-support-training-receipt-v1' if model.data['format'] == 'eidos-support-memory-v1'
                      else 'eidos-owned-linear-training-receipt-v1')
    if (type(receipt) is not dict or receipt.get('format') != receipt_format or receipt.get('complete') is not True or
            type(receipt.get('classes')) is not int or receipt['classes'] != len(model.data['labels']) or
            type(receipt.get('support_rows')) is not int or receipt['support_rows'] != 20 * len(model.data['labels']) or
            any(receipt.get(key) is not False for key in ('evaluation_or_calibration_performed', 'operational_acceptance_enabled',
                                                        'model_promoted', 'third_party_training_or_inference_packages'))):
        raise EvaluationError('completed_owned_training_receipt_required')
    if receipt.get('model') != model.identity or receipt.get('files') != {
            name: pin for name, pin in files.items() if name != 'TRAINING-RECEIPT.json'}:
        raise EvaluationError('training_receipt_mismatch')
    for key in ('input_sha256', 'provenance_sha256'):
        if receipt.get(key) != model.data['training'].get(key):
            raise EvaluationError('training_receipt_mismatch')
    if files != {name: {'bytes': len(data), 'sha256': digest(data)}
                 for name in sorted(names) for data in [read(directory / name, 16 * 1024**2)]}:
        raise EvaluationError('registry_changed_during_load')
    return engine, model, files


def request(model_id, row, labels):
    return {'schema_version': '1.0', 'input_version': 'owned-evaluation-v1',
            'request_id': row['id'], 'tenant_id': 'owned-evaluation', 'language': 'en',
            'state': {'utterance': row['text']}, 'sources': [], 'budget_ms': 5000,
            'questions': [{'id': 'intent', 'kind': 'CHOICE', 'profile': model_id,
                           'prompt': 'Classify this utterance', 'state_path': ['utterance'],
                           'choices': [{'id': label, 'class_id': label} for label in labels]}]}


def load_plan(path):
    raw = read(path, MAX_INPUT)
    plan = parsed(raw, MAX_INPUT)
    fields = {'format', 'code_files', 'model_files', 'model_identity', 'model_format', 'labels',
              'case_ids', 'input_sha256', 'n', 'prediction_work_units', 'policy', 'task',
              'support_sha256', 'support_provenance_sha256'}
    if type(plan) is not dict or set(plan) != fields or plan['format'] != 'eidos-owned-evaluation-plan-v1':
        raise EvaluationError('invalid_plan_schema')
    labels, ids = plan['labels'], plan['case_ids']
    if (type(labels) is not list or not 2 <= len(labels) <= 256 or
            any(type(x) is not str or not ID.fullmatch(x) for x in labels) or labels != sorted(set(labels))):
        raise EvaluationError('invalid_plan_labels')
    if (type(ids) is not list or not 1 <= len(ids) <= MAX_CASES or
            any(type(x) is not str or not ID.fullmatch(x) for x in ids) or ids != sorted(set(ids)) or
            type(plan['n']) is not int or plan['n'] != len(ids) or len(ids) * len(labels) > MAX_VALUES):
        raise EvaluationError('invalid_plan_case_inventory')
    if type(plan['prediction_work_units']) is not int or not 0 < plan['prediction_work_units'] <= MAX_WORK:
        raise EvaluationError('invalid_plan_work_bound')
    if plan['policy'] != POLICY or plan['task'] != 'all_trained_labels_english_choice':
        raise EvaluationError('invalid_plan_scope')
    for key in ('input_sha256', 'support_sha256', 'support_provenance_sha256'):
        if type(plan[key]) is not str or not SHA.fullmatch(plan[key]):
            raise EvaluationError('invalid_plan_hash')
    identity, files = plan['model_identity'], plan['model_files']
    identity_fields = {'id', 'version', 'sha256', 'feature_version', 'calibration_status'}
    if (type(identity) is not dict or set(identity) != identity_fields or
            any(type(identity[k]) is not str or not ID.fullmatch(identity[k]) for k in ('id', 'version')) or
            type(identity['sha256']) is not str or not SHA.fullmatch(identity['sha256']) or
            identity['feature_version'] != 'unicode-word-bigram-char345-tfidf-v2' or
            identity['calibration_status'] != 'unfitted'):
        raise EvaluationError('invalid_plan_model_identity')
    if plan['model_format'] not in ('eidos-support-memory-v1', 'eidos-linear-general-v1'):
        raise EvaluationError('invalid_plan_model_format')
    binary = 'support.sparse' if plan['model_format'] == 'eidos-support-memory-v1' else 'weights.f32'
    if type(files) is not dict or set(files) != {'model.json', 'registry.json', 'TRAINING-RECEIPT.json', binary}:
        raise EvaluationError('invalid_plan_model_files')
    for pin in files.values():
        if (type(pin) is not dict or set(pin) != {'bytes', 'sha256'} or type(pin['bytes']) is not int or
                not 0 < pin['bytes'] <= 16 * 1024**2 or type(pin['sha256']) is not str or not SHA.fullmatch(pin['sha256'])):
            raise EvaluationError('invalid_plan_model_file_pin')
    if files['model.json']['sha256'] != identity['sha256']:
        raise EvaluationError('invalid_plan_model_identity_binding')
    if plan['code_files'] != code_bindings():
        raise EvaluationError('evaluation_code_changed')
    return plan, digest(raw)


def prepare(registry_path, support_path, support_provenance_path, evaluation_path,
            evaluation_provenance_path, output_path):
    from .model import words
    from .train_support import parse_training
    start = (time.process_time(), time.monotonic())
    output = new_output(output_path)
    support_raw = read(support_path, MAX_INPUT)
    support_provenance_raw = read(support_provenance_path, 16_000)
    supports, _ = parse_training(support_raw, support_provenance_raw)
    _, model, files = registry(registry_path)
    if (model.data['training']['input_sha256'] != digest(support_raw) or
            model.data['training']['provenance_sha256'] != digest(support_provenance_raw)):
        raise EvaluationError('support_files_do_not_match_model_lineage')
    labels = model.data['labels']
    if sorted({row['label'] for row in supports}) != labels:
        raise EvaluationError('support_inventory_does_not_match_model')
    eval_raw = read(evaluation_path, MAX_INPUT)
    eval_provenance_raw = read(evaluation_provenance_path, 16_000)
    rights = declaration(eval_provenance_raw)
    cases = lines(eval_raw, {'id', 'text', 'label'})
    support_ids = {row['id'] for row in supports}
    support_text = {' '.join(words(row['text'])) for row in supports}
    seen_text = set()
    total = 0
    for row in cases:
        if type(row['label']) is not str or row['label'] not in labels:
            raise EvaluationError('unknown_gold_label')
        if type(row['text']) is not str or not 1 <= len(row['text']) <= 12_000:
            raise EvaluationError('invalid_evaluation_text')
        normalized = ' '.join(words(row['text']))
        if not normalized:
            raise EvaluationError('empty_word_sequence')
        if row['id'] in support_ids or normalized in support_text:
            raise EvaluationError('support_evaluation_id_or_normalized_text_overlap')
        if normalized in seen_text:
            raise EvaluationError('duplicate_normalized_evaluation_text')
        seen_text.add(normalized)
        total += len(row['text'])
        if total > MAX_TEXT:
            raise EvaluationError('aggregate_evaluation_text_limit')
        budget(start)
    cases.sort(key=lambda row: row['id'])
    if len(cases) * len(labels) > MAX_VALUES:
        raise EvaluationError('prediction_value_count_limit')
    work = len(cases) * (model.data.get('nnz', 0) + len(labels) * len(model.data['vocabulary']))
    if work > MAX_WORK:
        raise EvaluationError('prediction_work_budget_exceeded')
    longest = {'id': 'x' * max(len(row['id']) for row in cases),
               'text': max((row['text'] for row in cases), key=len)}
    validate_request(request(model.data['id'], longest, labels))
    inputs = b''.join(canonical({'id': row['id'], 'text': row['text']}) + b'\n' for row in cases)
    gold = b''.join(canonical({'id': row['id'], 'label': row['label']}) + b'\n' for row in cases)
    plan = {'format': 'eidos-owned-evaluation-plan-v1', 'code_files': code_bindings(),
            'model_files': files, 'model_identity': model.identity, 'model_format': model.data['format'],
            'labels': labels, 'case_ids': [row['id'] for row in cases], 'input_sha256': digest(inputs),
            'n': len(cases), 'prediction_work_units': work, 'policy': POLICY,
            'task': 'all_trained_labels_english_choice', 'support_sha256': digest(support_raw),
            'support_provenance_sha256': digest(support_provenance_raw)}
    plan_raw = canonical(plan) + b'\n'
    binding = {'format': 'eidos-owned-gold-binding-v1', 'plan_sha256': digest(plan_raw),
               'gold_sha256': digest(gold), 'n': len(cases), 'evaluation_input_sha256': digest(eval_raw),
               'evaluation_provenance_sha256': digest(eval_provenance_raw),
               'evaluation_declaration': rights, 'rights_independently_verified': False,
               'exact_id_and_normalized_text_overlap_refused': True,
               'source_speaker_paraphrase_or_fresh_holdout_independence_verified': False}
    budget(start)
    publish(output, {'prediction-input/plan.json': plan_raw, 'prediction-input/inputs.jsonl': inputs,
                     'gold-private/gold.jsonl': gold, 'gold-private/binding.json': canonical(binding) + b'\n'})
    return {'complete': True, 'stage': 'prepare', 'n': len(cases), 'plan_sha256': digest(plan_raw),
            'inference_training_or_scoring_performed': False, 'output_contains_private_evaluation_cases_and_gold': True}


def predict(registry_path, input_path, output_path):
    start = (time.process_time(), time.monotonic())
    output = new_output(output_path)
    input_path = Path(input_path)
    plan, plan_sha = load_plan(input_path / 'plan.json')
    inputs = read(input_path / 'inputs.jsonl', MAX_INPUT)
    if digest(inputs) != plan['input_sha256']:
        raise EvaluationError('prediction_input_hash_mismatch')
    cases = lines(inputs, {'id', 'text'})
    if [row['id'] for row in cases] != plan['case_ids']:
        raise EvaluationError('prediction_case_inventory_mismatch')
    engine, model, files = registry(registry_path)
    if files != plan['model_files'] or model.identity != plan['model_identity'] or model.data['labels'] != plan['labels']:
        raise EvaluationError('planned_model_mismatch')
    predictions = []
    for row in cases:
        decision = engine.decide(request(model.data['id'], row, plan['labels']))['decisions'][0]
        if (decision['status'] != 'abstained' or decision['value'] is not None or
                not decision['review_required'] or decision['calibrated_confidence'] is not None or
                [item['class_id'] for item in decision['distribution']] != plan['labels']):
            raise EvaluationError('complete_review_only_distribution_required')
        predictions.append({'id': row['id'], 'predicted': decision['candidate_value'],
                            'logits': [v['logit'] for v in decision['distribution']],
                            'probabilities': [v['probability'] for v in decision['distribution']],
                            'status': decision['status'], 'review_required': decision['review_required']})
        budget(start)
    raw = b''.join(canonical(row) + b'\n' for row in predictions)
    if len(raw) > MAX_PREDICTIONS:
        raise EvaluationError('prediction_output_size_limit')
    if registry(registry_path)[2] != files:
        raise EvaluationError('model_changed_during_prediction')
    if load_plan(input_path / 'plan.json')[1] != plan_sha or read(input_path / 'inputs.jsonl', MAX_INPUT) != inputs:
        raise EvaluationError('inputs_changed_during_prediction')
    receipt = {'format': 'eidos-owned-prediction-receipt-v1', 'complete': True,
               'plan_sha256': plan_sha, 'predictions_sha256': digest(raw), 'n': len(cases),
               'model_files': files, 'input_sha256': plan['input_sha256'],
               'gold_input_read': False, 'training_calibration_or_scoring_performed': False}
    budget(start)
    publish(output, {'predictions.jsonl': raw, 'receipt.json': canonical(receipt) + b'\n'})
    return receipt


def score(plan_path, gold_path, prediction_path, output_path):
    start = (time.process_time(), time.monotonic())
    output = new_output(output_path)
    plan, plan_sha = load_plan(plan_path)
    gold_path, prediction_path = Path(gold_path), Path(prediction_path)
    binding_raw = read(gold_path / 'binding.json', MAX_INPUT)
    binding = parsed(binding_raw, MAX_INPUT)
    expected = {'format', 'plan_sha256', 'gold_sha256', 'n', 'evaluation_input_sha256',
                'evaluation_provenance_sha256', 'evaluation_declaration', 'rights_independently_verified',
                'exact_id_and_normalized_text_overlap_refused',
                'source_speaker_paraphrase_or_fresh_holdout_independence_verified'}
    if (type(binding) is not dict or set(binding) != expected or binding['format'] != 'eidos-owned-gold-binding-v1' or
            binding['plan_sha256'] != plan_sha or type(binding['n']) is not int or binding['n'] != plan['n'] or
            binding['rights_independently_verified'] is not False or
            binding['exact_id_and_normalized_text_overlap_refused'] is not True or
            binding['source_speaker_paraphrase_or_fresh_holdout_independence_verified'] is not False):
        raise EvaluationError('gold_binding_mismatch')
    declaration(canonical(binding['evaluation_declaration']))
    for key in ('gold_sha256', 'evaluation_input_sha256', 'evaluation_provenance_sha256'):
        if type(binding[key]) is not str or not SHA.fullmatch(binding[key]):
            raise EvaluationError('invalid_gold_binding_hash')
    gold_raw = read(gold_path / 'gold.jsonl', MAX_INPUT)
    pred_raw = read(prediction_path / 'predictions.jsonl', MAX_PREDICTIONS)
    receipt = parsed(read(prediction_path / 'receipt.json', MAX_INPUT), MAX_INPUT)
    receipt_fields = {'format', 'complete', 'plan_sha256', 'predictions_sha256', 'n', 'model_files',
                      'input_sha256', 'gold_input_read', 'training_calibration_or_scoring_performed'}
    if (type(receipt) is not dict or set(receipt) != receipt_fields or
            receipt.get('format') != 'eidos-owned-prediction-receipt-v1' or receipt.get('complete') is not True or
            receipt.get('plan_sha256') != plan_sha or type(receipt.get('n')) is not int or receipt.get('n') != plan['n'] or
            receipt.get('model_files') != plan['model_files'] or receipt.get('input_sha256') != plan['input_sha256'] or
            receipt.get('gold_input_read') is not False or receipt.get('training_calibration_or_scoring_performed') is not False):
        raise EvaluationError('prediction_receipt_mismatch')
    if binding['gold_sha256'] != digest(gold_raw) or receipt.get('predictions_sha256') != digest(pred_raw):
        raise EvaluationError('prediction_or_gold_hash_mismatch')
    gold = lines(gold_raw, {'id', 'label'})
    predictions = lines(pred_raw, {'id', 'predicted', 'logits', 'probabilities', 'status', 'review_required'}, MAX_PREDICTIONS)
    if [row['id'] for row in gold] != plan['case_ids'] or [row['id'] for row in predictions] != plan['case_ids']:
        raise EvaluationError('complete_ordered_case_inventory_required')
    labels = plan['labels']
    rows = []
    for truth, prediction in zip(gold, predictions):
        if truth['label'] not in labels or prediction['predicted'] not in labels:
            raise EvaluationError('unknown_scored_label')
        logits, probabilities = prediction['logits'], prediction['probabilities']
        if (type(logits) is not list or type(probabilities) is not list or
                len(logits) != len(labels) or len(probabilities) != len(labels) or
                any(type(z) not in (int, float) or not math.isfinite(z) or abs(z) > 1e9 for z in logits) or
                any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities)):
            raise EvaluationError('invalid_numeric_distribution')
        maximum = max(logits)
        values = [math.exp(z - maximum) for z in logits]
        total = math.fsum(values)
        if (not math.isclose(math.fsum(probabilities), 1.0, abs_tol=1e-12, rel_tol=0) or
                any(abs(p - value / total) > 1e-12 for p, value in zip(probabilities, values)) or
                prediction['predicted'] != labels[max(range(len(labels)), key=lambda i: probabilities[i])] or
                prediction['status'] != 'abstained' or prediction['review_required'] is not True):
            raise EvaluationError('inconsistent_prediction_or_policy')
        rows.append({**prediction, 'label': truth['label']})
        budget(start)
    metrics = summarize(rows, labels)
    result = {'format': 'eidos-owned-evaluation-result-v1', 'complete': True, 'metrics': metrics,
              'plan_sha256': plan_sha, 'model_identity': plan['model_identity'],
              'predictions_sha256': digest(pred_raw), 'gold_sha256': digest(gold_raw),
              'gold_binding_sha256': digest(binding_raw),
              'source_speaker_paraphrase_or_fresh_holdout_independence_verified': False,
              'model_weights_or_source_text_read_for_scoring': False,
              'fit_calibration_promotion_or_rival_comparison_performed': False,
              'per_case_rows_written': False, 'evaluation_cases_now_revealed': True,
              'rights_independently_verified': False, 'production_certified': False}
    budget(start)
    publish(output, {'result.json': canonical(result) + b'\n'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='stage', required=True)
    prepare_args = commands.add_parser('prepare', help='Refuse exact support overlap; separate private input and gold files')
    for name in ['registry', 'support', 'support-provenance', 'evaluation', 'evaluation-provenance', 'output']:
        prepare_args.add_argument('--' + name, required=True)
    predict_args = commands.add_parser('predict', help='Read only planned case text and the bound owned registry')
    for name in ['registry', 'inputs', 'output']:
        predict_args.add_argument('--' + name, required=True)
    score_args = commands.add_parser('score', help='Require complete aligned predictions/gold; write aggregate metrics only')
    for name in ['plan', 'gold', 'predictions', 'output']:
        score_args.add_argument('--' + name, required=True)
    args = parser.parse_args()
    mask = os.umask(0o077)
    try:
        if args.stage == 'prepare':
            result = prepare(args.registry, args.support, args.support_provenance, args.evaluation,
                             args.evaluation_provenance, args.output)
        elif args.stage == 'predict':
            result = predict(args.registry, args.inputs, args.output)
        else:
            result = score(args.plan, args.gold, args.predictions, args.output)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except EvaluationError as error:
        print(json.dumps({'error': 'owned_evaluation_refused', 'reason': str(error)}), file=sys.stderr)
        return 2
    except (ContractError, ValueError, TypeError, KeyError, AttributeError, OSError, UnicodeError, MemoryError, OverflowError):
        print(json.dumps({'error': 'owned_evaluation_refused', 'reason': 'invalid_input_or_artifact'}), file=sys.stderr)
        return 2
    finally:
        os.umask(mask)


if __name__ == '__main__':
    raise SystemExit(main())
