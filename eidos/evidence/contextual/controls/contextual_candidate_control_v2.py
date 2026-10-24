
import errno,hashlib,json,math,os,pathlib,resource,socket,sys,time
from collections import Counter,defaultdict
assert sys.flags.no_site and sys.flags.dont_write_bytecode and os.getuid()==1000
assert os.getcwd()=='/app' and {n for _,n in socket.if_nameindex()}=={'lo'}
assert not any(pathlib.Path(p).exists() for p in ('/workspace','/root','/etc','/sys','/opt','/dataset','/deps'))
assert set(os.environ)<={'PATH','HOME','LC_ALL','PWD'}
freeze=json.loads(pathlib.Path('/freeze.json').read_bytes())
assert hashlib.sha256(pathlib.Path('/control.py').read_bytes()).hexdigest()==freeze['control_sha256']
resource.setrlimit(resource.RLIMIT_CPU,(freeze['cpu_seconds'],)*2)
resource.setrlimit(resource.RLIMIT_AS,(768*1024**2,)*2)
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_FSIZE,(32*1024**2,)*2)
with socket.socket() as probe:
 try:probe.connect(('198.51.100.1',443))
 except OSError as e:assert e.errno==errno.ENETUNREACH
 else:raise AssertionError('unexpected route')
for prefix,pins in freeze['mounts'].items():
 for name,pin in pins.items():
  path=pathlib.Path(prefix)/name
  assert not path.is_symlink() and path.is_file()
  assert hashlib.sha256(path.read_bytes()).hexdigest()==pin
  assert os.statvfs(path).f_flag & os.ST_RDONLY
sys.path[:]=['/app']+[p for p in sys.path if p.startswith('/usr/') and 'site-packages' not in p and 'dist-packages' not in p]
stage=freeze['stage'];out=pathlib.Path('/output')
def dump(name,obj):
 with (out/name).open('x') as f:f.write(json.dumps(obj,sort_keys=True,allow_nan=False,indent=2)+'\n')
def lines(path):return [json.loads(x) for x in pathlib.Path(path).read_text().splitlines()]
if stage=='units':
 import unittest
 suite=unittest.TestLoader().discover('/app/tests',pattern='test_decision*.py')
 suite.addTests(unittest.TestLoader().discover('/app/tests',pattern='test_contextual*.py'))
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 dump('unit-result.json',{'complete':result.wasSuccessful(),'methods':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),'original_fixtures_only':True,'real_quality_or_vendor_evaluation':False})
 raise SystemExit(0 if result.wasSuccessful() else 1)
elif stage=='mechanics':
 import runpy
 sys.argv=['tools/contextual_smoke.py','--output','/output/smoke']
 runpy.run_path('/app/tools/contextual_smoke.py',run_name='__main__')
elif stage=='serving':
 import runpy
 sys.argv=['tools/contextual_serving_smoke.py','--registry','/model','--output','/output/serving']
 runpy.run_path('/app/tools/contextual_serving_smoke.py',run_name='__main__')
