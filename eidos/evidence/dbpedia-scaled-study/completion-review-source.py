"""Independently inspect terminal scaled-study receipts; never fit or rescore.

Invoke only after the parent launcher has ended. This program imports standard
library modules only. It never opens corpus/history/case/gold files, parses a
prediction row, loads a model, reads development metrics, or repeats statistics.
Completed prediction streams and model artifacts are hashed as opaque bytes.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys

ROOT = Path('/workspace/eidos')
STUDY = ROOT / 'research/dbpedia-scaled-study-v1'
COHORT_SOURCE = ROOT / 'research/dbpedia-scaled-cohort-v1'
OUT = ROOT / 'evaluation/production/dbpedia-scaled-study-v1/v1'
REVIEW = OUT.parent / 'completion-review-v1.json'
SCOPE = 'eidos-dbpedia-scaled-study-v1'
PLAN_SHA = '880284f8fc3aaadd3b07933a724abaa86b3c71392c738202d447d7365548302c'
PREPARED_SHA = '6405fa4f0e7ae7d43df931d079e8da365bbf54a6fddd7e0cf039d244a2a496e6'
STAGES = ('history', 'corpus', 'train', 'development', 'eidos', 'laya', 'score')
LIMITS = {'history': [90, 120, 768], 'corpus': [600, 900, 2048],
          'train': [600, 900, 2048], 'development': [120, 180, 768],
          'eidos': [120, 180, 768], 'laya': [4800, 7200, 4096],
          'score': [180, 300, 768]}
COHORT_FILES = {'training.jsonl', 'development.jsonl', 'test-inputs.jsonl',
                'test-gold.jsonl', 'test-groups.jsonl', 'labels.json', 'provenance.json'}
MODEL_FILES = {'model.json', 'weights.f32', 'registry.json', 'TRAINING-RECEIPT.json'}
METRICS = ('candidate_accuracy', 'macro_f1_fixed_offered_labels')
REFERENCE = {'source_commit': 'ca5045a3f1fac06fc6daa5cf3371110f322f5255',
             'model_revision': '1c5edc17a7acd8701df6fc341c0d179f1c62c982',
             'weight_sha256': '891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c'}


class AuditError(ValueError):
    pass


def need(value, reason):
    if not value:
        raise AuditError(reason)


def regular(path):
    path = Path(path)
    need(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink_refused')
    info = path.stat()
    need(stat.S_ISREG(info.st_mode), 'regular_file_required')
    return path, info


def read(path, maximum=4 * 1024**2):
    path, before = regular(path)
    need(0 < before.st_size <= maximum, 'metadata_size_bound')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    need(len(raw) == before.st_size and
         (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
         (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
         'metadata_changed_during_read')
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate_metadata_key')
            result[key] = value
        return result
    def invalid(_value):
        raise AuditError('nonfinite_metadata')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    json.dumps(value, allow_nan=False)  # Also reject exponent overflow.
    return value


def opaque(path, *, lines=False, maximum=32 * 1024**2):
    """Hash bytes, optionally count delimiters; never decode or parse rows."""
    path, before = regular(path)
    need(0 <= before.st_size <= maximum, 'opaque_file_size_bound')
    h, count, size = hashlib.sha256(), 0, 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            h.update(block)
            size += len(block)
            if lines:
                count += block.count(b'\n')
        after = os.fstat(stream.fileno())
    need(size == before.st_size and
         (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
         (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
         'opaque_file_changed_during_read')
    result = {'bytes': size, 'sha256': h.hexdigest()}
    if lines:
        result['newline_delimiters'] = count
    return result


def sha(path):
    return opaque(path)['sha256']


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def number(value, low=0, high=math.inf):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def same_number(left, right):
    return number(left, -math.inf) and number(right, -math.inf) and math.isclose(
        left, right, rel_tol=0, abs_tol=1e-12)


def common():
    need(sha(STUDY / 'plan.json') == PLAN_SHA, 'plan_sha_mismatch')
    need(sha(STUDY / 'prepared-manifest-v1.json') == PREPARED_SHA, 'prepared_sha_mismatch')
    plan = read(STUDY / 'plan.json')
    prepared = read(STUDY / 'prepared-manifest-v1.json')
    need(plan['scope'] == prepared['scope'] == SCOPE, 'scope_mismatch')
    need(plan['stage_limits'] == LIMITS, 'stage_limits_mismatch')
    need(plan['automatic_retry'] is plan['acquisition'] is plan['new_installation'] is False,
         'forbidden_retry_acquisition_install')
    for name, pin in prepared['files'].items():
        need(opaque(name) == pin, 'reviewed_source_pin_mismatch')
    # Recheck only public manifests/rights metadata here, never their underlying
    # corpus/history/weights. Complete native trees were checked by each guard.
    metadata = {prepared['rights_file'],
                str(ROOT / 'evaluation/production/dbpedia-reader-v1/v1/reader-manifest.json')}
    metadata.update(tree['manifest'] for tree in prepared['trees'].values())
    for name in metadata:
        need(opaque(name) == prepared['external_files'][name], 'external_metadata_pin_mismatch')
    for source, staged in ((COHORT_SOURCE, OUT / 'cohort-controls'),
                           (STUDY, OUT / 'study-controls')):
        if not staged.exists():
            continue
        expected = {Path(n).name: p for n, p in prepared['files'].items()
                    if Path(n).parent == source and Path(n).suffix == '.py'}
        need({p.name for p in staged.iterdir()} == set(expected), 'staged_source_inventory')
        for name, pin in expected.items():
            need(opaque(staged / name) == pin, 'staged_source_pin_mismatch')
            need((staged / name).stat().st_mode & 0o777 == 0o400, 'staged_source_mode')
    auth = read(STUDY / 'authorization-v1.json')
    prelaunch = read(OUT / 'prelaunch.json')
    need(auth['approved'] is True and auth['scope'] == SCOPE, 'approval_required')
    need(auth['plan_sha256'] == prelaunch['plan_sha256'] == PLAN_SHA, 'approval_plan_binding')
    need(auth['prepared_manifest_sha256'] == prelaunch['prepared_manifest_sha256'] ==
         PREPARED_SHA, 'approval_prepared_binding')
    need(prelaunch['authorization_sha256'] == sha(STUDY / 'authorization-v1.json'),
         'prelaunch_approval_binding')
    need(prelaunch['planned_cpu_seconds'] == 7200 and prelaunch['whole_wall_seconds'] == 10800,
         'prelaunch_resource_envelope')
    need(prelaunch['automatic_retry'] is prelaunch['aggregate_cgroup_guarantee'] is False,
         'prelaunch_control_claim')
    provenance = read(STUDY / 'approval-provenance-v1.json')
    need(provenance['format'] == 'eidos-dbpedia-scaled-study-approval-provenance-v1' and
         provenance['approved'] is True and provenance['scope'] == SCOPE and
         provenance['plan_sha256'] == PLAN_SHA and provenance['prepared_manifest_sha256'] == PREPARED_SHA,
         'approval_provenance_scope_binding')
    need(provenance['raw_user_reply'] == 'Continue' and
         hashlib.sha256(provenance['raw_user_reply'].encode()).hexdigest() ==
         provenance['raw_user_reply_sha256'] == auth['user_instruction_sha256'] and
         provenance['approved_at_utc'] == auth['approved_at_utc'], 'approval_reply_binding')
    need(provenance['planned_cpu_seconds'] == 7200 and provenance['whole_wall_seconds'] == 10800 and
         provenance['native_threads'] == 1 and provenance['reference_memory_mib'] == 4096 and
         provenance['one_attempt'] is provenance['per_process_caps_not_aggregate_cgroup'] is True and
         provenance['new_installation_download_GPU_paid_compute'] is
         provenance['retry_fallback_recovery_resume_partial_score_alternate_fit_or_old_budget_reuse'] is False,
         'approval_provenance_resource_envelope')
    return plan, prepared, auth


def role(name, prepared):
    result = read(OUT / name / 'execution-summary.json')
    need(result['stage'] == name, 'stage_name_mismatch')
    need([result[k] for k in ('cpu_seconds_limit', 'wall_seconds_limit', 'memory_mib')] ==
         LIMITS[name], 'stage_resource_cap_mismatch')
    need(result['native_reference_mounted'] is (name == 'laya'), 'native_mount_role_mismatch')
    need(result['aggregate_cgroup_guarantee'] is False, 'aggregate_control_overclaim')
    freeze = read(OUT / name / 'freeze.json')
    need(freeze['cpu_seconds'] == LIMITS[name][0] and freeze['memory_mib'] == LIMITS[name][2],
         'namespace_resource_freeze_mismatch')
    source = COHORT_SOURCE if name in ('history', 'corpus') else STUDY
    expected_controls = {Path(p).name: v['sha256'] for p, v in prepared['files'].items()
                         if Path(p).parent == source and Path(p).suffix == '.py'}
    need(freeze['control_names'] == sorted(expected_controls), 'control_inventory_mismatch')
    for filename, pin in expected_controls.items():
        need(freeze['files'].get('/controls/' + filename) == pin, 'frozen_control_pin_mismatch')
    if name in ('history', 'corpus'):
        trees = {}
    elif name == 'laya':
        trees = {k: v for k, v in prepared['trees'].items() if k != '/owned-app'}
    else:
        trees = {'/owned-app': prepared['trees']['/owned-app']}
    need(freeze['trees'] == trees, 'role_tree_inventory_mismatch')
    complete = result.get('complete') is True
    if complete:
        need(result['exit_code'] == 0 and result['expected_refused_preflights'] == 1 and
             result['unexpected_ip_connects'] == result['unexpected_ip_socket_creations'] == 0,
             'role_completion_network_contract')
    return result, freeze


def expected_files(freeze, values, config_sha):
    controls = {name: pin for name, pin in freeze['files'].items() if name.startswith('/controls/')}
    need(freeze['files'] == {**controls, '/config.json': config_sha, **values},
         'role_input_boundary_or_hash_mismatch')


def history_check(prepared, freeze):
    history = read(OUT / 'history/data/result.json')
    need(history['complete'] is True and history['source_file_count'] == 40 and
         history['prior_exposure_rows'] == 840, 'history_inventory')
    need(history['prior_exposure_file_counts'] ==
         {'support.jsonl': 280, 'development.jsonl': 280, 'test-inputs.jsonl': 280},
         'prior_exposure_counts')
    need(history['prior_training_or_development_label_values_accessed'] is False and
         history['quality_evaluation_answer_key_or_prediction_read'] is False and
         history['comprehensive_historical_or_pretraining_independence'] is False,
         'history_scope_flags')
    sources = read(STUDY / 'historical-source-manifest.json')
    exposure = read(STUDY / 'exposure-source-manifest.json')
    files = {'/history-manifest.json': sha(STUDY / 'historical-source-manifest.json'),
             '/exposure-source-manifest.json': sha(STUDY / 'exposure-source-manifest.json')}
    files.update({'/history/' + n: p['sha256'] for n, p in sources['files'].items()})
    files.update({'/exposure-sources/' + n: p['sha256'] for n, p in exposure['files'].items()})
    expected_files(freeze, files, sha(STUDY / 'config-template.json'))
    need(history['manifest_sha256'] == files['/history-manifest.json'] and
         history['prior_exposure_source_manifest_sha256'] == files['/exposure-source-manifest.json'],
         'history_manifest_binding')
    return history


def corpus_check(plan, history, freeze):
    result = read(OUT / 'corpus/data/result.json')
    need(result['complete'] is True and result['plan_sha256'] == PLAN_SHA and
         result['history_result_sha256'] == sha(OUT / 'history/data/result.json'), 'cohort_receipt_binding')
    need(result['role_rows'] == {'training': 14000, 'development': 1400, 'test': 1400} and
         result['quotas'] == {'training': 1000, 'development': 100, 'test': 100}, 'cohort_role_counts')
    lineage = result['lineage']
    for key, value in {'selected_rows': 16800, 'components': 16800, 'max_component_rows': 1,
                       'cross_role_components': 0, 'historical_exact_overlaps': 0,
                       'resolved_previous_exposure_cases': 840}.items():
        need(type(lineage.get(key)) is int and lineage[key] == value, 'cohort_lineage')
    need(result['source_wide_eligibility_exclusions_precede_hash_ranking'] is True and
         result['target_population_restricted_to_unique_unexposed_articles'] is True and
         result['selected_row_removal_replacement_truncation_or_resampling'] is False and
         result['model_fit_prediction_or_scoring'] is False and result['native_reader_only'] is True,
         'cohort_policy_flags')
    need(result['salt'] == plan['selection']['salt'], 'cohort_salt')
    need(set(result['files']) == COHORT_FILES, 'cohort_file_inventory')
    need({p.name for p in (OUT / 'corpus/data').iterdir()} == COHORT_FILES | {'result.json'},
         'cohort_actual_file_inventory')
    for name, pin in result['files'].items():
        path, info = regular(OUT / 'corpus/data' / name)
        need(info.st_size == pin['bytes'] and info.st_mode & 0o777 == 0o400, 'cohort_file_size_mode')
    labels = read(STUDY / 'config-template.json')['labels']
    for split, count in (('train', 40000), ('test', 5000)):
        source = result['source_counts'][split]
        need(source['rows'] == plan['corpus'][split]['rows'] and
             source['class_counts'] == {label: count for label in labels}, 'source_class_counts')
        eligible = result['eligibility'][split]
        need(eligible['source_rows'] == source['rows'] and eligible['source_changed_between_passes'] is False,
             'eligibility_source_binding')
        need(sum(eligible['eligible_per_class'].values()) + eligible['excluded_rows'] == source['rows'],
             'eligibility_aggregate_counts')
        quota = 1100 if split == 'train' else 100
        need(all(integer(eligible['eligible_per_class'][label], quota, count) for label in labels),
             'eligible_quota_inventory')
    reader_manifest_path = ROOT / 'evaluation/production/dbpedia-reader-v1/v1/reader-manifest.json'
    reader = read(reader_manifest_path)
    files = {'/reader/' + n: p['sha256'] for n, p in reader.items()}
    files.update({'/reader-manifest.json': sha(reader_manifest_path), '/plan.json': PLAN_SHA,
                  '/history-result.json': sha(OUT / 'history/data/result.json'),
                  '/fingerprints.json': history['fingerprints_sha256'],
                  '/exposure.json': history['exposure_sha256']})
    files.update({'/corpus/' + split + '.parquet': plan['corpus'][split]['sha256']
                  for split in ('train', 'test')})
    expected_files(freeze, files, sha(STUDY / 'config-template.json'))
    return result


def primary_check(cfg, config_sha, freeze, require_primary_freeze):
    primary = read(OUT / 'train/data/result.json')
    need(primary['complete'] is True and primary['stage'] == 'train' and
         primary['config_sha256'] == config_sha and primary['test_or_development_available'] is False and
         primary['model_promoted'] is False, 'primary_result_binding')
    registry = OUT / 'train/data/registry'
    need(registry.stat().st_mode & 0o777 == 0o700, 'primary_directory_mode')
    need(set(primary['registry_files']) == {p.name for p in registry.iterdir()} == MODEL_FILES,
         'primary_file_inventory')
    for name, pin in primary['registry_files'].items():
        need(opaque(registry / name) == pin, 'primary_file_pin')
        need((registry / name).stat().st_mode & 0o777 == 0o400, 'primary_file_mode')
    receipt = read(registry / 'TRAINING-RECEIPT.json')
    need(receipt == primary['training_receipt'], 'primary_receipt_equality')
    need(receipt['format'] == 'eidos-owned-sparse-training-receipt-v1' and
         receipt['complete'] is True and receipt['classes'] == 14 and receipt['training_rows'] == 14000 and
         receipt['class_counts'] == {label: 1000 for label in cfg['labels']}, 'fixed_training_counts')
    need(receipt['epochs'] == 3 and receipt['seed'] == 113 and
         receipt['training_recipe'] == 'sparse-tfidf-online-softmax-sgd-v1' and
         receipt['checkpoint_selection'] == 'fixed_final_epoch_no_development_or_test_input',
         'fixed_training_recipe')
    for key in ('operational_acceptance_enabled', 'model_promoted', 'evaluation_or_calibration_performed',
                'privacy_guarantee', 'third_party_training_or_inference_packages'):
        need(receipt[key] is False, 'primary_review_scope')
    need(receipt['input_sha256'] == cfg['cohort_files']['training.jsonl']['sha256'] and
         receipt['provenance_sha256'] == cfg['cohort_files']['provenance.json']['sha256'], 'training_input_binding')
    need(primary['model_identity'] == receipt['model'] and
         receipt['model']['id'] == 'dbpedia_scaled_sparse' and receipt['model']['version'] == '1' and
         receipt['model']['calibration_status'] == 'unfitted', 'primary_model_identity')
    freeze_path = OUT / 'primary-freeze.json'
    need(not require_primary_freeze or freeze_path.exists(), 'required_primary_freeze_missing')
    if freeze_path.exists():
        frozen = read(freeze_path)
        need(frozen['fixed_before_development_prediction'] is True and frozen['development_selects_model'] is False and
             frozen['training_result_sha256'] == sha(OUT / 'train/data/result.json') and
             frozen['config_sha256'] == config_sha and frozen['files'] ==
             {name: pin['sha256'] for name, pin in primary['registry_files'].items()}, 'primary_before_development_freeze')
    expected_files(freeze, {'/data/' + n: cfg['cohort_files'][n]['sha256']
                   for n in ('training.jsonl', 'provenance.json')}, config_sha)
    return primary


def trained_role_files(primary, cfg, field):
    files = {'/registry/' + name: pin['sha256'] for name, pin in primary['registry_files'].items()}
    files['/training-result.json'] = sha(OUT / 'train/data/result.json')
    files['/data/' + field] = cfg['cohort_files'][field]['sha256']
    return files


def prediction_check(name, cfg, config_sha, primary, freeze, prepared):
    receipt = read(OUT / name / 'data/result.json')
    need(receipt['complete'] is True and receipt['stage'] == name and receipt['rows'] == 1400 and
         receipt['config_sha256'] == config_sha and receipt['fit_or_scoring_performed'] is False,
         'completed_prediction_receipt')
    stream = opaque(OUT / name / 'data/predictions.jsonl', lines=True, maximum=4 * 1024**2)
    need(stream['newline_delimiters'] == 1400 and stream['sha256'] == receipt['predictions_sha256'] and
         receipt['input_sha256'] == cfg['cohort_files']['test-inputs.jsonl']['sha256'], 'completed_prediction_byte_binding')
    if name == 'eidos':
        need(receipt['primary_receipt_sha256'] == sha(OUT / 'train/data/result.json') and
             receipt['model_files'] == primary['registry_files'] and
             receipt['model_identity'] == primary['model_identity'] and
             receipt['gold_or_development_available'] is False and
             receipt['operational_acceptance_enabled'] is False, 'primary_prediction_binding')
        expected_files(freeze, trained_role_files(primary, cfg, 'test-inputs.jsonl'), config_sha)
    else:
        need(receipt['reference_api'] == 'public_predict_long_default_window_stride_aggregate_auto_batch_one' and
             receipt['gold_or_peer_predictions_available'] is False and
             receipt['all_choices_distinct_for_every_case'] is True and
             receipt['truncated_window_count_reported_by_public_API'] == 0,
             'reference_api_or_coverage')
        need(all(receipt['adapter'][k] == v for k, v in REFERENCE.items()), 'reference_identity')
        adapter = receipt['adapter']
        need(adapter['device'] == 'cpu' and adapter['threads'] == 1 and
             adapter['weight_io']['backend'] == 'pread' and
             adapter['weight_io']['all_206_tensor_payloads_checked_against_raw_file'] is True and
             adapter['weight_io']['published_source_files_modified'] is False, 'reference_numeric_loader_policy')
        need(integer(receipt['actual_windows'], 1400, 2100), 'reference_actual_windows')
        need(number(receipt['process_cpu_seconds'], 0, 4801) and
             number(receipt['peak_rss_kib'], 0, 4096 * 1024), 'reference_reported_resources')
        feasibility = read(OUT / 'laya/data/feasibility.json')
        need(feasibility['complete'] is True and feasibility['cases'] == 1400 and
             feasibility['offered_choices'] == 14 and feasibility['all_choices_distinct'] is True and
             feasibility['planned_windows'] == receipt['actual_windows'] and
             integer(feasibility['largest_case_windows'], 1, 16) and
             feasibility['quality_or_gold_read'] is False, 'reference_feasibility_binding')
        files = {'/data/test-inputs.jsonl': cfg['cohort_files']['test-inputs.jsonl']['sha256'],
                 '/rights.json': prepared['external_files'][prepared['rights_file']]['sha256']}
        expected_files(freeze, files, config_sha)
    return receipt, stream


def score_check(cfg, config_sha, primary, predictions, freeze):
    score = read(OUT / 'score/data/result.json')
    need(score['complete'] is True and score['stage'] == 'score' and score['n'] == 1400 and
         score['config_sha256'] == config_sha and score['replicates'] == 2000 and score['seed'] == 113,
         'score_scope_or_statistics')
    need(score['reference_is_authored_substitute'] is False and
         score['development_used_for_model_selection_or_quality_based_stop'] is False and
         score['numeric_gate_only_not_general_superiority_or_promotion'] is True and
         score['mission_complete'] is score['production_certified'] is False, 'score_limit_flags')
    need(score['primary_receipt_sha256'] == sha(OUT / 'train/data/result.json') and
         score['primary_model'] == primary['model_identity'], 'score_primary_binding')
    files = {'/training-result.json': sha(OUT / 'train/data/result.json')}
    for filename, field in (('test-gold.jsonl', 'gold_sha256'), ('test-groups.jsonl', 'groups_sha256')):
        need(score[field] == cfg['cohort_files'][filename]['sha256'], 'score_gold_group_binding')
        files['/data/' + filename] = score[field]
    for name, receipt in predictions.items():
        receipt_sha = sha(OUT / name / 'data/result.json')
        need(score['prediction_receipts'][name] == receipt_sha, 'score_prediction_receipt_binding')
        files['/' + name + '/result.json'] = receipt_sha
        files['/' + name + '/predictions.jsonl'] = receipt['predictions_sha256']
    files['/laya/feasibility.json'] = sha(OUT / 'laya/data/feasibility.json')
    expected_files(freeze, files, config_sha)
    paired = score['paired']
    need(paired['groups'] == 1400 and paired['minimum_group_rows'] == paired['maximum_group_rows'] == 1,
         'paired_group_inventory')
    for side in ('left', 'right'):
        value = paired[side]
        need(value['n'] == 1400 and integer(value['correct'], 0, 1400) and
             same_number(value['candidate_accuracy'], value['correct'] / 1400) and
             number(value['macro_f1_fixed_offered_labels'], 0, 1), 'aggregate_metric_consistency')
    need(paired['left']['correct'] == score['eidos_descriptive_metrics']['correct'], 'left_aggregate_count')
    descriptive = score['eidos_descriptive_metrics']
    need(descriptive['accepted_answers'] == 0 and descriptive['accepted_coverage'] == 0 and
         descriptive['selective_accuracy'] is None, 'score_review_only_coverage')
    for metric in METRICS:
        need(same_number(paired['left'][metric], score['eidos_descriptive_metrics'][metric]), 'left_aggregate_metric')
        diff = paired['differences'][metric]
        need(same_number(diff['left_minus_right'], paired['left'][metric] - paired['right'][metric]),
             'aggregate_difference_consistency')
        interval = diff['individual_approximate_95_percent_interval']
        need(type(interval) is list and len(interval) == 2 and all(number(v, -1, 1) for v in interval) and
             interval[0] <= interval[1], 'aggregate_interval_bounds')
        need(paired['numeric_gates'][metric] is (interval[0] > 0), 'aggregate_gate_consistency')
    need(paired['numeric_gates']['both_pass'] is all(paired['numeric_gates'][k] for k in METRICS),
         'both_gate_consistency')
    correctness = paired['paired_correctness_counts']
    need(set(correctness) == {'both_correct', 'left_only_correct', 'right_only_correct', 'both_wrong'} and
         all(integer(v, 0, 1400) for v in correctness.values()) and sum(correctness.values()) == 1400 and
         correctness['both_correct'] + correctness['left_only_correct'] == paired['left']['correct'] and
         correctness['both_correct'] + correctness['right_only_correct'] == paired['right']['correct'],
         'paired_correctness_aggregate_consistency')
    return score


def main():
    need(Path.cwd() == ROOT and os.getuid() == 1000 and not sys.flags.optimize, 'cloud_workspace_required')
    need(OUT.exists() and not OUT.is_symlink(), 'attempt_output_required')
    success = (OUT / 'execution-summary.json').exists()
    failure = (OUT / 'failure.json').exists()
    need(success != failure, 'exactly_one_terminal_parent_marker_required')
    plan, prepared, auth = common()
    completed, failed, freezes = [], [], {}
    for name in STAGES:
        path = OUT / name / 'execution-summary.json'
        if not path.exists():
            continue
        summary, freeze = role(name, prepared)
        freezes[name] = freeze
        if summary.get('complete') is True:
            completed.append(name)
        else:
            failed.append(name)
    need(completed == list(STAGES[:len(completed)]), 'completed_stages_must_be_prefix')
    review = {'format': 'eidos-dbpedia-scaled-completion-review-v1', 'complete': True,
        'study_completed_successfully': success, 'plan_sha256': PLAN_SHA,
        'prepared_manifest_sha256': PREPARED_SHA,
        'authorization_sha256': sha(STUDY / 'authorization-v1.json'),
        'approval_provenance_sha256': sha(STUDY / 'approval-provenance-v1.json'),
        'review_source_sha256': sha(Path(__file__)),
        'completed_stages': completed, 'failed_stages': failed,
        'source_and_staged_control_pins_verified': True,
        'verification_only_no_new_quality_computation': True,
        'corpus_case_gold_or_history_files_opened': False,
        'prediction_rows_decoded_or_parsed': False, 'development_metrics_read': False,
        'model_import_load_fit_or_inference_performed': False,
        'metrics_or_bootstrap_repeated': False, 'one_attempt_consumed': True,
        'automatic_retry_fallback_extension_or_alternative_fit_authorized': False,
        'model_promoted_or_operational_acceptance_enabled': False,
        'mission_complete': False, 'production_certified': False}
    history = corpus = cfg = primary = None
    if 'history' in completed:
        history = history_check(prepared, freezes['history'])
    if 'corpus' in completed:
        corpus = corpus_check(plan, history, freezes['corpus'])
        review['cohort_lineage'] = corpus['lineage']
        review['cohort_eligibility_aggregates'] = corpus['eligibility']
    if (OUT / 'config.json').exists():
        need(corpus is not None, 'configuration_requires_completed_cohort')
        cfg = read(OUT / 'config.json')
        config_sha = sha(OUT / 'config.json')
        need(cfg['authored_fixture_only'] is False and cfg['cohort_files'] == corpus['files'] and
             cfg['cohort_readiness_sha256'] == sha(OUT / 'corpus/data/result.json') and
             cfg['n'] == 1400 and cfg['recipe'] == {'epochs': 3, 'seed': 113} and
             cfg['development_policy'] == 'diagnostic_only_no_selection_or_quality_based_stop',
             'sealed_runtime_configuration_binding')
    if 'train' in completed:
        primary = primary_check(cfg, config_sha, freezes['train'],
                                success or 'development' in completed)
        review['primary_model'] = primary['model_identity']
        review['primary_registry_files'] = primary['registry_files']
        review['primary_freeze_present'] = (OUT / 'primary-freeze.json').exists()
    if 'development' in completed:
        expected_files(freezes['development'], trained_role_files(primary, cfg, 'development.jsonl'), config_sha)
    predictions = {}
    for name in ('eidos', 'laya'):
        if name in completed:
            predictions[name], stream = prediction_check(name, cfg, config_sha, primary, freezes[name], prepared)
            review[name + '_completed_opaque_prediction_stream'] = stream
    if success:
        execution = read(OUT / 'execution-summary.json')
        need(completed == list(STAGES) and not failed and execution['complete'] is True and
             execution['all_seven_stages_complete'] is True and execution['primary_fixed_before_diagnostic_development'] is True,
             'whole_completed_study_required')
        need([r['stage'] for r in execution['stages']] == list(STAGES) and
             execution['automatic_retry'] is execution['acquisition_or_installation'] is False and
             execution['aggregate_cgroup_guarantee'] is False and
             number(execution['elapsed_seconds'], 0, 10800), 'whole_execution_contract')
        for result in execution['stages']:
            need(result == read(OUT / result['stage'] / 'execution-summary.json'), 'parent_child_stage_receipt_equality')
        score = score_check(cfg, config_sha, primary, predictions, freezes['score'])
        review['score_sha256'] = sha(OUT / 'score/data/result.json')
        review['paired_aggregate_result'] = score['paired']
        review['elapsed_seconds'] = execution['elapsed_seconds']
        review['reference_metadata'] = predictions['laya']
        review['reference_float32_policy_checked_by_pinned_stage_not_reloaded_by_audit'] = True
    else:
        result = read(OUT / 'failure.json')
        need(result['complete'] is False and result['automatic_retry_allowed'] is False and
             result['fallback_resume_or_partial_scoring_allowed'] is False, 'failed_attempt_contract')
        review['failure_metadata'] = result
        review['uncompleted_stage_outputs_not_opened'] = True
        # Even a completed score on a later-failed parent is not promoted or
        # accepted here; no failed-branch quality is opened by this review.
    need(not REVIEW.exists() and not REVIEW.is_symlink(), 'new_review_output_required')
    fd = os.open(REVIEW, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(review, sort_keys=True, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'complete': True, 'study_completed_successfully': success,
        'completed_stages': completed, 'failed_stages': failed, 'review': str(REVIEW),
        'score_sha256': review.get('score_sha256'),
        'numeric_gates': review.get('paired_aggregate_result', {}).get('numeric_gates'),
        'no_refit_prediction_rescore_or_bootstrap': True}, sort_keys=True))


if __name__ == '__main__':
    main()
