"""Exercise owned evaluation CLI stages with separate offline file mounts."""
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time

ROOT = Path('/workspace/eidos')
APP = Path('/workspace/external-research-lab/eidos')
OUT = ROOT / 'evaluation/production/owned-adam-training-v1/roles-v1'
GUARD = r'''
import errno,json,os,pathlib,resource,socket,subprocess,sys
role,method=sys.argv[1:]
assert os.getuid()==1000 and sys.flags.no_site and sys.flags.dont_write_bytecode
assert os.getcwd()=='/code' and {n for _,n in socket.if_nameindex()}=={'lo'}
assert set(os.environ)<={'PATH','LC_ALL','HOME','PWD'}
assert all(not pathlib.Path(p).exists() for p in ('/workspace','/root','/run','/etc','/sys','/opt','/deps'))
with socket.socket() as probe:
 try:probe.connect(('198.51.100.1',443))
 except OSError as e:assert e.errno==errno.ENETUNREACH
 else:raise AssertionError('Unexpected external route')
resource.setrlimit(resource.RLIMIT_CPU,(120,120))
resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2))
os.umask(0o077)
base=[sys.executable,'-S','-B','-m']
if role=='prepare':
 assert not pathlib.Path('/registry').exists() and not pathlib.Path('/gold').exists()
 trainer='eidos.train_support' if method=='memory' else 'eidos.train_linear_support'
 commands=[base+[trainer,'--input','/fixture/support.jsonl','--provenance','/fixture/support-provenance.json',
                  '--output','/output/model','--model-id','owned-evaluation-demo-v1','--optimizer','adam'],
           base+['eidos.evaluate_owned','prepare','--registry','/output/model','--support','/fixture/support.jsonl',
                 '--support-provenance','/fixture/support-provenance.json','--evaluation','/fixture/evaluation.jsonl',
                 '--evaluation-provenance','/fixture/evaluation-provenance.json','--output','/output/bundle']]
elif role=='predict':
 assert all(not pathlib.Path(p).exists() for p in ('/fixture','/gold','/plan.json','/support','/evaluation'))
 assert {p.name for p in pathlib.Path('/input').iterdir()}=={'plan.json','inputs.jsonl'}
 commands=[base+['eidos.evaluate_owned','predict','--registry','/registry','--inputs','/input','--output','/output/predictions']]
else:
 assert role=='score'
 assert all(not pathlib.Path(p).exists() for p in ('/fixture','/registry','/input','/support','/evaluation'))
 assert {p.name for p in pathlib.Path('/gold').iterdir()}=={'gold.jsonl','binding.json'}
 commands=[base+['eidos.evaluate_owned','score','--plan','/plan.json','--gold','/gold',
                 '--predictions','/predictions','--output','/output/score']]
for i,command in enumerate(commands):
 result=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=25)
 pathlib.Path('/output/command-%d-stdout.json'%i).write_bytes(result.stdout)
 pathlib.Path('/output/command-%d-stderr.log'%i).write_bytes(result.stderr)
 assert result.returncode==0,(role,i,result.stderr.decode(errors='replace')[-1000:])
 value=json.loads(result.stdout);assert value['complete']
assert value.get('n',value.get('metrics',{}).get('n'))==4
if role=='predict':assert not value['gold_input_read'] and not value['training_calibration_or_scoring_performed']
if role=='score':
 assert not value['model_weights_or_source_text_read_for_scoring'] and not value['per_case_rows_written']
 assert value['metrics']['accepted_coverage']==0 and value['metrics']['selective_accuracy'] is None
 assert sum(map(sum,value['metrics']['confusion']['counts']))==4
print(json.dumps({'complete':True,'role':role,'trainer':method,'n':4,
 'gold_absent_from_prediction_namespace':role=='predict',
 'models_and_source_case_text_absent_from_score_namespace':role=='score',
 'authored_mechanical_fixture_only':True,'semantic_or_rival_quality_claim':False}))
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code():
    return {p.name: sha(p) for p in sorted((APP / 'eidos').iterdir()) if p.is_file()}


def phase(method, role):
    path = OUT / method / role
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = path / 'output'
    output.mkdir(mode=0o700)
    prepared = OUT / method / 'prepare/output'
    command = ['/usr/bin/bwrap','--unshare-all','--new-session','--die-with-parent',
        '--uid','1000','--gid','1000','--cap-drop','ALL',
        '--ro-bind','/usr/bin','/usr/bin','--ro-bind','/usr/lib','/usr/lib',
        '--ro-bind','/usr/lib64','/usr/lib64','--symlink','usr/bin','/bin',
        '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
        '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/home/research',
        '--dir','/code','--ro-bind',str(APP/'eidos'),'/code/eidos','--dir','/output',
        '--bind',str(output),'/output']
    mounted = {}
    if role == 'prepare':
        command += ['--dir','/fixture']
        for source, name in [('owned-support-demo.jsonl','support.jsonl'),
                             ('owned-support-demo-provenance.json','support-provenance.json'),
                             ('owned-evaluation-demo.jsonl','evaluation.jsonl'),
                             ('owned-evaluation-demo-provenance.json','evaluation-provenance.json')]:
            mounted[APP/'examples'/source] = '/fixture/'+name
    elif role == 'predict':
        command += ['--dir','/registry','--ro-bind',str(prepared/'model'),'/registry',
                    '--dir','/input','--ro-bind',str(prepared/'bundle/prediction-input'),'/input']
    else:
        command += ['--ro-bind',str(prepared/'bundle/prediction-input/plan.json'),'/plan.json',
                    '--dir','/gold','--ro-bind',str(prepared/'bundle/gold-private'),'/gold',
                    '--dir','/predictions','--ro-bind',str(OUT/method/'predict/output/predictions'),'/predictions']
    for source, target in mounted.items():
        command += ['--ro-bind',str(source),target]
    command += ['--clearenv','--setenv','PATH','/usr/bin:/bin','--setenv','LC_ALL','C.UTF-8',
                '--setenv','HOME','/home/research','--chdir','/code','--',
                '/usr/bin/python3','-S','-B','-c',GUARD,role,method]
    command = ['/usr/bin/strace','-f','-qq','-e','trace=network',
        '-e','raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg','-o',str(path/'network.trace'),*command]
    before = code()
    started = time.monotonic()
    process = subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               env={'PATH':'/usr/bin:/bin'},start_new_session=True)
    try:
        stdout,stderr=process.communicate(timeout=35)
        status=process.returncode
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL)
        stdout,stderr=process.communicate(timeout=5)
        status=124
    (path/'stdout.json').write_bytes(stdout)
    (path/'stderr.log').write_bytes(stderr)
    trace=(path/'network.trace').read_text()
    ip=[line for line in trace.splitlines() if 'connect(' in line and 'AF_INET' in line]
    probes=[line for line in ip if 'inet_addr("198.51.100.1")' in line and 'ENETUNREACH' in line]
    result={'complete':status==0 and before==code() and len(probes)==1 and len(ip)==1,
            'trainer':method,'role':role,'exit_code':status,'elapsed_seconds':time.monotonic()-started,
            'code_unchanged':before==code(),'code_file_count':len(before),
            'expected_refused_preflights':len(probes),'unexpected_ip_connects':len(ip)-len(probes),
            'stdout_sha256':hashlib.sha256(stdout).hexdigest(),'operating_system_file_role_boundary':True,
            'existing_credentials_or_benchmark_data_mounted':False,'only_authored_mechanical_fixtures':True}
    (path/'execution-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    if not result['complete']:
        print(stderr.decode(errors='replace')[-2000:])
        raise RuntimeError('Role stage failed; preserve evidence, no partial scoring')
    return result


def main():
    assert os.getuid()==1000 and Path.cwd()==ROOT
    OUT.mkdir(mode=0o700,parents=True,exist_ok=False)
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_CPU,(150,150))
    resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024**2,64*1024**2))
    started=time.monotonic()
    results=[]
    for method in ('adam',):
        for role in ('prepare','predict','score'):
            results.append(phase(method,role))
    summary={'complete':True,'stages':results,'elapsed_seconds':time.monotonic()-started,
             'unit_or_stage_reference_benchmark_run':False,'quality_claim_from_demo':False,
             'separate_prediction_without_gold':True,'separate_scoring_without_models_or_case_text':True,
             'new_keys_or_existing_credentials_used':False,'models_trained_only_from_authored_demo':True,
             'per_stage_cpu_seconds':120,'per_stage_memory_mib':512,'per_stage_wall_seconds':35,
             'aggregate_cgroup_guarantee':False}
    (OUT/'execution-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
