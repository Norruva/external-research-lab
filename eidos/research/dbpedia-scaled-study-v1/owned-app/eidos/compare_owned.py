"""Plan a paired owned-model comparison, then score complete frozen predictions.

Planning reads no gold/predictions. Scoring reads no case text/model weights.
Portable file roles require external process isolation for a hard boundary.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

from .contracts import ContractError
from .evaluate_owned import (EvaluationError, ID, MAX_INPUT, SHA, budget, canonical,
                             code_bindings, completed_rows, declaration, digest,
                             lines, load_plan, new_output, parsed, publish, read)
from .paired_metrics import bootstrap

REPLICATES = 1000
SEED = 113
MAX_WORK = 20_000_000
METRICS = ['candidate_accuracy', 'macro_f1_fixed_offered_labels']
RULE = 'both_individual_approximate_95_percent_lower_bounds_strictly_positive'
FILES = {'comparison.json', 'left-plan.json', 'right-plan.json', 'groups.jsonl', 'group-provenance.json'}


def settings(replicates, seed, n, classes, group_count):
    if type(replicates) is not int or not 200 <= replicates <= 2000:
        raise EvaluationError('replicates_outside_200_to_2000')
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise EvaluationError('seed_outside_uint32')
    work = (replicates + 1) * (8 * n + 6 * classes + 3 * group_count)
    if work > MAX_WORK:
        raise EvaluationError('paired_comparison_work_limit')
    return work


def aligned(left, right):
    # Enforce identical offered labels/cases/utterance bytes and labeled supports.
    for key in ('labels', 'case_ids', 'input_sha256', 'support_sha256', 'support_provenance_sha256',
                'code_files', 'task', 'policy'):
        if left[key] != right[key]:
            raise EvaluationError('comparison_evaluation_or_support_scope_mismatch')
    if left['model_files'] == right['model_files']:
        raise EvaluationError('distinct_frozen_models_required')


def group_rows(raw, identifiers, *, ordered=False):
    rows = lines(raw, {'id', 'group_id'})
    if any(type(row['group_id']) is not str or not ID.fullmatch(row['group_id']) for row in rows):
        raise EvaluationError('invalid_group_id')
    rows_sorted = sorted(rows, key=lambda row: row['id'])
    if [row['id'] for row in rows_sorted] != identifiers or (ordered and rows != rows_sorted):
        raise EvaluationError('complete_group_case_inventory_required')
    if len({row['group_id'] for row in rows}) < 2:
        raise EvaluationError('at_least_two_groups_required')
    return rows_sorted


def plan(left_path, right_path, groups_path, provenance_path, output_path, *,
         replicates=REPLICATES, seed=SEED):
    start = (time.process_time(), time.monotonic())
    output = new_output(output_path)
    left, left_sha = load_plan(left_path)
    right, right_sha = load_plan(right_path)
    aligned(left, right)
    raw = read(groups_path, MAX_INPUT)
    rows = group_rows(raw, left['case_ids'])
    provenance_raw = read(provenance_path, 16_000)
    declaration(provenance_raw)
    group_count = len({row['group_id'] for row in rows})
    work = settings(replicates, seed, left['n'], len(left['labels']), group_count)
    groups = b''.join(canonical(row) + b'\n' for row in rows)
    left_raw, right_raw = read(left_path, MAX_INPUT), read(right_path, MAX_INPUT)
    if digest(left_raw) != left_sha or digest(right_raw) != right_sha:
        raise EvaluationError('evaluation_plans_changed_during_comparison_planning')
    protocol = {'format': 'eidos-owned-paired-comparison-plan-v1',
                'code_files': code_bindings(), 'left_plan_sha256': left_sha, 'right_plan_sha256': right_sha,
                'groups_sha256': digest(groups), 'original_groups_input_sha256': digest(raw),
                'group_provenance_sha256': digest(provenance_raw),
                'n': left['n'], 'groups': group_count, 'replicates': replicates, 'seed': seed,
                'work_units': work, 'metrics': METRICS, 'difference': 'left_minus_right',
                'method': 'paired_whole_group_percentile_bootstrap', 'numeric_gate': RULE,
                'nominal_individual_interval_level': .95,
                'gold_or_predictions_read_for_planning': False,
                'planning_before_outcomes_independently_verified': False,
                'group_independence_or_exchangeability_verified': False}
    budget(start)
    publish(output, {'comparison.json': canonical(protocol) + b'\n', 'left-plan.json': left_raw,
                     'right-plan.json': right_raw, 'groups.jsonl': groups,
                     'group-provenance.json': provenance_raw})
    return {'complete': True, 'stage': 'plan', 'n': left['n'], 'groups': group_count,
            'comparison_sha256': digest(canonical(protocol) + b'\n'),
            'gold_or_predictions_read': False, 'inference_fit_scoring_or_promotion_performed': False}


def load_comparison(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or {p.name for p in directory.iterdir()} != FILES:
        raise EvaluationError('complete_comparison_bundle_required')
    raw = read(directory / 'comparison.json', MAX_INPUT)
    value = parsed(raw, MAX_INPUT)
    fields = {'format', 'code_files', 'left_plan_sha256', 'right_plan_sha256', 'groups_sha256',
              'original_groups_input_sha256', 'group_provenance_sha256', 'n', 'groups',
              'replicates', 'seed', 'work_units', 'metrics', 'difference', 'method', 'numeric_gate',
              'nominal_individual_interval_level', 'gold_or_predictions_read_for_planning',
              'planning_before_outcomes_independently_verified', 'group_independence_or_exchangeability_verified'}
    if type(value) is not dict or set(value) != fields or value['format'] != 'eidos-owned-paired-comparison-plan-v1':
        raise EvaluationError('invalid_comparison_plan')
    for key in ('left_plan_sha256', 'right_plan_sha256', 'groups_sha256', 'original_groups_input_sha256',
                'group_provenance_sha256'):
        if type(value[key]) is not str or not SHA.fullmatch(value[key]):
            raise EvaluationError('invalid_comparison_hash')
    if (value['code_files'] != code_bindings() or value['metrics'] != METRICS or
            value['difference'] != 'left_minus_right' or
            value['method'] != 'paired_whole_group_percentile_bootstrap' or value['numeric_gate'] != RULE or
            type(value['nominal_individual_interval_level']) is not float or value['nominal_individual_interval_level'] != .95 or
            any(value[key] is not False for key in ('gold_or_predictions_read_for_planning',
                'planning_before_outcomes_independently_verified', 'group_independence_or_exchangeability_verified'))):
        raise EvaluationError('comparison_code_or_protocol_mismatch')
    left, left_sha = load_plan(directory / 'left-plan.json')
    right, right_sha = load_plan(directory / 'right-plan.json')
    aligned(left, right)
    if left_sha != value['left_plan_sha256'] or right_sha != value['right_plan_sha256']:
        raise EvaluationError('comparison_evaluation_plan_hash_mismatch')
    raw_groups = read(directory / 'groups.jsonl', MAX_INPUT)
    provenance_raw = read(directory / 'group-provenance.json', 16_000)
    if digest(raw_groups) != value['groups_sha256'] or digest(provenance_raw) != value['group_provenance_sha256']:
        raise EvaluationError('comparison_group_hash_mismatch')
    declaration(provenance_raw)
    rows = group_rows(raw_groups, left['case_ids'], ordered=True)
    groups = [row['group_id'] for row in rows]
    if (type(value['n']) is not int or value['n'] != left['n'] or type(value['groups']) is not int or
            value['groups'] != len(set(groups)) or type(value['work_units']) is not int or
            value['work_units'] != settings(value['replicates'], value['seed'], left['n'], len(left['labels']), len(set(groups)))):
        raise EvaluationError('comparison_counts_or_work_mismatch')
    return value, digest(raw), groups


def score(comparison_path, left_gold, right_gold, left_predictions, right_predictions, output_path):
    start = (time.process_time(), time.monotonic())
    output = new_output(output_path)
    protocol, protocol_sha, groups = load_comparison(comparison_path)
    path = Path(comparison_path)
    left_plan, left, left_ids, left_binding = completed_rows(path / 'left-plan.json', left_gold, left_predictions, start)
    right_plan, right, right_ids, right_binding = completed_rows(path / 'right-plan.json', right_gold, right_predictions, start)
    aligned(left_plan, right_plan)
    if (left_ids['plan_sha256'] != protocol['left_plan_sha256'] or right_ids['plan_sha256'] != protocol['right_plan_sha256'] or
            left_ids['gold_sha256'] != right_ids['gold_sha256'] or any(
                left_binding[key] != right_binding[key] for key in (
                    'evaluation_input_sha256', 'evaluation_provenance_sha256', 'evaluation_declaration'))):
        raise EvaluationError('paired_gold_or_source_mismatch')
    result = bootstrap(left, right, groups, left_plan['labels'], protocol['replicates'], protocol['seed'], lambda: budget(start))
    if load_comparison(comparison_path)[1:] != (protocol_sha, groups):
        raise EvaluationError('comparison_changed_during_scoring')
    result.update({'format': 'eidos-owned-paired-comparison-result-v1', 'complete': True, 'n': left_plan['n'],
        'comparison_sha256': protocol_sha, 'left_evidence': left_ids, 'right_evidence': right_ids,
        'replicates': protocol['replicates'], 'seed': protocol['seed'], 'difference': 'left_minus_right',
        'method': protocol['method'], 'numeric_gate_rule': RULE,
        'nominal_individual_interval_level': .95, 'simultaneous_or_global_coverage_guarantee': False,
        'group_independence_or_exchangeability_verified': False, 'planning_before_outcomes_independently_verified': False,
        'same_case_candidate_support_and_code_scope_required': True,
        'all_offered_labels_count_in_macro_even_if_absent_in_a_draw': True,
        'bootstrap_case_count_may_vary_with_group_sizes': True,
        'model_weights_or_source_case_text_read': False, 'per_case_or_per_group_rows_written': False,
        'evaluation_outcomes_now_revealed': True, 'inference_fit_calibration_or_promotion_performed': False,
        'accepted_coverage': 0., 'action_authorized': False,
        'rival_or_general_superiority_certified': False, 'production_certified': False})
    budget(start)
    publish(output, {'result.json': canonical(result) + b'\n'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='stage', required=True)
    plan_args = commands.add_parser('plan', help='Freeze paired scope/groups/statistics without gold or predictions')
    for name in ('left-plan', 'right-plan', 'groups', 'group-provenance', 'output'):
        plan_args.add_argument('--' + name, required=True)
    plan_args.add_argument('--replicates', type=int, default=REPLICATES)
    plan_args.add_argument('--seed', type=int, default=SEED)
    score_args = commands.add_parser('score', help='Verify both complete streams and compute aggregate paired differences')
    for name in ('comparison', 'left-gold', 'right-gold', 'left-predictions', 'right-predictions', 'output'):
        score_args.add_argument('--' + name, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        if args.stage == 'plan':
            result = plan(args.left_plan, args.right_plan, args.groups, args.group_provenance, args.output,
                          replicates=args.replicates, seed=args.seed)
        else:
            result = score(args.comparison, args.left_gold, args.right_gold,
                           args.left_predictions, args.right_predictions, args.output)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except EvaluationError as error:
        print(json.dumps({'error': 'owned_comparison_refused', 'reason': str(error)}), file=sys.stderr)
        return 2
    except (ContractError, ValueError, TypeError, KeyError, AttributeError, OSError, UnicodeError, MemoryError, OverflowError):
        print(json.dumps({'error': 'owned_comparison_refused', 'reason': 'invalid_input_or_artifact'}), file=sys.stderr)
        return 2
    finally:
        os.umask(previous)


if __name__ == '__main__':
    raise SystemExit(main())
