"""Explicit offline stages for a new fixed-recipe study; import is inert.

Development is diagnostic only. No development or test outcome selects, changes,
refits, calibrates, promotes or enables acceptance for the frozen primary model.
"""
import collections
import math
import os
import resource
import sys
import time

from contracts import (ID, SHA, align, canonical, digest, inputs, number, parse,
                       pinned, read, require, rows, validate_prediction, write)

LABELS = sorted(('Company', 'EducationalInstitution', 'Artist', 'Athlete', 'OfficeHolder',
                 'MeanOfTransportation', 'Building', 'NaturalPlace', 'Village', 'Animal',
                 'Plant', 'Album', 'Film', 'WrittenWork'))
ALIASES = {'Album': 'album', 'Animal': 'animal', 'Artist': 'artist', 'Athlete': 'athlete',
           'Building': 'building', 'Company': 'company',
           'EducationalInstitution': 'educational_institution', 'Film': 'film',
           'MeanOfTransportation': 'mean_of_transportation', 'NaturalPlace': 'natural_place',
           'OfficeHolder': 'office_holder', 'Plant': 'plant', 'Village': 'village',
           'WrittenWork': 'written_work'}
REFERENCE = {'source_commit': 'ca5045a3f1fac06fc6daa5cf3371110f322f5255',
             'model_revision': '1c5edc17a7acd8701df6fc341c0d179f1c62c982',
             'weight_sha256': '891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c'}
PROMPT = 'Classify this encyclopedia article into one of the offered categories.'
MODEL_ID = 'dbpedia_scaled_sparse'
RECIPE = {'epochs': 3, 'seed': 113}
DEVELOPMENT_POLICY = 'diagnostic_only_no_selection_or_quality_based_stop'
REFERENCE_API = 'public_predict_long_default_window_stride_aggregate_auto_batch_one'
COHORT_NAMES = {'training.jsonl', 'development.jsonl', 'test-inputs.jsonl',
                'test-gold.jsonl', 'test-groups.jsonl', 'labels.json', 'provenance.json'}


def validate_configuration(cfg):
    expected = {'format', 'authored_fixture_only', 'cohort_readiness_sha256', 'labels', 'n',
                'train_per_class', 'development_per_class', 'test_per_class', 'recipe',
                'reference_aliases', 'prompt', 'max_windows_per_case', 'max_total_windows',
                'paired_replicates', 'paired_seed', 'reference', 'cohort_files',
                'model_promoted_or_acceptance_enabled', 'development_policy'}
    require(type(cfg) is dict and set(cfg) == expected and
            cfg['format'] == 'eidos-dbpedia-scaled-study-config-v1', 'configuration_schema')
    require(type(cfg['authored_fixture_only']) is bool and
            cfg['model_promoted_or_acceptance_enabled'] is False and
            cfg['development_policy'] == DEVELOPMENT_POLICY, 'fixed_review_development_policy')
    labels = cfg['labels']
    require(type(labels) is list and
            all(type(label) is str and ID.fullmatch(label) for label in labels) and
            labels == sorted(set(labels)), 'labels')
    require(type(cfg['recipe']) is dict and cfg['recipe'] == RECIPE and
            all(type(value) is int for value in cfg['recipe'].values()), 'fixed_primary_recipe')
    for key in ('n', 'train_per_class', 'development_per_class', 'test_per_class',
                'max_windows_per_case', 'max_total_windows', 'paired_replicates', 'paired_seed'):
        require(type(cfg[key]) is int, 'configuration_integer')
    require(cfg['paired_replicates'] == 2000 and cfg['paired_seed'] == 113, 'fixed_statistics')
    if cfg['authored_fixture_only']:
        require(labels == ['A', 'B'] and all(1 <= cfg[key] <= 5 for key in
                ('train_per_class', 'development_per_class', 'test_per_class')),
                'authored_fixture_inventory')
        require(cfg['n'] == 2 * cfg['test_per_class'] and 1 <= cfg['max_windows_per_case'] <= 16 and
                cfg['n'] <= cfg['max_total_windows'] <= 16 * cfg['n'], 'authored_fixture_bounds')
    else:
        require(labels == LABELS and cfg['n'] == 1400 and cfg['train_per_class'] == 1000 and
                cfg['development_per_class'] == cfg['test_per_class'] == 100,
                'production_inventory')
        require(cfg['max_windows_per_case'] == 16 and cfg['max_total_windows'] == 2100 and
                cfg['prompt'] == PROMPT and cfg['reference_aliases'] == ALIASES and
                cfg['reference'] == REFERENCE, 'frozen_reference_protocol')
    aliases = cfg['reference_aliases']
    require(type(aliases) is dict and set(aliases) == set(labels) and
            all(type(alias) is str and ID.fullmatch(alias) for alias in aliases.values()) and
            len(set(aliases.values())) == len(labels), 'reference_aliases')
    require(type(cfg['prompt']) is str and 1 <= len(cfg['prompt']) <= 1000 and cfg['prompt'].strip(),
            'prompt')
    reference = cfg['reference']
    require(type(reference) is dict and set(reference) == set(REFERENCE) and
            all(type(value) is str and len(value) == len(REFERENCE[key]) and
                all(ch in '0123456789abcdef' for ch in value) for key, value in reference.items()),
            'reference_identity')
    require(type(cfg['cohort_readiness_sha256']) is str and SHA.fullmatch(cfg['cohort_readiness_sha256']),
            'cohort_readiness_pin')
    require(type(cfg['cohort_files']) is dict and set(cfg['cohort_files']) == COHORT_NAMES,
            'cohort_file_inventory')
    for name, pin in cfg['cohort_files'].items():
        maximum = 32 * 1024**2 if name == 'training.jsonl' else 4 * 1024**2
        require(type(pin) is dict and set(pin) == {'bytes', 'sha256'} and
                type(pin['bytes']) is int and 0 < pin['bytes'] <= maximum and
                type(pin['sha256']) is str and SHA.fullmatch(pin['sha256']), 'cohort_file_pin')
    return cfg


