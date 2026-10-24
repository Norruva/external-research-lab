"""Default: verify the frozen closure only. Real processing needs exact approval.

One offline history/cohort/training/comparison attempt, without installation,
acquisition, recovery, fallback, resumption or partial scoring.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import runpy
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path('/workspace/eidos')
STUDY = ROOT / 'research/dbpedia-scaled-study-v1'
COHORT_SOURCE = ROOT / 'research/dbpedia-scaled-cohort-v1'
OUTPUT = ROOT / 'evaluation/production/dbpedia-scaled-study-v1/v1'
PLAN = STUDY / 'plan.json'
PREPARED = STUDY / 'prepared-manifest-v1.json'
AUTH = STUDY / 'authorization-v1.json'
TEMPLATE = STUDY / 'config-template.json'
HISTORY = STUDY / 'historical-source-manifest.json'
EXPOSURES = STUDY / 'exposure-source-manifest.json'
PYTHON = Path('/opt/codex/runtimes/codex-primary-runtime/dependencies/python')
READER = ROOT / 'evaluation/production/dbpedia-reader-v1/v1/reader'
READER_MANIFEST = READER.parent / 'reader-manifest.json'
SCOPE = 'eidos-dbpedia-scaled-study-v1'
WHOLE_SECONDS = 10800
PLANNED_CPU_SECONDS = 7200
STAGES = ('history', 'corpus', 'train', 'development', 'eidos', 'laya', 'score')
LIMITS = {'history': (90, 120, 768), 'corpus': (600, 900, 2048),
          'train': (600, 900, 2048), 'development': (120, 180, 768),
          'eidos': (120, 180, 768), 'laya': (4800, 7200, 4096),
          'score': (180, 300, 768)}
COHORT_FILES = {'training.jsonl', 'development.jsonl', 'test-inputs.jsonl',
                'test-gold.jsonl', 'test-groups.jsonl', 'labels.json', 'provenance.json'}
SHA = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
PYTHON_SHA = 'fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7'
LIBPYTHON_SHA = '7e177a680eabb11fcb7d3867f7a1b9c4b93a9bd5b18129ce1af51ec07977b024'
READER_MANIFEST_SHA = '75ab93d4d71f90c6e2b6fe1197faf0270f6279da9d634e5742a94524911d0334'
TREE_REVIEW_SHA = '4e863c06c68a041c03f1ef53699a524a0535978a40d8ca27edb7571286938509'

GUARD = r'''
import errno,hashlib,json,os,pathlib,resource,runpy,socket,sys
name,script,native=sys.argv[1],sys.argv[2],sys.argv[3]=='1'
assert sys.flags.no_site and sys.flags.dont_write_bytecode and not sys.flags.optimize
assert os.getuid()==1000 and os.getcwd()=='/controls'
assert {n for _,n in socket.if_nameindex()}=={'lo'}
assert script in ('study.py','prepare.py','test_cohort.py','test_contracts.py',
                  'authored_cohort_fixture.py','authored_reference_fixture.py')
assert not any(pathlib.Path(p).exists() for p in ('/workspace','/root','/run','/etc','/sys','/opt'))
assert set(os.environ)<={'PATH','LC_ALL','HOME','PWD','HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE',
 'HF_HUB_DISABLE_TELEMETRY','TOKENIZERS_PARALLELISM','OMP_NUM_THREADS','MKL_NUM_THREADS',
 'OPENBLAS_NUM_THREADS','MALLOC_ARENA_MAX','ARROW_DEFAULT_MEMORY_POOL'}
assert all(os.environ[k]=='1' for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'))
freeze=json.loads(pathlib.Path('/freeze.json').read_bytes())
resource.setrlimit(resource.RLIMIT_CPU,(freeze['cpu_seconds'],)*2)
resource.setrlimit(resource.RLIMIT_AS,(freeze['memory_mib']*1024**2,)*2)
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
with socket.socket() as probe:
 try:probe.connect(('198.51.100.1',443))
 except OSError as e:assert e.errno==errno.ENETUNREACH
 else:raise AssertionError('Unexpected outbound route')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024**2),b''):h.update(b)
 return h.hexdigest()
for location,pin in freeze['files'].items():
 p=pathlib.Path(location)
 assert p.is_file() and not any(a.is_symlink() for a in (p,*p.parents)) and sha(p)==pin
 assert os.statvfs(p).f_flag & os.ST_RDONLY
assert {p.name for p in pathlib.Path('/controls').iterdir()}==set(freeze['control_names'])
sys.path[:]=['/controls']+[p for p in sys.path if p.startswith('/tooling/') or p.startswith('/usr/')]
verified={}
if freeze['trees']:
 from tree_review import verify_large_tree,validate_rights
 for destination,tree in freeze['trees'].items():
  assert os.statvfs(destination).f_flag & os.ST_RDONLY
  verified[destination]=verify_large_tree(destination,destination+'-manifest.json',tree['sha256'])['manifest']
cfg=json.loads(pathlib.Path('/config.json').read_bytes())
authored=cfg.get('authored_fixture_only') is True
if script in ('authored_cohort_fixture.py','authored_reference_fixture.py','test_cohort.py','test_contracts.py'):
 assert authored and not native
elif script=='prepare.py':
 assert name in ('history','corpus') and not native
else:
 assert script=='study.py' and name in ('train','development','eidos','laya','score')
if native:
 assert name=='laya' and script=='study.py' and not authored
 assert not pathlib.Path('/owned-app').exists()
 assert set(verified)=={'/deps','/legacy-app','/laya-source','/reference-model'}
 validate_rights(json.loads(pathlib.Path('/rights.json').read_bytes()),verified['/reference-model'],verified['/laya-source'])
 sys.path[:0]=['/deps','/legacy-app','/laya-source']
else:
 assert not any(pathlib.Path(p).exists() for p in ('/deps','/reference-model','/legacy-app','/laya-source','/rights.json'))
 if pathlib.Path('/owned-app').exists():sys.path.insert(0,'/owned-app')
if name=='history':
 assert script=='prepare.py' and not any(pathlib.Path(p).exists() for p in
  ('/reader','/corpus','/owned-app','/fingerprints.json','/exposure.json','/registry','/training-result.json','/data'))
elif name=='corpus':
 assert script=='prepare.py' and not any(pathlib.Path(p).exists() for p in
  ('/history','/exposure-sources','/owned-app','/registry','/training-result.json','/data'))
 manifest=json.loads(pathlib.Path('/reader-manifest.json').read_bytes())
 paths=list(pathlib.Path('/reader').rglob('*'))
 assert not any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in paths)
 actual={p.relative_to('/reader').as_posix():p for p in paths if p.is_file()}
 assert len(actual)==752 and set(actual)==set(manifest)
 for key,p in actual.items():
  assert os.statvfs(p).f_flag & os.ST_RDONLY
  assert p.stat().st_size==manifest[key]['bytes'] and sha(p)==manifest[key]['sha256']
 sys.path.insert(0,'/reader')
elif name in ('train','development','eidos','laya','score'):
 assert not any(pathlib.Path(p).exists() for p in ('/reader','/corpus','/history','/exposure-sources'))
 if name=='train':
  assert not any(pathlib.Path(p).exists() for p in ('/registry','/training-result.json','/data/development.jsonl',
   '/data/test-inputs.jsonl','/data/test-gold.jsonl','/data/test-groups.jsonl','/eidos','/laya'))
 elif name=='development':
  assert not any(pathlib.Path(p).exists() for p in ('/data/training.jsonl','/data/test-inputs.jsonl',
   '/data/test-gold.jsonl','/data/test-groups.jsonl','/eidos','/laya'))
 elif name=='eidos':
  assert not any(pathlib.Path(p).exists() for p in ('/data/training.jsonl','/data/development.jsonl',
   '/data/test-gold.jsonl','/data/test-groups.jsonl','/laya'))
 elif name=='laya':
  assert not any(pathlib.Path(p).exists() for p in ('/registry','/training-result.json','/primary-freeze.json',
   '/data/training.jsonl','/data/development.jsonl','/data/test-gold.jsonl','/data/test-groups.jsonl','/eidos'))
 elif name=='score':
  assert not any(pathlib.Path(p).exists() for p in ('/registry','/data/training.jsonl',
   '/data/development.jsonl','/data/test-inputs.jsonl','/reader','/corpus'))
if script=='authored_reference_fixture.py':assert name=='laya' and not pathlib.Path('/registry').exists()
os.umask(0o077)
sys.argv=['/controls/'+script]+([name] if script in ('study.py','prepare.py') else [])
runpy.run_path('/controls/'+script,run_name='__main__')
'''


def sha(path):
    p = Path(path)
    assert p.is_file() and not any(a.is_symlink() for a in (p, *p.parents))
    result = hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            result.update(block)
    return result.hexdigest()


def read(path, maximum=4 * 1024**2):
    p = Path(path)
    assert p.is_file() and not any(a.is_symlink() for a in (p, *p.parents))
    assert 0 < p.stat().st_size <= maximum
    def unique(items):
        result = {}
        for key, value in items:
            assert key not in result
            result[key] = value
        return result
    return json.loads(p.read_bytes(), object_pairs_hook=unique,
                      parse_constant=lambda _v: (_ for _ in ()).throw(ValueError('Nonfinite metadata')))


def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')


def verify_files(files):
    assert type(files) is dict and files
    for name, pin in files.items():
        assert type(pin) is dict and type(pin.get('bytes')) is int and pin['bytes'] >= 0
        assert type(pin.get('sha256')) is str and SHA.fullmatch(pin['sha256'])
        assert Path(name).stat().st_size == pin['bytes'] and sha(name) == pin['sha256']


def validate_template(template, plan):
    expected = {'format', 'authored_fixture_only', 'cohort_files', 'cohort_readiness_sha256',
                'development_per_class', 'development_policy', 'labels', 'max_total_windows',
                'max_windows_per_case', 'model_promoted_or_acceptance_enabled', 'n',
                'paired_replicates', 'paired_seed', 'prompt', 'recipe', 'reference',
                'reference_aliases', 'test_per_class', 'train_per_class'}
    assert type(template) is dict and set(template) == expected
    assert template['format'] == 'eidos-dbpedia-scaled-study-config-v1'
    assert template['authored_fixture_only'] is False
    assert template['cohort_files'] is None and template['cohort_readiness_sha256'] is None
    assert template['recipe'] == plan['recipe'] == {'epochs': 3, 'seed': 113}
    assert template['n'] == plan['comparison']['n'] == 1400
    assert template['train_per_class'] == plan['selection']['training_per_class'] == 1000
    assert template['development_per_class'] == plan['selection']['development_per_class'] == 100
    assert template['test_per_class'] == plan['selection']['test_per_class'] == 100
    assert template['max_total_windows'] == plan['comparison']['max_total_windows'] == 2100
    assert template['max_windows_per_case'] == plan['comparison']['max_windows_per_case'] == 16
    assert template['paired_replicates'] == plan['comparison']['replicates'] == 2000
    assert template['paired_seed'] == plan['comparison']['seed'] == 113
    assert template['reference'] == plan['comparison']['reference']
    assert template['development_policy'] == plan['development_policy'] == 'diagnostic_only_no_selection_or_quality_based_stop'
    assert template['model_promoted_or_acceptance_enabled'] is False
    labels = template['labels']
    assert labels == sorted(('Company', 'EducationalInstitution', 'Artist', 'Athlete',
        'OfficeHolder', 'MeanOfTransportation', 'Building', 'NaturalPlace', 'Village',
        'Animal', 'Plant', 'Album', 'Film', 'WrittenWork'))
    aliases = template['reference_aliases']
    assert aliases == {'Album': 'album', 'Animal': 'animal', 'Artist': 'artist', 'Athlete': 'athlete',
        'Building': 'building', 'Company': 'company', 'EducationalInstitution': 'educational_institution',
        'Film': 'film', 'MeanOfTransportation': 'mean_of_transportation', 'NaturalPlace': 'natural_place',
        'OfficeHolder': 'office_holder', 'Plant': 'plant', 'Village': 'village', 'WrittenWork': 'written_work'}
    assert template['reference'] == {'source_commit': 'ca5045a3f1fac06fc6daa5cf3371110f322f5255',
        'model_revision': '1c5edc17a7acd8701df6fc341c0d179f1c62c982',
        'weight_sha256': '891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c'}
    assert template['prompt'] == 'Classify this encyclopedia article into one of the offered categories.'
    for name in ('development_per_class', 'max_total_windows', 'max_windows_per_case', 'n',
                 'paired_replicates', 'paired_seed', 'test_per_class', 'train_per_class'):
        assert type(template[name]) is int
    assert all(type(value) is int for value in template['recipe'].values())


def check():
    """Opaque hashing plus metadata/source closure checks; never parse cases."""
    prepared, plan = read(PREPARED), read(PLAN)
    assert prepared['format'] == 'eidos-dbpedia-scaled-study-prepared-v1'
    assert prepared['scope'] == plan['scope'] == SCOPE
    assert plan['automatic_retry'] is plan['acquisition'] is plan['new_installation'] is False
    assert plan['stage_limits'] == {k: list(v) for k, v in LIMITS.items()}
    assert plan['resources']['proposed_planned_cpu_seconds'] == PLANNED_CPU_SECONDS
    assert plan['resources']['whole_wall_seconds'] == WHOLE_SECONDS
    assert set(prepared['trees']) == {'/owned-app', '/deps', '/legacy-app', '/laya-source', '/reference-model'}
    assert Path(prepared['trees']['/owned-app']['root']) == STUDY / 'owned-app'
    verify_files(prepared['files'])
    verify_files(prepared['external_files'])
    for source in (STUDY, COHORT_SOURCE):
        listed = {Path(p).name for p in prepared['files'] if Path(p).parent == source and Path(p).suffix == '.py'}
        actual = {p.name for p in source.glob('*.py')}
        assert listed and listed == actual
    required = {str(p) for p in (PLAN, TEMPLATE, HISTORY, EXPOSURES, STUDY / 'tree_review.py', Path(__file__))}
    assert required <= set(prepared['files'])
    assert prepared['rights_file'] in prepared['external_files']
    assert sha(PYTHON / 'bin/python3.12') == PYTHON_SHA
    assert sha(PYTHON / 'lib/libpython3.12.so.1.0') == LIBPYTHON_SHA
    assert sha(STUDY / 'tree_review.py') == TREE_REVIEW_SHA
    assert sha(READER_MANIFEST) == READER_MANIFEST_SHA
    manifest = read(READER_MANIFEST)
    reader_paths = list(READER.rglob('*'))
    assert not any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in reader_paths)
    assert len(manifest) == 752 and set(manifest) == {p.relative_to(READER).as_posix() for p in reader_paths if p.is_file()}
    verify_files({str(READER / name): pin for name, pin in manifest.items()})
    history, exposures = read(HISTORY), read(EXPOSURES)
    assert len(history['files']) == 40 and len(exposures['files']) == 3 and exposures['expected_rows'] == 840
    for group in (history, exposures):
        for name, item in group['files'].items():
            relative = PurePosixPath(name)
            assert relative.parts and not relative.is_absolute() and '..' not in relative.parts
            assert item['path'] in prepared['external_files']
            assert item['bytes'] == prepared['external_files'][item['path']]['bytes']
            assert item['sha256'] == prepared['external_files'][item['path']]['sha256']
    for split in ('train', 'test'):
        path, pin = plan['cached_corpus_paths'][split], plan['corpus'][split]
        assert path in prepared['external_files']
        assert prepared['external_files'][path] == {key: pin[key] for key in ('bytes', 'sha256')}
    # The reviewed verifier reads complete public file trees without imports.
    helpers = runpy.run_path(str(STUDY / 'tree_review.py'))
    trees = {name: helpers['verify_large_tree'](tree['root'], tree['manifest'], tree['sha256'])['manifest']
             for name, tree in prepared['trees'].items()}
    helpers['validate_rights'](read(prepared['rights_file']), trees['/reference-model'], trees['/laya-source'])
    validate_template(read(TEMPLATE), plan)
    return prepared, plan, history, exposures


def role(name, root, controls, config, mounts, trees, limits, active, *, native=False,
         script='study.py', expected_files=None):
    """Execute a frozen role, also usable with explicit authored fixture scripts."""
    cpu, wall, memory = limits
    assert type(cpu) is int and cpu > 0 and type(wall) is int and wall > 0 and type(memory) is int and memory > 0
    controls = Path(controls)
    paths = list(controls.iterdir())
    assert paths and len(paths) <= 64 and all(p.is_file() and not p.is_symlink() for p in paths)
    output = Path(root) / name
    output.mkdir(mode=0o700, exist_ok=False)
    payload = output / 'data'
    payload.mkdir(mode=0o700, exist_ok=False)
    files = {'/config.json': sha(config)}
    if expected_files is not None and str(config) in expected_files:
        assert Path(config).stat().st_size == expected_files[str(config)]['bytes']
        assert files['/config.json'] == expected_files[str(config)]['sha256']
    files.update({'/controls/' + p.name: sha(p) for p in paths})
    for origin, destination in mounts:
        assert type(destination) is str and destination.startswith('/') and '..' not in PurePosixPath(destination).parts
        assert not any(destination == x or destination.startswith(x + '/') for x in
                       ('/workspace', '/root', '/run', '/etc', '/sys', '/opt', '/tooling', '/controls', '/output', '/config.json', '/freeze.json'))
        p = Path(origin)
        if p.is_file():
            assert destination not in files
            files[destination] = sha(p)
            if expected_files is not None and str(p) in expected_files:
                assert p.stat().st_size == expected_files[str(p)]['bytes']
                assert files[destination] == expected_files[str(p)]['sha256']
        elif p.is_dir():
            all_paths = list(p.rglob('*'))
            assert not any(q.is_symlink() or not (q.is_file() or q.is_dir()) for q in all_paths)
            for f in all_paths:
                if f.is_file():
                    key = destination + '/' + f.relative_to(p).as_posix()
                    assert key not in files
                    files[key] = sha(f)
        else:
            raise ValueError('Invalid role mount')
    freeze = output / 'freeze.json'
    write(freeze, {'files': files, 'trees': trees, 'control_names': sorted(p.name for p in paths),
                   'cpu_seconds': cpu, 'memory_mib': memory})
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
    environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8', 'HOME': '/home/research',
                   'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                   'ARROW_DEFAULT_MEMORY_POOL': 'system'}
    if native:
        environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                           TOKENIZERS_PARALLELISM='false', MALLOC_ARENA_MAX='1')
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
        try:
            stdout, stderr = process.communicate(timeout=wall)
            code = process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
            code = 124
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
            raise
    finally:
        active[0] = None
    (output / 'stdout.log').write_bytes(stdout)
    (output / 'stderr.log').write_bytes(stderr)
    trace = (output / 'network.trace').read_text()
    ip = [line for line in trace.splitlines() if 'connect(' in line and 'AF_INET' in line]
    probes = [line for line in ip if 'inet_addr("198.51.100.1")' in line and 'ENETUNREACH' in line]
    sockets = [line for line in trace.splitlines() if 'socket(AF_INET' in line]
    result = read(payload / 'result.json') if (payload / 'result.json').exists() else {}
    unit = script in ('test_cohort.py', 'test_contracts.py')
    success = code == 0 and len(probes) == len(ip) == len(sockets) == 1 and (unit or result.get('complete') is True)
    summary = {'complete': success, 'stage': name, 'exit_code': code, 'elapsed_seconds': time.monotonic() - started,
               'expected_refused_preflights': len(probes), 'unexpected_ip_connects': len(ip) - len(probes),
               'unexpected_ip_socket_creations': max(0, len(sockets) - 1),
               'cpu_seconds_limit': cpu, 'wall_seconds_limit': wall, 'memory_mib': memory,
               'native_reference_mounted': native, 'aggregate_cgroup_guarantee': False}
    write(output / 'execution-summary.json', summary)
    assert success, 'Incomplete stage; retain slot without retry, fallback or partial scoring'
    return summary


def stage_controls(source, destination, expected=None):
    destination.mkdir(mode=0o700, exist_ok=False)
    paths = sorted(source.glob('*.py'))
    if expected is not None:
        names = {Path(name).name for name in expected
                 if Path(name).parent == source and Path(name).suffix == '.py'}
        assert names == {p.name for p in paths}
    for p in paths:
        assert p.is_file() and not p.is_symlink()
        if expected is not None:
            assert p.stat().st_size == expected[str(p)]['bytes'] and sha(p) == expected[str(p)]['sha256']
        shutil.copyfile(p, destination / p.name)
        if expected is not None:
            assert (destination / p.name).stat().st_size == expected[str(p)]['bytes']
            assert sha(destination / p.name) == expected[str(p)]['sha256']
        (destination / p.name).chmod(0o400)
    destination.chmod(0o500)
    return destination


def bind_cohort(data, template, plan, history_result_sha256):
    """Read aggregate readiness metadata; hash case/gold bytes opaquely only."""
    result_path = data / 'result.json'
    result = read(result_path)
    assert result['complete'] is True and result['plan_sha256'] == sha(PLAN)
    assert result['history_result_sha256'] == history_result_sha256
    assert result['role_rows'] == {'training': 14000, 'development': 1400, 'test': 1400}
    assert all(type(value) is int for value in result['role_rows'].values())
    assert result['quotas'] == plan['selection']['quotas'] == {'training': 1000, 'development': 100, 'test': 100}
    assert result['salt'] == plan['selection']['salt']
    assert result['model_fit_prediction_or_scoring'] is False
    assert result['selected_row_removal_replacement_truncation_or_resampling'] is False
    assert result['source_wide_eligibility_exclusions_precede_hash_ranking'] is True
    assert result['target_population_restricted_to_unique_unexposed_articles'] is True
    lineage = result['lineage']
    assert lineage['resolved_previous_exposure_cases'] == 840
    assert lineage['selected_rows'] == lineage['components'] == 16800
    assert lineage['max_component_rows'] == 1
    assert lineage['cross_role_components'] == lineage['historical_exact_overlaps'] == 0
    assert all(type(lineage[key]) is int for key in ('resolved_previous_exposure_cases',
        'selected_rows', 'components', 'max_component_rows', 'cross_role_components', 'historical_exact_overlaps'))
    assert lineage['semantic_template_speaker_or_pretraining_independence_verified'] is False
    assert set(result['files']) == COHORT_FILES
    assert {p.name for p in data.iterdir()} == COHORT_FILES | {'result.json'}
    files = {}
    for name, pin in result['files'].items():
        maximum = 32 * 1024**2 if name == 'training.jsonl' else 4 * 1024**2
        assert type(pin) is dict and set(pin) == {'bytes', 'sha256'}
        assert type(pin['bytes']) is int and 0 < pin['bytes'] <= maximum
        assert type(pin['sha256']) is str and SHA.fullmatch(pin['sha256'])
        path = data / name
        assert path.stat().st_size == pin['bytes'] and sha(path) == pin['sha256']
        files[name] = pin
    config = dict(template)
    config['cohort_files'] = files
    config['cohort_readiness_sha256'] = sha(result_path)
    return config


def authorization():
    auth = read(AUTH)
    assert set(auth) == {'scope', 'approved', 'plan_sha256', 'prepared_manifest_sha256',
                         'user_instruction_sha256', 'approved_at_utc'}
    assert auth['scope'] == SCOPE and auth['approved'] is True
    assert auth['plan_sha256'] == sha(PLAN) and auth['prepared_manifest_sha256'] == sha(PREPARED)
    assert type(auth['user_instruction_sha256']) is str and SHA.fullmatch(auth['user_instruction_sha256'])
    assert type(auth['approved_at_utc']) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', auth['approved_at_utc'])
    return auth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-approved', action='store_true')
    args = parser.parse_args()
    assert Path.cwd() == ROOT and os.getuid() == 1000 and os.uname().sysname == 'Linux'
    assert sys.flags.no_site and sys.flags.dont_write_bytecode and not sys.flags.optimize
    os.umask(0o077)
    active, started = [None], time.monotonic()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if args.execute_approved:
        # Approval and the one deadline precede even static input hashing.
        authorization()
        assert not OUTPUT.exists() and not OUTPUT.is_symlink()
        def deadline(_signum, _frame):
            if active[0] is not None and active[0].poll() is None:
                os.killpg(active[0].pid, signal.SIGKILL)
            raise TimeoutError('Whole single-attempt deadline; no retry or recovery')
        signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, WHOLE_SECONDS)
        resource.setrlimit(resource.RLIMIT_CPU, (300, PLANNED_CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 4096 * 1024**2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024**2,) * 2)
    prepared, plan, history, exposures = check()
    if not args.execute_approved:
        print(json.dumps({'complete': True, 'mode': 'static-check-only', 'scope': SCOPE,
              'plan_sha256': sha(PLAN), 'prepared_manifest_sha256': sha(PREPARED),
              'case_gold_or_corpus_data_parsed': False, 'model_training_reference_or_scoring_performed': False,
              'new_scoped_approval_required': True}))
        return 0
    # The exact reviewed authorization is checked again before reserving output.
    authorization()
    OUTPUT.mkdir(mode=0o700, parents=True, exist_ok=False)
    write(OUTPUT / 'prelaunch.json', {'plan_sha256': sha(PLAN), 'prepared_manifest_sha256': sha(PREPARED),
          'authorization_sha256': sha(AUTH), 'automatic_retry': False,
          'planned_cpu_seconds': PLANNED_CPU_SECONDS, 'whole_wall_seconds': WHOLE_SECONDS,
          'aggregate_cgroup_guarantee': False})
    try:
        expected_inputs = {**prepared['files'], **prepared['external_files']}
        template = read(TEMPLATE)
        assert sha(TEMPLATE) == expected_inputs[str(TEMPLATE)]['sha256']
        validate_template(template, plan)
        cohort_controls = stage_controls(COHORT_SOURCE, OUTPUT / 'cohort-controls', prepared['files'])
        study_controls = stage_controls(STUDY, OUTPUT / 'study-controls', prepared['files'])
        summaries = []
        mounts = [(HISTORY, '/history-manifest.json'), (EXPOSURES, '/exposure-source-manifest.json')]
        mounts += [(Path(item['path']), '/history/' + name) for name, item in history['files'].items()]
        mounts += [(Path(item['path']), '/exposure-sources/' + name) for name, item in exposures['files'].items()]
        summaries.append(role('history', OUTPUT, cohort_controls, TEMPLATE, mounts, {}, LIMITS['history'], active,
                              script='prepare.py', expected_files=expected_inputs))
        mounts = [(READER, '/reader'), (READER_MANIFEST, '/reader-manifest.json'), (PLAN, '/plan.json'),
                  (OUTPUT / 'history/data/result.json', '/history-result.json'),
                  (OUTPUT / 'history/data/fingerprints.json', '/fingerprints.json'),
                  (OUTPUT / 'history/data/exposure.json', '/exposure.json')]
        mounts += [(Path(plan['cached_corpus_paths'][split]), '/corpus/' + split + '.parquet') for split in ('train', 'test')]
        summaries.append(role('corpus', OUTPUT, cohort_controls, TEMPLATE, mounts, {}, LIMITS['corpus'], active,
                              script='prepare.py', expected_files=expected_inputs))
        data = OUTPUT / 'corpus/data'
        config = OUTPUT / 'config.json'
        write(config, bind_cohort(data, template, plan, sha(OUTPUT / 'history/data/result.json')))
        trees = {'/owned-app': prepared['trees']['/owned-app']}
        mounts = [(data / p, '/data/' + p) for p in ('training.jsonl', 'provenance.json')]
        summaries.append(role('train', OUTPUT, study_controls, config, mounts, trees, LIMITS['train'], active,
                              expected_files=expected_inputs))
        registry = OUTPUT / 'train/data/registry'
        train_result = OUTPUT / 'train/data/result.json'
        assert read(train_result)['complete'] is True
        names = {'model.json', 'weights.f32', 'registry.json', 'TRAINING-RECEIPT.json'}
        assert {p.name for p in registry.iterdir()} == names
        frozen_primary = {'training_result_sha256': sha(train_result), 'config_sha256': sha(config),
                          'files': {name: sha(registry / name) for name in sorted(names)},
                          'fixed_before_development_prediction': True, 'development_selects_model': False}
        write(OUTPUT / 'primary-freeze.json', frozen_primary)
        for name in ('development', 'eidos', 'laya', 'score'):
            assert sha(config) == frozen_primary['config_sha256'] and sha(train_result) == frozen_primary['training_result_sha256']
            assert all(sha(registry / key) == pin for key, pin in frozen_primary['files'].items())
            mounts, role_trees = [], trees
            if name == 'development':
                mounts = [(registry, '/registry'), (train_result, '/training-result.json'),
                          (data / 'development.jsonl', '/data/development.jsonl')]
            elif name == 'eidos':
                mounts = [(registry, '/registry'), (train_result, '/training-result.json'),
                          (data / 'test-inputs.jsonl', '/data/test-inputs.jsonl')]
            elif name == 'laya':
                role_trees = {key: tree for key, tree in prepared['trees'].items() if key != '/owned-app'}
                mounts = [(data / 'test-inputs.jsonl', '/data/test-inputs.jsonl'),
                          (Path(prepared['rights_file']), '/rights.json')]
            else:
                mounts = [(data / p, '/data/' + p) for p in ('test-gold.jsonl', 'test-groups.jsonl')]
                mounts += [(train_result, '/training-result.json')]
                mounts += [(OUTPUT / p / 'data', '/' + p) for p in ('eidos', 'laya')]
            summaries.append(role(name, OUTPUT, study_controls, config, mounts, role_trees,
                                  LIMITS[name], active, native=name == 'laya', expected_files=expected_inputs))
        check()
        assert sha(config) == frozen_primary['config_sha256'] and sha(train_result) == frozen_primary['training_result_sha256']
        write(OUTPUT / 'execution-summary.json', {'complete': True, 'stages': summaries,
              'elapsed_seconds': time.monotonic() - started, 'all_seven_stages_complete': True,
              'automatic_retry': False, 'acquisition_or_installation': False,
              'primary_fixed_before_diagnostic_development': True, 'aggregate_cgroup_guarantee': False,
              'mission_complete': False, 'production_certified': False})
        print(json.dumps({'complete': True, 'stages': len(summaries), 'mission_complete': False}))
        return 0
    except Exception as error:
        write(OUTPUT / 'failure.json', {'complete': False, 'error_type': type(error).__name__,
              'elapsed_seconds': time.monotonic() - started, 'automatic_retry_allowed': False,
              'fallback_resume_or_partial_scoring_allowed': False,
              'score_complete': (OUTPUT / 'score/data/result.json').exists()})
        print(json.dumps({'complete': False, 'error_type': type(error).__name__, 'automatic_retry_allowed': False}))
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    raise SystemExit(main())
