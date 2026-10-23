"""Default is static verification. Data acquisition/processing needs scoped approval."""
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
STUDY = ROOT / 'research/dbpedia-cohort-v1'
OUTPUT = ROOT / 'evaluation/production/dbpedia-cohort-v1/v1'
PLAN = STUDY / 'plan.json'
PREPARED = STUDY / 'prepared-manifest-v1.json'
AUTH = STUDY / 'authorization-v1.json'
RUNTIME = Path('/opt/codex/runtimes/codex-primary-runtime/dependencies/python')
READER = ROOT / 'evaluation/production/dbpedia-reader-v1/v1/reader'
READER_MANIFEST = READER.parent / 'reader-manifest.json'
SCOPE = 'eidos-dbpedia-cohort-acquisition-preparation-v1'

GUARD = r'''
import errno,hashlib,json,os,pathlib,resource,runpy,socket,sys
stage=sys.argv[1]
assert stage in ('history','corpus') and os.getuid()==1000
assert sys.flags.no_site and sys.flags.dont_write_bytecode and not sys.flags.optimize
assert os.getcwd()=='/source' and {n for _,n in socket.if_nameindex()}=={'lo'}
assert all(not pathlib.Path(p).exists() for p in ('/workspace','/root','/run','/etc','/sys','/opt','/model','/deps','/application'))
assert set(os.environ)<={'PATH','LC_ALL','HOME','PWD','OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','ARROW_DEFAULT_MEMORY_POOL'}
assert all(os.environ[k]=='1' for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'))
resource.setrlimit(resource.RLIMIT_CPU,((90 if stage=='history' else 180),)*2)
resource.setrlimit(resource.RLIMIT_AS,((768*1024**2 if stage=='history' else 2*1024**3),)*2)
with socket.socket() as probe:
 try:probe.connect(('198.51.100.1',443))
 except OSError as error:assert error.errno==errno.ENETUNREACH
 else:raise AssertionError('Unexpected outbound route')
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1024**2),b''):h.update(block)
 return h.hexdigest()
pins=json.loads(pathlib.Path('/code-pins.json').read_bytes())
for name,pin in pins.items():
 p=pathlib.Path('/source')/name
 assert p.is_file() and not p.is_symlink() and sha(p)==pin
 assert os.statvfs(p).f_flag & os.ST_RDONLY
sys.path[:]=['/source']+[p for p in sys.path if p.startswith('/tooling/') or p.startswith('/usr/')]
if stage=='history':
 assert not any(pathlib.Path(p).exists() for p in ('/reader','/corpus','/fingerprints.json'))
else:
 assert not pathlib.Path('/history').exists()
 manifest=json.loads(pathlib.Path('/reader-manifest.json').read_bytes())
 paths=list(pathlib.Path('/reader').rglob('*'))
 assert not any(p.is_symlink() for p in paths)
 actual={p.relative_to('/reader').as_posix():p for p in paths if p.is_file()}
 assert set(actual)==set(manifest)
 for name,p in actual.items():
  assert os.statvfs(p).f_flag & os.ST_RDONLY
  assert p.stat().st_size==manifest[name]['bytes'] and sha(p)==manifest[name]['sha256']
 sys.path.insert(0,'/reader')
sys.argv=['/source/prepare.py',stage]
runpy.run_path('/source/prepare.py',run_name='__main__')
'''


def sha(path):
    p = Path(path)
    assert p.is_file() and not any(a.is_symlink() for a in (p, *p.parents))
    result = hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            result.update(block)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')


def verify_files(files):
    for name, pin in files.items():
        p = Path(name)
        assert p.stat().st_size == pin['bytes'] and sha(p) == pin['sha256']


def check():
    prepared, plan = read(PREPARED), read(PLAN)
    assert prepared['scope'] == plan['scope'] == SCOPE and plan['automatic_retry'] is False
    verify_files(prepared['files'])
    verify_files(prepared['external_files'])
    expected_code = {Path(name).name for name in prepared['files']
                     if Path(name).parent == STUDY and Path(name).suffix == '.py'}
    assert expected_code == {p.name for p in STUDY.glob('*.py')}
    history = read(STUDY / 'historical-source-manifest.json')
    verify_files({v['path']: v for v in history['files'].values()})
    reader_manifest = read(READER_MANIFEST)
    assert set(reader_manifest) == {p.relative_to(READER).as_posix() for p in READER.rglob('*') if p.is_file()}
    assert not any(p.is_symlink() for p in READER.rglob('*'))
    verify_files({str(READER / n): pin for n, pin in reader_manifest.items()})
    assert read(READER.parent / 'completion-review-v1.json')['complete'] is True
    assert plan['model_fit_prediction_scoring'] is False
    return prepared, plan, history


