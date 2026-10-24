"""Checked private artifacts for original question-conditioned decision models.

No executable serialization, dynamic imports, remote resources or model-service
calls are used. Hashes establish byte identity; callers need an independently
retained registry digest to authenticate an expected release.
"""
from array import array
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

from .decision_neural import DecisionNetwork

FORMAT = 'eidos-question-conditioned-model-v1'
REGISTRY_FORMAT = 'eidos-decision-registry-v1'
RECEIPT_FORMAT = 'eidos-owned-decision-training-receipt-v1'
WEIGHTS = 'weights.f64'
FILES = {'model.json', WEIGHTS, 'registry.json', 'TRAINING-RECEIPT.json'}
MAX_JSON = 4 * 1024**2
MAX_WEIGHTS = 4 * 1024**2
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z', re.ASCII)
SHA = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)


class ArtifactError(ValueError):
    pass


def need(value, reason):
    if not value:
        raise ArtifactError(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw, maximum=MAX_JSON):
    need(type(raw) is bytes and 0 < len(raw) <= maximum, 'metadata_size_refused')
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate_metadata_key')
            result[key] = value
        return result
    def nonfinite(_value):
        raise ArtifactError('nonfinite_metadata')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                            parse_constant=nonfinite)
        canonical(result)
        return result
    except ArtifactError:
        raise
    except (UnicodeError, ValueError, RecursionError, OverflowError) as error:
        raise ArtifactError('malformed_metadata') from error


