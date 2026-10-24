"""Package only a completed study's owned registry; never train/infer/activate."""
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

ROOT = Path('/workspace/eidos')
BASE = ROOT / 'evaluation/production/dbpedia-scaled-study-v1'
RUN = BASE / 'v1'
PREFIX = 'eidos-dbpedia-scaled-owned-model-v1'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    p = Path(path)
    assert p.is_file() and not any(a.is_symlink() for a in (p, *p.parents))
    return json.loads(p.read_bytes())


def main():
    assert read(RUN / 'execution-summary.json')['complete'] is True
    review = read(BASE / 'completion-review-v1.json')
    assert review['complete'] is True and review['study_completed_successfully'] is True
    assert review['review_source_sha256'] == digest((ROOT / 'review_dbpedia_scaled_completion_v1.py').read_bytes())
    score = read(RUN / 'score/data/result.json')
    assert score['complete'] and score['n'] == 1400 and score['reference_is_authored_substitute'] is False
    assert review['score_sha256'] == digest((RUN / 'score/data/result.json').read_bytes())
    frozen = read(RUN / 'primary-freeze.json')
    trained = read(RUN / 'train/data/result.json')
    assert trained['complete'] and trained['model_promoted'] is False
    assert digest((RUN / 'train/data/result.json').read_bytes()) == frozen['training_result_sha256']
    registry = RUN / 'train/data/registry'
    names = {'model.json', 'weights.f32', 'registry.json', 'TRAINING-RECEIPT.json'}
    assert {p.name for p in registry.iterdir()} == names
    files = {}
    for name in sorted(names):
        p = registry / name
        assert p.is_file() and not any(a.is_symlink() for a in (p, *p.parents))
        assert p.stat().st_mode & 0o777 == 0o400
        raw = p.read_bytes()
        assert digest(raw) == frozen['files'][name]
        assert trained['registry_files'][name] == {'bytes': len(raw), 'sha256': digest(raw)}
        files['registry/' + name] = raw
    cfg = read(RUN / 'config.json')
    assert digest((RUN / 'config.json').read_bytes()) == frozen['config_sha256']
    request = {'schema_version': '1.0', 'input_version': 'authored-artifact-demo',
        'request_id': 'original-artifact-example', 'tenant_id': 'owned-artifact-demo', 'language': 'en',
        'state': {'utterance': 'An educational institution teaching university courses.'}, 'sources': [],
        'questions': [{'id': 'category', 'kind': 'CHOICE', 'profile': 'dbpedia_scaled_sparse',
            'prompt': 'Classify this article', 'state_path': ['utterance'],
            'choices': [{'id': label, 'class_id': label} for label in cfg['labels']]}]}
    files['request.example.json'] = json.dumps(request, ensure_ascii=False, sort_keys=True, indent=2).encode() + b'\n'
    files['README.md'] = b'''# Private owned EIDOS model\n\nThis archive contains one stdlib sparse-SGD registry from the approved DBpedia study with 14,000 training examples. Training used a fixed recipe of three epochs and seed 113, with no pretrained EIDOS weights. The original implementation and inference are locally controlled; the publicly licensed training text is not exclusive property.\n\nKeep this archive private. Dataset and model attribution, share-alike and redistribution obligations remain unresolved. The vocabulary and weights can disclose training information; no privacy guarantee is provided.\n\nThe model is uncalibrated and covers English inputs within the 14 known classes. This package retains the study's review-only policy (value=null) and does not authorize actions. Packaging performs no calibration, threshold change, serving activation or production certification. The attached request is original authored material and was not drawn from the benchmark. It was not executed during packaging and has no promised result. The separate study report defines the observed comparison and its limits.\n\nExtract in a private cloud directory. From the public source checkout's eidos directory, an optional caller-controlled request is:\n\n```sh\npython3 -S -B -m eidos --registry /absolute/path/eidos-dbpedia-scaled-owned-model-v1/registry --tenant owned-artifact-demo < /absolute/path/eidos-dbpedia-scaled-owned-model-v1/request.example.json\n```\n\nThe packager does not execute this command. Inference requires Python 3.12+ on POSIX with the standard SQLite and OpenSSL modules. It needs no pip or npm packages or hosted model service.\n\nThe archive contains no source corpus, gold labels, per-case predictions, LAYA tensors, native packages, credentials, environment configuration, keys or application state.\n'''
    files['MANIFEST.json'] = json.dumps({'format': 'eidos-dbpedia-scaled-owned-model-manifest-v1',
        'scope': 'private_owned_registry_and_unexecuted_authored_example',
        'training_result_sha256': frozen['training_result_sha256'],
        'authoritative_score_sha256': digest((RUN / 'score/data/result.json').read_bytes()),
        'completion_review_sha256': digest((BASE / 'completion-review-v1.json').read_bytes()),
        'model': trained['model_identity'], 'files': {name: {'bytes': len(raw), 'sha256': digest(raw)}
            for name, raw in sorted(files.items())}, 'model_promoted': False,
        'redistribution_rights_certified': False}, sort_keys=True, indent=2).encode() + b'\n'
    assert len(files) == 7
    destination = ROOT / (PREFIX + '.tar.gz')
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        with tarfile.open(fileobj=stream, mode='w:gz', format=tarfile.USTAR_FORMAT) as archive:
            for name, raw in sorted(files.items()):
                item = tarfile.TarInfo(PREFIX + '/' + name)
                item.mode, item.size, item.mtime = 0o400, len(raw), 0
                archive.addfile(item, io.BytesIO(raw))
    with tarfile.open(destination, 'r:gz') as archive:
        assert {m.name.removeprefix(PREFIX + '/'): archive.extractfile(m).read()
                for m in archive.getmembers()} == files
    value = {'format': 'eidos-dbpedia-scaled-owned-model-packaging-v1', 'complete': True,
        'file': str(destination), 'bytes': destination.stat().st_size, 'sha256': digest(destination.read_bytes()),
        'files_verified': len(files), 'owned_model': trained['model_identity'],
        'fit_prediction_scoring_installation_activation_or_network_performed': False,
        'raw_corpus_gold_percase_predictions_native_or_keys_included': False,
        'private_distribution_rights_unresolved': True, 'model_promoted': False}
    receipt = ROOT / (PREFIX + '-receipt.json')
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
    print(json.dumps(value))


if __name__ == '__main__':
    main()