def stage(name, source, pins, mounts, wall, active):
    out = OUTPUT / name
    out.mkdir(mode=0o700)
    payload = out / 'data'
    payload.mkdir(mode=0o700)
    command = ['/usr/bin/bwrap', '--unshare-all', '--new-session', '--die-with-parent',
        '--uid', '1000', '--gid', '1000', '--cap-drop', 'ALL',
        '--ro-bind', '/usr/bin', '/usr/bin', '--ro-bind', '/usr/lib', '/usr/lib',
        '--ro-bind', '/usr/lib64', '/usr/lib64', '--symlink', 'usr/bin', '/bin',
        '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
        '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/home/research',
        '--ro-bind', str(RUNTIME / 'bin/python3.12'), '/tooling/python/bin/python3.12',
        '--ro-bind', str(RUNTIME / 'lib/libpython3.12.so.1.0'), '/tooling/python/lib/libpython3.12.so.1.0',
        '--ro-bind', str(RUNTIME / 'lib/python3.12'), '/tooling/python/lib/python3.12',
        '--tmpfs', '/tooling/python/lib/python3.12/site-packages',
        '--ro-bind', str(source), '/source', '--ro-bind', str(pins), '/code-pins.json',
        '--bind', str(payload), '/output']
    for origin, destination in mounts:
        command += ['--ro-bind', str(origin), destination]
    command += ['--clearenv']
    for key, value in {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8', 'HOME': '/home/research',
                       'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                       'ARROW_DEFAULT_MEMORY_POOL': 'system'}.items():
        command += ['--setenv', key, value]
    command += ['--chdir', '/source', '--', '/tooling/python/bin/python3.12', '-S', '-B', '-c', GUARD, name]
    command = ['/usr/bin/strace', '-f', '-qq', '-e', 'trace=network',
        '-e', 'raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg', '-o', str(out / 'network.trace'), *command]
    start = time.monotonic()
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
    (out / 'stdout.log').write_bytes(stdout)
    (out / 'stderr.log').write_bytes(stderr)
    ip = [s for s in (out / 'network.trace').read_text().splitlines() if 'connect(' in s and 'AF_INET' in s]
    probes = [s for s in ip if 'inet_addr("198.51.100.1")' in s and 'ENETUNREACH' in s]
    success = code == 0 and len(ip) == len(probes) == 1 and read(payload / 'result.json')['complete'] is True
    result = {'stage': name, 'complete': success, 'exit_code': code,
              'elapsed_seconds': time.monotonic() - start, 'expected_refused_preflights': len(probes),
              'unexpected_ip_connects': len(ip) - len(probes), 'wall_seconds_limit': wall}
    write(out / 'execution-summary.json', result)
    assert success, 'Incomplete stage; retain slot, do not retry'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-approved', action='store_true')
    args = parser.parse_args()
    assert Path.cwd() == ROOT and os.getuid() == 1000 and sys.flags.no_site and sys.flags.dont_write_bytecode
    assert not sys.flags.optimize and os.uname().sysname == 'Linux'
    prepared, plan, history = check()
    if not args.execute_approved:
        print(json.dumps({'complete': True, 'mode': 'static-check-only', 'scope': SCOPE,
              'plan_sha256': sha(PLAN), 'prepared_manifest_sha256': sha(PREPARED),
              'data_downloaded_or_processed': False, 'new_scoped_approval_required': True}))
        return 0
    auth = read(AUTH)
    assert set(auth) == {'scope', 'approved', 'plan_sha256', 'prepared_manifest_sha256',
                         'user_instruction_sha256', 'approved_at_utc'}
    assert auth['scope'] == SCOPE and auth['approved'] is True
    assert auth['plan_sha256'] == sha(PLAN) and auth['prepared_manifest_sha256'] == sha(PREPARED)
    assert type(auth['user_instruction_sha256']) is str and len(auth['user_instruction_sha256']) == 64
    OUTPUT.mkdir(mode=0o700, parents=True, exist_ok=False)
    active = [None]
    def deadline(_signum, _frame):
        if active[0] is not None and active[0].poll() is None:
            os.killpg(active[0].pid, signal.SIGKILL)
        raise TimeoutError('Whole one-attempt preparation deadline')
    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 900)
    resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024**2, 256 * 1024**2))
    start = time.monotonic()
    write(OUTPUT / 'prelaunch.json', {'plan_sha256': sha(PLAN), 'prepared_manifest_sha256': sha(PREPARED),
          'authorization_sha256': sha(AUTH), 'automatic_retry': False})
    try:
        source = OUTPUT / 'staged/source'
        source.mkdir(mode=0o700, parents=True)
        for p in STUDY.glob('*.py'):
            shutil.copyfile(p, source / p.name)
            (source / p.name).chmod(0o400)
        source.chmod(0o500)
        pins = OUTPUT / 'code-pins.json'
        write(pins, {p.name: sha(p) for p in source.iterdir()})
        acquired = OUTPUT / 'acquired'
        acquired.mkdir(mode=0o700)
        download = runpy.run_path(str(source / 'acquire.py'))['download']
        receipts = []
        for split in ('train', 'test'):
            receipts.append(download(split, plan['corpus'][split], acquired / (split + '.parquet')))
            write(OUTPUT / ('acquisition-' + split + '.json'), receipts[-1])
        mounts = [(STUDY / 'historical-source-manifest.json', '/history-manifest.json')]
        mounts += [(item['path'], '/history/' + name) for name, item in history['files'].items()]
        results = [stage('history', source, pins, mounts, 120, active)]
        mounts = [(READER, '/reader'), (READER_MANIFEST, '/reader-manifest.json'),
                  (acquired, '/corpus'), (PLAN, '/plan.json'),
                  (OUTPUT / 'history/data/fingerprints.json', '/fingerprints.json'),
                  (OUTPUT / 'history/data/result.json', '/history-result.json')]
        results.append(stage('corpus', source, pins, mounts, 300, active))
        check()
        for p in source.iterdir():
            assert sha(p) == read(pins)[p.name]
        result = {'complete': True, 'stages': results, 'elapsed_seconds': time.monotonic() - start,
                  'automatic_retry': False, 'model_fit_prediction_scoring': False,
                  'aggregate_cgroup_guarantee': False, 'mission_complete': False, 'production_certified': False}
        write(OUTPUT / 'execution-summary.json', result)
        print(json.dumps(result))
    except Exception as error:
        # Do not print redirect queries, credentials or corpus rows from errors.
        write(OUTPUT / 'failure.json', {'complete': False, 'error_type': type(error).__name__,
              'elapsed_seconds': time.monotonic() - start, 'retry_allowed': False})
        print(json.dumps({'complete': False, 'error_type': type(error).__name__, 'retry_allowed': False}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
