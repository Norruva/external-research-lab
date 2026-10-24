"""Data-only private artifacts for the separately versioned contextual model.

Hashes establish byte identity, not ownership or model quality. Callers must
retain an expected registry digest independently to authenticate a release.
This module never deserializes executable objects or discovers remote models.
"""
from array import array
from dataclasses import dataclass
import math
import os
from pathlib import Path
import sys

from .decision_artifact import (ArtifactError, SHA, canonical, digest,
                                identity_identifier, regular_bytes, strict_json)
from .decision_contextual import ARCHITECTURE, MODEL_FORMAT, DecisionNetwork

FORMAT = 'eidos-contextual-decision-artifact-v3'
REGISTRY_FORMAT = 'eidos-contextual-decision-registry-v3'
RECEIPT_FORMAT = 'eidos-owned-contextual-training-receipt-v3'
LINEAGE_FORMAT = 'eidos-owned-contextual-training-lineage-v3'
CAPABILITY = 'original_contextual_memory_research_not_general_semantic_certification'
WEIGHTS = 'weights.f64'
FILES = frozenset(('model.json', WEIGHTS, 'registry.json', 'TRAINING-RECEIPT.json'))
MAX_JSON = 4 * 1024**2
MAX_WEIGHTS = 4 * 1024**2
MAX_RECEIPT = 65_536
POLICY = {'review_required': True, 'operational_acceptance_enabled': False,
          'action_authority': False}


def need(condition, reason):
    if not condition:
        raise ArtifactError(reason)


def _pins(files):
    return {name: {'bytes': len(raw), 'sha256': digest(raw)}
            for name, raw in sorted(files.items())}


def _finite(value, lower=0):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= lower
    except OverflowError:
        return False


def _lineage(value):
    fields = {'format', 'input_sha256', 'provenance_sha256', 'declaration',
        'rights_verified', 'declaration_status', 'training_rows', 'source_groups',
        'source_group_ids_sha256', 'source_group_counts_sha256',
        'source_group_independence_verified', 'kind_counts', 'recipe', 'epochs',
        'seed', 'learning_rate', 'l2', 'l2_scope', 'gradient_clip_norm', 'optimizer',
        'checkpoint_selection', 'pretrained_or_vendor_model_used',
        'third_party_training_or_inference_packages', 'tokenizer_and_vocabulary_fitted_on',
        'raw_training_rows_embedded', 'privacy_guarantee', 'work_units_estimate',
        'work_units_limit', 'cpu_seconds_limit', 'wall_seconds_limit', 'source_module_sha256'}
    need(type(value) is dict and set(value) == fields and value.get('format') == LINEAGE_FORMAT and
         value.get('checkpoint_selection') == 'fixed_final_epoch_no_development_or_test_input' and
         value.get('rights_verified') is False and
         value.get('pretrained_or_vendor_model_used') is False and
         value.get('third_party_training_or_inference_packages') is False and
         value.get('tokenizer_and_vocabulary_fitted_on') == 'training_only',
         'contextual_training_lineage_required')
    for name in ('input_sha256', 'provenance_sha256', 'source_group_ids_sha256',
                 'source_group_counts_sha256'):
        need(type(value.get(name)) is str and SHA.fullmatch(value[name]),
             'contextual_training_digest_required')
    declaration = value.get('declaration')
    need(type(declaration) is dict and set(declaration) ==
         {'source', 'license', 'citation', 'rights_basis'} and
         all(type(v) is str and 1 <= len(v) <= 1000 and v.strip()
             for v in declaration.values()), 'contextual_rights_declaration_required')
    for name, low, high in (('training_rows', 2, 2000), ('source_groups', 1, 2000),
                            ('epochs', 1, 100), ('seed', 0, 2**31 - 1)):
        need(type(value.get(name)) is int and low <= value[name] <= high,
             'contextual_training_count_refused')
    need(value['source_groups'] <= value['training_rows'] and
         _finite(value.get('learning_rate'), .000001) and value['learning_rate'] <= .2,
         'contextual_training_settings_refused')
    need(value['source_group_independence_verified'] is False and
         value['raw_training_rows_embedded'] is False and value['privacy_guarantee'] is False and
         value['declaration_status'] == 'caller_supplied_not_independently_verified' and
         value['recipe'] == 'original-contextual-memory-adam-float64-v3' and
         value['optimizer'] == 'adam_float64' and value['l2'] == .00001 and
         value['l2_scope'] == 'all_parameters_including_biases' and value['gradient_clip_norm'] == 5.,
         'unsupported_contextual_training_recipe')
    kinds = value['kind_counts']
    need(type(kinds) is dict and kinds and set(kinds) <= {'CHOICE', 'BINARY', 'SCORE'} and
         all(type(count) is int and count >= 1 for count in kinds.values()) and
         sum(kinds.values()) == value['training_rows'], 'contextual_kind_counts_refused')
    need(type(value['work_units_estimate']) is int and
         1 <= value['work_units_estimate'] <= 1_000_000_000 and
         value['work_units_limit'] == 1_000_000_000 and
         value['cpu_seconds_limit'] == 600 and value['wall_seconds_limit'] == 900,
         'contextual_resource_lineage_refused')
    modules = value['source_module_sha256']
    required = {'decision_contextual.py', 'contextual_artifact.py', 'train_contextual.py',
                'contextual.py', 'decision.py', 'decision_contract.py',
                'decision_neural.py', 'decision_artifact.py', 'train_decision.py'}
    need(type(modules) is dict and set(modules) == required and
         all(type(pin) is str and SHA.fullmatch(pin) for pin in modules.values()),
         'contextual_source_lineage_refused')
    return value


