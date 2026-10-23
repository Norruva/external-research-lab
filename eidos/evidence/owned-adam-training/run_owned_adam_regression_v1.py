"""Run checkout tests in offline namespaces with fresh temporary state only."""
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
OUTPUT = ROOT / 'evaluation/production/owned-adam-training-v1/regression-v1'
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
resource.setrlimit(resource.RLIMIT_CPU,(120,120))
resource.setrlimit(resource.RLIMIT_AS,(768*1024**2,768*1024**2))

source = r"""
import hashlib,json,pathlib,tempfile
from eidos.train_linear_support import build
with tempfile.TemporaryDirectory() as temporary:
 out=pathlib.Path(temporary)/'model'
 build('/work/examples/owned-support-demo.jsonl','/work/examples/owned-support-demo-provenance.json',out,'owned-regression-v1')
 print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir())},sort_keys=True))
"""
results={}
for name,directory in [('parent','/baseline'),('current','/work')]:
 result=subprocess.run([sys.executable,'-S','-B','-c',source],cwd=directory,stdin=subprocess.DEVNULL,capture_output=True,timeout=20)
 assert result.returncode==0,result.stderr.decode(errors='replace')[-1000:]
 results[name]=json.loads(result.stdout)
assert results['parent']==results['current']
value={'complete':True,'default_gradient_descent_artifacts_byte_identical_to_parent':True,
       'parent_commit':'3e9644c22f3d1f02f45be23a910c26f053982318','files':results,
       'scope':'fixed authored 40-row demo; same identity, input, default recipe and tested runtime',
       'reference_or_existing_model_used':False}
pathlib.Path('/output/regression-result.json').write_text(json.dumps(value,indent=2)+'\n')
print(json.dumps(value))

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
        '--dir','/work','--ro-bind',str(SOURCE),'/work',
        '--dir','/baseline','--ro-bind',str(ROOT/'evaluation/production/owned-adam-training-v1/baseline-code'),'/baseline','--dir','/output',
        '--bind',str(OUTPUT),'/output','--clearenv','--setenv','PATH','/usr/bin:/bin',
        '--setenv','LC_ALL','C.UTF-8','--setenv','HOME','/home/research','--chdir','/work',
        '--','/usr/bin/python3','-S','-B','-c',GUARD]
    command = ['/usr/bin/strace','-f','-qq','-e','trace=network',
        '-e','raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg','-o',str(OUTPUT/'network.trace'),*command]
    resource.setrlimit(resource.RLIMIT_CPU,(150,150))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024**2,64*1024**2))
    started=time.monotonic()
    process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,env={'PATH':'/usr/bin:/bin'},start_new_session=True)
    try:
        stdout,stderr=process.communicate(timeout=110)
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
        'authored_fixture_training_only':True,
        'memory_mib':768,'wall_seconds_limit':110,'per_process_cpu_seconds':120,
        'aggregate_cgroup_guarantee':False}
    (OUTPUT/'execution-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if not complete:print(stderr.decode(errors='replace')[-2000:])
    return 0 if complete else 1


if __name__=='__main__':raise SystemExit(main())
