"""Run one bounded original-synthetic curriculum study in separated offline roles.

No previous study, public corpus, vendor checkpoint/service or installed package
is mounted. A single frozen fitting recipe is evaluated without quality-driven
refits. The result concerns a finite synthetic generator, not general semantics.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import time

ROOT = Path('/workspace/eidos')
SOURCE = Path('/workspace/external-research-lab/eidos')
BASE = ROOT / 'evaluation/production/decision-curriculum-v1'

GUARD = r'''
import errno,hashlib,json,math,os,pathlib,resource,socket,sys,time
from collections import Counter,defaultdict
assert sys.flags.no_site and sys.flags.dont_write_bytecode and os.getuid()==1000
assert os.getcwd()=='/app' and {n for _,n in socket.if_nameindex()}=={'lo'}
assert not any(pathlib.Path(p).exists() for p in ('/workspace','/root','/etc','/sys','/opt','/dataset','/deps'))
assert set(os.environ)<={'PATH','HOME','LC_ALL','PWD'}
freeze=json.loads(pathlib.Path('/freeze.json').read_bytes())
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
if stage=='generate':
 from eidos.decision_curriculum import materialize
 receipt=materialize('/output/corpus',training_worlds=30,development_worlds=6,evaluation_worlds=6,seed=113)
 # Partition control metadata and labels away from later prediction mounts.
 # The request ID is administrative and ignored by the neural representation.
 ledger=json.loads((out/'corpus/ORACLE.json').read_text())
 metadata={s['request_id']:s for role in ('training','development','evaluation') for s in ledger['tasks'][role]}
 requests=[];gold=[]
 for role in ('training','development','evaluation'):
  for row in lines(out/'corpus'/f'{role}.jsonl'):
   request=row['request']
   requests.append(request)
   gold.append({'role':role,'family':metadata[request['request_id']]['task']['family'],**row})
 with (out/'requests.jsonl').open('x') as f:
  for request in requests:f.write(json.dumps(request,sort_keys=True,separators=(',',':'))+'\n')
 with (out/'gold.jsonl').open('x') as f:
  for row in gold:f.write(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n')
 dump('generation-result.json',{'complete':True,'synthetic_only':True,'total_tasks':len(requests),'no_fit_or_model_predictions':True,'generator_receipt':receipt})
elif stage=='preflight':
 from eidos.train_decision import parse_training,MAX_VOCABULARY,MAX_WORK_UNITS
 from eidos.decision_neural import build_vocabulary,DecisionNetwork
 rows,_=parse_training(pathlib.Path('/training/training.jsonl').read_bytes(),pathlib.Path('/training/provenance.json').read_bytes())
 model=DecisionNetwork.initialize(build_vocabulary([r.request for r in rows],max_vocabulary=MAX_VOCABULARY),dimension=12,hidden=16,seed=113)
 estimated_pass=sum(model.estimate_work(model.encode(row.request)) for row in rows)
 estimates={e:(e+2)*estimated_pass+e*len(rows)*model.parameter_count*40 for e in (6,4,2)}
 eligible=[e for e in (6,4,2) if estimates[e]<=MAX_WORK_UNITS]
 epochs=max(eligible) if eligible else None
 result={'complete':epochs is not None,'training_rows':len(rows),'parameter_count':model.parameter_count,'epochs':epochs,'work_units_estimates':estimates,'work_units_estimate':estimates[epochs] if epochs else None,'epoch_selection':'largest_of_predeclared_6_4_2_within_unchanged_resource_bound_only','work_units_limit':MAX_WORK_UNITS,'inference_training_or_quality_scoring_performed':False,'heldout_or_gold_available':False}
 dump('preflight-result.json',result)
 assert result['complete'],'fixed recipe exceeds budget; do not fit'
elif stage=='train':
 from eidos.train_decision import build
 receipt=build('/training/training.jsonl','/training/provenance.json','/output/model','owned-relational-curriculum-v1',epochs=freeze['fit_epochs'],seed=113,learning_rate=.005)
 dump('train-result.json',{'complete':True,'single_fixed_fit':True,'heldout_input_available':False,'recipe_selected_before_quality':True,'receipt':receipt})
elif stage=='predict':
 from eidos.decision import DecisionEngine
 engine=DecisionEngine.from_registry('/model')
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
  paired[(row['role'],row['source_group'],q['prompt'],q['kind'])].append(
     {'correct':item['correct'],'target':semantic(target),'top':semantic(best)})
 def aggregate(items):
  result={'n':len(items),'correct':sum(x['correct'] for x in items)}
  for key in ('correct','nll','brier','uniform_expected_accuracy'):
   result['accuracy' if key=='correct' else key]=math.fsum(x[key] for x in items)/len(items)
  score=[x['score_absolute_error'] for x in items if 'score_absolute_error'in x]
  if score:result['score_mean_absolute_error']=math.fsum(score)/len(score)
  return result
 pair_results={}
 for role in ('training','development','evaluation'):
  pairs=[v for (r,*_),v in paired.items() if r==role]
  assert pairs and all(len(v)==2 for v in pairs)
  changed=[v for v in pairs if v[0]['target']!=v[1]['target']]
  unchanged=[v for v in pairs if v[0]['target']==v[1]['target']]
  pair_results[role]={'paired_tasks':len(pairs),'gold_changed_pairs':len(changed),
   'both_correct_when_gold_changed':sum(all(x['correct'] for x in v) for v in changed),
   'model_top_changed_when_gold_changed':sum(v[0]['top']!=v[1]['top'] for v in changed),
   'gold_unchanged_pairs':len(unchanged),
   'model_top_unchanged_when_gold_unchanged':sum(v[0]['top']==v[1]['top'] for v in unchanged)}
 result={'format':'eidos-original-synthetic-curriculum-score-v1','complete':True,
  'single_fixed_recipe_no_quality_selected_refit':True,
  'public_generator_synthetic_roles_not_independent_real_benchmark':True,
  'LAYA_JEV_or_real_domain_quality_measured':False,'accepted_coverage':0,
  'calibration_fitted':False,'production_certified':False,'mission_complete':False,
  'source_groups':{role:len({r['source_group'] for r in rows if r['role']==role}) for role in ('training','development','evaluation')},
  'row_IID_or_statistical_interval_claimed':False,'counterfactual_pairs':pair_results,
  'roles':{role:{kind:aggregate(v) for (r,kind),v in groups.items() if r==role}
           for role in ('training','development','evaluation')}}
 dump('score.json',result)
else:raise AssertionError('unknown stage')
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stage', required=True, choices=('generate','preflight','train','predict','score'))
    args = p.parse_args()
    os.umask(0o077)
    assert Path.cwd() == ROOT and os.getuid() == 1000 and os.uname().sysname == 'Linux'
    BASE.mkdir(mode=0o700, exist_ok=True)
    prepared=json.loads((BASE/'prepared-manifest-v1.json').read_text())
    assert prepared['runner_sha256']==sha(Path(__file__))
    assert prepared['protocol_sha256']==sha(ROOT/'research/59-owned-semantic-curriculum-protocol.md')
    for name,pin in prepared['core_sources'].items():
        assert sha(SOURCE/name)==pin,name
    out = BASE / (args.stage+'-v1')
    out.mkdir(mode=0o700)
    payload = out/'data';payload.mkdir(mode=0o700)
    app = out/'app';app.mkdir(mode=0o700)
    sources = list((SOURCE/'eidos').glob('*.py'))
    assert sources and all(p.is_file() and not p.is_symlink() for p in sources)
    for path in sources:
        target=app/'eidos'/path.name;target.parent.mkdir(exist_ok=True)
        shutil.copyfile(path,target);target.chmod(0o400)
    mounts={'/app':{p.relative_to(app).as_posix():sha(p) for p in app.rglob('*') if p.is_file()}}
    assert mounts['/app']==prepared['core_sources']
    extra=[];fit_epochs=None
    if args.stage in ('preflight','train'):
        assert (BASE/'generate-v1/execution-summary.json').is_file()
        assert json.loads((BASE/'generate-v1/execution-summary.json').read_text())['complete']
        training=out/'training';training.mkdir(mode=0o700)
        for name in ('training.jsonl','provenance.json'):
            shutil.copyfile(BASE/'generate-v1/data/corpus'/name,training/name)
            (training/name).chmod(0o400)
        extra.append((training,'/training'))
        if args.stage=='train':
            preflight=json.loads((BASE/'preflight-v1/data/preflight-result.json').read_text())
            assert preflight['complete'] and preflight['epochs'] in (6,4,2)
            fit_epochs=preflight['epochs']
    elif args.stage=='predict':
        assert json.loads((BASE/'train-v1/execution-summary.json').read_text())['complete']
        extra.append((BASE/'train-v1/data/model','/model'))
        requests=out/'requests';requests.mkdir(mode=0o700)
        shutil.copyfile(BASE/'generate-v1/data/requests.jsonl',requests/'requests.jsonl')
        extra.append((requests,'/requests'))
    elif args.stage=='score':
        assert json.loads((BASE/'predict-v1/execution-summary.json').read_text())['complete']
        for filename,mount,source in [
            ('gold.jsonl','/gold',BASE/'generate-v1/data/gold.jsonl'),
            ('predictions.jsonl','/predictions',BASE/'predict-v1/data/predictions.jsonl'),
        ]:
            folder=out/mount[1:];folder.mkdir(mode=0o700);shutil.copyfile(source,folder/filename)
            extra.append((folder,mount))
    for host,target in extra:
        assert not any(p.is_symlink() for p in (host,*host.parents))
        mounts[target]={p.relative_to(host).as_posix():sha(p) for p in host.rglob('*') if p.is_file()}
    cpu,wall=(600,900) if args.stage=='train' else (120,180)
    freeze={'format':'eidos-owned-curriculum-role-freeze-v1','stage':args.stage,
            'mounts':mounts,'cpu_seconds':cpu,'wall_seconds':wall,'memory_mib':768,
            'single_fixed_fit':{'epochs':'largest_predeclared_6_4_2_fitting_work_cap_before_quality','seed':113,'learning_rate':.005},
            'fit_epochs':fit_epochs,
            'new_authored_generator_only':True,'prior_benchmark_or_vendor_used':False,
            'execution_basis':'Original authored-fixture engineering under explicit user instruction to build EIDOS; no reuse of consumed real-study approvals.'}
    (out/'freeze.json').write_text(json.dumps(freeze,sort_keys=True,indent=2)+'\n')
    command=['/usr/bin/bwrap','--unshare-all','--new-session','--die-with-parent','--uid','1000','--gid','1000','--cap-drop','ALL','--ro-bind','/usr/bin','/usr/bin','--ro-bind','/usr/lib','/usr/lib','--ro-bind','/usr/lib64','/usr/lib64','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/home/research','--ro-bind',str(app),'/app','--ro-bind',str(out/'freeze.json'),'/freeze.json','--bind',str(payload),'/output']
    for host,target in extra:command.extend(['--ro-bind',str(host),target])
    command.extend(['--clearenv','--setenv','PATH','/usr/bin:/bin','--setenv','HOME','/home/research','--setenv','LC_ALL','C.UTF-8','--chdir','/app','--','/usr/bin/python3','-S','-B','-c',GUARD])
    command=['/usr/bin/strace','-f','-qq','-e','trace=network','-e','raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg','-o',str(out/'network.trace'),*command]
    start=time.monotonic();before=resource.getrusage(resource.RUSAGE_CHILDREN)
    child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':'/usr/bin:/bin'},start_new_session=True)
    try:stdout,stderr=child.communicate(timeout=wall);code=child.returncode
    except subprocess.TimeoutExpired:
        os.killpg(child.pid,signal.SIGKILL);stdout,stderr=child.communicate(timeout=5);code=124
    (out/'stdout.log').write_bytes(stdout);(out/'stderr.log').write_bytes(stderr)
    trace=(out/'network.trace').read_text(errors='replace')
    connects=[s for s in trace.splitlines() if 'connect('in s and 'AF_INET'in s]
    probes=[s for s in connects if '198.51.100.1'in s and 'ENETUNREACH'in s]
    unexpected=[s for s in connects if s not in probes]
    unchanged=all(sha(app/n)==pin for n,pin in mounts['/app'].items())
    if len(probes)!=1 or unexpected or not unchanged:code=code or 1
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    result={'format':'eidos-owned-curriculum-role-execution-v1','complete':code==0,
            'stage':args.stage,'exit_code':code,'elapsed_seconds':time.monotonic()-start,
            'waited_child_CPU_seconds_not_all_descendants':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
            'peak_waited_child_RSS_kib':after.ru_maxrss,'expected_refused_probes':len(probes),
            'unexpected_external_IP_connections':len(unexpected),'staged_sources_unchanged':unchanged,
            'cpu_seconds_per_process':cpu,'wall_seconds':wall,'memory_mib_per_process':768,
            'aggregate_cgroup_guarantee':False,'prior_real_benchmark_or_vendor':False}
    (out/'execution-summary.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    print(json.dumps(result));print(stderr.decode(errors='replace')[-4000:])
    return code


if __name__=='__main__':
    raise SystemExit(main())
