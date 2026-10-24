"""Explicit isolated history/corpus stages; importing this file performs no I/O."""
import hashlib
import json
import os
from pathlib import Path
import sys

from cohort import (EXPECTED, LABELS, QUOTAS, SALT, canonical, check_cohort, exposure_rows,
                    historical_texts, fingerprints, require, scan_sources,
                    select_sources, unique_json, validate_exposures)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            value.update(block)
    return value.hexdigest()


def read_json(path, maximum=4 * 1024**2):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= maximum,
            'json_regular_file_or_size')
    return unique_json(path.read_bytes().decode('utf-8', 'strict'))


def write(path, value, jsonl=False):
    name = Path(path).name
    maximum = (32 * 1024**2 if name == 'training.jsonl' else
               16 * 1024**2 if name == 'fingerprints.json' else 4 * 1024**2)
    data = (b''.join(canonical(row) + b'\n' for row in value) if jsonl else
            (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + '\n').encode('utf-8'))
    require(0 < len(data) <= maximum, 'output_file_size')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def pinned_text_file(root, name, pin, maximum=32 * 1024**2):
    relative = Path(name)
    require(not relative.is_absolute() and '..' not in relative.parts and relative.parts, 'manifest_relative_path')
    path = Path(root) / relative
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), 'history_regular_file')
    require(type(pin) is dict and type(pin.get('bytes')) is int and 0 < pin['bytes'] <= maximum and
            path.stat().st_size == pin['bytes'] and sha(path) == pin.get('sha256'), 'history_pin')
    return path.read_bytes()


def history():
    """Fingerprint enumerated public source text and bind 840 prior case texts.

    No prior gold, predictions, quality score or model artifact is an input.
    Labels in prior training/development JSONL are not accessed by this code.
    """
    manifest = read_json('/history-manifest.json')
    exposure_manifest = read_json('/exposure-source-manifest.json')
    require(type(manifest.get('files')) is dict and len(manifest['files']) == 40, 'historical_file_inventory')
    require(type(exposure_manifest.get('files')) is dict and len(exposure_manifest['files']) == 3,
            'exposure_file_inventory')
    values, rows, total_bytes = set(), 0, 0
    for name, item in manifest['files'].items():
        raw = pinned_text_file('/history', name, item)
        total_bytes += len(raw)
        require(total_bytes <= 128 * 1024**2, 'history_total_bytes')
        for text in historical_texts(raw, item['kind']):
            rows += 1
            require(rows <= 200_000, 'history_rows')
            values.update(fingerprints(text))
            require(len(values) <= 180_000, 'history_fingerprint_count')
    exposures, exposure_bytes, file_counts = {}, 0, {}
    for name, item in exposure_manifest['files'].items():
        raw = pinned_text_file('/exposure-sources', name, item, 4 * 1024**2)
        exposure_bytes += len(raw)
        require(exposure_bytes <= 8 * 1024**2, 'exposure_total_bytes')
        count = 0
        for identifier, text_sha in exposure_rows(raw, item['kind']):
            require(identifier not in exposures, 'duplicate_prior_exposure_id')
            exposures[identifier] = text_sha
            count += 1
        require(count == 280, 'prior_exposure_file_count')
        file_counts[name] = count
    require(len(exposures) == 840, 'prior_exposure_total')
    validate_exposures(exposures)
    write('/output/fingerprints.json', sorted(values))
    write('/output/exposure.json', exposures)
    write('/output/result.json', {'complete': True, 'source_file_count': len(manifest['files']),
          'source_bytes': total_bytes, 'rows_fingerprinted': rows, 'unique_fingerprints': len(values),
          'manifest_sha256': sha('/history-manifest.json'),
          'fingerprints_sha256': sha('/output/fingerprints.json'),
          'prior_exposure_source_manifest_sha256': sha('/exposure-source-manifest.json'),
          'prior_exposure_file_counts': file_counts, 'prior_exposure_source_bytes': exposure_bytes,
          'prior_exposure_rows': len(exposures), 'exposure_sha256': sha('/output/exposure.json'),
          'prior_training_or_development_label_values_accessed': False,
          'quality_evaluation_answer_key_or_prediction_read': False,
          'scope': 'enumerated public historical text and previously selected DBpedia input text only',
          'comprehensive_historical_or_pretraining_independence': False})