def _network(document, weights):
    fields = {'format', 'id', 'version', 'network', 'weights', 'training',
              'calibration', 'policy', 'languages', 'capability_scope'}
    need(type(document) is dict and set(document) == fields and
         document['format'] == FORMAT, 'contextual_model_schema_mismatch')
    need(identity_identifier(document['id']) and identity_identifier(document['version']),
         'contextual_model_identity_refused')
    spec = document['network']
    need(type(spec) is dict and spec.get('architecture') == ARCHITECTURE and
         type(spec.get('parameter_count')) is int and
         1 <= spec['parameter_count'] <= MAX_WEIGHTS // 8,
         'unsupported_contextual_network')
    need(type(weights) is bytes and len(weights) == spec['parameter_count'] * 8 and
         0 < len(weights) <= MAX_WEIGHTS, 'contextual_weights_length_mismatch')
    need(type(document['weights']) is dict and document['weights'] == {
        'file': WEIGHTS, 'dtype': 'float64-little-endian', 'bytes': len(weights),
        'sha256': digest(weights)}, 'contextual_weights_identity_mismatch')
    need(document['calibration'] is None and document['policy'] == POLICY and
         type(document['policy']) is dict and
         all(document['policy'][name] is expected for name, expected in POLICY.items()),
         'unsupported_contextual_decision_policy')
    need(document['languages'] == ['en'] and document['capability_scope'] == CAPABILITY,
         'unsupported_contextual_capability')
    _lineage(document['training'])
    need(array('d').itemsize == 8, 'unsupported_float64_layout')
    parameters = array('d')
    parameters.frombytes(weights)
    if sys.byteorder != 'little':
        parameters.byteswap()
    need(all(math.isfinite(x) and abs(x) <= 1_000_000 for x in parameters),
         'nonfinite_contextual_parameters')
    try:
        model = DecisionNetwork.from_document({'format': MODEL_FORMAT,
                    'metadata': spec, 'parameters': list(parameters)})
        need(model.metadata() == spec, 'contextual_metadata_mismatch')
    except (TypeError, ValueError, KeyError, OverflowError) as error:
        raise ArtifactError('invalid_contextual_network') from error
    return model


def _diagnostics(value, model, training):
    fields = {'initial_training_cross_entropy', 'final_training_cross_entropy',
              'scope', 'optimizer_steps', 'parameter_count', 'vocabulary_size',
              'process_cpu_seconds', 'elapsed_seconds'}
    need(type(value) is dict and set(value) == fields and
         value['scope'] == 'training_fit_diagnostic_not_quality_evaluation_or_calibration',
         'contextual_diagnostics_schema_refused')
    for name in ('initial_training_cross_entropy', 'final_training_cross_entropy',
                 'process_cpu_seconds', 'elapsed_seconds'):
        need(_finite(value[name]), 'nonfinite_contextual_diagnostics')
    for name, expected in (('optimizer_steps', training['epochs'] * training['training_rows']),
                           ('parameter_count', model.parameter_count),
                           ('vocabulary_size', len(model.metadata()['vocabulary']))):
        need(type(value[name]) is int and value[name] == expected,
             'contextual_diagnostics_count_mismatch')


@dataclass(frozen=True)
class ContextualArtifact:
    model: DecisionNetwork
    identity: dict
    registry_sha256: str
    training: dict


