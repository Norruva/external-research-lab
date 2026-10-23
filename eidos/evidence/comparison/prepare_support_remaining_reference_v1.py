"""One proposed group-disjoint follow-up; never imports/executes either model."""
import ast,collections,hashlib,json,re,shutil
from pathlib import Path
ROOT=Path('/workspace/eidos');OLD=ROOT/'research/support-reference-v1';STUDY=ROOT/'research/support-remaining-reference-v1'
RUN=ROOT/'evaluation/production/support-memory-v1/v2';PILOT=ROOT/'evaluation/production/support-reference-v1/v1'
PLAN=ROOT/'research/production/reference-snips-remaining-resource-plan-v1.json'
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        while block:=f.read(1024**2):h.update(block)
    return h.hexdigest()
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def write(p,v):
    with p.open('xb') as f:f.write(canonical(v)+b'\n')
def jsonl(p,values):
    with p.open('xb') as f:
        for v in values:f.write(canonical(v)+b'\n')
def rows(p):return [json.loads(v) for v in p.read_bytes().splitlines()]


def main():
    assert not STUDY.exists();STUDY.mkdir(mode=0o700)
    old_manifest=json.loads((OLD/'prepared-manifest-v1.json').read_bytes())
    for relative,pin in old_manifest['files'].items():
        p=ROOT/relative;assert p.stat().st_size==pin['bytes'] and sha(p)==pin['sha256']
    pilot=json.loads((PILOT/'execution-summary.json').read_bytes());assert pilot['complete'] and not pilot['automatic_retry']
    preparation=json.loads((RUN/'prepare/data/result.json').read_bytes());prediction=json.loads((RUN/'predict/data/result.json').read_bytes())
    assert preparation['complete'] and prediction['complete'] and prediction['n']==2194
    for name in ('target-inputs.jsonl','target-gold.jsonl'):
        assert sha(RUN/'prepare/data'/name)==preparation['files'][name]
    assert sha(RUN/'predict/data/predictions.jsonl')==prediction['predictions_sha256']
    # Selection depends only on already frozen component identities. No score,
    # correctness, confidence, utterance length or desired effect enters it.
    previous=rows(OLD/'inputs.jsonl');excluded={r['group_hash'] for r in previous};all_inputs=rows(RUN/'prepare/data/target-inputs.jsonl')
    inputs=[r for r in all_inputs if r['group_hash'] not in excluded];byid={r['id']:r for r in inputs}
    assert len(previous)==len(excluded)==140 and len(byid)==len(inputs)>140
    assert not excluded&{r['group_hash'] for r in inputs}
    assert {r['id'] for r in previous}.isdisjoint(byid)
    gold=[]
    for row in rows(RUN/'prepare/data/target-gold.jsonl'):
        if row['id'] in byid:
            assert row['group_hash']==byid[row['id']]['group_hash']
            gold.append(dict(row,input_row_sha256=hashlib.sha256(canonical(byid[row['id']])).hexdigest()))
    assert [r['id'] for r in inputs]==[r['id'] for r in gold]
    eidos={}
    for row in rows(RUN/'predict/data/predictions.jsonl'):
        if row['id'] in byid:eidos[row['id']]={'id':row['id'],'results':{'seven':row['results']['seven']}}
    assert set(eidos)==set(byid)
    jsonl(STUDY/'inputs.jsonl',inputs);jsonl(STUDY/'gold.jsonl',gold);jsonl(STUDY/'eidos.jsonl',(eidos[r['id']] for r in inputs))
    shutil.copyfile(OLD/'labels.json',STUDY/'labels.json')
    counts=dict(collections.Counter(r['label'] for r in gold));labels=json.loads((STUDY/'labels.json').read_bytes());assert set(counts)==set(labels)
    group_counts={label:len({r['group_hash'] for r in gold if r['label']==label}) for label in labels}
    metadata={'format':'eidos-remaining-component-selection-v1','complete':True,'rule':'all frozen test components absent from the completed 140-case pilot',
        'original_test_n':len(all_inputs),'pilot_cases':140,'excluded_pilot_components':140,'removed_component_rows':len(all_inputs)-len(inputs),
        'n':len(inputs),'gold_class_counts':counts,'component_counts':group_counts,'components':sum(group_counts.values()),
        'pilot_case_and_component_overlap':0,'selection_uses_correctness_or_confidence':False,'eidos_target_collection_already_revealed':True,
        'laya_not_yet_predicted_on_these_components':True,'pilot_quality_seen_before_followup_plan':True,
        'confirmatory_or_global_optional_stopping_error_control_claimed':False,'no_further_automatic_expansions':True,
        'new_fit_or_eidos_inference':False,'source_full_predictions_sha256':prediction['predictions_sha256'],
        'data_files':{n:sha(STUDY/n) for n in ('inputs.jsonl','gold.jsonl','eidos.jsonl','labels.json')}}
    write(STUDY/'cohort-selection.json',metadata)
    for name in ('statistics.py','primary_predict.py','test_reference.py'):shutil.copyfile(OLD/name,STUDY/name)
    control=(OLD/'control.py').read_text();control=re.sub(r'\b140\b','N',control)
    control=control.replace('from pathlib import Path','from pathlib import Path\nN='+str(len(inputs)),1)
    control=control.replace("assert collections.Counter(r['label'] for r in gold)==dict.fromkeys(labels,20)","assert dict(collections.Counter(r['label'] for r in gold))==pins['gold_class_counts']")
    control=control.replace('if (i+1)%20==0:', 'if (i+1)%20==0 or i+1==N:')
    control=control.replace("'posthoc_previously_revealed_eidos_cohort':True,","'posthoc_previously_revealed_eidos_cohort':True,'pilot_quality_seen_before_followup_plan':True,'confirmatory_or_global_error_control_claimed':False,")
    with (STUDY/'control.py').open('x') as f:f.write(control)
    wrapper=(ROOT/'run_support_reference_v1.py').read_text()
    wrapper=wrapper.replace('research/support-reference-v1','research/support-remaining-reference-v1').replace('reference-snips-seven-resource-plan-v1.json','reference-snips-remaining-resource-plan-v1.json').replace('evaluation/production/support-reference-v1/v1','evaluation/production/support-remaining-reference-v1/v1')
    wrapper=wrapper.replace('eidos-snips-seven-reference-v1','eidos-snips-remaining-reference-v1')
    wrapper=wrapper.replace('2400','5400').replace('1440','3600').replace('(180,3600)','(480,3600)').replace('1200','3000').replace('1800','4200')
    wrapper=wrapper.replace("else (60,60)","else (120,120)").replace("('score',120)","('score',180)")
    wrapper=wrapper.replace("receipt['n']==140","receipt['n']=="+str(len(inputs)))
    wrapper=wrapper.replace("pins={'files':{'/labels.json':sha(STUDY/'labels.json')}}", "pins={'files':{'/labels.json':sha(STUDY/'labels.json')},'gold_class_counts':"+repr(counts)+"}")
    with (ROOT/'run_support_remaining_reference_v1.py').open('x') as f:f.write(wrapper)
    protocol='''# Proposed single exploratory comparison on remaining components

No execution permission is conveyed by preparation. A new explicit approval must bind the resource plan and prepared manifest. The completed140-case pilot's one attempt is consumed; neither it nor historical BANKING77 slots/budgets can be reset/reused. No model fitting, EIDOS inference, public LAYA code/model execution, or new quality scoring occurs during preparation.

Purpose: measure the same frozen EIDOS linear and memory models versus the same public direct seven-choice LAYA path on EVERY remaining original evaluation component absent from the pilot. Pilot outcomes have been seen; this follow-up was not preregistered before that result and is exploratory. EIDOS outcomes on the original2194casecollection were already revealed before the pilot. Do not call the follow-up an independent fresh holdout, a confirmatory replication, matched adaptation, or globally error-controlled optional stopping. No further automatic expansions/retries/refits/configuration changes. Preserve negative/uncertain results.

Selection uses only frozen component IDs: exclude the ENTIRE components containing any of the140pilotcases, including all unmeasured companion rows, then retain every other original test row in original order. Never select by correctness, confidence, text length or desired effect. Cohort-selection.json records exact row/class/component counts and input/key/existing EIDOS stream pins. All retained components are disjoint from pilot components and original support/development components. Source/speaker/paraphrase independence and exchangeability remain unverified. Dataset SNIPS@b86ac7f1577868c42158d0dec77db50956046696 CC0-1.0,Couckeetal2018arXiv1805.10190;CLINCtraining@828f8093932c8fe6ca7936c3d2e52903b1c523de CC BY3.0,LarsonetalEMNLP2019. Public data are not exclusive property.

Both EIDOS models are unchanged20-support-per-class supervised classifiers; their original seven-option predictions are filtered, not recomputed. Linear primary and memory secondary, as in pilot; no switching after LAYA. LAYA is the unchanged published zero-shot checkpoint/source/public Agent.predict directall7choice path. Same state,question,sortedlabels,camel-split descriptions,maxlen512/head192,CPUfloat32,fast/compilefalse,1inference/interopthread,4GiBprediction. Retain every public returned choice including rounded probability ties. Before each forward reject any state/option truncation or lost/duplicate markers. No shortlist/tournament needed with seven options. Preserve existing byte-verified pread loader and all206tensor checks. No newmodel/tokenizer/config/calibration/threshold/teacher outputs or algorithms.

Predict once, gold-free and peer-output-free in a private readonly network/PID/mount namespace, cleared environment and explicitofflineflags. Only complete predictions may enter separate stdlib scoring with no text/model/dependencies mounted. Report accuracy, fixedsevenmacroF1,confusion,pairedcounts for all3models; approximate paired whole-component bootstrap stratified by gold label,2000draws,seed113,individual95percentile intervals. Both differences/lowerbounds must be positive for a cohort-scoped numeric gate; never equate this with global/confirmatory/JEV/strongest-LAYA/production superiority. Unadjusted intervals do not account for this outcome-informed follow-up decision or the project's multiple experiments. EIDOS remains abstained/value-null/review/no action authority and no model is promoted regardless of result.

One new attempt, no automatic retry/resume/fallback/partial score or outcome-driven expansion. New planned3600CPUseconds(60minutes): native3000,score120,wrapper/tracer/hashreserve480; per-process limits plus reserves, not an aggregate cgroup guarantee. Prediction4200wallseconds,score180,wholeoperation5400seconds(90minutes). Existing local reviewed artifacts only, no install/download/network/GPU/paidcompute/private repo/existingcredential/Mac/VPN/production/TypeSafe/JEVservice/model extraction/proxyTLSbypass. Prior pilot native98.094437CPU for140cases is resource evidence only; no guaranteed latency for longer cohort. Any limit/capacity/schema/integrity/model failure retains evidence and stops. All source/rights/runtime/model/tree/input identities verified before imports and after. mission_complete=false,production_certified=false.
'''
    with (STUDY/'PROTOCOL.md').open('x') as f:f.write(protocol)
    plan=json.loads((ROOT/'research/production/reference-snips-seven-resource-plan-v1.json').read_bytes())
    plan.update(format='eidos-snips-remaining-reference-resource-plan-v1',scope='eidos-snips-remaining-reference-v1',status='PREPARED_AWAITING_NEW_EXPLICIT_APPROVAL',
        cases=len(inputs),per_label=counts,gold_component_counts=group_counts,cohort_selection_sha256=sha(STUDY/'cohort-selection.json'),
        inputs_and_filtered_existing_predictions=metadata['data_files'],subset_fixed_before_fitting=False,
        selection_rule=metadata['rule'],pilot_component_overlap=0,pilot_quality_seen_before_followup_plan=True,
        exploratory=True,confirmatory_or_global_optional_stopping_error_control_claimed=False,
        planned_cpu_seconds=3600,native_prediction_cpu_seconds=3000,score_cpu_seconds=120,wrapper_tracer_hash_reserve_seconds=480,
        prediction_wall_seconds=4200,score_wall_seconds=180,whole_operation_wall_seconds=5400,
        old_pilot_approval_reused=False,old_pilot_attempt_consumed=True,
        scientific_protocol='research/support-remaining-reference-v1/PROTOCOL.md',
        required_authorization='New explicit approval for this larger group-disjoint exploratory cohort and source manifest; the completed140-case one-attempt approval does not authorize it.',
        review_command='/usr/bin/python3 -S -B run_support_remaining_reference_v1.py',
        execution_command_after_approval_only='/usr/bin/python3 -S -B run_support_remaining_reference_v1.py --execute-approved')
    write(PLAN,plan)
    paths=[STUDY/n for n in ('PROTOCOL.md','control.py','statistics.py','primary_predict.py','test_reference.py','cohort-selection.json','inputs.jsonl','gold.jsonl','eidos.jsonl','labels.json')]
    paths += [PLAN,ROOT/'run_support_remaining_reference_v1.py',Path(__file__),OLD/'prepared-manifest-v1.json',PILOT/'execution-summary.json',PILOT/'score/data/result.json']
    manifest={k:v for k,v in old_manifest.items() if k!='files'}
    for t in manifest['trees'].values():paths.append(Path(t['manifest']))
    paths.append(Path(manifest['rights_file']))
    for p in paths:
        if p.suffix=='.py':ast.parse(p.read_text(),filename=str(p))
    manifest['files']={str(p.relative_to(ROOT)):{'bytes':p.stat().st_size,'sha256':sha(p)} for p in paths}
    write(STUDY/'prepared-manifest-v1.json',manifest)
    print(json.dumps({'complete':True,'n':len(inputs),'class_counts':counts,'components':sum(group_counts.values()),
        'excluded_pilot_component_rows':metadata['removed_component_rows'],'case_and_component_overlap':0,
        'plan_sha256':sha(PLAN),'prepared_manifest_sha256':sha(STUDY/'prepared-manifest-v1.json'),'model_execution':False,'approval_required':True},indent=2))


if __name__=='__main__':main()
