"""Default: static closure check. Real study requires its own exact approval."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import runpy
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path('/workspace/eidos')
STUDY = ROOT / 'research/dbpedia-study-v1'
OUTPUT = ROOT / 'evaluation/production/dbpedia-study-v1/v1'
PLAN = STUDY / 'plan.json'
PREPARED = STUDY / 'prepared-manifest-v1.json'
AUTH = STUDY / 'authorization-v1.json'
COHORT = ROOT / 'evaluation/production/dbpedia-cohort-v1/v1/corpus/data'
PYTHON = Path('/opt/codex/runtimes/codex-primary-runtime/dependencies/python')
SCOPE = 'eidos-dbpedia-independent-study-v1'
CONTROL_NAMES = ('contracts.py', 'study.py', 'tree_review.py')
LIMITS = {'memory': (90, 120, 768), 'linear_gd': (90, 120, 768),
          'linear_adam': (90, 120, 768), 'select': (90, 120, 768),
          'eidos': (60, 90, 768), 'laya': (2760, 4200, 4096), 'score': (120, 180, 768)}
GUARD = r'''
import errno,hashlib,json,os,pathlib,resource,runpy,socket,sys
name,script,native=sys.argv[1],sys.argv[2],sys.argv[3]=='1'
assert sys.flags.no_site and sys.flags.dont_write_bytecode and not sys.flags.optimize
assert os.getuid()==1000 and os.getcwd()=='/controls' and {n for _,n in socket.if_nameindex()}=={'lo'}
assert script in ('study.py','authored_reference_fixture.py','test_contracts.py')
assert not any(pathlib.Path(p).exists() for p in ('/workspace','/root','/run','/etc','/sys','/opt','/reader','/corpus'))
assert set(os.environ)<={'PATH','LC_ALL','HOME','PWD','HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY','TOKENIZERS_PARALLELISM','OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','MALLOC_ARENA_MAX'}
freeze=json.loads(pathlib.Path('/freeze.json').read_bytes())
resource.setrlimit(resource.RLIMIT_CPU,(freeze['cpu_seconds'],)*2)
resource.setrlimit(resource.RLIMIT_AS,(freeze['memory_mib']*1024**2,)*2)
with socket.socket() as probe:
 try:probe.connect(('198.51.100.1',443))
 except OSError as e:assert e.errno==errno.ENETUNREACH
 else:raise AssertionError('Unexpected outbound route')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024**2),b''):h.update(b)
 return h.hexdigest()
for name_on_disk,pin in freeze['files'].items():
 p=pathlib.Path(name_on_disk)
 assert p.is_file() and not p.is_symlink() and sha(p)==pin
 assert os.statvfs(p).f_flag & os.ST_RDONLY
sys.path[:]=['/controls']+[p for p in sys.path if p.startswith('/tooling/') or p.startswith('/usr/')]
from tree_review import verify_large_tree,validate_rights
verified={}
for destination,tree in freeze['trees'].items():
 assert os.statvfs(destination).f_flag & os.ST_RDONLY
 verified[destination]=verify_large_tree(destination,destination+'-manifest.json',tree['sha256'])['manifest']
if native:
 assert name=='laya' and script=='study.py' and not pathlib.Path('/owned-app').exists()
 assert not any(pathlib.Path(p).exists() for p in ('/selection.json','/selected-registry','/models','/data/test-gold.jsonl','/data/development.jsonl','/data/support.jsonl','/eidos'))
 validate_rights(json.loads(pathlib.Path('/rights.json').read_bytes()),verified['/reference-model'],verified['/laya-source'])
 sys.path[:0]=['/deps','/legacy-app','/laya-source']
else:
 assert not any(pathlib.Path(p).exists() for p in ('/deps','/reference-model','/legacy-app','/laya-source','/rights.json'))
 sys.path.insert(0,'/owned-app')
if name in ('memory','linear_gd','linear_adam'):
 assert not any(pathlib.Path(p).exists() for p in ('/data/development.jsonl','/data/test-inputs.jsonl','/data/test-gold.jsonl','/selection.json'))
elif name=='select':
 assert not any(pathlib.Path(p).exists() for p in ('/data/test-inputs.jsonl','/data/test-gold.jsonl','/data/test-groups.jsonl'))
elif name=='eidos':
 assert not any(pathlib.Path(p).exists() for p in ('/data/test-gold.jsonl','/data/development.jsonl','/laya'))
elif name=='score':
 assert not any(pathlib.Path(p).exists() for p in ('/data/test-inputs.jsonl','/data/support.jsonl','/data/development.jsonl','/selected-registry','/models'))
if script=='authored_reference_fixture.py':
 assert not native and json.loads(pathlib.Path('/config.json').read_bytes()).get('authored_fixture_only') is True
sys.argv=['/controls/'+script]+([name] if script=='study.py' else [])
runpy.run_path('/controls/'+script,run_name='__main__')
'''


def sha(path):
    p = Path(path)
    assert p.is_file() and not any(a.is_symlink() for a in (p, *p.parents))
    value = hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')


def verify_files(files):
    for name, pin in files.items():
        assert Path(name).stat().st_size == pin['bytes'] and sha(name) == pin['sha256']


def check():
    prepared = read(PREPARED)
    assert prepared['scope'] == read(PLAN)['scope'] == SCOPE
    verify_files(prepared['files'])
    verify_files(prepared['external_files'])
    # Reviewed stdlib verifier only; imports no model/application/native package.
    helpers = runpy.run_path(str(STUDY / 'tree_review.py'))
    trees = {}
    for name, tree in prepared['trees'].items():
        trees[name] = helpers['verify_large_tree'](tree['root'], tree['manifest'], tree['sha256'])['manifest']
    helpers['validate_rights'](read(prepared['rights_file']), trees['/reference-model'], trees['/laya-source'])
    assert read(COHORT.parent.parent / 'completion-review-v1.json')['complete'] is True
    assert read(STUDY / 'config.json').get('authored_fixture_only') is False
    assert read(STUDY / 'config.json')['n'] == 280
    assert read(PLAN)['automatic_retry'] is False
    assert read(PLAN)['stage_limits'] == {k: list(v) for k, v in LIMITS.items()}
    return prepared


def role(name, root, controls, config, mounts, trees, limits, active, *, native=False, script='study.py'):
    """Shared execution boundary, also exercised using authored fixture inputs."""
    cpu, wall, memory = limits
    output = root / name
    output.mkdir(mode=0o700)
    payload = output / 'data'
    payload.mkdir(mode=0o700)
    files = {'/config.json': sha(config)}
    files.update({'/controls/' + p.name: sha(p) for p in controls.iterdir() if p.is_file()})
    for origin, destination in mounts:
        p = Path(origin)
        if p.is_file():
            files[destination] = sha(p)
        elif p.is_dir():
            files.update({destination + '/' + f.relative_to(p).as_posix(): sha(f)
                          for f in p.rglob('*') if f.is_file()})
        else:
            raise ValueError('Invalid role mount')
    freeze = output / 'freeze.json'
    write(freeze, {'files': files, 'trees': trees, 'cpu_seconds': cpu, 'memory_mib': memory})
    command = ['/usr/bin/bwrap', '--unshare-all', '--new-session', '--die-with-parent',
        '--uid', '1000', '--gid', '1000', '--cap-drop', 'ALL',
        '--ro-bind', '/usr/bin', '/usr/bin', '--ro-bind', '/usr/lib', '/usr/lib',
        '--ro-bind', '/usr/lib64', '/usr/lib64', '--symlink', 'usr/bin', '/bin',
        '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
        '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/home/research',
        '--ro-bind', str(PYTHON / 'bin/python3.12'), '/tooling/python/bin/python3.12',
        '--ro-bind', str(PYTHON / 'lib/libpython3.12.so.1.0'), '/tooling/python/lib/libpython3.12.so.1.0',
        '--ro-bind', str(PYTHON / 'lib/python3.12'), '/tooling/python/lib/python3.12',
        '--tmpfs', '/tooling/python/lib/python3.12/site-packages',
        '--ro-bind', str(controls), '/controls', '--ro-bind', str(config), '/config.json',
        '--ro-bind', str(freeze), '/freeze.json', '--bind', str(payload), '/output']
    for origin, destination in mounts:
        command += ['--ro-bind', str(origin), destination]
    for destination, tree in trees.items():
        command += ['--ro-bind', tree['root'], destination,
                    '--ro-bind', tree['manifest'], destination + '-manifest.json']
    command += ['--clearenv']
    environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8', 'HOME': '/home/research'}
    if native:
        environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                           TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
                           OPENBLAS_NUM_THREADS='1', MALLOC_ARENA_MAX='1')
    for key, value in environment.items():
        command += ['--setenv', key, value]
    command += ['--chdir', '/controls', '--', '/tooling/python/bin/python3.12', '-S', '-B', '-c',
                GUARD, name, script, '1' if native else '0']
    command = ['/usr/bin/strace', '-f', '-qq', '-e', 'trace=network',
        '-e', 'raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg', '-o', str(output / 'network.trace'), *command]
    started = time.monotonic()
    print(json.dumps({'stage_started': name}), flush=True)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={'PATH': '/usr/bin:/bin'}, start_new_session=True)
    active[0] = process
    try:
        stdout, stderr = process.communicate(timeout=wall)
        code = process.returncode
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=5)
        code = 124
    active[0] = None
    (output / 'stdout.log').write_bytes(stdout)
    (output / 'stderr.log').write_bytes(stderr)
    ip = [s for s in (output / 'network.trace').read_text().splitlines() if 'connect(' in s and 'AF_INET' in s]
    probes = [s for s in ip if 'inet_addr("198.51.100.1")' in s and 'ENETUNREACH' in s]
    result = read(payload / 'result.json') if (payload / 'result.json').exists() else {}
    success = code == 0 and len(probes) == len(ip) == 1 and (script == 'test_contracts.py' or result.get('complete') is True)
    summary = {'complete': success, 'stage': name, 'exit_code': code, 'elapsed_seconds': time.monotonic() - started,
               'expected_refused_preflights': len(probes), 'unexpected_ip_connects': len(ip) - len(probes),
               'cpu_seconds_limit': cpu, 'wall_seconds_limit': wall, 'memory_mib': memory,
               'native_reference_mounted': native, 'aggregate_cgroup_guarantee': False}
    write(output / 'execution-summary.json', summary)
    assert success, 'Incomplete stage; retain slot without retry or partial scoring'
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-approved', action='store_true')
    args = parser.parse_args()
    assert Path.cwd() == ROOT and os.getuid() == 1000 and os.uname().sysname == 'Linux'
    assert sys.flags.no_site and sys.flags.dont_write_bytecode and not sys.flags.optimize
    active = [None]
    started = time.monotonic()
    if args.execute_approved:
        def deadline(_signum, _frame):
            if active[0] is not None and active[0].poll() is None:
                os.killpg(active[0].pid, signal.SIGKILL)
            raise TimeoutError('Whole operation deadline; no automatic retry')
        signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, 5400)
        resource.setrlimit(resource.RLIMIT_CPU, (300, 3600))
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 4 * 1024**3))
    prepared = check()
    if not args.execute_approved:
        print(json.dumps({'complete': True, 'mode': 'static-check-only', 'scope': SCOPE,
              'plan_sha256': sha(PLAN), 'prepared_manifest_sha256': sha(PREPARED),
              'real_training_reference_prediction_or_scoring': False, 'new_scoped_approval_required': True}))
        return 0
    auth = read(AUTH)
    assert set(auth) == {'scope', 'approved', 'plan_sha256', 'prepared_manifest_sha256',
                         'user_instruction_sha256', 'approved_at_utc'}
    assert auth['scope'] == SCOPE and auth['approved'] is True
    assert auth['plan_sha256'] == sha(PLAN) and auth['prepared_manifest_sha256'] == sha(PREPARED)
    assert type(auth['user_instruction_sha256']) is str and len(auth['user_instruction_sha256']) == 64
    OUTPUT.mkdir(mode=0o700, parents=True, exist_ok=False)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2,)*2)
    write(OUTPUT / 'prelaunch.json', {'plan_sha256': sha(PLAN), 'prepared_manifest_sha256': sha(PREPARED),
          'authorization_sha256': sha(AUTH), 'automatic_retry': False, 'planned_cpu_seconds': 3600,
          'whole_wall_seconds': 5400, 'aggregate_cgroup_guarantee': False})
    try:
        controls = OUTPUT / 'controls'
        controls.mkdir(mode=0o700)
        for name in CONTROL_NAMES:
            shutil.copyfile(STUDY / name, controls / name)
            (controls / name).chmod(0o400)
        controls.chmod(0o500)
        config = STUDY / 'config.json'
        summaries = []
        for name in LIMITS:
            check()
            mounts = []
            trees = {'/owned-app': prepared['trees']['/owned-app']}
            if name in ('memory', 'linear_gd', 'linear_adam'):
                mounts += [(COHORT / p, '/data/' + p) for p in ('support.jsonl', 'provenance.json')]
            elif name == 'select':
                mounts += [(COHORT / 'development.jsonl', '/data/development.jsonl')]
                mounts += [(OUTPUT / p / 'data/registry', '/models/' + p) for p in ('memory', 'linear_gd', 'linear_adam')]
            elif name == 'eidos':
                selection = OUTPUT / 'select/data/result.json'
                chosen = read(selection)['selected']
                assert chosen in ('memory', 'linear_gd', 'linear_adam')
                mounts += [(selection, '/selection.json'), (OUTPUT / chosen / 'data/registry', '/selected-registry'),
                           (COHORT / 'test-inputs.jsonl', '/data/test-inputs.jsonl')]
            elif name == 'laya':
                trees = {k: t for k, t in prepared['trees'].items() if k != '/owned-app'}
                mounts += [(COHORT / 'test-inputs.jsonl', '/data/test-inputs.jsonl'),
                           (Path(prepared['rights_file']), '/rights.json')]
            else:
                mounts += [(COHORT / p, '/data/' + p) for p in ('test-gold.jsonl', 'test-groups.jsonl')]
                mounts += [(OUTPUT / 'select/data/result.json', '/selection.json')]
                mounts += [(OUTPUT / p / 'data', '/' + p) for p in ('eidos', 'laya')]
            summaries.append(role(name, OUTPUT, controls, config, mounts, trees, LIMITS[name], active, native=name == 'laya'))
        check()
        write(OUTPUT / 'execution-summary.json', {'complete': True, 'stages': summaries,
              'elapsed_seconds': time.monotonic() - started, 'automatic_retry': False,
              'all_seven_stages_complete': True, 'aggregate_cgroup_guarantee': False,
              'mission_complete': False, 'production_certified': False})
        print(json.dumps({'complete': True, 'stages': len(summaries), 'mission_complete': False}))
    except Exception as error:
        write(OUTPUT / 'failure.json', {'complete': False, 'error_type': type(error).__name__,
              'elapsed_seconds': time.monotonic() - started, 'automatic_retry_allowed': False,
              'score_complete': (OUTPUT / 'score/data/result.json').exists()})
        print(json.dumps({'complete': False, 'error_type': type(error).__name__, 'automatic_retry_allowed': False}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
