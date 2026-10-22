"""Build a new review-only support registry from caller-supplied labeled data.

Uses only Python's standard library. Never activates a release or executes an
action. No corpus, old model, benchmark predictions or hosted service is read.
"""
import argparse
import collections
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

from .contracts import ContractError, finite_json, parse_json
from .general_linear import FEATURES, terms
from .model import canonical, words
from .support_memory import read_regular, write_memory

MAX_INPUT = 2 * 1024**2
MAX_TEXT = 200_000
MAX_OBSERVED_TERMS = 100_000
MAX_NNZ = 500_000
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z', re.ASCII)


class TrainingError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def parse_training(raw, provenance_raw):
    if not 1 <= len(raw) <= MAX_INPUT or len(provenance_raw) > 16_000:
        raise TrainingError('input_size_limit')
    provenance = parse_json(provenance_raw.decode('utf-8'), maximum=16_000)
    if type(provenance) is not dict or set(provenance) != {'source', 'license', 'citation', 'rights_basis'}:
        raise TrainingError('provenance_fields_required')
    if any(type(v) is not str or not 1 <= len(v) <= 1_000 or not v.strip() for v in provenance.values()):
        raise TrainingError('invalid_provenance')
    canonical(provenance)  # Refuse unpaired Unicode surrogates.
    lines = raw.decode('utf-8').splitlines()
    if not 40 <= len(lines) <= 5_120 or any(not line.strip() for line in lines):
        raise TrainingError('row_count_or_blank_line')
    rows = []
    ids, normalized = set(), set()
    total = 0
    for line in lines:
        row = parse_json(line, maximum=64_000)
        finite_json(row, max_nodes=20, max_text=15_000)
        if type(row) is not dict or set(row) != {'id', 'text', 'label'}:
            raise TrainingError('row_fields_required')
        if not identifier(row['id']) or not identifier(row['label']):
            raise TrainingError('invalid_id_or_label')
        if type(row['text']) is not str or not 1 <= len(row['text']) <= 12_000:
            raise TrainingError('invalid_text')
        row['text'].encode('utf-8')
        text_key = ' '.join(words(row['text']))
        if not text_key:
            raise TrainingError('empty_word_sequence')
        if row['id'] in ids:
            raise TrainingError('duplicate_id')
        if text_key in normalized:
            raise TrainingError('duplicate_normalized_text')
        total += len(row['text'])
        if total > MAX_TEXT:
            raise TrainingError('aggregate_text_limit')
        ids.add(row['id'])
        normalized.add(text_key)
        rows.append(row)
    counts = collections.Counter(row['label'] for row in rows)
    if not 2 <= len(counts) <= 256 or set(counts.values()) != {20}:
        raise TrainingError('exactly_20_rows_per_class_required')
    return sorted(rows, key=lambda r: (r['label'], r['id'])), provenance


