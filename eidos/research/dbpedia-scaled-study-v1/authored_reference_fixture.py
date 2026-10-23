"""Explicit original reference substitute; production refuses this script."""
from contracts import digest, inputs, pinned, read, require, validate_prediction, write
from study import configuration


if __name__ == '__main__':
    cfg, config_sha = configuration()
    require(cfg['authored_fixture_only'] is True and cfg['labels'] == ['A', 'B'], 'authored_fixture_only')
    raw = pinned('/data/test-inputs.jsonl', cfg['cohort_files']['test-inputs.jsonl'])
    values = inputs(raw, cfg['n'])
    output = []
    for value in values:
        index = 0 if 'Alpha' in value['text'] else 1
        output.append(validate_prediction({'id': value['id'], 'text_sha256': digest(value['text'].encode()),
            'predicted': cfg['labels'][index], 'probabilities': [1. if i == index else 0. for i in range(2)],
            'answered': True}, cfg['labels'], 'laya'))
    write('/output/predictions.jsonl', output, jsonl=True)
    write('/output/result.json', {'complete': True, 'stage': 'laya', 'config_sha256': config_sha,
          'rows': len(output), 'input_sha256': digest(raw),
          'predictions_sha256': digest(read('/output/predictions.jsonl')),
          'gold_or_peer_predictions_available': False, 'fit_or_scoring_performed': False,
          'authored_substitute_not_actual_LAYA': True})