def configuration():
    raw = read('/config.json')
    return validate_configuration(parse(raw)), digest(raw)


def model(path, cfg):
    from eidos.evaluate_owned import registry
    engine, profile, files = registry(path)
    training = profile.data['training']
    require(profile.data['format'] == 'eidos-linear-general-v1' and
            training.get('format') == 'eidos-caller-sparse-training-v1' and
            profile.data['id'] == MODEL_ID and profile.data['version'] == '1' and
            profile.data['labels'] == cfg['labels'] and
            training['input_sha256'] == cfg['cohort_files']['training.jsonl']['sha256'] and
            training['provenance_sha256'] == cfg['cohort_files']['provenance.json']['sha256'] and
            training['training_rows'] == len(cfg['labels']) * cfg['train_per_class'] and
            training['class_counts'] == {label: cfg['train_per_class'] for label in cfg['labels']} and
            training['epochs'] == 3 and training['seed'] == 113, 'owned_sparse_model_lineage')
    return engine, profile, files


def validate_training_result(value, cfg, config_sha):
    expected = {'complete', 'stage', 'config_sha256', 'model_identity', 'registry_files',
                'training_receipt', 'test_or_development_available', 'model_promoted'}
    require(type(value) is dict and set(value) == expected and value['complete'] is True and
            value['stage'] == 'train' and value['config_sha256'] == config_sha and
            value['test_or_development_available'] is False and value['model_promoted'] is False,
            'frozen_primary_receipt')
    files, identity, receipt = value['registry_files'], value['model_identity'], value['training_receipt']
    require(type(files) is dict and set(files) ==
            {'model.json', 'weights.f32', 'registry.json', 'TRAINING-RECEIPT.json'}, 'primary_file_inventory')
    for pin in files.values():
        require(type(pin) is dict and set(pin) == {'bytes', 'sha256'} and type(pin['bytes']) is int and
                0 < pin['bytes'] <= 16 * 1024**2 and type(pin['sha256']) is str and
                SHA.fullmatch(pin['sha256']), 'primary_file_pin')
    require(type(identity) is dict and set(identity) ==
            {'id', 'version', 'sha256', 'feature_version', 'calibration_status'} and
            identity['id'] == MODEL_ID and identity['version'] == '1' and
            identity['sha256'] == files['model.json']['sha256'] and
            identity['feature_version'] == 'unicode-word-bigram-char345-tfidf-v2' and
            identity['calibration_status'] == 'unfitted', 'primary_model_identity')
    require(type(receipt) is dict and receipt.get('format') == 'eidos-owned-sparse-training-receipt-v1' and
            receipt.get('complete') is True and receipt.get('model') == identity and
            receipt.get('files') == {name: pin for name, pin in files.items() if name != 'TRAINING-RECEIPT.json'},
            'primary_training_receipt')
    require(type(receipt.get('classes')) is int and receipt['classes'] == len(cfg['labels']) and
            type(receipt.get('training_rows')) is int and
            receipt['training_rows'] == len(cfg['labels']) * cfg['train_per_class'] and
            type(receipt.get('class_counts')) is dict and
            receipt['class_counts'] == {label: cfg['train_per_class'] for label in cfg['labels']} and
            all(type(n) is int for n in receipt['class_counts'].values()) and
            receipt.get('input_sha256') == cfg['cohort_files']['training.jsonl']['sha256'] and
            receipt.get('provenance_sha256') == cfg['cohort_files']['provenance.json']['sha256'] and
            type(receipt.get('epochs')) is int and receipt['epochs'] == 3 and
            type(receipt.get('seed')) is int and receipt['seed'] == 113 and
            receipt.get('training_recipe') == 'sparse-tfidf-online-softmax-sgd-v1' and
            receipt.get('optimizer') == 'online_softmax_sgd_from_zero_float64' and
            receipt.get('checkpoint_selection') == 'fixed_final_epoch_no_development_or_test_input',
            'fixed_primary_training_contract')
    require(all(receipt.get(key) is False for key in
                ('third_party_training_or_inference_packages', 'operational_acceptance_enabled',
                 'model_promoted', 'evaluation_or_calibration_performed', 'privacy_guarantee',
                 'same_recipe_or_artifact_as_historical_laya_comparison')), 'primary_review_policy')
    blob = canonical(receipt) + b'\n'
    require(files['TRAINING-RECEIPT.json'] == {'bytes': len(blob), 'sha256': digest(blob)},
            'primary_training_receipt_byte_binding')
    return value


