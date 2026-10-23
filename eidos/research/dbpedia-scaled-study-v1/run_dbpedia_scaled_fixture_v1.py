"""Eight offline authored roles; no real corpus, reader or reference execution.

This runner shares the production launcher's reviewed namespace boundary while
mounting only source code and new original mechanical fixtures. It creates no
real experiment slot, approval, installation, calibration or promoted model.
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import re
import shutil
import signal
import time

from run_dbpedia_scaled_study_v1 import role

ROOT = Path('/workspace/eidos')
STUDY = ROOT / 'research/dbpedia-scaled-study-v1'
COHORT = ROOT / 'research/dbpedia-scaled-cohort-v1'
OUT = ROOT / 'evaluation/production/dbpedia-scaled-study-v1/authored-fixture-v1'
WHOLE_SECONDS = 180


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(value, sort_keys=True, allow_nan=False, indent=2) + '\n')


def source_files():
    paths = [*STUDY.glob('*.py'), *COHORT.glob('*.py')]
    assert len({p.name for p in paths}) == len(paths), 'Source basename collision'
    assert all(not p.is_symlink() for p in paths)
    return {str(p): sha(p) for p in paths}


def main():
    assert Path.cwd() == ROOT and os.getuid() == 1000 and os.uname().sysname == 'Linux'
    resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2,) * 2)
    os.umask(0o077)
    OUT.mkdir(mode=0o700, parents=True, exist_ok=False)
    active, started, summaries = [None], time.monotonic(), []

    def deadline(_signum, _frame):
        if active[0] is not None and active[0].poll() is None:
            os.killpg(active[0].pid, signal.SIGKILL)
        raise TimeoutError('Whole original-authored fixture deadline')

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, WHOLE_SECONDS)
    before = source_files()
    try:
        controls = OUT / 'controls'
        controls.mkdir(mode=0o700)
        for original in before:
            destination = controls / Path(original).name
            shutil.copyfile(original, destination)
            destination.chmod(0o400)
        controls.chmod(0o500)
        config = OUT / 'config-authored-preparation.json'
        write(config, {'authored_fixture_only': True})
        owned_tree = {'/owned-app': {'root': str(STUDY / 'owned-app'),
            'manifest': str(STUDY / 'owned-app-manifest.json'),
            'sha256': sha(STUDY / 'owned-app-manifest.json')}}
        summaries.append(role('unit_cohort', OUT, controls, config, [], {}, (60, 75, 768),
                              active, script='test_cohort.py'))
        summaries.append(role('unit_study', OUT, controls, config, [], owned_tree,
                              (60, 75, 768), active, script='test_contracts.py'))
        summaries.append(role('cohort_fixture', OUT, controls, config, [], {},
                              (60, 75, 768), active, script='authored_cohort_fixture.py'))
        data = OUT / 'cohort_fixture/data'
        readiness = read(data / 'result.json')
        assert readiness['complete'] is True
        assert readiness['authored_substitute_not_actual_DBpedia_or_native_reader'] is True
        cfg = read(STUDY / 'config-template.json')
        cfg.update(authored_fixture_only=True, labels=['A', 'B'], n=4,
                   train_per_class=3, development_per_class=2, test_per_class=2,
                   max_windows_per_case=4, max_total_windows=16,
                   reference_aliases={'A': 'authored_alpha', 'B': 'authored_beta'},
                   cohort_readiness_sha256=sha(data / 'result.json'),
                   cohort_files=readiness['files'])
        config = OUT / 'config.json'
        write(config, cfg)
        for name in ('train', 'development', 'eidos', 'laya', 'score'):
            if name == 'train':
                mounts = [(data / p, '/data/' + p) for p in ('training.jsonl', 'provenance.json')]
            elif name == 'development':
                mounts = [(data / 'development.jsonl', '/data/development.jsonl'),
                          (OUT / 'train/data/registry', '/registry'),
                          (OUT / 'train/data/result.json', '/training-result.json')]
            elif name == 'eidos':
                mounts = [(data / 'test-inputs.jsonl', '/data/test-inputs.jsonl'),
                          (OUT / 'train/data/registry', '/registry'),
                          (OUT / 'train/data/result.json', '/training-result.json')]
            elif name == 'laya':
                mounts = [(data / 'test-inputs.jsonl', '/data/test-inputs.jsonl')]
            else:
                mounts = [(data / p, '/data/' + p) for p in ('test-gold.jsonl', 'test-groups.jsonl')]
                mounts += [(OUT / 'train/data/result.json', '/training-result.json')]
                mounts += [(OUT / p / 'data', '/' + p) for p in ('eidos', 'laya')]
            summaries.append(role(name, OUT, controls, config, mounts, owned_tree,
                                  (90, 100, 768), active,
                                  script='authored_reference_fixture.py' if name == 'laya' else 'study.py'))
        unit_counts = {}
        for name in ('unit_cohort', 'unit_study'):
            log = (OUT / name / 'stderr.log').read_text()
            matches = re.findall(r'Ran ([0-9]+) tests? in ', log)
            assert len(matches) == 1 and int(matches[0]) > 0
            unit_counts[name] = int(matches[0])
        result = read(OUT / 'score/data/result.json')
        assert result['complete'] is True and result['n'] == 4
        assert result['reference_is_authored_substitute'] is True
        assert before == source_files()
        summary = {'complete': True, 'stages': summaries,
            'elapsed_seconds': time.monotonic() - started,
            'source_files_unchanged': len(before),
            'authored_test_method_counts': unit_counts,
            'authored_test_methods_total': sum(unit_counts.values()),
            'source_manifest_sha256': hashlib.sha256(json.dumps(before, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest(),
            'actual_owned_sparse_training_development_prediction_and_paired_score': True,
            'development_diagnostics_did_not_select_or_change_primary': True,
            'actual_two_pass_cohort_helpers_exercised': True,
            'cohort_was_explicit_authored_substitute_not_DBpedia_or_native_reader': True,
            'reference_was_explicit_authored_substitute_not_LAYA': True,
            'real_corpus_reader_native_reference_or_prior_model_mounted': False,
            'quality_or_LAYA_claim': False, 'model_promoted': False,
            'whole_wall_seconds_limit': WHOLE_SECONDS,
            'per_process_limits_not_aggregate_cgroup': True}
        write(OUT / 'execution-summary.json', summary)
        print(json.dumps(summary, sort_keys=True, indent=2))
        return 0
    except Exception as error:
        write(OUT / 'failure-summary.json', {'complete': False,
            'error_type': type(error).__name__, 'completed_stages': [s['stage'] for s in summaries],
            'elapsed_seconds': time.monotonic() - started,
            'automatic_retry_or_real_execution_allowed': False})
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    raise SystemExit(main())
