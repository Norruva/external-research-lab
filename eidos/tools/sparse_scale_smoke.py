"""Exercise bounded training capacity with original generated mechanical data.

Not a corpus benchmark, semantic accuracy test or throughput promise. No network,
reference model, existing registry/configuration, installation or model activation.
Run from the source checkout with python3 -S -B tools/sparse_scale_smoke.py.
Use external process limits when validating capacity.
"""
import hashlib
import json
from pathlib import Path
import resource
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eidos.engine import Engine
from eidos.model import canonical
from eidos.train_sparse import build


def main():
    if not (sys.flags.no_site and sys.flags.dont_write_bytecode and sys.version_info >= (3, 12)):
        raise RuntimeError('Use Python3.12+ with -S -B')
    started = time.monotonic()
    cpu_started = time.process_time()
    n, k = 20_000, 14
    with tempfile.TemporaryDirectory(prefix='eidos-sparse-capacity-') as temporary:
        root = Path(temporary)
        source = root / 'authored.jsonl'
        provenance = root / 'provenance.json'
        with source.open('xb') as stream:
            for i in range(n):
                label = 'topic-' + chr(ord('a') + i % k)
                stream.write(canonical({'id': 'original-' + str(i), 'label': label,
                    'text': 'Original authored mechanics example ' + label +
                            ' number ' + str(i) + ' with a distinct numeric token.'}) + b'\n')
        provenance.write_bytes(canonical({'source': 'Original generated capacity fixture',
            'license': 'CC0-1.0', 'citation': 'EIDOS mechanical capacity check',
            'rights_basis': 'Original authored text; no benchmark, private data or reference output'}))
        receipt = build(source, provenance, root / 'registry', 'authored-capacity')
        assert receipt['training_rows'] == n and receipt['classes'] == k
        assert sum(receipt['class_counts'].values()) == n
        assert len(set(receipt['class_counts'].values())) == 2
        engine = Engine.from_registry(root / 'registry', tenants=('authored-capacity',))
        model = engine.models['authored-capacity']
        response = engine.decide({'schema_version': '1.0', 'input_version': 'authored-capacity',
            'request_id': 'original-capacity-query', 'tenant_id': 'authored-capacity', 'language': 'en',
            'state': {'utterance': 'An original question for topic a after the capacity build'}, 'sources': [],
            'questions': [{'id': 'q', 'kind': 'CHOICE', 'profile': 'authored-capacity',
                'prompt': 'Classify this authored sentence', 'state_path': ['utterance'],
                'choices': [{'id': label, 'class_id': label} for label in model.data['labels']]}]})
        decision = response['decisions'][0]
        assert decision['status'] == 'abstained' and decision['value'] is None
        assert decision['review_required'] and decision['calibrated_confidence'] is None
        assert len(decision['distribution']) == k
        assert response['authorization'] == 'recommendation_only_no_action_authorized'
        result = {'complete': True, 'scope': 'original_authored_capacity_fixture_only',
            'training_rows': n, 'classes': k, 'class_counts': receipt['class_counts'],
            'features': receipt['features'], 'nnz': receipt['nnz'], 'epochs': receipt['epochs'],
            'work_units': receipt['work_units'], 'input_bytes': source.stat().st_size,
            'training_receipt_sha256': hashlib.sha256((root / 'registry/TRAINING-RECEIPT.json').read_bytes()).hexdigest(),
            'cpu_seconds': time.process_time() - cpu_started,
            'elapsed_seconds': time.monotonic() - started,
            'peak_self_rss_kib_linux': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'actual_engine_all_class_review_policy_verified': True,
            'semantic_quality_or_rival_comparison_performed': False,
            'external_runtime_package_used': False, 'existing_configuration_or_model_read': False,
            'model_promoted': False, 'temporary_artifacts_removed': False}
    result['temporary_artifacts_removed'] = True
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