def training_result(cfg, config_sha):
    raw = read('/training-result.json')
    return validate_training_result(parse(raw), cfg, config_sha), digest(raw)


def owned_predict(engine, profile, values, labels):
    from eidos.evaluate_owned import request
    output = []
    for row in values:
        decision = engine.decide(request(profile.data['id'], row, labels))['decisions'][0]
        require(decision['status'] == 'abstained' and decision['value'] is None and
                decision['review_required'] is True and decision['calibrated_confidence'] is None and
                [item['class_id'] for item in decision['distribution']] == labels,
                'review_only_complete_distribution')
        output.append(validate_prediction({'id': row['id'], 'text_sha256': digest(row['text'].encode()),
            'predicted': decision['candidate_value'], 'answered': False,
            'logits': [item['logit'] for item in decision['distribution']],
            'probabilities': [item['probability'] for item in decision['distribution']]}, labels, 'eidos'))
    return output


def train(cfg, config_sha):
    from eidos import train_sparse
    for filename in ('training.jsonl', 'provenance.json'):
        pinned('/data/' + filename, cfg['cohort_files'][filename],
               32 * 1024**2 if filename == 'training.jsonl' else 16_000)
    receipt = train_sparse.build('/data/training.jsonl', '/data/provenance.json', '/output/registry',
                                 MODEL_ID, epochs=3, seed=113)
    require(receipt['complete'] is True, 'training_complete')
    _, profile, files = model('/output/registry', cfg)
    value = {'complete': True, 'stage': 'train', 'config_sha256': config_sha,
             'registry_files': files, 'model_identity': profile.identity, 'training_receipt': receipt,
             'test_or_development_available': False, 'model_promoted': False}
    validate_training_result(value, cfg, config_sha)
    write('/output/result.json', value)


