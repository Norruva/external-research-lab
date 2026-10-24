"""Bounded offline original-v4 validation and a single fresh challenge study."""
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

ROOT=Path('/workspace/eidos');SOURCE=Path('/workspace/external-research-lab/eidos')
BASE=ROOT/'evaluation/production/routed-candidate-v1'
CONTROL=ROOT/'routed_candidate_control_v2.py'
PROTOCOL=ROOT/'research/68-question-routed-resource-revision.md'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stage',required=True,choices=('units','mechanics','generate','preflight','train','predict','score','serving','resourceprobe'))
    parser.add_argument('--attempt',default='v1')
    args=parser.parse_args();os.umask(0o077)
    assert Path.cwd()==ROOT and os.getuid()==1000 and os.uname().sysname=='Linux'
    assert args.attempt.isascii() and args.attempt.replace('-','').isalnum()
    study=args.stage not in ('units','mechanics')
    if study:assert args.attempt==('v2' if args.stage=='preflight' else 'v1'),'Only the declared single revised study stage exists'
    BASE.mkdir(mode=0o700,exist_ok=True)
    out=BASE/(args.stage+'-'+args.attempt);out.mkdir(mode=0o700)
    app=out/'app';app.mkdir(mode=0o700);payload=out/'data';payload.mkdir(mode=0o700)
    paths=list((SOURCE/'eidos').glob('*.py'))+[SOURCE/'eidos/decision-api.schema.json']
    if args.stage=='units':
        paths+=list((SOURCE/'tests').glob('test_decision*.py'))+list((SOURCE/'tests').glob('test_contextual*.py'))+list((SOURCE/'tests').glob('test_routed*.py'))
    if args.stage in ('units','mechanics','serving'):
        paths+=[SOURCE/'web'/name for name in ('decision.html','decision.js','style.css')]
    if args.stage in ('mechanics','serving'):paths.append(SOURCE/'tools/routed_smoke.py')
    if args.stage=='serving':paths.append(SOURCE/'tools/routed_serving_smoke.py')
    for path in paths:
        assert path.is_file() and not path.is_symlink()
        target=app/path.relative_to(SOURCE);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target);target.chmod(0o400)
    pins={p.relative_to(app).as_posix():sha(p) for p in app.rglob('*') if p.is_file()}
    if study:
        prepared=load(BASE/'prepared-manifest-v2.json')
        assert prepared['runner_sha256']==sha(Path(__file__)) and prepared['control_sha256']==sha(CONTROL)
        assert prepared['protocol_sha256']==sha(PROTOCOL)
        assert prepared['exposure_sha256']==sha(BASE/'exposure-hashes-v1.json')
        assert {name:pin for name,pin in pins.items() if name.startswith('eidos/')}==prepared['core_sources']
        if args.stage=='serving':
            for name in ('tools/routed_smoke.py','tools/routed_serving_smoke.py'):
                assert pins[name]==prepared['verification_sources'][name]
    extra=[];fit_epochs=None
    if args.stage=='generate':
        folder=out/'exposure';folder.mkdir(mode=0o700)
        shutil.copyfile(BASE/'exposure-hashes-v1.json',folder/'hashes.json')
        extra.append((folder,'/exposure'))
    if args.stage in ('preflight','train','resourceprobe'):
        assert load(BASE/'generate-v1/execution-summary.json')['complete']
        folder=out/'training';folder.mkdir(mode=0o700)
        for name in ('training.jsonl','provenance.json'):
            shutil.copyfile(BASE/'generate-v1/data/corpus'/name,folder/name)
            (folder/name).chmod(0o400)
        extra.append((folder,'/training'))
        if args.stage=='preflight':
            assert load(BASE/'resourceprobe-v1/execution-summary.json')['complete']
            extra.append((BASE/'resourceprobe-v1/data','/resourceprobe'))
        if args.stage=='train':
            p=load(BASE/'preflight-v2/data/preflight-result.json')
            assert p['complete'] and p['epochs'] in (16,8,4,2,1);fit_epochs=p['epochs']
    elif args.stage=='predict':
        assert load(BASE/'train-v1/execution-summary.json')['complete']
        extra.append((BASE/'train-v1/data/model','/model'))
        folder=out/'requests';folder.mkdir(mode=0o700)
        shutil.copyfile(BASE/'generate-v1/data/requests.jsonl',folder/'requests.jsonl')
        extra.append((folder,'/requests'))
    elif args.stage=='serving':
        assert load(BASE/'train-v1/execution-summary.json')['complete']
        extra.append((BASE/'train-v1/data/model','/model'))
    elif args.stage=='score':
        assert load(BASE/'predict-v1/execution-summary.json')['complete']
        for name,mount,source in [
            ('gold.jsonl','/gold',BASE/'generate-v1/data/gold.jsonl'),
            ('predictions.jsonl','/predictions',BASE/'predict-v1/data/predictions.jsonl')]:
            folder=out/mount[1:];folder.mkdir(mode=0o700);shutil.copyfile(source,folder/name)
            extra.append((folder,mount))
    mounts={'/app':pins}
    for host,target in extra:
        assert not any(p.is_symlink() for p in (host,*host.parents))
        mounts[target]={p.relative_to(host).as_posix():sha(p) for p in host.rglob('*') if p.is_file()}
    cpu,wall=(600,900) if args.stage in ('train','mechanics','units') else (120,180)
    freeze={'format':'eidos-contextual-candidate-role-freeze-v1','stage':args.stage,'mounts':mounts,
        'control_sha256':sha(CONTROL),'runner_sha256':sha(Path(__file__)),'cpu_seconds':cpu,
        'wall_seconds':wall,'memory_mib':768,'fit_epochs':fit_epochs,
        'execution_basis':'Original authored implementation verification/fresh synthetic diagnostic under user direction to build EIDOS; no consumed real-study allowance reused.',
        'epoch_rule':'largest_predeclared_16_8_4_2_1_within_v4_12b_work_and_probe_reserve_before_quality',
        'learning_rate':.005,'seed':113,'old_benchmark_reference_or_vendor_used':False}
    (out/'freeze.json').write_text(json.dumps(freeze,sort_keys=True,indent=2)+'\n')
    command=['/usr/bin/bwrap','--unshare-all','--new-session','--die-with-parent','--uid','1000','--gid','1000','--cap-drop','ALL',
        '--ro-bind','/usr/bin','/usr/bin','--ro-bind','/usr/lib','/usr/lib','--ro-bind','/usr/lib64','/usr/lib64',
        '--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
        '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/home/research',
        '--ro-bind',str(app),'/app','--ro-bind',str(CONTROL),'/control.py',
        '--ro-bind',str(out/'freeze.json'),'/freeze.json','--bind',str(payload),'/output']
    for host,target in extra:command.extend(['--ro-bind',str(host),target])
    command.extend(['--clearenv','--setenv','PATH','/usr/bin:/bin','--setenv','HOME','/home/research',
        '--setenv','LC_ALL','C.UTF-8','--chdir','/app','--','/usr/bin/python3','-S','-B','/control.py'])
    command=['/usr/bin/strace','-f','-qq','-e','trace=network',
        '-e','raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg','-o',str(out/'network.trace'),*command]
    before=resource.getrusage(resource.RUSAGE_CHILDREN);started=time.monotonic()
    child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                           env={'PATH':'/usr/bin:/bin'},start_new_session=True)
    try:stdout,stderr=child.communicate(timeout=wall);code=child.returncode
    except subprocess.TimeoutExpired:
        os.killpg(child.pid,signal.SIGKILL);stdout,stderr=child.communicate(timeout=5);code=124
    (out/'stdout.log').write_bytes(stdout);(out/'stderr.log').write_bytes(stderr)
    trace=(out/'network.trace').read_text(errors='replace')
    calls=[line for line in trace.splitlines() if 'connect(' in line and 'AF_INET' in line]
    probes=[line for line in calls if '198.51.100.1' in line and 'ENETUNREACH' in line]
    loopback=[line for line in calls if '127.0.0.1' in line]
    unexpected=[line for line in calls if line not in probes and line not in loopback]
    unchanged=all(sha(app/name)==pin for name,pin in pins.items()) and sha(CONTROL)==freeze['control_sha256']
    if len(probes)!=1 or unexpected or not unchanged:code=code or 1
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    result={'format':'eidos-contextual-candidate-role-execution-v1','complete':code==0,'stage':args.stage,
        'exit_code':code,'elapsed_seconds':time.monotonic()-started,
        'waited_child_CPU_seconds_not_all_descendants':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
        'peak_waited_child_RSS_kib':after.ru_maxrss,'expected_refused_probes':len(probes),
        'loopback_connections':len(loopback),'unexpected_external_IP_connections':len(unexpected),
        'staged_sources_unchanged':unchanged,'cpu_seconds_per_process':cpu,'wall_seconds':wall,
        'memory_mib_per_process':768,'aggregate_cgroup_guarantee':False,
        'prior_benchmark_reference_or_vendor_used':False}
    (out/'execution-summary.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    print(json.dumps(result));print(stderr.decode(errors='replace')[-6500:]);print(stdout.decode(errors='replace')[-1800:])
    return code


if __name__=='__main__':raise SystemExit(main())
