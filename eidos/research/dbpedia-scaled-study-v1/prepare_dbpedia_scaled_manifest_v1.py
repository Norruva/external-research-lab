"""Bind prepared source and opaque existing public research inputs; no data parsing."""
import hashlib
import json
from pathlib import Path

ROOT = Path('/workspace/eidos')
STUDY = ROOT / 'research/dbpedia-scaled-study-v1'
COHORT = ROOT / 'research/dbpedia-scaled-cohort-v1'


def pin(path):
    path = Path(path)
    assert path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024**2), b''):
            h.update(b)
    return {'bytes': path.stat().st_size, 'sha256': h.hexdigest()}


def main():
    prior = json.loads((ROOT / 'research/dbpedia-study-v1/prepared-manifest-v1.json').read_bytes())
    trees = {k: v for k, v in prior['trees'].items() if k != '/owned-app'}
    trees['/owned-app'] = {'root': str(STUDY / 'owned-app'),
        'manifest': str(STUDY / 'owned-app-manifest.json'),
        'sha256': pin(STUDY / 'owned-app-manifest.json')['sha256']}
    sources = [*COHORT.glob('*.py'), *STUDY.glob('*.py'),
        STUDY / 'PLAN.md', STUDY / 'plan.json', STUDY / 'config-template.json',
        STUDY / 'historical-source-manifest.json', STUDY / 'exposure-source-manifest.json',
        STUDY / 'owned-app-manifest.json', ROOT / 'run_dbpedia_scaled_study_v1.py',
        ROOT / 'run_dbpedia_scaled_fixture_v1.py', Path(__file__)]
    assert (COHORT / 'test_cohort.py').is_file()
    assert all((STUDY / n).is_file() for n in ('study.py', 'contracts.py', 'test_contracts.py',
        'tree_review.py', 'authored_reference_fixture.py', 'authored_cohort_fixture.py'))
    externals = [ROOT / 'evaluation/production/dbpedia-reader-v1/v1/reader-manifest.json',
        ROOT / 'evaluation/production/dbpedia-reader-v1/v1/completion-review-v1.json',
        Path(prior['rights_file'])]
    runtime = Path('/opt/codex/runtimes/codex-primary-runtime/dependencies/python')
    externals += [runtime / 'bin/python3.12', runtime / 'lib/libpython3.12.so.1.0']
    plan = json.loads((STUDY / 'plan.json').read_bytes())
    for name in ('train', 'test'):
        path = Path(plan['cached_corpus_paths'][name])
        assert pin(path) == {k: plan['corpus'][name][k] for k in ('bytes', 'sha256')}
        externals.append(path)
    for name in ('historical-source-manifest.json', 'exposure-source-manifest.json'):
        metadata = json.loads((STUDY / name).read_bytes())
        for item in metadata['files'].values():
            path = Path(item['path'])
            assert pin(path) == {k: item[k] for k in ('bytes', 'sha256')}
            externals.append(path)
    for item in trees.values():
        assert pin(item['manifest'])['sha256'] == item['sha256']
        externals.append(Path(item['manifest']))
    value = {'format': 'eidos-dbpedia-scaled-study-prepared-v1',
        'scope': 'eidos-dbpedia-scaled-study-v1', 'approval_required': True,
        'files': {str(p): pin(p) for p in sorted(set(sources))},
        'external_files': {str(p): pin(p) for p in sorted(set(externals))},
        'trees': trees, 'rights_file': prior['rights_file'],
        'real_data_processed_or_models_executed': False,
        'automatic_retry_or_prior_budget_reuse': False}
    output = STUDY / 'prepared-manifest-v1.json'
    with output.open('x') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'source_files': len(value['files']), 'external_opaque_files': len(value['external_files']),
        'complete_trees': len(trees), 'prepared_manifest_sha256': pin(output)['sha256']}))


if __name__ == '__main__':
    main()