def load_artifact(directory, expected_registry_sha256=None):
    root = Path(directory).absolute()
    need(not any(p.is_symlink() for p in (root, *root.parents)) and root.is_dir() and
         not root.stat().st_mode & 0o077, 'private_contextual_artifact_required')
    need({p.name for p in root.iterdir()} == FILES,
         'complete_contextual_artifact_files_required')
    for name in FILES:
        need(not (root / name).is_symlink() and not (root / name).stat().st_mode & 0o177,
             'private_nonexecutable_contextual_files_required')
    registry_raw = regular_bytes(root / 'registry.json', 16_384)
    registry_sha = digest(registry_raw)
    if expected_registry_sha256 is not None:
        need(type(expected_registry_sha256) is str and
             SHA.fullmatch(expected_registry_sha256) and
             expected_registry_sha256 == registry_sha,
             'contextual_registry_digest_mismatch')
    registry = strict_json(registry_raw, 16_384)
    need(type(registry) is dict and set(registry) ==
         {'format', 'model_file', 'model_sha256'} and
         registry['format'] == REGISTRY_FORMAT and registry['model_file'] == 'model.json' and
         type(registry['model_sha256']) is str and SHA.fullmatch(registry['model_sha256']),
         'contextual_registry_schema_mismatch')
    model_raw = regular_bytes(root / 'model.json', MAX_JSON)
    need(digest(model_raw) == registry['model_sha256'], 'contextual_model_digest_mismatch')
    document = strict_json(model_raw, MAX_JSON)
    weights = regular_bytes(root / WEIGHTS, MAX_WEIGHTS)
    model = _network(document, weights)
    identity = {'id': document['id'], 'version': document['version'],
                'sha256': digest(model_raw), 'feature_version': ARCHITECTURE,
                'calibration_status': 'unfitted'}
    receipt = strict_json(regular_bytes(root / 'TRAINING-RECEIPT.json', MAX_RECEIPT), MAX_RECEIPT)
    pins = _pins({'model.json': model_raw, WEIGHTS: weights, 'registry.json': registry_raw})
    receipt_fields = {'format', 'complete', 'model', 'files', 'training', 'diagnostics',
        'third_party_training_or_inference_packages', 'operational_acceptance_enabled',
        'model_promoted', 'evaluation_or_calibration_performed', 'privacy_guarantee',
        'rights_declaration_independently_verified', 'new_benchmark_or_rival_quality_evidence'}
    need(type(receipt) is dict and set(receipt) == receipt_fields and receipt.get('format') == RECEIPT_FORMAT and
         receipt.get('complete') is True and receipt.get('files') == pins and
         receipt.get('model') == identity and receipt.get('training') == document['training'] and
         receipt.get('model_promoted') is False and
         receipt.get('operational_acceptance_enabled') is False and
         receipt.get('third_party_training_or_inference_packages') is False and
         receipt.get('evaluation_or_calibration_performed') is False and
         receipt.get('privacy_guarantee') is False and
         receipt.get('rights_declaration_independently_verified') is False and
         receipt.get('new_benchmark_or_rival_quality_evidence') is False,
         'complete_contextual_training_receipt_required')
    _diagnostics(receipt.get('diagnostics'), model, document['training'])
    return ContextualArtifact(model, identity, registry_sha, document['training'])


def write_artifact(directory, model, model_id, version, training, diagnostics):
    """Write one new complete private artifact; never overwrite or activate it."""
    root = Path(directory).absolute()
    need(identity_identifier(model_id) and identity_identifier(version),
         'invalid_contextual_model_identity')
    need(not root.exists() and not root.is_symlink(), 'contextual_output_already_exists')
    need(not any(p.is_symlink() for p in (root.parent, *root.parent.parents)) and root.parent.is_dir(),
         'contextual_output_parent_refused')
    parameters = array('d', model.parameters)
    need(parameters.itemsize == 8, 'unsupported_float64_layout')
    if sys.byteorder != 'little':
        parameters.byteswap()
    weights = parameters.tobytes()
    document = {'format': FORMAT, 'id': model_id, 'version': version,
        'network': model.metadata(),
        'weights': {'file': WEIGHTS, 'dtype': 'float64-little-endian',
                    'bytes': len(weights), 'sha256': digest(weights)},
        'training': training, 'calibration': None, 'policy': dict(POLICY),
        'languages': ['en'], 'capability_scope': CAPABILITY}
    checked = _network(document, weights)
    _diagnostics(diagnostics, checked, training)
    model_raw = canonical(document) + b'\n'
    registry_raw = canonical({'format': REGISTRY_FORMAT, 'model_file': 'model.json',
                              'model_sha256': digest(model_raw)}) + b'\n'
    payloads = {'model.json': model_raw, WEIGHTS: weights, 'registry.json': registry_raw}
    identity = {'id': model_id, 'version': version, 'sha256': digest(model_raw),
                'feature_version': ARCHITECTURE, 'calibration_status': 'unfitted'}
    receipt = {'format': RECEIPT_FORMAT, 'complete': True, 'model': identity,
        'files': _pins(payloads), 'training': training, 'diagnostics': diagnostics,
        'third_party_training_or_inference_packages': False,
        'operational_acceptance_enabled': False, 'model_promoted': False,
        'evaluation_or_calibration_performed': False, 'privacy_guarantee': False,
        'rights_declaration_independently_verified': False,
        'new_benchmark_or_rival_quality_evidence': False}
    payloads['TRAINING-RECEIPT.json'] = canonical(receipt) + b'\n'
    need(len(model_raw) <= MAX_JSON and len(payloads['TRAINING-RECEIPT.json']) <= MAX_RECEIPT,
         'contextual_artifact_metadata_size_refused')
    root.mkdir(mode=0o700)
    written = []
    try:
        for name, raw in sorted(payloads.items(), key=lambda item: item[0] == 'TRAINING-RECEIPT.json'):
            fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            written.append(root / name)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        need(load_artifact(root).identity == identity, 'contextual_written_identity_mismatch')
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        root.rmdir()
        raise
    return receipt
