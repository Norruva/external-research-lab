"""Independent aggregate arithmetic and source/role bindings; no quality rerun."""
import hashlib,json,math
from pathlib import Path
ROOT=Path('/workspace/eidos');RUN=ROOT/'evaluation/production/support-remaining-reference-v1/v1'
STUDY=ROOT/'research/support-remaining-reference-v1'
def load(p):return json.loads(p.read_bytes())
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        while block:=f.read(1024**2):h.update(block)
    return h.hexdigest()


def main():
    execution=load(RUN/'execution-summary.json');score=load(RUN/'score/data/result.json');pred=load(RUN/'predict/data/result.json')
    assert execution['complete'] and score['complete'] and pred['complete'] and score['n']==pred['n']==1821
    assert execution['inputs_unchanged'] and not execution['automatic_retry']
    assert pred['public_direct_forward_calls']==1821 and pred['all_seven_options_and_state_preserved']
    assert not pred['gold_or_peer_outputs_available'] and not pred['quality_scored']
    assert pred['predictions_sha256']==sha(RUN/'predict/data/predictions.jsonl')
    authorization=load(STUDY/'authorization-v1.json');prepared=load(STUDY/'prepared-manifest-v1.json')
    plan=load(ROOT/'research/production/reference-snips-remaining-resource-plan-v1.json')
    assert score['pilot_quality_seen_before_followup_plan'] and not score['confirmatory_or_global_error_control_claimed']
    assert authorization['approved'] and authorization['prepared_manifest_sha256']==sha(STUDY/'prepared-manifest-v1.json')
    assert authorization['plan_sha256']==sha(ROOT/'research/production/reference-snips-remaining-resource-plan-v1.json')
    for n,pin in prepared['files'].items():assert (ROOT/n).stat().st_size==pin['bytes'] and sha(ROOT/n)==pin['sha256']
    pfreeze=load(RUN/'predict/freeze.json');sfreeze=load(RUN/'score/freeze.json')
    assert '/inputs.jsonl' in pfreeze and '/gold.jsonl' not in pfreeze and '/eidos.jsonl' not in pfreeze
    assert '/gold.jsonl' in sfreeze and '/eidos.jsonl' in sfreeze and '/inputs.jsonl' not in sfreeze
    assert not any(p.startswith(('/model/','/deps/','/laya-source/')) or p=='/model-manifest.json' for p in sfreeze)
    verified={}
    for arm,metric in score['metrics'].items():
        c=metric['confusion'];labels=c['labels'];matrix=c['counts']
        assert len(labels)==len(set(labels))==len(matrix)==7 and all(len(r)==7 for r in matrix)
        assert all(type(v)is int and v>=0 for r in matrix for v in r)
        assert list(map(sum,matrix))==[plan['per_label'][label] for label in labels]
        correct=sum(matrix[i][i] for i in range(7));n=sum(map(sum,matrix))
        macro=math.fsum(2*matrix[i][i]/(sum(matrix[i])+sum(r[i] for r in matrix)) for i in range(7))/7
        assert n==metric['n']==1821 and correct==metric['correct'] and metric['accuracy']==correct/n
        assert abs(macro-metric['fixed_seven_macro_f1'])<1e-15
        verified[arm]={'correct':correct,'n':n,'accuracy':correct/n,'macro_f1':macro}
    for arm,pair in score['paired'].items():
        assert sum(pair[k] for k in ('both_correct','both_wrong','eidos_only_correct','laya_only_correct'))==1821
        assert pair['both_correct']+pair['eidos_only_correct']==verified[arm]['correct']
        assert pair['both_correct']+pair['laya_only_correct']==verified['laya']['correct']
        for key in ('accuracy','fixed_seven_macro_f1'):
            assert abs(pair['differences_eidos_minus_laya'][key]-(score['metrics'][arm][key]-score['metrics']['laya'][key]))<1e-15
        interval=pair['approximate_component_bootstrap']
        assert interval['groups_per_label']==plan['gold_component_counts']
        expected=all(v>0 for v in pair['differences_eidos_minus_laya'].values()) and interval['accuracy_difference_95_percentile'][0]>0 and interval['fixed_seven_macro_f1_difference_95_percentile'][0]>0
        assert expected is pair['both_scoped_gates_passed']
    for stage in execution['stages']:
        assert stage['complete'] and stage['exit_code']==0 and stage['expected_refused_probe']==1 and stage['unexpected_ip_connections']==0
        assert stage['result_sha256']==sha(RUN/stage['stage']/'data/result.json')
    result={'complete':True,'scope':'aggregate arithmetic and recorded integrity/role bindings only','metrics':verified,
        'prepared_manifest_files_verified':len(prepared['files']),'source_or_models_changed':False,'predictions_recomputed':False,
        'gold_or_per_case_predictions_decoded_by_review':False,'bootstrap_regenerated':False,
        'primary_linear_scoped_gates_passed':score['paired']['linear']['both_scoped_gates_passed'],
        'exploratory_and_asymmetric_adaptation_limit_general_claims':True,
        'gold_free_prediction_and_text_free_scoring_recorded':True,'native_cpu_seconds':pred['cpu_seconds'],
        'score_cpu_seconds':score['cpu_seconds'],'cpu_total_excludes_wrappers_and_unwaited_descendants':True,
        'planned_cpu_seconds':3600,'aggregate_cgroup_guarantee':False,'whole_elapsed_seconds':execution['elapsed_seconds'],
        'score_sha256':sha(RUN/'score/data/result.json'),'prediction_result_sha256':sha(RUN/'predict/data/result.json'),
        'execution_sha256':sha(RUN/'execution-summary.json'),'authorization_sha256':sha(STUDY/'authorization-v1.json'),
        'new_jev_measurement':False,'model_promoted':False,'mission_complete':False,'production_certified':False}
    with (RUN/'independent-aggregate-review-v1.json').open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
