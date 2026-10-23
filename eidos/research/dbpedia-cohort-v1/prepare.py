"""Guarded research stages. No execution occurs on import."""
import hashlib
import json
import os
from pathlib import Path
import sys

from cohort import (LABELS, SALT, check_cohort, fingerprints, historical_texts,
                    reject_training_overlap, require, select_records)


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            result.update(block)
    return result.hexdigest()


def write(path, value, jsonl=False):
    data = (''.join(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n'
                    for row in value) if jsonl else
            json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + '\n').encode()
    require(len(data) <= (16 * 1024**2 if Path(path).name == 'fingerprints.json' else 2 * 1024**2),
            'output_file_size')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def history():
    manifest = json.loads(Path('/history-manifest.json').read_bytes())
    values, rows, total_bytes = set(), 0, 0
    for name, item in manifest['files'].items():
        path = Path('/history') / name
        require(path.is_file() and not path.is_symlink(), 'history_regular_file')
        require(path.stat().st_size == item['bytes'] and sha(path) == item['sha256'], 'history_pin')
        total_bytes += path.stat().st_size
        require(total_bytes <= 128 * 1024**2, 'history_total_bytes')
        for text in historical_texts(path.read_bytes(), item['kind']):
            rows += 1
            require(rows <= 200_000, 'history_rows')
            values.update(fingerprints(text))
            require(len(values) <= 180_000, 'history_fingerprint_count')
    write('/output/fingerprints.json', sorted(values))
    write('/output/result.json', {'complete': True, 'source_file_count': len(manifest['files']),
          'source_bytes': total_bytes, 'rows_fingerprinted': rows, 'unique_fingerprints': len(values),
          'manifest_sha256': sha('/history-manifest.json'),
          'fingerprints_sha256': sha('/output/fingerprints.json'),
          'quality_evaluation_or_answer_key_read': False,
          'scope': manifest['scope'], 'comprehensive_historical_or_pretraining_independence': False})


def corpus():
    # Imports native code only in this explicit stage, after the launcher guard.
    import pyarrow as pa
    import pyarrow.fs as fs
    import pyarrow.parquet as pq
    require(pa.__version__ == '26.0.0', 'reader_version')
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    plan = json.loads(Path('/plan.json').read_bytes())
    history_result = json.loads(Path('/history-result.json').read_bytes())
    require(history_result['complete'] is True and
            sha('/fingerprints.json') == history_result['fingerprints_sha256'], 'history_binding')
    historical = set(json.loads(Path('/fingerprints.json').read_bytes()))
    expected_schema = [('label', pa.int64()), ('title', pa.string()), ('content', pa.string())]

    def records(split):
        path = '/corpus/' + split + '.parquet'
        pin = plan['corpus'][split]
        require(Path(path).stat().st_size == pin['bytes'] and sha(path) == pin['sha256'], 'corpus_pin')
        source = pq.ParquetFile(path, filesystem=fs.LocalFileSystem(), memory_map=False,
                               pre_buffer=False, arrow_extensions_enabled=False,
                               thrift_string_size_limit=1024**2, thrift_container_size_limit=100_000,
                               schema_depth_limit=8, page_checksum_verification=True)
        try:
            require([(f.name, f.type) for f in source.schema_arrow] == expected_schema, 'parquet_schema')
            require(source.metadata.num_rows == pin['rows'], 'parquet_row_count')
            require(source.metadata.num_row_groups <= 1000, 'parquet_row_groups')
            for batch in source.iter_batches(batch_size=512, use_threads=False,
                                             columns=['label', 'title', 'content']):
                yield from batch.to_pylist()
        finally:
            source.close()

    selected_test, test_stats = select_records(records('test'), 'test', expected_per_label=5000)
    selected_train, train_stats = select_records(records('train'), 'train', expected_per_label=40_000,
                                                observer=reject_training_overlap(selected_test['test']))
    roles = {**selected_train, **selected_test}
    groups, lineage = check_cohort(roles, historical)
    provenance = {
        'source': 'fancyzhx/dbpedia_14@9abd46cf7fc8b4c64290f26993c540b92aa145ac',
        'license': 'Dataset card declares CC-BY-SA-3.0; also describes GFDL. Distribution review unresolved.',
        'citation': 'Zhang, Zhao and LeCun (2015), Character-level Convolutional Networks for Text Classification.',
        'rights_basis': 'Public research dataset with declared licenses; no exclusive ownership or redistribution certification.'}
    write('/output/provenance.json', provenance)
    for role in ('support', 'development'):
        write('/output/' + role + '.jsonl',
              [{k: r[k] for k in ('id', 'text', 'label')} for r in roles[role]], jsonl=True)
    write('/output/test-inputs.jsonl', [{k: r[k] for k in ('id', 'text')} for r in roles['test']], jsonl=True)
    write('/output/test-gold.jsonl', [{k: r[k] for k in ('id', 'label')} for r in roles['test']], jsonl=True)
    write('/output/test-groups.jsonl', [{'id': r['id'], 'group_id': groups[r['id']]} for r in roles['test']], jsonl=True)
    write('/output/labels.json', list(LABELS))
    files = {p.name: {'sha256': sha(p), 'bytes': p.stat().st_size}
             for p in Path('/output').iterdir() if p.is_file()}
    require('numpy' not in sys.modules and 'pandas' not in sys.modules, 'unexpected_python_dependency')
    write('/output/result.json', {'complete': True, 'files': files, 'salt': SALT,
          'plan_sha256': sha('/plan.json'), 'history_result_sha256': sha('/history-result.json'),
          'source_counts': {'train': train_stats, 'test': test_stats}, 'lineage': lineage,
          'role_rows': {k: len(v) for k, v in roles.items()},
          'selected_row_removal_replacement_or_truncation': False,
          'model_fit_prediction_or_scoring': False, 'native_reader_only': True,
          'semantic_template_speaker_or_pretraining_independence_verified': False,
          'mission_complete': False, 'production_certified': False})


if __name__ == '__main__':
    require(len(sys.argv) == 2 and sys.argv[1] in ('history', 'corpus'), 'explicit_stage_required')
    {'history': history, 'corpus': corpus}[sys.argv[1]]()