elif stage=='generate':
 from eidos.contextual_challenge import materialize,generate
 exclusion=json.loads(pathlib.Path('/exposure/hashes.json').read_text())
 old_structures=set(exclusion['relation_structural_hashes']);old_sequences=set(exclusion['sequence_group_hashes'])
 selected_seed=None
 for candidate_seed in range(37199,37327):
  candidate=generate(training_pairs=4,slice_pairs=2,ordering_pairs=4,seed=candidate_seed)
  structures={w['structural_hash'] for rows in candidate['worlds'].values() for w in rows}
  sequences={s['source_group'] for rows in candidate['sequences'].values() for s in rows}
  if structures.isdisjoint(old_structures) and sequences.isdisjoint(old_sequences):
   selected_seed=candidate_seed;break
 assert selected_seed is not None,'No seed satisfies frozen exposure exclusion'
 dump('exposure-exclusion-result.json',{'complete':True,'selected_seed':selected_seed,'attempted_seed_count':selected_seed-37199+1,'relation_structural_hash_overlap':0,'sequence_group_hash_overlap':0,'selection_uses_model_or_quality':False,'exclusion_sha256':hashlib.sha256(pathlib.Path('/exposure/hashes.json').read_bytes()).hexdigest()})
 receipt=materialize('/output/corpus',training_pairs=4,slice_pairs=2,ordering_pairs=4,seed=selected_seed)
 # Partition control metadata and labels away from later prediction mounts.
 # The request ID is administrative and ignored by the neural representation.
 ledger=json.loads((out/'corpus/ORACLE.json').read_text())
 metadata={s['request_id']:s for role in ('training','fresh_world','novel_entities','new_wording','joint') for s in ledger['tasks'][role]}
 requests=[];gold=[]
 for role in ('training','fresh_world','novel_entities','new_wording','joint'):
  for row in lines(out/'corpus'/f'{role}.jsonl'):
   request=row['request']
   requests.append(request)
   gold.append({'role':role,'family':metadata[request['request_id']]['task']['family'],'paired_task_key':metadata[request['request_id']]['paired_task_key'],**row})
 with (out/'requests.jsonl').open('x') as f:
  for request in requests:f.write(json.dumps(request,sort_keys=True,separators=(',',':'))+'\n')
 with (out/'gold.jsonl').open('x') as f:
  for row in gold:f.write(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n')
 dump('generation-result.json',{'complete':True,'synthetic_only':True,'total_tasks':len(requests),'no_fit_or_model_predictions':True,'generator_receipt':receipt})
elif stage=='preflight':
 from eidos.train_contextual import training_preflight
 results=[training_preflight('/training/training.jsonl','/training/provenance.json',epochs=e,seed=113,learning_rate=.005) for e in (16,8,4,2,1)]
 eligible=[r for r in results if r['within_work_budget']]
 selected=max(eligible,key=lambda r:r['epochs']) if eligible else None
 result={'complete':selected is not None,'selected':selected,'epochs':selected['epochs'] if selected else None,'all_predeclared_estimates':results,'selection':'largest_predeclared_16_8_4_2_1_within_unchanged_work_limit_no_quality','heldout_or_gold_available':False,'model_forward_loss_fit_or_scoring_performed':False}
 dump('preflight-result.json',result)
 assert result['complete'],'No fixed recipe fits the resource bound'
elif stage=='train':
 from eidos.train_contextual import build
 receipt=build('/training/training.jsonl','/training/provenance.json','/output/model','owned-contextual-memory-v3p1',epochs=freeze['fit_epochs'],seed=113,learning_rate=.005)
 dump('train-result.json',{'complete':True,'single_fixed_fit':True,'heldout_input_available':False,'recipe_selected_before_quality':True,'receipt':receipt})
elif stage=='predict':
 from eidos.contextual import ContextualEngine
 engine=ContextualEngine.from_registry('/model')
 count=0
 with (out/'predictions.jsonl').open('x') as f:
  for request in lines('/requests/requests.jsonl'):
   response=engine.decide(request)
   assert response['review_required'] and response['value'] is None
   f.write(json.dumps(response,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n');count+=1
 dump('prediction-result.json',{'complete':True,'requests':count,'no_gold_or_world_ledger_available':True,'model':engine.identity,'review_only':True})
elif stage=='score':
 assert not pathlib.Path('/model').exists() and not pathlib.Path('/training').exists()
 rows=lines('/gold/gold.jsonl');predictions=lines('/predictions/predictions.jsonl')
 ids=[row['request']['request_id'] for row in rows]
 assert len(ids)==len(set(ids))==len(predictions)
 by_id={p['request_id']:p for p in predictions};assert set(by_id)==set(ids)
 groups=defaultdict(list);paired=defaultdict(list)
 for row in rows:
  q=row['request'];p=by_id[q['request_id']]
  assert p['kind']==q['kind'] and p['format']=='eidos-decision-response-v2'
  assert p['review_required'] and p['value'] is None and p['calibrated_confidence'] is None
  options={v['id']:v for v in q['options']};dist={v['id']:v for v in p['distribution']}
  assert len(dist)==len(options)==len(p['distribution']) and set(options)==set(dist)
  probs={k:v['probability'] for k,v in dist.items()}
  assert all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in probs.values())
  assert math.isclose(math.fsum(probs.values()),1.,abs_tol=1e-12)
  target=row['target_option_id'];assert target in options
  best=max(sorted(probs),key=probs.get)
  # Uniform is an expected randomized baseline, not a fitted rival model.
  item={'correct':int(best==target),'nll':-math.log(max(probs[target],1e-300)),
        'brier':math.fsum((v-int(k==target))**2 for k,v in probs.items()),
        'uniform_expected_accuracy':1/len(options)}
  if q['kind']=='SCORE':
   expected=math.fsum(probs[k]*options[k]['value'] for k in options)
   assert math.isclose(expected,p['candidate_value'],abs_tol=1e-12)
   item['score_absolute_error']=abs(expected-options[target]['value'])
  groups[(row['role'],'all')].append(item)
  groups[(row['role'],q['kind'])].append(item)
  groups[(row['role'],row['family'])].append(item)
  semantic=lambda key:options[key]['value'] if q['kind']=='SCORE' else options[key]['description']
  paired[(row['role'],row['source_group'],row['paired_task_key'],q['kind'])].append(
     {'correct':item['correct'],'target':semantic(target),'top':semantic(best)})
 def aggregate(items):
  result={'n':len(items),'correct':sum(x['correct'] for x in items)}
  for key in ('correct','nll','brier','uniform_expected_accuracy'):
   result['accuracy' if key=='correct' else key]=math.fsum(x[key] for x in items)/len(items)
  score=[x['score_absolute_error'] for x in items if 'score_absolute_error'in x]
  if score:result['score_mean_absolute_error']=math.fsum(score)/len(score)
  return result
 pair_results={}
 for role in ('training','fresh_world','novel_entities','new_wording','joint'):
  pairs=[v for (r,*_),v in paired.items() if r==role]
  assert pairs and all(len(v)==2 for v in pairs)
  changed=[v for v in pairs if v[0]['target']!=v[1]['target']]
  unchanged=[v for v in pairs if v[0]['target']==v[1]['target']]
  pair_results[role]={'paired_tasks':len(pairs),'gold_changed_pairs':len(changed),
   'both_correct_when_gold_changed':sum(all(x['correct'] for x in v) for v in changed),
   'model_top_changed_when_gold_changed':sum(v[0]['top']!=v[1]['top'] for v in changed),
   'gold_unchanged_pairs':len(unchanged),
   'model_top_unchanged_when_gold_unchanged':sum(v[0]['top']==v[1]['top'] for v in unchanged)}
 result={'format':'eidos-original-contextual-challenge-score-v1','complete':True,
  'single_fixed_recipe_no_quality_selected_refit':True,
  'public_generator_synthetic_roles_not_independent_real_benchmark':True,
  'LAYA_JEV_or_real_domain_quality_measured':False,'accepted_coverage':0,
  'calibration_fitted':False,'production_certified':False,'mission_complete':False,
  'source_groups':{role:len({r['source_group'] for r in rows if r['role']==role}) for role in ('training','fresh_world','novel_entities','new_wording','joint')},
  'row_IID_or_statistical_interval_claimed':False,'counterfactual_pairs':pair_results,
  'roles':{role:{kind:aggregate(v) for (r,kind),v in groups.items() if r==role}
           for role in ('training','fresh_world','novel_entities','new_wording','joint')}}
 dump('score.json',result)
else:raise AssertionError('unknown stage')
