"""Validate sparse training source with authored fixtures in an offline namespace."""
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time

ROOT = Path('/workspace/eidos')
SOURCE = Path('/workspace/external-research-lab/eidos')
OUTPUT = ROOT / 'evaluation/production/sparse-training-v1/validation-v1'
GUARD = r'''
import errno,hashlib,json,os,pathlib,resource,socket,subprocess,sys
assert sys.flags.no_site and sys.flags.dont_write_bytecode and sys.version_info>=(3,12)
assert os.getuid()==1000 and os.getcwd()=='/work'
assert set(os.environ)<={'PATH','LC_ALL','HOME','PWD'}
assert {n for _,n in socket.if_nameindex()}=={'lo'}
assert all(not pathlib.Path(p).exists() for p in ('/workspace','/root','/run','/etc','/sys','/opt','/deps','/dataset'))
with socket.socket() as probe:
 try:probe.connect(('198.51.100.1',443))
 except OSError as e:assert e.errno==errno.ENETUNREACH
 else:raise AssertionError('Unexpected external route')
resource.setrlimit(resource.RLIMIT_CPU,(180,180))
resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
commands=[('unit',[sys.executable,'-S','-B','-m','unittest','discover','-s','tests','-p','test_*.py','-v'],90),
 ('smoke-sparse',[sys.executable,'-S','-B','tools/smoke.py','--trainer','sparse'],45),
 ('scale-sparse',[sys.executable,'-S','-B','tools/sparse_scale_smoke.py'],240)]
results=[]
for name,command,timeout in commands:
 result=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=timeout)
 pathlib.Path('/output/'+name+'-stdout.json').write_bytes(result.stdout)
 pathlib.Path('/output/'+name+'-stderr.log').write_bytes(result.stderr)
 results.append({'name':name,'exit_code':result.returncode,
                 'stdout_sha256':hashlib.sha256(result.stdout).hexdigest(),
                 'stderr_sha256':hashlib.sha256(result.stderr).hexdigest()})
 if result.returncode:break
value={'complete':len(results)==3 and all(v['exit_code']==0 for v in results),
       'python':sys.version.split()[0],'stages':results,'offline_namespace':True,
       'fresh_temporary_state_only':True}
pathlib.Path('/output/runner-result.json').write_text(json.dumps(value,indent=2)+'\n')
print(json.dumps(value));raise SystemExit(0 if value['complete'] else 1)
'''


def tree():
    files = list(SOURCE.rglob('*'))
    assert all(not p.is_symlink() for p in files)
    return {str(p.relative_to(SOURCE)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files if p.is_file()}


def main():
    assert os.getuid() == 1000 and Path.cwd() == ROOT
    OUTPUT.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = tree()
    command = ['/usr/bin/bwrap','--unshare-all','--new-session','--die-with-parent',
        '--uid','1000','--gid','1000','--cap-drop','ALL',
        '--ro-bind','/usr/bin','/usr/bin','--ro-bind','/usr/lib','/usr/lib',
        '--ro-bind','/usr/lib64','/usr/lib64','--symlink','usr/bin','/bin',
        '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
        '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/home/research',
        '--dir','/work','--ro-bind',str(SOURCE),'/work','--dir','/output',
        '--bind',str(OUTPUT),'/output','--clearenv','--setenv','PATH','/usr/bin:/bin',
        '--setenv','LC_ALL','C.UTF-8','--setenv','HOME','/home/research','--chdir','/work',
        '--','/usr/bin/python3','-S','-B','-c',GUARD]
    command = ['/usr/bin/strace','-f','-qq','-e','trace=network',
        '-e','raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg','-o',str(OUTPUT/'network.trace'),*command]
    resource.setrlimit(resource.RLIMIT_CPU,(180,180))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024**2,64*1024**2))
    started=time.monotonic()
    process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,env={'PATH':'/usr/bin:/bin'},start_new_session=True)
    try:
        stdout,stderr=process.communicate(timeout=390)
        code=process.returncode
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL)
        stdout,stderr=process.communicate(timeout=5)
        code=124
    (OUTPUT/'stdout.json').write_bytes(stdout)
    (OUTPUT/'stderr.log').write_bytes(stderr)
    after=tree()
    trace=(OUTPUT/'network.trace').read_text()
    ip=[line for line in trace.splitlines() if 'connect(' in line and 'AF_INET' in line]
    probes=[line for line in ip if 'inet_addr("198.51.100.1")' in line and 'ENETUNREACH' in line]
    local=[line for line in ip if 'inet_addr("127.0.0.1")' in line]
    unexpected=len(ip)-len(probes)-len(local)
    complete=code==0 and before==after and len(probes)==1 and unexpected==0
    result={'complete':complete,'exit_code':code,'elapsed_seconds':time.monotonic()-started,
        'source_files_verified':len(before),'source_unchanged':before==after,
        'source_manifest_sha256':hashlib.sha256(json.dumps(before,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'expected_refused_preflights':len(probes),'private_loopback_connect_attempts':len(local),
        'unexpected_ip_connects':unexpected,'existing_credentials_corpora_or_models_mounted':False,
        'historical_or_rival_quality_experiment_performed':False,
        'authored_fixture_training_prediction_and_metric_scoring_performed':True,
        'memory_mib':2048,'wall_seconds_limit':390,'per_process_cpu_seconds':180,
        'aggregate_cgroup_guarantee':False}
    (OUTPUT/'execution-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if not complete:print(stderr.decode(errors='replace')[-2000:])
    return 0 if complete else 1


if __name__=='__main__':raise SystemExit(main())
