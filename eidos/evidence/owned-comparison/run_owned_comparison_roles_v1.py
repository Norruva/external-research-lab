"""Exercise paired comparison with five separately mounted offline file roles."""
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
OUT = ROOT / 'evaluation/production/owned-comparison-v1/roles-v1'
GUARD = r'''
import errno,json,os,pathlib,resource,socket,subprocess,sys
role=sys.argv[1]
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
commands=[]
if role=='prepare':
 for arm,optimizer in [('left','adam'),('right','gradient_descent')]:
  commands.extend([
   base+['eidos.train_linear_support','--input','/fixture/support.jsonl','--provenance','/fixture/support-provenance.json',
         '--output','/output/'+arm+'-model','--model-id','owned-paired-demo-v1','--optimizer',optimizer],
   base+['eidos.evaluate_owned','prepare','--registry','/output/'+arm+'-model','--support','/fixture/support.jsonl',
         '--support-provenance','/fixture/support-provenance.json','--evaluation','/fixture/evaluation.jsonl',
         '--evaluation-provenance','/fixture/evaluation-provenance.json','--output','/output/'+arm+'-bundle']])
elif role=='plan':
 assert all(not pathlib.Path(p).exists() for p in ('/fixture','/registry','/input','/gold','/predictions','/comparison'))
 commands=[base+['eidos.compare_owned','plan','--left-plan','/left-plan.json','--right-plan','/right-plan.json',
       '--groups','/groups.jsonl','--group-provenance','/group-provenance.json','--output','/output/comparison']]
elif role in ('left-predict','right-predict'):
 assert all(not pathlib.Path(p).exists() for p in ('/fixture','/gold','/left-gold','/right-gold','/comparison'))
 assert {p.name for p in pathlib.Path('/input').iterdir()}=={'plan.json','inputs.jsonl'}
 commands=[base+['eidos.evaluate_owned','predict','--registry','/registry','--inputs','/input','--output','/output/predictions']]
else:
 assert role=='score'
 assert all(not pathlib.Path(p).exists() for p in ('/fixture','/registry','/input','/left-model','/right-model'))
 assert {p.name for p in pathlib.Path('/comparison').iterdir()}=={
  'comparison.json','left-plan.json','right-plan.json','groups.jsonl','group-provenance.json'}
 commands=[base+['eidos.compare_owned','score','--comparison','/comparison','--left-gold','/left-gold',
       '--right-gold','/right-gold','--left-predictions','/left-predictions',
       '--right-predictions','/right-predictions','--output','/output/score']]
for i,command in enumerate(commands):
 result=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=25)
 pathlib.Path('/output/command-%d-stdout.json'%i).write_bytes(result.stdout)
 pathlib.Path('/output/command-%d-stderr.log'%i).write_bytes(result.stderr)
 assert result.returncode==0,(role,i,result.stderr.decode(errors='replace')[-1000:])
 value=json.loads(result.stdout);assert value['complete']
assert value['n']==4
if role=='plan':assert not value['gold_or_predictions_read'] and not value['inference_fit_scoring_or_promotion_performed']
if role.endswith('predict'):assert not value['gold_input_read'] and not value['training_calibration_or_scoring_performed']
if role=='score':
 assert not value['model_weights_or_source_case_text_read'] and not value['per_case_or_per_group_rows_written']
 assert value['accepted_coverage']==0 and not value['rival_or_general_superiority_certified']
 assert sum(value['paired_correctness_counts'].values())==4 and value['groups']==2
 assert not value['group_independence_or_exchangeability_verified']
print(json.dumps({'complete':True,'role':role,'n':4,'authored_mechanical_fixture_only':True,
 'planning_without_gold_predictions_weights_or_case_text':role=='plan',
 'prediction_without_gold':role.endswith('predict'),
 'paired_scoring_without_weights_or_case_text':role=='score',
 'historical_or_rival_quality_claim':False}))
'''


def code():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((APP / 'eidos').iterdir()) if p.is_file()}


