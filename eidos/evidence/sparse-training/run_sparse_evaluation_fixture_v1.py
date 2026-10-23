"""Four offline OS roles exercising sparse training and owned evaluation.

All input sentences are original authored mechanics fixtures. No historical
corpus, trained model, credentials, native reference or quality experiment is
read. The unique output slot is never retried or overwritten automatically.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import signal
import subprocess
import time


ROOT = Path('/workspace/eidos')
APP = Path('/workspace/external-research-lab/eidos')
OUT = ROOT / 'evaluation/production/sparse-training-v1/evaluation-fixture-v1'
ROLE_SECONDS = 45
WHOLE_SECONDS = 180
GUARD = r'''
import errno,json,os,pathlib,resource,socket,subprocess,sys
role=sys.argv[1]
assert role in ('train','prepare','predict','score')
assert os.getuid()==1000 and sys.flags.no_site and sys.flags.dont_write_bytecode
assert sys.version_info>=(3,12) and os.getcwd()=='/work'
assert set(os.environ)<={'PATH','LC_ALL','HOME','PWD'}
assert {name for _,name in socket.if_nameindex()}=={'lo'}
assert all(not pathlib.Path(p).exists() for p in
           ('/workspace','/root','/run','/etc','/sys','/opt','/deps','/dataset'))
assert not list(pathlib.Path('/work').rglob('weights.f32'))
with socket.socket() as probe:
 try:probe.connect(('198.51.100.1',443))
 except OSError as error:assert error.errno==errno.ENETUNREACH
 else:raise AssertionError('Unexpected external route')
resource.setrlimit(resource.RLIMIT_CPU,(120,120))
resource.setrlimit(resource.RLIMIT_AS,(768*1024**2,768*1024**2))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024**2,64*1024**2))
os.umask(0o077)
base=[sys.executable,'-S','-B','-m']
if role=='train':
 assert all(not pathlib.Path(p).exists() for p in
            ('/registry','/evaluation.jsonl','/evaluation-provenance.json','/input','/gold','/predictions','/plan.json'))
 assert pathlib.Path('/train.jsonl').is_file() and pathlib.Path('/training-provenance.json').is_file()
 command=base+['eidos.train_sparse','--input','/train.jsonl',
               '--provenance','/training-provenance.json','--output','/output/model',
               '--model-id','owned-sparse-mechanics','--epochs','3','--seed','113']
elif role=='prepare':
 assert all(not pathlib.Path(p).exists() for p in ('/input','/gold','/predictions','/plan.json'))
 assert pathlib.Path('/registry/TRAINING-RECEIPT.json').is_file()
 assert all(pathlib.Path(p).is_file() for p in
            ('/train.jsonl','/training-provenance.json','/evaluation.jsonl','/evaluation-provenance.json'))
 command=base+['eidos.evaluate_owned','prepare','--registry','/registry',
               '--support','/train.jsonl','--support-provenance','/training-provenance.json',
               '--evaluation','/evaluation.jsonl','--evaluation-provenance','/evaluation-provenance.json',
               '--output','/output/bundle']
elif role=='predict':
 assert all(not pathlib.Path(p).exists() for p in
            ('/train.jsonl','/training-provenance.json','/evaluation.jsonl','/evaluation-provenance.json',
             '/gold','/predictions','/plan.json'))
 assert {p.name for p in pathlib.Path('/input').iterdir()}=={'plan.json','inputs.jsonl'}
 assert all(set(json.loads(line))=={'id','text'} for line in pathlib.Path('/input/inputs.jsonl').read_text().splitlines())
 command=base+['eidos.evaluate_owned','predict','--registry','/registry',
               '--inputs','/input','--output','/output/predictions']
else:
 assert all(not pathlib.Path(p).exists() for p in
            ('/train.jsonl','/training-provenance.json','/evaluation.jsonl','/evaluation-provenance.json',
             '/registry','/input'))
 assert {p.name for p in pathlib.Path('/gold').iterdir()}=={'gold.jsonl','binding.json'}
 assert {p.name for p in pathlib.Path('/predictions').iterdir()}=={'predictions.jsonl','receipt.json'}
 assert all(set(json.loads(line))=={'id','label'} for line in pathlib.Path('/gold/gold.jsonl').read_text().splitlines())
 command=base+['eidos.evaluate_owned','score','--plan','/plan.json',
               '--gold','/gold','--predictions','/predictions','--output','/output/score']
completed=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=40)
pathlib.Path('/output/command-stdout.json').write_bytes(completed.stdout)
pathlib.Path('/output/command-stderr.log').write_bytes(completed.stderr)
assert completed.returncode==0,(role,completed.returncode,completed.stderr.decode(errors='replace')[-1000:])
result=json.loads(completed.stdout)
assert result['complete'] is True
if role=='train':
 assert result['format']=='eidos-owned-sparse-training-receipt-v1'
 assert result['training_rows']==32 and result['class_counts']=={'invoice_help':23,'music_help':7,'plant_help':2}
 assert result['operational_acceptance_enabled'] is False and result['model_promoted'] is False
 assert result['evaluation_or_calibration_performed'] is False
 assert pathlib.Path('/output/model').stat().st_mode&0o777==0o700
 assert all(p.stat().st_mode&0o777==0o400 for p in pathlib.Path('/output/model').iterdir())
else:
 assert result.get('n',result.get('metrics',{}).get('n'))==3
 if role=='predict':
  assert result['gold_input_read'] is False and result['training_calibration_or_scoring_performed'] is False
 if role=='score':
  assert result['model_weights_or_source_text_read_for_scoring'] is False
  assert result['fit_calibration_promotion_or_rival_comparison_performed'] is False
  assert result['per_case_rows_written'] is False
  assert result['metrics']['accepted_coverage']==0 and result['metrics']['selective_accuracy'] is None
  assert sum(map(sum,result['metrics']['confusion']['counts']))==3
print(json.dumps({'complete':True,'role':role,'authored_training_rows':32,'authored_evaluation_rows':3,
 'gold_absent_from_prediction_namespace':role=='predict',
 'models_and_source_case_text_absent_from_score_namespace':role=='score',
 'source_fixture_only_no_quality_or_rival_claim':True}))
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def tree():
    files = sorted(APP.rglob('*'))
    assert all(not path.is_symlink() for path in files)
    return {str(path.relative_to(APP)): sha(path.read_bytes()) for path in files if path.is_file()}


def fixtures():
    directory = OUT / 'authored-fixtures'
    directory.mkdir(mode=0o700)
    rows = []
    for label, stem, count in (
            ('invoice_help', 'help with invoice payment reference', 23),
            ('music_help', 'play my song music selection', 7),
            ('plant_help', 'describe a flowering plant leaf', 2)):
        for number in range(count):
            rows.append({'id': f'train-{label}-{number:03d}',
                         'text': f'{stem} original training example number {number}', 'label': label})
    evaluation = [
        {'id': 'authored-eval-001', 'label': 'invoice_help',
         'text': 'Please check an authored demonstration invoice issued yesterday.'},
        {'id': 'authored-eval-002', 'label': 'music_help',
         'text': 'Play an original demonstration musical composition using a speaker.'},
        {'id': 'authored-eval-003', 'label': 'plant_help',
         'text': 'Describe the leaves and blossoms on an original demonstration garden specimen.'},
    ]
    for name, values in (('train.jsonl', rows), ('evaluation.jsonl', evaluation)):
        write(directory / name, b''.join(canonical(row) + b'\n' for row in values))
    for split in ('training', 'evaluation'):
        write(directory / (split + '-provenance.json'), canonical({
            'source': f'Original authored {split} mechanics fixtures',
            'license': 'CC0-1.0', 'citation': 'EIDOS sparse owned-evaluation workflow demonstration',
            'rights_basis': 'Original authored material; no benchmark, private or rival data',
        }) + b'\n')
    return directory


def phase(name, fixture_root, active):
    directory = OUT / name
    directory.mkdir(mode=0o700)
    output = directory / 'output'
    output.mkdir(mode=0o700)
    mounts = []
    if name in ('train', 'prepare'):
        mounts += [(fixture_root / p, '/' + p) for p in ('train.jsonl', 'training-provenance.json')]
    if name == 'prepare':
        mounts += [(fixture_root / p, '/' + p) for p in ('evaluation.jsonl', 'evaluation-provenance.json')]
    if name in ('prepare', 'predict'):
        mounts.append((OUT / 'train/output/model', '/registry'))
    if name == 'predict':
        mounts.append((OUT / 'prepare/output/bundle/prediction-input', '/input'))
    if name == 'score':
        mounts += [(OUT / 'prepare/output/bundle/prediction-input/plan.json', '/plan.json'),
                   (OUT / 'prepare/output/bundle/gold-private', '/gold'),
                   (OUT / 'predict/output/predictions', '/predictions')]
    command = ['/usr/bin/bwrap', '--unshare-all', '--new-session', '--die-with-parent',
               '--uid', '1000', '--gid', '1000', '--cap-drop', 'ALL',
               '--ro-bind', '/usr/bin', '/usr/bin', '--ro-bind', '/usr/lib', '/usr/lib',
               '--ro-bind', '/usr/lib64', '/usr/lib64', '--symlink', 'usr/bin', '/bin',
               '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
               '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
               '--dir', '/home/research', '--dir', '/work', '--ro-bind', str(APP), '/work',
               '--dir', '/output', '--bind', str(output), '/output']
    for source, destination in mounts:
        assert source.exists() and not source.is_symlink()
        if source.is_dir():
            command += ['--dir', destination]
        command += ['--ro-bind', str(source), destination]
    command += ['--clearenv', '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'LC_ALL', 'C.UTF-8',
                '--setenv', 'HOME', '/home/research', '--chdir', '/work', '--',
                '/usr/bin/python3', '-S', '-B', '-c', GUARD, name]
    command = ['/usr/bin/strace', '-f', '-qq', '-e', 'trace=network',
               '-e', 'raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg',
               '-o', str(directory / 'network.trace'), *command]
    before = tree()
    started = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env={'PATH': '/usr/bin:/bin'}, start_new_session=True)
    active[0] = process
    try:
        try:
            stdout, stderr = process.communicate(timeout=ROLE_SECONDS)
            status = process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
            status = 124
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
            raise
    finally:
        active[0] = None
    write(directory / 'stdout.json', stdout)
    write(directory / 'stderr.log', stderr)
    trace = (directory / 'network.trace').read_text()
    ip = [line for line in trace.splitlines() if 'connect(' in line and 'AF_INET' in line]
    sockets = [line for line in trace.splitlines() if 'socket(AF_INET' in line]
    probes = [line for line in ip if 'inet_addr("198.51.100.1")' in line and 'ENETUNREACH' in line]
    after = tree()
    complete = status == 0 and before == after and len(ip) == len(probes) == len(sockets) == 1
    result = {
        'complete': complete, 'role': name, 'exit_code': status,
        'elapsed_seconds': time.monotonic() - started,
        'source_unchanged': before == after, 'source_files_verified': len(before),
        'source_manifest_sha256': sha(canonical(before)),
        'expected_refused_preflights': len(probes),
        'unexpected_ip_connects': len(ip) - len(probes),
        'unexpected_ip_socket_creations': max(0, len(sockets) - 1),
        'stdout_sha256': sha(stdout), 'stderr_sha256': sha(stderr),
        'operating_system_file_role_boundary': True,
        'gold_absent_from_prediction_namespace': name == 'predict',
        'models_and_source_case_text_absent_from_score_namespace': name == 'score',
        'existing_credentials_historical_corpora_or_pretrained_models_mounted': False,
        'new_authored_fixture_model_mounted': name in ('prepare', 'predict'),
        'only_original_authored_mechanical_fixtures': True,
        'quality_or_rival_experiment_performed': False,
        'per_process_cpu_seconds': 120, 'memory_mib': 768, 'wall_seconds_limit': ROLE_SECONDS,
        'aggregate_cgroup_guarantee': False,
    }
    write(directory / 'execution-summary.json', canonical(result) + b'\n')
    if not complete:
        raise RuntimeError(f'Authored {name} role failed; preserve evidence and do not retry')
    return result


def main():
    assert os.getuid() == 1000 and Path.cwd() == ROOT
    assert platform.system() == 'Linux' and platform.machine() == 'x86_64'
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2,) * 2)
    os.umask(0o077)
    OUT.mkdir(mode=0o700, parents=True, exist_ok=False)
    started, active, results = time.monotonic(), [None], []

    def deadline(_signum, _frame):
        if active[0] is not None and active[0].poll() is None:
            os.killpg(active[0].pid, signal.SIGKILL)
        raise TimeoutError('Whole authored fixture deadline exceeded')

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, WHOLE_SECONDS)
    before = tree()
    try:
        fixture_root = fixtures()
        for name in ('train', 'prepare', 'predict', 'score'):
            results.append(phase(name, fixture_root, active))
        complete = before == tree() and all(result['complete'] for result in results) and len(results) == 4
        summary = {
            'complete': complete, 'stages': results, 'elapsed_seconds': time.monotonic() - started,
            'source_files_verified': len(before), 'source_unchanged': before == tree(),
            'source_manifest_sha256': sha(canonical(before)),
            'authored_training_rows': 32, 'class_counts': {'invoice_help': 23, 'music_help': 7, 'plant_help': 2},
            'authored_evaluation_rows': 3, 'actual_sparse_training_and_owned_evaluation_cli': True,
            'separate_prediction_without_gold': True, 'separate_scoring_without_models_or_case_text': True,
            'new_keys_existing_credentials_historical_corpus_or_native_reference_used': False,
            'historical_or_rival_quality_experiment_performed': False,
            'quality_claim_from_authored_fixture': False, 'model_promoted': False,
            'whole_wall_seconds_limit': WHOLE_SECONDS, 'parent_cpu_seconds': 120,
            'per_role_process_cpu_seconds': 120, 'per_role_memory_mib': 768,
            'per_role_wall_seconds_limit': ROLE_SECONDS, 'aggregate_cgroup_guarantee': False,
        }
        write(OUT / 'execution-summary.json', canonical(summary) + b'\n')
        print(json.dumps(summary, indent=2))
        return 0 if complete else 1
    except Exception as error:
        write(OUT / 'failure-summary.json', canonical({
            'complete': False, 'completed_stages': [item['role'] for item in results],
            'error_type': type(error).__name__, 'elapsed_seconds': time.monotonic() - started,
            'automatic_retry_or_partial_scoring_allowed': False,
        }) + b'\n')
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    raise SystemExit(main())
