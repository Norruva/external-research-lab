"""Explicit isolated stages for a future approved comparison. Import is inert."""
import collections
import json
import math
import os
from pathlib import Path
import resource
import sys
import time

from contracts import (align, canonical, choose_candidate, digest, inputs, parse,
                       pinned, read, require, rows, validate_prediction, write)


def configuration():
    raw = read('/config.json')
    value = parse(raw)
    labels = value['labels']
    require(type(labels) is list and labels == sorted(set(labels)) and 2 <= len(labels) <= 14, 'labels')
    require(set(value['reference_aliases']) == set(labels) and
            len(set(value['reference_aliases'].values())) == len(labels), 'reference_aliases')
    return value, digest(raw)


def model(path, cfg):
    from eidos.evaluate_owned import registry
    engine, profile, files = registry(path)
    require(profile.data['labels'] == cfg['labels'] and
            profile.data['training']['input_sha256'] == cfg['cohort_files']['support.jsonl']['sha256'] and
            profile.data['training']['provenance_sha256'] == cfg['cohort_files']['provenance.json']['sha256'],
            'owned_model_lineage')
    return engine, profile, files


def owned_predict(engine, profile, values, labels):
    from eidos.evaluate_owned import request
    output = []
    for row in values:
        decision = engine.decide(request(profile.data['id'], row, labels))['decisions'][0]
        require(decision['status'] == 'abstained' and decision['value'] is None and
                decision['review_required'] is True and decision['calibrated_confidence'] is None and
                [x['class_id'] for x in decision['distribution']] == labels, 'review_only_complete_distribution')
        record = {'id': row['id'], 'text_sha256': digest(row['text'].encode()),
                  'predicted': decision['candidate_value'], 'answered': False,
                  'logits': [x['logit'] for x in decision['distribution']],
                  'probabilities': [x['probability'] for x in decision['distribution']]}
        output.append(validate_prediction(record, labels, 'eidos'))
    return output


def train(name, cfg, config_sha):
    from eidos import train_linear_support, train_support
    for filename in ('support.jsonl', 'provenance.json'):
        pinned('/data/' + filename, cfg['cohort_files'][filename])
    recipe = cfg['recipes'][name]
    args = ('/data/support.jsonl', '/data/provenance.json', '/output/registry', 'dbpedia_' + name)
    receipt = (train_support.build(*args) if name == 'memory' else
               train_linear_support.build(*args, optimizer=recipe['optimizer'], epochs=recipe['epochs']))
    require(receipt['complete'] is True, 'training_complete')
    write('/output/result.json', {'complete': True, 'stage': name, 'config_sha256': config_sha,
          'registry_files': model('/output/registry', cfg)[2], 'training_receipt': receipt,
          'test_or_development_available': False, 'model_promoted': False})


def select(cfg, config_sha):
    from eidos.owned_metrics import summarize
    raw = pinned('/data/development.jsonl', cfg['cohort_files']['development.jsonl'])
    development = rows(raw, {'id', 'text', 'label'}, cfg['n'])
    require(collections.Counter(r['label'] for r in development) == {label: 20 for label in cfg['labels']}, 'development_inventory')
    # Reuse the same bounded text contract as prediction before any inference.
    inputs(b''.join(canonical({k: r[k] for k in ('id', 'text')}) + b'\n' for r in development), cfg['n'])
    metrics, artifacts = {}, {}
    for name in cfg['recipe_order']:
        engine, profile, files = model('/models/' + name, cfg)
        predictions = owned_predict(engine, profile, development, cfg['labels'])
        metrics[name] = summarize([{**p, 'label': r['label']} for p, r in zip(predictions, development)], cfg['labels'])
        artifacts[name] = {'model': profile.identity, 'files': files}
    selected = choose_candidate(metrics, cfg['recipe_order'])
    write('/output/result.json', {'complete': True, 'stage': 'select', 'config_sha256': config_sha,
          'selected': selected, 'development_metrics': metrics, 'models': artifacts,
          'development_input_sha256': digest(raw), 'test_inputs_or_gold_available': False,
          'checkpoint_selection': 'development_macro_f1_accuracy_negative_nll_then_fixed_recipe_order',
          'additional_fit_refit_calibration_or_promotion': False})