def bound_model(cfg, config_sha):
    primary, primary_sha = training_result(cfg, config_sha)
    engine, profile, files = model('/registry', cfg)
    require(files == primary['registry_files'] and profile.identity == primary['model_identity'],
            'primary_artifact_pin')
    return engine, profile, files, primary_sha


def development(cfg, config_sha):
    from eidos.owned_metrics import summarize
    engine, profile, files, primary_sha = bound_model(cfg, config_sha)
    count = len(cfg['labels']) * cfg['development_per_class']
    raw = pinned('/data/development.jsonl', cfg['cohort_files']['development.jsonl'])
    values = rows(raw, {'id', 'text', 'label'}, count)
    require(collections.Counter(row['label'] for row in values) ==
            {label: cfg['development_per_class'] for label in cfg['labels']}, 'development_inventory')
    inputs(b''.join(canonical({key: row[key] for key in ('id', 'text')}) + b'\n' for row in values), count)
    predictions = owned_predict(engine, profile, values, cfg['labels'])
    write('/output/result.json', {'complete': True, 'stage': 'development', 'config_sha256': config_sha,
          'primary_receipt_sha256': primary_sha, 'model_files': files,
          'development_input_sha256': digest(raw),
          'development_metrics': summarize([{**prediction, 'label': row['label']}
                                           for prediction, row in zip(predictions, values)], cfg['labels']),
          'policy': DEVELOPMENT_POLICY, 'quality_based_stop': False,
          'model_selection_replacement_fit_calibration_or_promotion': False,
          'test_inputs_or_gold_available': False})


def predict_eidos(cfg, config_sha):
    engine, profile, files, primary_sha = bound_model(cfg, config_sha)
    raw = pinned('/data/test-inputs.jsonl', cfg['cohort_files']['test-inputs.jsonl'])
    output = owned_predict(engine, profile, inputs(raw, cfg['n']), cfg['labels'])
    write('/output/predictions.jsonl', output, jsonl=True)
    write('/output/result.json', {'complete': True, 'stage': 'eidos', 'config_sha256': config_sha,
          'rows': len(output), 'input_sha256': digest(raw), 'primary_receipt_sha256': primary_sha,
          'model_files': files, 'model_identity': profile.identity,
          'predictions_sha256': digest(read('/output/predictions.jsonl')),
          'gold_or_development_available': False, 'fit_or_scoring_performed': False,
          'operational_acceptance_enabled': False})