def build(input_path, provenance_path, output_path, model_id, version='1'):
    if not identifier(model_id) or not identifier(version):
        raise TrainingError('invalid_model_id_or_version')
    output = Path(output_path).absolute()
    if output.exists() or output.is_symlink():
        raise TrainingError('output_already_exists')
    if not output.parent.is_dir() or any(p.is_symlink() for p in (output.parent, *output.parent.parents)):
        raise TrainingError('output_parent_must_be_existing_regular_directory')
    raw = read_regular(input_path, MAX_INPUT)
    provenance_raw = read_regular(provenance_path, 16_000)
    rows, provenance = parse_training(raw, provenance_raw)
    labels = sorted({r['label'] for r in rows})
    df = collections.Counter()
    for row in rows:
        df.update(set(terms(row['text'])))
        if len(df) > MAX_OBSERVED_TERMS:
            raise TrainingError('observed_term_limit')
    vocabulary = sorted(sorted(df, key=lambda t: (-df[t], t))[:10_000])
    idf = [math.log((1 + len(rows)) / (1 + df[t])) + 1 for t in vocabulary]
    index = {t: i for i, t in enumerate(vocabulary)}
    records = []
    nnz = 0
    for row in rows:
        counts = collections.Counter(terms(row['text']))
        pairs = sorted((index[t], math.log1p(n) * idf[index[t]]) for t, n in counts.items() if t in index)
        norm = math.sqrt(math.fsum(v*v for _, v in pairs))
        if not norm:
            raise TrainingError('support_without_selected_features')
        pairs = [(i, v/norm) for i, v in pairs]
        nnz += len(pairs)
        if nnz > MAX_NNZ:
            raise TrainingError('sparse_vector_limit')
        opaque = 'support-' + digest(canonical({'id': row['id'], 'label': row['label'], 'words': words(row['text'])}))[:32]
        records.append((labels.index(row['label']), opaque, pairs))
    lineage = {'format': 'eidos-caller-support-training-v1', 'input_sha256': digest(raw),
               'provenance_sha256': digest(provenance_raw), 'declaration': provenance,
               'declaration_status': 'caller_supplied_not_independently_verified',
               'support_rows': len(rows), 'per_class': 20, 'training_recipe': 'support-only-tfidf-top5-logmeanexp-10cosine-v1',
               'input_schema': 'id,text,label JSONL', 'raw_training_text_embedded': False,
               'privacy_guarantee': False, 'benchmark_or_calibration_evidence_created': False}
    document = {'format': 'eidos-support-memory-v1', 'id': model_id, 'version': version,
                'feature_version': FEATURES, 'labels': labels, 'vocabulary': vocabulary, 'idf': idf,
                'temperatures': {str(k): 1. for k in range(2, len(labels)+1)},
                'training': lineage, 'calibration': None, 'languages': ['en'],
                'domain': 'Caller-supplied labeled support; English research profile; human review required',
                'consequential': True, 'thresholds': {'probability': 1., 'margin': 1., 'coverage': .35}}
    output.mkdir(mode=0o700, exist_ok=False)
    # The existing checked writer publishes registry.json only after artifact
    # validation. A filesystem failure may leave this new directory incomplete;
    # never remove or overwrite caller files and never activate a registry.
    model = write_memory(output, document, records)
    files = {name: {'bytes': (output/name).stat().st_size, 'sha256': digest((output/name).read_bytes())}
             for name in ('model.json', 'support.sparse', 'registry.json')}
    receipt = {'format': 'eidos-owned-support-training-receipt-v1', 'complete': True,
               'model': model.identity, 'classes': len(labels), 'support_rows': len(rows),
               'features': len(vocabulary), 'nnz': nnz, 'files': files,
               'input_sha256': digest(raw), 'provenance_sha256': digest(provenance_raw),
               'third_party_training_or_inference_packages': False,
               'operational_acceptance_enabled': False, 'model_promoted': False,
               'evaluation_or_calibration_performed': False, 'privacy_guarantee': False}
    with (output/'TRAINING-RECEIPT.json').open('xb') as stream:
        stream.write(canonical(receipt) + b'\n')
    for name in (*files, 'TRAINING-RECEIPT.json'):
        (output/name).chmod(0o400, follow_symlinks=False)
    return receipt


def main():
    parser = argparse.ArgumentParser(description='Train a NEW local review-only support registry, using standard-library Python')
    parser.add_argument('--input', required=True, help='UTF-8 JSONL: id,text,label; exactly20 distinct rows/class')
    parser.add_argument('--provenance', required=True, help='JSON: source,license,citation,rights_basis; caller declaration')
    parser.add_argument('--output', required=True, help='NEW registry directory; existing output is never overwritten')
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--version', default='1')
    args = parser.parse_args()
    previous_mask = os.umask(0o077)
    try:
        receipt = build(args.input, args.provenance, args.output, args.model_id, args.version)
        print(json.dumps(receipt, sort_keys=True, allow_nan=False))
        return 0
    except TrainingError as error:
        print(json.dumps({'error': 'support_training_refused', 'reason': error.code}), file=sys.stderr)
        return 2
    except (ContractError, ValueError, TypeError, OSError, UnicodeError, MemoryError):
        print(json.dumps({'error': 'support_training_refused', 'reason': 'invalid_input_or_artifact'}), file=sys.stderr)
        return 2
    finally:
        os.umask(previous_mask)


if __name__ == '__main__':
    raise SystemExit(main())