def predict_eidos(cfg, config_sha):
    selection = parse(read('/selection.json'))
    require(selection['complete'] is True and selection['config_sha256'] == config_sha and
            selection['selected'] in cfg['recipe_order'] and selection['test_inputs_or_gold_available'] is False,
            'selection_receipt')
    engine, profile, files = model('/selected-registry', cfg)
    require(files == selection['models'][selection['selected']]['files'] and
            profile.identity == selection['models'][selection['selected']]['model'], 'selected_artifact_pin')
    raw = pinned('/data/test-inputs.jsonl', cfg['cohort_files']['test-inputs.jsonl'])
    values = inputs(raw, cfg['n'])
    output = owned_predict(engine, profile, values, cfg['labels'])
    write('/output/predictions.jsonl', output, jsonl=True)
    write('/output/result.json', {'complete': True, 'stage': 'eidos', 'config_sha256': config_sha,
          'rows': len(output), 'input_sha256': digest(raw), 'selection_sha256': digest(read('/selection.json')),
          'model_files': files, 'predictions_sha256': digest(read('/output/predictions.jsonl', 4 * 1024**2)),
          'gold_available': False, 'fit_or_scoring_performed': False, 'operational_acceptance_enabled': False})


def predict_laya(cfg, config_sha):
    # The outer guard verifies all native/model/application trees before this import.
    from benchmarks.predict import Laya, decode_laya_choice
    adapter = Laya('/reference-model', '/laya-source', '/rights.json', threads=1)
    require(adapter.agent.cfg.get('max_len', 512) == 512 and
            adapter.agent.cfg.get('head_max_len', 192) == 192, 'documented_token_budgets')
    import torch
    require(torch.get_num_threads() == torch.get_num_interop_threads() == 1 and
            all(p.dtype == torch.float32 for p in adapter.agent.model.parameters()), 'reference_float32_one_thread')
    from laya.agent import window_budget
    from laya.common import encode_text, serialize_state
    labels = cfg['labels']
    aliases = [cfg['reference_aliases'][label] for label in labels]
    inverse = dict(zip(aliases, labels))
    definition = {'intent': {'type': 'choice', 'instructions': cfg['prompt'],
                             'criteria': {alias: alias.replace('_', ' ') for alias in aliases}}}
    adapter.agent._check_question('intent', definition['intent'])
    internal = {'intent': adapter.agent._to_internal(definition['intent'])}
    budget, step, room = window_budget(adapter.agent.tok, list(internal.values()), 512, 192)
    raw = pinned('/data/test-inputs.jsonl', cfg['cohort_files']['test-inputs.jsonl'])
    values = inputs(raw, cfg['n'])
    # Complete input-only feasibility preflight before the first reference forward.
    windows = []
    for value in values:
        state = {'utterance': value['text']}
        item = adapter.agent._encode_state(state, ['intent'], internal)[0]
        require(len(item['markers']) == len(labels) == item['options']['options'] ==
                item['options']['options_distinct'], 'complete_distinct_reference_options')
        tokens = encode_text(adapter.agent.tok, serialize_state(state).replace(adapter.agent.tok.mask_token, ' '),
                             add_special_tokens=False)['input_ids']
        count = 1 if len(tokens) <= room else 1 + max(0, math.ceil((len(tokens) - budget) / step))
        require(1 <= count <= cfg['max_windows_per_case'], 'per_case_window_bound')
        windows.append(count)
    require(sum(windows) <= cfg['max_total_windows'], 'total_window_bound')
    write('/output/feasibility.json', {'complete': True, 'cases': len(values), 'offered_choices': len(labels),
          'all_choices_distinct': True, 'planned_windows': sum(windows), 'largest_case_windows': max(windows),
          'window': budget, 'stride': step, 'room': room, 'quality_or_gold_read': False})
    output, truncated_windows, actual_windows, answered = [], 0, 0, 0
    started = time.monotonic()
    for index, value in enumerate(values):
        response = adapter.agent.predict_long({'utterance': value['text']}, definition,
                                              aggregate='auto', batch_size=1, lang='en')
        probabilities, predicted, accepted, diagnostics = decode_laya_choice(response, aliases)
        usage = response['usage']
        require(type(usage['windows']) is int and usage['windows'] == windows[index], 'reference_window_inventory')
        actual_windows += usage['windows']
        truncated_windows += int(usage.get('truncated', 0))
        answered += accepted
        record = {'id': value['id'], 'text_sha256': digest(value['text'].encode()),
                  'predicted': inverse[predicted], 'probabilities': probabilities, 'answered': accepted}
        output.append(validate_prediction(record, labels, 'laya'))
        # Progress is count/resource metadata only; no partial quality scoring.
        if (index + 1) % 20 == 0:
            print(json.dumps({'completed_cases': index + 1, 'elapsed_seconds': time.monotonic() - started}), flush=True)
    write('/output/predictions.jsonl', output, jsonl=True)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    write('/output/result.json', {'complete': True, 'stage': 'laya', 'config_sha256': config_sha,
          'rows': len(output), 'input_sha256': digest(raw),
          'predictions_sha256': digest(read('/output/predictions.jsonl', 4 * 1024**2)),
          'adapter': adapter.metadata, 'reference_api': 'public_predict_long_default_window_stride_aggregate_auto_batch_one',
          'reference_accepted_candidates': answered, 'actual_windows': actual_windows,
          'truncated_window_count_reported_by_public_API': truncated_windows,
          'all_choices_distinct_for_every_case': True, 'process_cpu_seconds': usage.ru_utime + usage.ru_stime,
          'peak_rss_kib': usage.ru_maxrss, 'gold_or_peer_predictions_available': False,
          'fit_or_scoring_performed': False})