def predict_laya(cfg, config_sha):
    # Native/source/checkpoint trees and rights are checked by the outer guard.
    from benchmarks.predict import Laya, decode_laya_choice
    adapter = Laya('/reference-model', '/laya-source', '/rights.json', threads=1)
    require(adapter.agent.cfg.get('max_len', 512) == 512 and
            adapter.agent.cfg.get('head_max_len', 192) == 192, 'documented_token_budgets')
    import torch
    require(torch.get_num_threads() == torch.get_num_interop_threads() == 1 and
            all(parameter.dtype == torch.float32 for parameter in adapter.agent.model.parameters()),
            'reference_float32_one_thread')
    from laya.agent import window_budget
    from laya.common import encode_text, serialize_state
    labels = cfg['labels']
    aliases = [cfg['reference_aliases'][label] for label in labels]
    inverse = dict(zip(aliases, labels))
    definition = {'intent': {'type': 'choice', 'instructions': cfg['prompt'],
                             'criteria': {alias: alias.replace('_', ' ') for alias in aliases}}}
    adapter.agent._check_question('intent', definition['intent'])
    internal = {'intent': adapter.agent._to_internal(definition['intent'])}
    window, stride, room = window_budget(adapter.agent.tok, list(internal.values()), 512, 192)
    require(type(window) is int and type(stride) is int and type(room) is int and
            0 < stride <= window <= room, 'reference_window_budgets')
    raw = pinned('/data/test-inputs.jsonl', cfg['cohort_files']['test-inputs.jsonl'])
    values = inputs(raw, cfg['n'])
    windows = []
    for value in values:
        state = {'utterance': value['text']}
        item = adapter.agent._encode_state(state, ['intent'], internal)[0]
        require(len(item['markers']) == len(labels) == item['options']['options'] ==
                item['options']['options_distinct'], 'complete_distinct_reference_options')
        tokens = encode_text(adapter.agent.tok, serialize_state(state).replace(adapter.agent.tok.mask_token, ' '),
                             add_special_tokens=False)['input_ids']
        count = 1 if len(tokens) <= room else 1 + max(0, math.ceil((len(tokens) - window) / stride))
        require(1 <= count <= cfg['max_windows_per_case'], 'per_case_window_bound')
        windows.append(count)
    require(sum(windows) <= cfg['max_total_windows'], 'total_window_bound')
    write('/output/feasibility.json', {'complete': True, 'cases': len(values), 'offered_choices': len(labels),
          'all_choices_distinct': True, 'planned_windows': sum(windows), 'largest_case_windows': max(windows),
          'window': window, 'stride': stride, 'room': room, 'quality_or_gold_read': False})
    output, actual_windows, answered = [], 0, 0
    started = time.monotonic()
    for index, value in enumerate(values):
        response = adapter.agent.predict_long({'utterance': value['text']}, definition,
                                              aggregate='auto', batch_size=1, lang='en')
        probabilities, predicted, accepted, _diagnostics = decode_laya_choice(response, aliases)
        usage = response['usage']
        require(type(usage['windows']) is int and usage['windows'] == windows[index],
                'reference_window_inventory')
        # Public single-window system_one returns bool; multi-window aggregation
        # returns an integer count. Accept precisely their two zero forms.
        truncated = usage.get('truncated')
        require(truncated is False or (type(truncated) is int and truncated == 0),
                'reference_untruncated_coverage')
        actual_windows += usage['windows']
        answered += accepted
        output.append(validate_prediction({'id': value['id'], 'text_sha256': digest(value['text'].encode()),
            'predicted': inverse[predicted], 'probabilities': probabilities, 'answered': accepted}, labels, 'laya'))
        if (index + 1) % 20 == 0:
            print(canonical({'completed_cases': index + 1,
                             'elapsed_seconds': time.monotonic() - started}).decode(), flush=True)
    write('/output/predictions.jsonl', output, jsonl=True)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    write('/output/result.json', {'complete': True, 'stage': 'laya', 'config_sha256': config_sha,
          'rows': len(output), 'input_sha256': digest(raw),
          'predictions_sha256': digest(read('/output/predictions.jsonl')),
          'adapter': adapter.metadata, 'reference_api': REFERENCE_API,
          'reference_accepted_candidates': answered, 'actual_windows': actual_windows,
          'truncated_window_count_reported_by_public_API': 0, 'all_choices_distinct_for_every_case': True,
          'process_cpu_seconds': usage.ru_utime + usage.ru_stime, 'peak_rss_kib': usage.ru_maxrss,
          'gold_or_peer_predictions_available': False, 'fit_or_scoring_performed': False})