def regular_bytes(path, maximum):
    """Read bounded regular bytes; refuse symlinks, devices and changed files."""
    p = Path(path).absolute()
    need(not any(part.is_symlink() for part in (p, *p.parents)), 'symlink_refused')
    info = p.stat()
    need(stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= maximum,
         'regular_file_and_size_required')
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    need(len(raw) == info.st_size and
         (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
         (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
         'artifact_changed_during_read')
    return raw


def identity_identifier(value):
    return type(value) is str and ID.fullmatch(value) is not None


def _pins(files):
    return {name: {'bytes': len(raw), 'sha256': digest(raw)}
            for name, raw in sorted(files.items())}


def _network(document, weights):
    expected = {'format', 'id', 'version', 'network', 'weights', 'training',
                'calibration', 'policy', 'languages', 'capability_scope'}
    need(type(document) is dict and set(document) == expected, 'model_schema_mismatch')
    need(document['format'] == FORMAT and identity_identifier(document['id']) and
         identity_identifier(document['version']), 'model_identity_refused')
    spec = document['network']
    need(type(spec) is dict and set(spec) == {'architecture', 'dimension', 'hidden',
         'vocabulary', 'parameter_count', 'tokenizer', 'span_tokens'}, 'network_schema_mismatch')
    need(spec['architecture'] == 'eidos-question-conditioned-attention-mlp-v2' and
         spec['tokenizer'] == 'eidos-unicode-word-punctuation-v1' and
         type(spec['span_tokens']) is int and spec['span_tokens'] == 48,
         'unsupported_network_version')
    need(type(spec['dimension']) is int and 2 <= spec['dimension'] <= 32 and
         type(spec['hidden']) is int and 2 <= spec['hidden'] <= 64 and
         type(spec['parameter_count']) is int and
         1 <= spec['parameter_count'] <= MAX_WEIGHTS // 8, 'network_dimension_refused')
    need(type(spec['vocabulary']) is list and 1 <= len(spec['vocabulary']) <= 4096 and
         all(type(term) is str and 1 <= len(term) <= 256 for term in spec['vocabulary']),
         'network_vocabulary_refused')
    need(type(document['weights']) is dict and document['weights'] == {
         'file': WEIGHTS, 'dtype': 'float64-little-endian', 'bytes': len(weights),
         'sha256': digest(weights)}, 'weights_identity_mismatch')
    need(len(weights) == spec['parameter_count'] * 8 and 0 < len(weights) <= MAX_WEIGHTS,
         'weights_length_mismatch')
    policy = document['policy']
    need(document['calibration'] is None and type(policy) is dict and set(policy) ==
         {'review_required', 'operational_acceptance_enabled', 'action_authority'} and
         policy['review_required'] is True and policy['operational_acceptance_enabled'] is False and
         policy['action_authority'] is False, 'unsupported_decision_policy')
    need(document['languages'] == ['en'] and
         document['capability_scope'] == 'original_compact_question_conditioned_research_not_general_semantic_certification',
         'unsupported_capability_declaration')
    need(type(document['training']) is dict and document['training'].get('format') ==
         'eidos-owned-decision-training-lineage-v1' and
         document['training'].get('checkpoint_selection') == 'fixed_final_epoch_no_development_or_test_input' and
         document['training'].get('rights_verified') is False and
         document['training'].get('pretrained_or_vendor_model_used') is False,
         'training_lineage_required')
    need(array('d').itemsize == 8, 'unsupported_float64_layout')
    parameters = array('d')
    parameters.frombytes(weights)
    if sys.byteorder != 'little':
        parameters.byteswap()
    need(all(math.isfinite(x) and abs(x) <= 1_000_000 for x in parameters),
         'nonfinite_or_unbounded_parameters')
    try:
        model = DecisionNetwork(spec['vocabulary'], spec['dimension'], spec['hidden'], parameters)
        need(model.metadata() == spec, 'network_metadata_mismatch')
    except (TypeError, ValueError, KeyError, OverflowError) as error:
        raise ArtifactError('invalid_network_parameters') from error
    return model


@dataclass(frozen=True)
class DecisionArtifact:
    model: DecisionNetwork
    identity: dict
    registry_sha256: str
    training: dict


def load_artifact(directory, expected_registry_sha256=None):
    root = Path(directory).absolute()
    need(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)),
         'artifact_directory_refused')
    need({p.name for p in root.iterdir()} == FILES, 'complete_artifact_files_required')
    registry_raw = regular_bytes(root / 'registry.json', 16_384)
    registry_sha = digest(registry_raw)
    if expected_registry_sha256 is not None:
        need(type(expected_registry_sha256) is str and
             SHA.fullmatch(expected_registry_sha256) is not None and
             expected_registry_sha256 == registry_sha, 'release_registry_digest_mismatch')
    registry = strict_json(registry_raw, 16_384)
    need(type(registry) is dict and set(registry) == {'format', 'model_file', 'model_sha256'} and
         registry['format'] == REGISTRY_FORMAT and registry['model_file'] == 'model.json' and
         type(registry['model_sha256']) is str and SHA.fullmatch(registry['model_sha256']) is not None,
         'registry_schema_mismatch')
    model_raw = regular_bytes(root / 'model.json', MAX_JSON)
    need(digest(model_raw) == registry['model_sha256'], 'model_digest_mismatch')
    document = strict_json(model_raw)
    weights = regular_bytes(root / WEIGHTS, MAX_WEIGHTS)
    model = _network(document, weights)
    receipt = strict_json(regular_bytes(root / 'TRAINING-RECEIPT.json', 65_536), 65_536)
    pins = _pins({'model.json': model_raw, WEIGHTS: weights, 'registry.json': registry_raw})
    identity = {'id': document['id'], 'version': document['version'],
                'sha256': digest(model_raw), 'feature_version': document['network']['architecture'],
                'calibration_status': 'unfitted'}
    need(type(receipt) is dict and receipt.get('format') == RECEIPT_FORMAT and
         receipt.get('complete') is True and receipt.get('files') == pins and
         receipt.get('model') == identity and receipt.get('training') == document['training'] and
         receipt.get('model_promoted') is False and
         receipt.get('operational_acceptance_enabled') is False,
         'complete_training_receipt_required')
    return DecisionArtifact(model, identity, registry_sha, document['training'])


def write_artifact(directory, model, model_id, version, training, diagnostics):
    """Publish a NEW private artifact and a complete final receipt; never activate."""
    root = Path(directory).absolute()
    need(identity_identifier(model_id) and identity_identifier(version), 'invalid_model_identity')
    need(not root.exists() and not root.is_symlink(), 'output_already_exists')
    need(root.parent.is_dir() and not any(p.is_symlink() for p in (root.parent, *root.parent.parents)),
         'output_parent_refused')
    parameters = array('d', model.parameters)
    need(parameters.itemsize == 8, 'unsupported_float64_layout')
    if sys.byteorder != 'little':
        parameters.byteswap()
    weights = parameters.tobytes()
    document = {'format': FORMAT, 'id': model_id, 'version': version,
        'network': model.metadata(),
        'weights': {'file': WEIGHTS, 'dtype': 'float64-little-endian',
                    'bytes': len(weights), 'sha256': digest(weights)},
        'training': training, 'calibration': None,
        'policy': {'review_required': True, 'operational_acceptance_enabled': False,
                   'action_authority': False}, 'languages': ['en'],
        'capability_scope': 'original_compact_question_conditioned_research_not_general_semantic_certification'}
    _network(document, weights)  # Validate before any output is created.
    model_raw = canonical(document) + b'\n'
    registry_raw = canonical({'format': REGISTRY_FORMAT, 'model_file': 'model.json',
                              'model_sha256': digest(model_raw)}) + b'\n'
    payloads = {'model.json': model_raw, WEIGHTS: weights, 'registry.json': registry_raw}
    identity = {'id': model_id, 'version': version, 'sha256': digest(model_raw),
                'feature_version': document['network']['architecture'], 'calibration_status': 'unfitted'}
    receipt = {'format': RECEIPT_FORMAT, 'complete': True, 'model': identity,
        'files': _pins(payloads), 'training': training, 'diagnostics': diagnostics,
        'third_party_training_or_inference_packages': False,
        'operational_acceptance_enabled': False, 'model_promoted': False,
        'evaluation_or_calibration_performed': False, 'privacy_guarantee': False,
        'rights_declaration_independently_verified': False,
        'new_benchmark_or_rival_quality_evidence': False}
    payloads['TRAINING-RECEIPT.json'] = canonical(receipt) + b'\n'
    need(len(model_raw) <= MAX_JSON and len(payloads['TRAINING-RECEIPT.json']) <= 65_536,
         'artifact_metadata_size_refused')
    root.mkdir(mode=0o700, exist_ok=False)
    for name, raw in sorted(payloads.items(), key=lambda item: item[0] == 'TRAINING-RECEIPT.json'):
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    loaded = load_artifact(root)
    need(loaded.identity == identity, 'published_artifact_identity_mismatch')
    return receipt
