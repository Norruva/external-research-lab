"""Bounded offline validation of new original code and explicitly authored tasks.

No benchmark, native reader/reference, model download, old release artifact,
existing configuration/credential, private repository or production mount.
"""
import argparse, hashlib, json, os
from pathlib import Path
import resource, shutil, signal, subprocess, time
ROOT=Path('/workspace/eidos')
SOURCE=Path('/workspace/external-research-lab/eidos')
BASE=ROOT/'evaluation/production/decision-model-v1'
GUARD=r'''
import errno,hashlib,json,os,pathlib,resource,socket,sys,runpy,unittest
assert sys.flags.no_site and sys.flags.dont_write_bytecode and os.getuid()==1000
assert os.getcwd()=='/app' and {n for _,n in socket.if_nameindex()}=={'lo'}
assert not any(pathlib.Path(p).exists() for p in ('/workspace','/root','/etc','/sys','/opt','/model','/dataset','/deps'))
assert set(os.environ)<={'PATH','HOME','LC_ALL','PWD'}
freeze=json.loads(pathlib.Path('/freeze.json').read_bytes())
resource.setrlimit(resource.RLIMIT_CPU,(freeze['cpu_seconds'],)*2)
if freeze['memory_mib'] is not None:
 resource.setrlimit(resource.RLIMIT_AS,(freeze['memory_mib']*1024**2,)*2)
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_FSIZE,(32*1024**2,)*2)
with socket.socket() as p:
 try:p.connect(('198.51.100.1',443))
 except OSError as e:assert e.errno==errno.ENETUNREACH
 else:raise AssertionError('Unexpected external route')
for name,pin in freeze['files'].items():
 p=pathlib.Path('/app')/name
 assert not p.is_symlink() and p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==pin
 assert os.statvfs(p).f_flag & os.ST_RDONLY
sys.path[:]=['/app']+[p for p in sys.path if p.startswith('/usr/') and 'site-packages' not in p and 'dist-packages' not in p]
if freeze['stage']=='optimization':
 from eidos.train_decision import build
 rows=[]
 for rate in (.02,.005,.001):
  receipt=build('/app/examples/decision-owned-demo/training.jsonl','/app/examples/decision-owned-demo/provenance.json','/output/model-rate-'+str(rate),'authored-optimization-'+str(rate).replace('.','-'),epochs=10,seed=113,learning_rate=rate)
  rows.append({'learning_rate':rate,'diagnostics':receipt['diagnostics'],'model':receipt['model']})
 result={'format':'eidos-original-training-only-optimization-diagnostic-v1','complete':True,'epochs':10,'seed':113,'predeclared_rates':[.02,.005,.001],'selection_criterion':'lowest_final_training_cross_entropy_only_not_evaluation_quality','rows':rows,'smoke_or_other_test_data_available':False,'generalization_or_LAYA_JEV_superiority_established':False}
 pathlib.Path('/output/result.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
 print(json.dumps(result,sort_keys=True))
elif freeze['stage']=='browser':
 for name,pin in freeze['registry_files'].items():
  p=pathlib.Path('/registry')/name
  assert hashlib.sha256(p.read_bytes()).hexdigest()==pin and os.statvfs(p).f_flag & os.ST_RDONLY
 sys.argv=['tools/decision_browser_smoke.py','--registry','/registry','--output','/output/browser']
 runpy.run_path('/app/tools/decision_browser_smoke.py',run_name='__main__')
elif freeze['stage']=='endtoend':
 sys.argv=['tools/decision_smoke.py','--output','/output/demo']
 runpy.run_path('/app/tools/decision_smoke.py',run_name='__main__')
else:
 suite=unittest.TestLoader().discover('/app/tests',pattern='test_decision*.py')
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 pathlib.Path('/output/unit-result.json').write_text(json.dumps({'complete':result.wasSuccessful(),'methods':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),'authored_only':True,'semantic_quality_or_rival_measurement':False},sort_keys=True)+'\n')
 raise SystemExit(0 if result.wasSuccessful() else 1)
'''

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=('core','units','endtoend','epistemic','browser','optimization'),required=True);parser.add_argument('--attempt',required=True);parser.add_argument('--registry');args=parser.parse_args()
 assert Path.cwd()==ROOT and os.getuid()==1000 and os.uname().sysname=='Linux'
 assert args.attempt.isascii() and args.attempt.replace('-','').isalnum()
 BASE.mkdir(exist_ok=True)
 out=BASE/(args.stage+'-'+args.attempt);out.mkdir(mode=0o700,exist_ok=False)
 app=out/'staged';app.mkdir(mode=0o700);payload=out/'data';payload.mkdir(mode=0o700)
 if args.stage=='epistemic':
  paths=[SOURCE/'eidos/__init__.py',SOURCE/'eidos/decision_epistemic.py',SOURCE/'tests/test_decision_epistemic.py']
 elif args.stage=='core':
  paths=[SOURCE/'eidos'/n for n in ('__init__.py','decision_contract.py','decision_neural.py','decision.py')]
  paths += [SOURCE/'tests'/n for n in ('test_decision_contract.py','test_decision_neural.py','test_decision_engine.py')]
 else:
  paths=list((SOURCE/'eidos').glob('*.py'))+[SOURCE/'eidos/decision-api.schema.json']
  paths+=list((SOURCE/'tests').glob('test_decision*.py'))
  paths += [SOURCE/'web'/n for n in ('decision.html','decision.js','style.css')]
  if args.stage=='optimization':
   paths += [SOURCE/'examples/decision-owned-demo'/n for n in ('training.jsonl','provenance.json')]
  if args.stage=='browser':
   paths += [SOURCE/'tools/decision_browser_smoke.py',SOURCE/'examples/decision-owned-demo/smoke.jsonl']
  if args.stage=='endtoend':
   paths += [SOURCE/'examples/decision-owned-demo'/n for n in ('training.jsonl','smoke.jsonl','provenance.json')]
   paths += [SOURCE/'tools/decision_smoke.py']
 assert paths and all(p.is_file() and not p.is_symlink() for p in paths)
 files={}
 for p in paths:
  name=p.relative_to(SOURCE);q=app/name;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q);q.chmod(0o400);files[name.as_posix()]=sha(q)
 cpu,wall,memory=(120,180,768) if args.stage!='endtoend' else (600,900,768)
 registry=None
 if args.stage=='browser':
  cpu,wall,memory=120,150,None
  registry=Path(args.registry).absolute()
  assert registry.is_dir() and not any(p.is_symlink() for p in (registry,*registry.parents))
  assert {p.name for p in registry.iterdir()}=={'model.json','weights.f64','registry.json','TRAINING-RECEIPT.json'}
 freeze={'format':'eidos-original-decision-authored-validation-v1','stage':args.stage,'files':files,'cpu_seconds':cpu,'wall_seconds':wall,'memory_mib':memory,'source_only_original_or_preexisting_owned_code':True,'authored_tasks_only':True,'real_benchmark_or_reference':False,'new_installation_download_paid_compute_GPU':False,'execution_basis':'User explicitly instructed to build own decision model; bounded original-fixture implementation verification, no reuse of consumed real-study approvals.'}
 if registry is not None:freeze['registry_files']={p.name:sha(p) for p in registry.iterdir()}
 (out/'freeze.json').write_text(json.dumps(freeze,sort_keys=True,indent=2)+'\n')
 command=['/usr/bin/bwrap','--unshare-all','--new-session','--die-with-parent','--uid','1000','--gid','1000','--cap-drop','ALL','--ro-bind','/usr/bin','/usr/bin','--ro-bind','/usr/lib','/usr/lib','--ro-bind','/usr/lib64','/usr/lib64','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/home/research','--ro-bind',str(app),'/app','--ro-bind',str(out/'freeze.json'),'/freeze.json','--bind',str(payload),'/output','--clearenv','--setenv','PATH','/usr/bin:/bin','--setenv','HOME','/home/research','--setenv','LC_ALL','C.UTF-8','--chdir','/app','--','/usr/bin/python3','-S','-B','-c',GUARD]
 if registry is not None:
  at=command.index('--clearenv');command[at:at]=['--ro-bind',str(registry),'/registry','--ro-bind','/usr/share/fonts','/usr/share/fonts']
 command=['/usr/bin/strace','-f','-qq','-e','trace=network','-e','raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg','-o',str(out/'network.trace'),*command]
 before=resource.getrusage(resource.RUSAGE_CHILDREN);start=time.monotonic()
 process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':'/usr/bin:/bin'},start_new_session=True)
 try:stdout,stderr=process.communicate(timeout=wall);code=process.returncode
 except subprocess.TimeoutExpired:
  os.killpg(process.pid,signal.SIGKILL);stdout,stderr=process.communicate(timeout=5);code=124
 (out/'stdout.log').write_bytes(stdout);(out/'stderr.log').write_bytes(stderr)
 trace=(out/'network.trace').read_text(errors='replace');ip=[l for l in trace.splitlines() if 'connect(' in l and 'AF_INET' in l]
 probes=[l for l in ip if '198.51.100.1' in l and 'ENETUNREACH' in l]
 loopback=[l for l in ip if '127.0.0.1' in l]
 blocked_browser=[l for l in ip if args.stage=='browser' and '2001:4860:4860::8888' in l and 'ENETUNREACH' in l]
 other=[l for l in ip if l not in probes and l not in loopback and l not in blocked_browser]
 unchanged=all(sha(app/n)==pin for n,pin in files.items())
 if len(probes)!=1 or other or not unchanged:code=code or 1
 after=resource.getrusage(resource.RUSAGE_CHILDREN)
 result={'format':'eidos-original-decision-authored-stage-v1','complete':code==0,'stage':args.stage,'exit_code':code,'elapsed_seconds':time.monotonic()-start,'waited_child_CPU_seconds':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,'peak_child_RSS_kib':after.ru_maxrss,'expected_refused_preflights':len(probes),'loopback_connects':len(loopback),'blocked_Chromium_IPv6_probe_connects':len(blocked_browser),'unexpected_external_IP_connects':len(other),'staged_sources_unchanged':unchanged,'staged_files':len(files),'cpu_seconds_limit_per_process':cpu,'wall_seconds_limit':wall,'memory_mib_limit_per_process':memory,'aggregate_cgroup_guarantee':False,'new_installation_download_real_benchmark_or_reference':False}
 (out/'execution-summary.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
 print(json.dumps(result));print(stderr.decode(errors='replace')[-5500:]);print(stdout.decode(errors='replace')[-2000:])
 return code
if __name__=='__main__':raise SystemExit(main())