def score(cfg, config_sha):
    from eidos.paired_metrics import bootstrap
    from eidos.owned_metrics import summarize
    started = time.process_time()
    primary, primary_sha = training_result(cfg, config_sha)
    gold_raw = pinned('/data/test-gold.jsonl', cfg['cohort_files']['test-gold.jsonl'])
    groups_raw = pinned('/data/test-groups.jsonl', cfg['cohort_files']['test-groups.jsonl'])
    gold = rows(gold_raw, {'id', 'label'}, cfg['n'])
    groups = rows(groups_raw, {'id', 'group_id'}, cfg['n'])
    streams, receipts = {}, {}
    for name in ('eidos', 'laya'):
        raw = read('/' + name + '/predictions.jsonl')
        receipt = parse(read('/' + name + '/result.json'))
        require(type(receipt) is dict and receipt.get('complete') is True and receipt.get('stage') == name and
                receipt.get('config_sha256') == config_sha and type(receipt.get('rows')) is int and
                receipt['rows'] == cfg['n'] and receipt.get('predictions_sha256') == digest(raw) and
                receipt.get('input_sha256') == cfg['cohort_files']['test-inputs.jsonl']['sha256'] and
                receipt.get('fit_or_scoring_performed') is False, 'completed_prediction_receipt')
        if name == 'eidos':
            require(receipt.get('primary_receipt_sha256') == primary_sha and
                    receipt.get('model_files') == primary['registry_files'] and
                    receipt.get('model_identity') == primary['model_identity'] and
                    receipt.get('gold_or_development_available') is False and
                    receipt.get('operational_acceptance_enabled') is False, 'primary_prediction_binding')
        else:
            require(receipt.get('gold_or_peer_predictions_available') is False, 'reference_gold_boundary')
            if cfg['authored_fixture_only']:
                require(receipt.get('authored_substitute_not_actual_LAYA') is True, 'explicit_authored_reference_fixture')
            else:
                adapter = receipt.get('adapter', {})
                require(type(adapter) is dict and all(adapter.get(key) == value for key, value in cfg['reference'].items()) and
                        receipt.get('reference_api') == REFERENCE_API and
                        receipt.get('all_choices_distinct_for_every_case') is True and
                        type(receipt.get('actual_windows')) is int and
                        cfg['n'] <= receipt['actual_windows'] <= cfg['max_total_windows'] and
                        type(receipt.get('truncated_window_count_reported_by_public_API')) is int and
                        receipt['truncated_window_count_reported_by_public_API'] == 0,
                        'reference_identity_api_and_window_contract')
        streams[name] = [parse(line) for line in raw.decode().splitlines()]
        receipts[name] = digest(read('/' + name + '/result.json'))
    left, right, group_ids = align(streams['eidos'], streams['laya'], gold, groups, cfg['labels'], cfg['n'])
    require(collections.Counter(row['label'] for row in gold) ==
            {label: cfg['test_per_class'] for label in cfg['labels']}, 'gold_inventory')
    require(len(set(group_ids)) == cfg['n'], 'sourcewide_singleton_group_inventory')
    def check_budget():
        require(time.process_time() - started <= 120, 'scoring_cpu_budget')
    paired = bootstrap(left, right, group_ids, cfg['labels'], 2000, 113, check_budget)
    write('/output/result.json', {'complete': True, 'stage': 'score', 'config_sha256': config_sha,
          'n': cfg['n'], 'primary_receipt_sha256': primary_sha, 'primary_model': primary['model_identity'],
          'prediction_receipts': receipts, 'gold_sha256': digest(gold_raw), 'groups_sha256': digest(groups_raw),
          'eidos_descriptive_metrics': summarize(left, cfg['labels']), 'paired': paired,
          'replicates': 2000, 'seed': 113, 'intervals': 'individual_approximate_percentile_95_not_joint_or_global',
          'numeric_gate_only_not_general_superiority_or_promotion': True,
          'scope': ('authored_fixture_mechanics_not_actual_LAYA' if cfg['authored_fixture_only'] else
                    '14_class_1000_training_per_class_fixed_sparse_EIDOS_vs_public_zero_shot_LAYA_predict_long'),
          'reference_is_authored_substitute': cfg['authored_fixture_only'],
          'development_used_for_model_selection_or_quality_based_stop': False,
          'population': 'predeclared_sourcewide_title_fulltext_content_singletons_excluding_historical_exposures',
          'asymmetric_adaptation_and_unknown_pretraining_exposure': True,
          'semantic_or_pretraining_independence_certified': False,
          'mission_complete': False, 'production_certified': False})


if __name__ == '__main__':
    os.umask(0o077)
    cfg, config_sha = configuration()
    stage = sys.argv[1]
    require(stage in ('train', 'development', 'eidos', 'laya', 'score'), 'explicit_stage')
    {'train': train, 'development': development, 'eidos': predict_eidos,
     'laya': predict_laya, 'score': score}[stage](cfg, config_sha)