def corpus():
    # Installed native reader is imported only in this explicit approved stage.
    import pyarrow as pa
    import pyarrow.fs as fs
    import pyarrow.parquet as pq
    require(pa.__version__ == '26.0.0', 'reader_version')
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    plan = read_json('/plan.json')
    history_result = read_json('/history-result.json')
    require(history_result.get('complete') is True and
            sha('/fingerprints.json') == history_result.get('fingerprints_sha256') and
            sha('/exposure.json') == history_result.get('exposure_sha256') and
            history_result.get('prior_exposure_rows') == 840, 'history_binding')
    historical = set(read_json('/fingerprints.json', 16 * 1024**2))
    exposures = read_json('/exposure.json')
    validate_exposures(exposures)
    require(len(exposures) == 840, 'prior_exposure_total')
    require(plan['selection']['salt'] == SALT and plan['selection']['quotas'] == QUOTAS,
            'fixed_selection_plan')
    schema = [('label', pa.int64()), ('title', pa.string()), ('content', pa.string())]
    source_pins = {}
    for split in ('train', 'test'):
        path = Path('/corpus') / (split + '.parquet')
        pin = plan['corpus'][split]
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == pin['bytes'] and
                sha(path) == pin['sha256'], 'corpus_pin')
        source_pins[split] = sha(path)

    def records(split):
        path = '/corpus/' + split + '.parquet'
        source = pq.ParquetFile(path, filesystem=fs.LocalFileSystem(), memory_map=False,
                               pre_buffer=False, arrow_extensions_enabled=False,
                               thrift_string_size_limit=1024**2, thrift_container_size_limit=100_000,
                               schema_depth_limit=8, page_checksum_verification=True)
        try:
            require([(field.name, field.type) for field in source.schema_arrow] == schema, 'parquet_schema')
            require(source.metadata.num_rows == plan['corpus'][split]['rows'], 'parquet_row_count')
            require(source.metadata.num_row_groups <= 1000, 'parquet_row_groups')
            for batch in source.iter_batches(batch_size=512, use_threads=False,
                                             columns=['label', 'title', 'content']):
                yield from batch.to_pylist()
        finally:
            source.close()

    census = scan_sources(records, exposures)
    roles, eligibility_stats = select_sources(records, census, historical)
    groups, lineage = check_cohort(roles, census, historical)
    # Native inputs are immutable mounts; additionally check their opaque bytes
    # after both passes before publishing any selected case.
    for split in ('train', 'test'):
        require(sha('/corpus/' + split + '.parquet') == source_pins[split], 'corpus_changed')
    provenance = {
        'source': 'fancyzhx/dbpedia_14@9abd46cf7fc8b4c64290f26993c540b92aa145ac',
        'license': 'Dataset card declares CC-BY-SA-3.0; also describes GFDL. Distribution review unresolved.',
        'citation': 'Zhang, Zhao and LeCun (2015), Character-level Convolutional Networks for Text Classification.',
        'rights_basis': 'Public research dataset with declared licenses; no exclusive ownership or redistribution certification.'}
    write('/output/provenance.json', provenance)
    for role in ('training', 'development'):
        write('/output/' + role + '.jsonl',
              [{k: row[k] for k in ('id', 'text', 'label')} for row in roles[role]], jsonl=True)
    write('/output/test-inputs.jsonl', [{k: row[k] for k in ('id', 'text')} for row in roles['test']], jsonl=True)
    write('/output/test-gold.jsonl', [{k: row[k] for k in ('id', 'label')} for row in roles['test']], jsonl=True)
    write('/output/test-groups.jsonl', [{'id': row['id'], 'group_id': groups[row['id']]} for row in roles['test']], jsonl=True)
    write('/output/labels.json', list(LABELS))
    files = {path.name: {'sha256': sha(path), 'bytes': path.stat().st_size}
             for path in Path('/output').iterdir() if path.is_file()}
    require('numpy' not in sys.modules and 'pandas' not in sys.modules, 'unexpected_python_dependency')
    write('/output/result.json', {'complete': True, 'files': files, 'salt': SALT, 'quotas': QUOTAS,
          'plan_sha256': sha('/plan.json'), 'history_result_sha256': sha('/history-result.json'),
          'source_counts': census.source_stats, 'eligibility': eligibility_stats, 'lineage': lineage,
          'global_census_unique_key_counts': dict(zip(('title', 'full_feature', 'content'),
                                                    (len(counter) for counter in census.counts))),
          'role_rows': {role: len(values) for role, values in roles.items()},
          'source_wide_eligibility_exclusions_precede_hash_ranking': True,
          'target_population_restricted_to_unique_unexposed_articles': True,
          'selected_row_removal_replacement_truncation_or_resampling': False,
          'model_fit_prediction_or_scoring': False, 'native_reader_only': True,
          'semantic_template_speaker_or_pretraining_independence_verified': False,
          'larger_trainer_feature_and_fit_work_readiness_verified': False,
          'mission_complete': False, 'production_certified': False})


if __name__ == '__main__':
    require(len(sys.argv) == 2 and sys.argv[1] in ('history', 'corpus'), 'explicit_stage_required')
    os.umask(0o077)
    {'history': history, 'corpus': corpus}[sys.argv[1]]()
