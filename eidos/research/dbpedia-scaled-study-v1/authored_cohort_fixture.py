"""Original authored substitute for native corpus preparation, never real data.

Exercises the actual two-pass census/eligibility/ranking/grouping helpers. This
substitute neither imports PyArrow nor opens a real corpus or prior case file.
"""
import hashlib
import json
import os
from pathlib import Path

from cohort import (canonical, check_cohort, digest, fingerprints, require,
                    scan_sources, select_sources, validate_source)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value, jsonl=False):
    payload = (b''.join(canonical(row) + b'\n' for row in value) if jsonl else
               canonical(value) + b'\n')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)


def main():
    cfg = json.loads(Path('/config.json').read_bytes())
    require(cfg.get('authored_fixture_only') is True, 'explicit_authored_scope_required')
    labels = ('A', 'B')
    sources = {}
    for split, count in (('train', 12), ('test', 6)):
        sources[split] = [
            {'label': label_index,
             'title': f'Original {split} article {label_index} number {i}',
             'content': f'{word} authored {split} passage topic {label_index} example {i}.'}
            for label_index, word in enumerate(('Alpha invoice', 'Beta melody'))
            for i in range(count)]
    # Identical content under different titles across official-role surrogates.
    sources['train'][1]['content'] = 'Original shared body crossing the two authored source splits.'
    sources['test'][1]['content'] = sources['train'][1]['content']
    # A source-reconstructed prior case is excluded before ranking.
    exposed = validate_source(sources['train'][0], 'train', 0, labels)
    exposures = {exposed['id']: digest(exposed['text'])}
    # Independent historical full/content fingerprint exclusion before ranking.
    historical = fingerprints(sources['train'][2]['content'])
    factory = lambda split: iter(sources[split])
    census = scan_sources(factory, exposures, labels, {'train': 12, 'test': 6})
    quotas = {'training': 3, 'development': 2, 'test': 2}
    roles, eligibility = select_sources(factory, census, historical, quotas, labels)
    groups, lineage = check_cohort(roles, census, historical, quotas, labels)
    require(lineage['resolved_previous_exposure_cases'] == 1, 'old_case_reconstruction')
    require(eligibility['train']['excluded_rows'] == 3 and
            eligibility['test']['excluded_rows'] == 1, 'expected_authored_eligibility')
    require(all(row['id'] != exposed['id'] for rows in roles.values() for row in rows),
            'previous_case_not_selected')
    provenance = {'source': 'Original authored Alpha/Beta cohort mechanics fixture',
        'license': 'CC0-1.0', 'citation': 'EIDOS original source/role integration check',
        'rights_basis': 'Original synthetic text; no DBpedia, benchmark, private data or reference output'}
    write('/output/provenance.json', provenance)
    for role in ('training', 'development'):
        write('/output/' + role + '.jsonl', [{k: row[k] for k in ('id', 'text', 'label')}
                                            for row in roles[role]], jsonl=True)
    write('/output/test-inputs.jsonl', [{k: row[k] for k in ('id', 'text')}
                                       for row in roles['test']], jsonl=True)
    write('/output/test-gold.jsonl', [{k: row[k] for k in ('id', 'label')}
                                     for row in roles['test']], jsonl=True)
    write('/output/test-groups.jsonl', [{'id': row['id'], 'group_id': groups[row['id']]}
                                       for row in roles['test']], jsonl=True)
    write('/output/labels.json', list(labels))
    files = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
             for p in Path('/output').iterdir() if p.is_file()}
    write('/output/result.json', {'complete': True, 'stage': 'cohort_fixture',
        'files': files, 'role_rows': {role: len(rows) for role, rows in roles.items()},
        'lineage': lineage, 'eligibility': eligibility,
        'authored_substitute_not_actual_DBpedia_or_native_reader': True,
        'actual_pure_cohort_helpers_exercised': True,
        'real_corpus_prior_case_or_model_read': False,
        'quality_or_reference_claim': False})


if __name__ == '__main__':
    main()
