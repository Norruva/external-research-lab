"""Read-only verification of completed study receipts; never refits or rescores."""
import hashlib
import json
import math
import os
from pathlib import Path

ROOT = Path('/workspace/eidos')
STUDY = ROOT / 'research/dbpedia-study-v1'
OUT = ROOT / 'evaluation/production/dbpedia-study-v1/v1'
EXPECTED_PLAN = '686b1ef74b5ed6defacb61031d4ab83de93f6d470629855d61c5ac0cda8e4d3d'
EXPECTED_PREPARED = '15e97a495e897d0be897bb75e067fca9d9187e355c7e0050754fe24912b3e39d'


def read(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    p = Path(path)
    assert p.is_file() and not any(a.is_symlink() for a in (p, *p.parents))
    value = hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            value.update(block)
    return value.hexdigest()


def main():
    assert Path.cwd() == ROOT and os.getuid() == 1000
    assert sha(STUDY / 'plan.json') == EXPECTED_PLAN
    assert sha(STUDY / 'prepared-manifest-v1.json') == EXPECTED_PREPARED
    plan, cfg = read(STUDY / 'plan.json'), read(STUDY / 'config.json')
    config_sha = sha(STUDY / 'config.json')
    auth, prelaunch = read(STUDY / 'authorization-v1.json'), read(OUT / 'prelaunch.json')
    assert auth['approved'] is True and auth['scope'] == plan['scope']
    assert auth['plan_sha256'] == prelaunch['plan_sha256'] == EXPECTED_PLAN
    assert auth['prepared_manifest_sha256'] == prelaunch['prepared_manifest_sha256'] == EXPECTED_PREPARED
    assert prelaunch['authorization_sha256'] == sha(STUDY / 'authorization-v1.json')
    execution = read(OUT / 'execution-summary.json')
    expected_roles = ['memory', 'linear_gd', 'linear_adam', 'select', 'eidos', 'laya', 'score']
    assert execution['complete'] is True and execution['all_seven_stages_complete'] is True
    assert [r['stage'] for r in execution['stages']] == expected_roles
    assert not (OUT / 'failure.json').exists()
    for result in execution['stages']:
        assert result == read(OUT / result['stage'] / 'execution-summary.json')
        assert result['complete'] is True and result['exit_code'] == 0
        assert result['expected_refused_preflights'] == 1 and result['unexpected_ip_connects'] == 0
        assert [result[k] for k in ('cpu_seconds_limit', 'wall_seconds_limit', 'memory_mib')] == plan['stage_limits'][result['stage']]
        assert result['native_reference_mounted'] is (result['stage'] == 'laya')
    trained = {}
    for name in cfg['recipe_order']:
        result = read(OUT / name / 'data/result.json')
        registry = OUT / name / 'data/registry'
        assert result['complete'] and result['config_sha256'] == config_sha
        assert set(result['registry_files']) == {p.name for p in registry.iterdir()}
        for filename, pin in result['registry_files'].items():
            p = registry / filename
            assert sha(p) == pin['sha256'] and p.stat().st_size == pin['bytes']
            assert p.stat().st_mode & 0o777 == 0o400
        receipt = read(registry / 'TRAINING-RECEIPT.json')
        assert receipt == result['training_receipt']
        assert receipt['support_rows'] == 280 and receipt['classes'] == 14
        assert receipt['input_sha256'] == cfg['cohort_files']['support.jsonl']['sha256']
        assert receipt['provenance_sha256'] == cfg['cohort_files']['provenance.json']['sha256']
        assert receipt['operational_acceptance_enabled'] is receipt['model_promoted'] is False
        trained[name] = {'model': receipt['model'], 'files': result['registry_files'],
                         'features': receipt['features'], 'nnz': receipt['nnz']}
    selection = read(OUT / 'select/data/result.json')
    selected = selection['selected']
    assert selected in cfg['recipe_order'] and selection['test_inputs_or_gold_available'] is False
    assert selection['models'][selected]['files'] == trained[selected]['files']
    for name in ('eidos', 'laya'):
        receipt = read(OUT / name / 'data/result.json')
        predictions = OUT / name / 'data/predictions.jsonl'
        assert receipt['complete'] is True and receipt['rows'] == 280 and receipt['config_sha256'] == config_sha
        assert receipt['predictions_sha256'] == sha(predictions)
        assert receipt['input_sha256'] == cfg['cohort_files']['test-inputs.jsonl']['sha256']
        # Structural count only: do not parse case outcomes or repeat scoring.
        assert predictions.read_bytes().count(b'\n') == 280
    reference = read(OUT / 'laya/data/result.json')
    for key, value in cfg['reference'].items():
        assert reference['adapter'][key] == value
    assert reference['adapter']['threads'] == 1 and reference['gold_or_peer_predictions_available'] is False
    assert reference['adapter']['weight_io']['all_206_tensor_payloads_checked_against_raw_file'] is True
    assert reference['all_choices_distinct_for_every_case'] is True
    assert read(OUT / 'laya/data/feasibility.json')['planned_windows'] == reference['actual_windows']
    score = read(OUT / 'score/data/result.json')
    assert score['complete'] is True and score['config_sha256'] == config_sha and score['n'] == 280
    assert score['selected'] == selected and score['replicates'] == 1000 and score['seed'] == 113
    assert score['reference_is_authored_substitute'] is False
    for name, pin in score['prediction_receipts'].items():
        assert pin == sha(OUT / name / 'data/result.json')
    for name, field in (('test-gold.jsonl', 'gold_sha256'), ('test-groups.jsonl', 'groups_sha256')):
        assert score[field] == cfg['cohort_files'][name]['sha256']
    paired = score['paired']
    assert paired['groups'] == 280 and paired['minimum_group_rows'] == paired['maximum_group_rows'] == 1
    left, right = paired['left'], paired['right']
    assert left['n'] == right['n'] == 280
    assert left['correct'] == score['eidos_descriptive_metrics']['correct']
    for key in ('candidate_accuracy', 'macro_f1_fixed_offered_labels'):
        assert math.isclose(left[key], score['eidos_descriptive_metrics'][key], abs_tol=1e-12)
        assert math.isclose(paired['differences'][key]['left_minus_right'], left[key] - right[key], abs_tol=1e-12)
        assert paired['numeric_gates'][key] is (paired['differences'][key]['individual_approximate_95_percent_interval'][0] > 0)
    assert paired['numeric_gates']['both_pass'] is all(paired['numeric_gates'][k] for k in ('candidate_accuracy', 'macro_f1_fixed_offered_labels'))
    review = {'format': 'eidos-dbpedia-study-completion-review-v1', 'complete': True,
              'plan_sha256': EXPECTED_PLAN, 'prepared_manifest_sha256': EXPECTED_PREPARED,
              'authorization_sha256': sha(STUDY / 'authorization-v1.json'),
              'score_sha256': sha(OUT / 'score/data/result.json'), 'selected': selected,
              'models': trained, 'execution': execution, 'reference': reference,
              'paired': paired, 'old_benchmarks_refitted_rescored_or_reselected': False,
              'verification_only_no_new_quality_computation': True,
              'one_attempt_consumed': True, 'automatic_retry_or_extension_authorized': False,
              'model_promoted_or_operational_acceptance_enabled': False,
              'mission_complete': False, 'production_certified': False}
    fd = os.open(OUT / 'completion-review-v1.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(review, sort_keys=True, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'complete': True, 'selected': selected, 'eidos': left, 'laya': right,
                      'differences': paired['differences'], 'numeric_gates': paired['numeric_gates'],
                      'elapsed_seconds': execution['elapsed_seconds'], 'score_sha256': review['score_sha256']}))


if __name__ == '__main__':
    main()