def phase(role):
    path = OUT / role
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = path / 'output'
    output.mkdir(mode=0o700)
    prepared = OUT / 'prepare/output'
    command = ['/usr/bin/bwrap', '--unshare-all', '--new-session', '--die-with-parent',
        '--uid', '1000', '--gid', '1000', '--cap-drop', 'ALL',
        '--ro-bind', '/usr/bin', '/usr/bin', '--ro-bind', '/usr/lib', '/usr/lib',
        '--ro-bind', '/usr/lib64', '/usr/lib64', '--symlink', 'usr/bin', '/bin',
        '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
        '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/home/research',
        '--dir', '/code', '--ro-bind', str(APP / 'eidos'), '/code/eidos',
        '--dir', '/output', '--bind', str(output), '/output']
    if role == 'prepare':
        command += ['--dir', '/fixture']
        mounted = {APP / 'examples' / source: '/fixture/' + name for source, name in [
            ('owned-support-demo.jsonl', 'support.jsonl'),
            ('owned-support-demo-provenance.json', 'support-provenance.json'),
            ('owned-evaluation-demo.jsonl', 'evaluation.jsonl'),
            ('owned-evaluation-demo-provenance.json', 'evaluation-provenance.json')]}
    elif role == 'plan':
        mounted = {prepared / (arm + '-bundle/prediction-input/plan.json'): '/' + arm + '-plan.json'
                   for arm in ('left', 'right')}
        mounted.update({APP / 'examples/owned-evaluation-groups-demo.jsonl': '/groups.jsonl',
                        APP / 'examples/owned-evaluation-groups-demo-provenance.json': '/group-provenance.json'})
    elif role.endswith('predict'):
        arm = role.split('-')[0]
        command += ['--dir', '/registry', '--dir', '/input']
        mounted = {prepared / (arm + '-model'): '/registry',
                   prepared / (arm + '-bundle/prediction-input'): '/input'}
    else:
        command += ['--dir', '/comparison', '--dir', '/left-gold', '--dir', '/right-gold',
                    '--dir', '/left-predictions', '--dir', '/right-predictions']
        mounted = {OUT / 'plan/output/comparison': '/comparison'}
        for arm in ('left', 'right'):
            mounted[prepared / (arm + '-bundle/gold-private')] = '/' + arm + '-gold'
            mounted[OUT / (arm + '-predict/output/predictions')] = '/' + arm + '-predictions'
    for source, target in mounted.items():
        command += ['--ro-bind', str(source), target]
    command += ['--clearenv', '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'LC_ALL', 'C.UTF-8',
                '--setenv', 'HOME', '/home/research', '--chdir', '/code', '--',
                '/usr/bin/python3', '-S', '-B', '-c', GUARD, role]
    command = ['/usr/bin/strace', '-f', '-qq', '-e', 'trace=network',
        '-e', 'raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg', '-o', str(path / 'network.trace'), *command]
    before = code()
    started = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env={'PATH': '/usr/bin:/bin'}, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=35)
        status = process.returncode
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=5)
        status = 124
    (path / 'stdout.json').write_bytes(stdout)
    (path / 'stderr.log').write_bytes(stderr)
    trace = (path / 'network.trace').read_text()
    ip = [line for line in trace.splitlines() if 'connect(' in line and 'AF_INET' in line]
    probes = [line for line in ip if 'inet_addr("198.51.100.1")' in line and 'ENETUNREACH' in line]
    result = {'complete': status == 0 and before == code() and len(probes) == len(ip) == 1,
        'role': role, 'exit_code': status, 'elapsed_seconds': time.monotonic() - started,
        'code_unchanged': before == code(), 'code_file_count': len(before),
        'expected_refused_preflights': len(probes), 'unexpected_ip_connects': len(ip) - len(probes),
        'stdout_sha256': hashlib.sha256(stdout).hexdigest(), 'operating_system_file_role_boundary': True,
        'existing_credentials_or_benchmark_data_mounted': False, 'only_authored_mechanical_fixtures': True}
    (path / 'execution-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    if not result['complete']:
        print(stderr.decode(errors='replace')[-2000:])
        raise RuntimeError('Role stage failed; preserve evidence, no partial scoring')
    return result


def main():
    assert os.getuid() == 1000 and Path.cwd() == ROOT
    OUT.mkdir(mode=0o700, parents=True, exist_ok=False)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (150, 150))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))
    started = time.monotonic()
    results = [phase(role) for role in ('prepare', 'plan', 'left-predict', 'right-predict', 'score')]
    summary = {'complete': True, 'stages': results, 'elapsed_seconds': time.monotonic() - started,
        'historical_or_rival_quality_experiment_performed': False, 'quality_claim_from_demo': False,
        'models_trained_only_from_authored_demo': True, 'existing_credentials_used': False,
        'per_stage_cpu_seconds': 120, 'per_stage_memory_mib': 512, 'per_stage_wall_seconds': 35,
        'aggregate_cgroup_guarantee': False}
    (OUT / 'execution-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