def score(cfg, config_sha):
    from eidos.paired_metrics import bootstrap
    from eidos.owned_metrics import summarize
    started = time.process_time()
    gold_raw = pinned('/data/test-gold.jsonl', cfg['cohort_files']['test-gold.jsonl'])
    groups_raw = pinned('/data/test-groups.jsonl', cfg['cohort_files']['test-groups.jsonl'])
    gold = rows(gold_raw, {'id', 'label'}, cfg['n'])
    groups = rows(groups_raw, {'id', 'group_id'}, cfg['n'])
    selection = parse(read('/selection.json'))
    require(selection['complete'] is True and selection['config_sha256'] == config_sha, 'selection_config_pin')
    streams, receipts = {}, {}
    for name in ('eidos', 'laya'):
        raw = read('/' + name + '/predictions.jsonl', 4 * 1024**2)
        receipt = parse(read('/' + name + '/result.json'))
        require(receipt['complete'] is True and receipt['stage'] == name and receipt['config_sha256'] == config_sha and
                receipt['rows'] == cfg['n'] and receipt['predictions_sha256'] == digest(raw) and
                receipt['input_sha256'] == cfg['cohort_files']['test-inputs.jsonl']['sha256'] and
                receipt['fit_or_scoring_performed'] is False, 'completed_prediction_receipt')
        if name == 'eidos':
            require(receipt['selection_sha256'] == digest(read('/selection.json')) and
                    receipt['model_files'] == selection['models'][selection['selected']]['files'] and
                    receipt['gold_available'] is False, 'selected_prediction_binding')
        else:
            require(receipt['gold_or_peer_predictions_available'] is False, 'reference_gold_boundary')
            if cfg.get('authored_fixture_only') is True:
                require(receipt.get('authored_substitute_not_actual_LAYA') is True, 'explicit_authored_reference_fixture')
            else:
                require(receipt['adapter']['source_commit'] == cfg['reference']['source_commit'] and
                        receipt['adapter']['model_revision'] == cfg['reference']['model_revision'] and
                        receipt['adapter']['weight_sha256'] == cfg['reference']['weight_sha256'] and
                        receipt['reference_api'] == 'public_predict_long_default_window_stride_aggregate_auto_batch_one' and
                        receipt['all_choices_distinct_for_every_case'] is True and
                        type(receipt['actual_windows']) is int and cfg['n'] <= receipt['actual_windows'] <= cfg['max_total_windows'],
                        'reference_identity_api_and_window_contract')
        streams[name] = [parse(line) for line in raw.decode().splitlines()]
        receipts[name] = digest(read('/' + name + '/result.json'))
    left, right, group_ids = align(streams['eidos'], streams['laya'], gold, groups, cfg['labels'], cfg['n'])
    require(collections.Counter(r['label'] for r in gold) == {label: 20 for label in cfg['labels']}, 'gold_inventory')
    def check_budget():
        require(time.process_time() - started <= 60, 'scoring_cpu_budget')
    paired = bootstrap(left, right, group_ids, cfg['labels'], 1000, 113, check_budget)
    write('/output/result.json', {'complete': True, 'stage': 'score', 'config_sha256': config_sha,
          'n': cfg['n'], 'selected': selection['selected'], 'prediction_receipts': receipts,
          'gold_sha256': digest(gold_raw), 'groups_sha256': digest(groups_raw),
          'eidos_descriptive_metrics': summarize(left, cfg['labels']), 'paired': paired,
          'replicates': 1000, 'seed': 113, 'intervals': 'individual_approximate_percentile_95_not_joint_or_global',
          'numeric_gate_only_not_general_superiority_or_promotion': True,
          'scope': ('authored_fixture_mechanics_not_actual_LAYA' if cfg.get('authored_fixture_only') is True else
                    '14_class_20_support_EIDOS_development_selected_vs_public_zero_shot_LAYA_predict_long'),
          'reference_is_authored_substitute': cfg.get('authored_fixture_only') is True,
          'asymmetric_adaptation_and_unknown_pretraining_exposure': True,
          'semantic_or_pretraining_independence_certified': False,
          'mission_complete': False, 'production_certified': False})


if __name__ == '__main__':
    os.umask(0o077)
    cfg, config_sha = configuration()
    stage = sys.argv[1]
    if stage in cfg['recipe_order']:
        train(stage, cfg, config_sha)
    else:
        require(stage in ('select', 'eidos', 'laya', 'score'), 'explicit_stage')
        {'select': select, 'eidos': predict_eidos, 'laya': predict_laya, 'score': score}[stage](cfg, config_sha)
