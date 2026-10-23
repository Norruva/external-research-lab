"""Original authored numerical and control fixtures; never a corpus benchmark.

Only root may run this prepared suite in a bounded offline namespace. This
module reads no real corpus, weights, previous outcomes, credentials or network.
"""
import copy
import hashlib
import math
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from contracts import (align, canonical, inputs, parse, pinned, read, rows,
                       validate_prediction, write)


LABELS = ['A', 'B']


def prediction(identifier, engine='eidos', choice='A'):
    value = {'id': identifier, 'text_sha256': '0' * 64, 'predicted': choice,
             'probabilities': [.5, .5], 'answered': False}
    if engine == 'eidos':
        value['logits'] = [0., 0.]
    return value


class NumericTests(unittest.TestCase):
    def test_actual_reference_choice_survives_rounded_tie(self):
        row = prediction('authored', 'laya', 'B')
        self.assertEqual(validate_prediction(row, LABELS, 'laya')['predicted'], 'B')
        with self.assertRaises(ValueError):
            validate_prediction(prediction('authored', choice='B'), LABELS, 'eidos')

    def test_nonfinite_boolean_inconsistent_or_wrong_shape_numbers_refused(self):
        for probabilities in ([True, 0.], [math.nan, .5], [-.1, 1.1], [.4, .4], [.5]):
            value = prediction('authored')
            value['probabilities'] = probabilities
            with self.subTest(probabilities=probabilities), self.assertRaises(ValueError):
                validate_prediction(value, LABELS, 'eidos')
        for logits in ([math.inf, 0.], [True, 0.], [1., 0.]):
            value = prediction('authored')
            value['logits'] = logits
            with self.subTest(logits=logits), self.assertRaises(ValueError):
                validate_prediction(value, LABELS, 'eidos')

    def test_review_policy_identifiers_hashes_and_candidate_scope_refused(self):
        for key, value in (('answered', True), ('answered', 0), ('id', '../bad'),
                           ('text_sha256', 'bad'), ('predicted', 'untrained')):
            row = prediction('authored')
            row[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_prediction(row, LABELS, 'eidos')


class PairTests(unittest.TestCase):
    def setUp(self):
        self.left = [prediction('a'), prediction('b')]
        self.right = [prediction('a', 'laya', 'B'), prediction('b', 'laya', 'B')]
        self.truth = [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}]
        self.groups = [{'id': 'a', 'group_id': 'group-a'}, {'id': 'b', 'group_id': 'group-b'}]

    def check(self):
        return align(self.left, self.right, self.truth, self.groups, LABELS, 2)

    def test_complete_pairing_preserves_both_actual_choices(self):
        left, right, groups = self.check()
        self.assertEqual([v['predicted'] for v in left], ['A', 'A'])
        self.assertEqual([v['predicted'] for v in right], ['B', 'B'])
        self.assertEqual(groups, ['group-a', 'group-b'])

    def test_missing_reordered_duplicate_or_text_changed_pairs_refused(self):
        original = copy.deepcopy(self.right)
        for mode in ('missing', 'reorder', 'text', 'duplicate'):
            self.right = copy.deepcopy(original)
            if mode == 'missing':
                self.right.pop()
            elif mode == 'reorder':
                self.right.reverse()
            elif mode == 'text':
                self.right[0]['text_sha256'] = '1' * 64
            else:
                self.right[1]['id'] = 'a'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.check()

    def test_unknown_gold_or_single_group_refused(self):
        self.groups[1]['group_id'] = 'group-a'
        with self.assertRaises(ValueError):
            self.check()
        self.groups[1]['group_id'] = 'group-b'
        self.truth[0]['label'] = 'absent'
        with self.assertRaises(ValueError):
            self.check()


class FileTests(unittest.TestCase):
    def test_strict_json_and_gold_free_input_fields(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse(raw)
        raw = canonical({'id': 'a', 'text': 'Original authored input', 'label': 'A'}) + b'\n'
        with self.assertRaises(ValueError):
            inputs(raw, 1)

    def test_exact_counts_identifiers_and_text_bounds(self):
        raw = canonical({'id': 'a', 'text': 'Original authored input'}) + b'\n'
        self.assertEqual(inputs(raw, 1)[0]['id'], 'a')
        for payload, count in ((raw, 2), (raw + raw, 2), (raw, True)):
            with self.subTest(count=count), self.assertRaises(ValueError):
                inputs(payload, count)
        with self.assertRaises(ValueError):
            inputs(canonical({'id': 'a', 'text': 'x' * 12_001}), 1)
        too_large = b''.join(canonical({'id': f'row-{i}', 'text': 'x' * 12_000}) + b'\n'
                             for i in range(84))
        with self.assertRaises(ValueError):
            inputs(too_large, 84)

    def test_private_exclusive_hashed_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'artifact.json'
            write(path, {'original_authored_fixture': True})
            raw = read(path)
            pin = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            self.assertEqual(pinned(path, pin), raw)
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            with self.assertRaises(FileExistsError):
                write(path, {})
            with self.assertRaises(ValueError):
                pinned(path, {**pin, 'sha256': '0' * 64})
            with self.assertRaises(ValueError):
                read(path, 1)
            alias = Path(directory) / 'alias'
            alias.symlink_to(path)
            with self.assertRaises(ValueError):
                read(alias)


class ReferenceControlTests(unittest.TestCase):
    def exercise(self, *, collapsed=False, maximum_windows=4,
                 total_windows=5, truncated=False):
        import study
        captured, forwards = {}, []
        cfg = {'labels': ['A', 'B'], 'n': 2,
               'reference_aliases': {'A': 'alias_a', 'B': 'alias_b'},
               'prompt': 'Original authored classifier controller fixture',
               'max_windows_per_case': maximum_windows, 'max_total_windows': total_windows,
               'cohort_files': {'test-inputs.jsonl': {}}}
        values = [{'id': 'short', 'text': 'a' * 9}, {'id': 'long', 'text': 'b' * 21}]
        raw = b''.join(canonical(v) + b'\n' for v in values)

        class Agent:
            cfg = {'max_len': 512, 'head_max_len': 192}
            tok = types.SimpleNamespace(mask_token='[MASK]')
            model = types.SimpleNamespace(parameters=lambda: [types.SimpleNamespace(dtype='float32')])

            def _check_question(self, name, question):
                self.question = question
                assert name == 'intent'
                assert list(question['criteria']) == ['alias_a', 'alias_b']

            def _to_internal(self, question):
                return question

            def _encode_state(self, state, ids, internal):
                return [{'markers': [1, 2],
                         'options': {'options': 2, 'options_distinct': 1 if collapsed else 2}}]

            def predict_long(self, state, definition, **kwargs):
                assert kwargs == {'aggregate': 'auto', 'batch_size': 1, 'lang': 'en'}
                forwards.append(len(state['utterance']))
                usage = {'windows': 1 if len(state['utterance']) == 9 else 4}
                if truncated != 'missing':
                    usage['truncated'] = (False if len(state['utterance']) == 9 else 0) if truncated is False else truncated
                return {'usage': usage}

        class Adapter:
            def __init__(self, *_args, threads):
                assert threads == 1
                self.agent = Agent()
                self.metadata = {'original_authored_mock_only': True}

        parent = types.ModuleType('benchmarks')
        parent.__path__ = []
        adapter_module = types.ModuleType('benchmarks.predict')
        adapter_module.Laya = Adapter
        adapter_module.decode_laya_choice = lambda _response, _aliases: ([.5, .5], 'alias_b', True, {})
        laya = types.ModuleType('laya')
        laya.__path__ = []
        agent = types.ModuleType('laya.agent')
        agent.window_budget = lambda *_args: (10, 5, 20)
        common = types.ModuleType('laya.common')
        common.serialize_state = lambda state: state['utterance']
        common.encode_text = lambda _tok, text, **_kw: {'input_ids': list(range(len(text)))}
        torch = types.ModuleType('torch')
        torch.float32 = 'float32'
        torch.get_num_threads = torch.get_num_interop_threads = lambda: 1
        modules = {'benchmarks': parent, 'benchmarks.predict': adapter_module,
                   'laya': laya, 'laya.agent': agent, 'laya.common': common, 'torch': torch}

        def save(path, value, **kwargs):
            captured[path] = copy.deepcopy(value)

        def output_read(path, *_args):
            assert path == '/output/predictions.jsonl'
            return b''.join(canonical(row) + b'\n' for row in captured[path])

        with patch.dict('sys.modules', modules), patch.object(study, 'pinned', return_value=raw), \
             patch.object(study, 'write', side_effect=save), patch.object(study, 'read', side_effect=output_read):
            if collapsed or maximum_windows < 4 or total_windows < 5:
                with self.assertRaises(ValueError):
                    study.predict_laya(cfg, '0' * 64)
                self.assertEqual(forwards, [])
            elif truncated is not False:
                with self.assertRaises(ValueError):
                    study.predict_laya(cfg, '0' * 64)
                self.assertEqual(forwards, [9])
                self.assertNotIn('/output/predictions.jsonl', captured)
            else:
                study.predict_laya(cfg, '0' * 64)
                self.assertEqual(forwards, [9, 21])
                self.assertEqual(captured['/output/feasibility.json']['planned_windows'], 5)
                self.assertEqual(captured['/output/result.json']['actual_windows'], 5)
                self.assertEqual([p['predicted'] for p in captured['/output/predictions.jsonl']], ['B', 'B'])
                self.assertIs(captured['/output/result.json']['gold_or_peer_predictions_available'], False)

    def test_public_long_controller_counts_windows_and_keeps_actual_tied_choice(self):
        self.exercise()

    def test_collapsed_candidates_fail_before_first_forward(self):
        self.exercise(collapsed=True)

    def test_per_case_and_aggregate_window_bounds_fail_before_first_forward(self):
        self.exercise(maximum_windows=3)
        self.exercise(total_windows=4)

    def test_reported_truncation_aborts_without_complete_prediction_stream(self):
        for invalid in (True, 1, -1, 0.0, 'missing'):
            with self.subTest(truncation=invalid):
                self.exercise(truncated=invalid)


def fixture_configuration():
    import study
    return {'format': 'eidos-dbpedia-scaled-study-config-v1', 'authored_fixture_only': True,
            'cohort_readiness_sha256': '0' * 64, 'labels': ['A', 'B'], 'n': 2,
            'train_per_class': 3, 'development_per_class': 1, 'test_per_class': 1,
            'recipe': {'epochs': 3, 'seed': 113},
            'reference_aliases': {'A': 'authored_alpha', 'B': 'authored_beta'},
            'prompt': 'Original authored classifier mechanics',
            'max_windows_per_case': 4, 'max_total_windows': 8,
            'paired_replicates': 2000, 'paired_seed': 113,
            'reference': copy.deepcopy(study.REFERENCE),
            'cohort_files': {name: {'bytes': 1, 'sha256': '0' * 64} for name in study.COHORT_NAMES},
            'model_promoted_or_acceptance_enabled': False,
            'development_policy': 'diagnostic_only_no_selection_or_quality_based_stop'}


def authored_primary(cfg, config_sha='c' * 64):
    """Synthetic internally consistent byte bindings; never a fitted model."""
    identity = {'id': 'dbpedia_scaled_sparse', 'version': '1', 'sha256': '1' * 64,
                'feature_version': 'unicode-word-bigram-char345-tfidf-v2', 'calibration_status': 'unfitted'}
    files = {name: {'bytes': size, 'sha256': digit * 64} for name, size, digit in
             [('model.json', 11, '1'), ('weights.f32', 32, '2'), ('registry.json', 17, '3')]}
    receipt = {'format': 'eidos-owned-sparse-training-receipt-v1', 'complete': True,
               'model': identity, 'files': copy.deepcopy(files), 'classes': len(cfg['labels']),
               'training_rows': len(cfg['labels']) * cfg['train_per_class'],
               'class_counts': {label: cfg['train_per_class'] for label in cfg['labels']},
               'input_sha256': cfg['cohort_files']['training.jsonl']['sha256'],
               'provenance_sha256': cfg['cohort_files']['provenance.json']['sha256'],
               'epochs': 3, 'seed': 113, 'training_recipe': 'sparse-tfidf-online-softmax-sgd-v1',
               'optimizer': 'online_softmax_sgd_from_zero_float64',
               'checkpoint_selection': 'fixed_final_epoch_no_development_or_test_input',
               'third_party_training_or_inference_packages': False,
               'operational_acceptance_enabled': False, 'model_promoted': False,
               'evaluation_or_calibration_performed': False, 'privacy_guarantee': False,
               'same_recipe_or_artifact_as_historical_laya_comparison': False}
    blob = canonical(receipt) + b'\n'
    files['TRAINING-RECEIPT.json'] = {'bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()}
    return {'complete': True, 'stage': 'train', 'config_sha256': config_sha, 'model_identity': identity,
            'registry_files': files, 'training_receipt': receipt,
            'test_or_development_available': False, 'model_promoted': False}


class FrozenConfigurationTests(unittest.TestCase):
    def test_authored_and_fixed_production_configuration_validate(self):
        import study
        cfg = fixture_configuration()
        self.assertIs(study.validate_configuration(cfg), cfg)
        cfg.update(authored_fixture_only=False, labels=copy.deepcopy(study.LABELS), n=1400,
                   train_per_class=1000, development_per_class=100, test_per_class=100,
                   reference_aliases=copy.deepcopy(study.ALIASES), prompt=study.PROMPT,
                   max_windows_per_case=16, max_total_windows=2100)
        self.assertIs(study.validate_configuration(cfg), cfg)
        cfg['max_windows_per_case'] = 4
        with self.assertRaisesRegex(ValueError, 'frozen_reference_protocol'):
            study.validate_configuration(cfg)

    def test_recipe_statistics_review_and_diagnostic_policy_are_frozen(self):
        import study
        for field, value in [('recipe', {'epochs': 4, 'seed': 113}),
                             ('recipe', {'epochs': 3, 'seed': 114}),
                             ('recipe', {'epochs': True, 'seed': 113}),
                             ('paired_replicates', 1000), ('paired_seed', 114),
                             ('n', True), ('train_per_class', True),
                             ('development_policy', 'select_highest_development_score'),
                             ('model_promoted_or_acceptance_enabled', True),
                             ('authored_fixture_only', 1)]:
            cfg = fixture_configuration()
            cfg[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                study.validate_configuration(cfg)

    def test_unplanned_selection_fields_labels_and_file_pins_are_refused(self):
        import study
        for field, value in [('selected', 'a_better_test_model'), ('labels', ['B', 'A']),
                             ('labels', ['A', ['not-hashable']]),
                             ('reference_aliases', {'A': 'same', 'B': 'same'}),
                             ('cohort_readiness_sha256', 'not-a-hash')]:
            cfg = fixture_configuration()
            cfg[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                study.validate_configuration(cfg)
        cfg = fixture_configuration()
        cfg['cohort_files']['training.jsonl']['bytes'] = True
        with self.assertRaises(ValueError):
            study.validate_configuration(cfg)


class FrozenPrimaryTests(unittest.TestCase):
    def test_primary_receipt_is_owned_sparse_and_reads_no_development_metrics(self):
        import study
        cfg = fixture_configuration()
        value = authored_primary(cfg)
        self.assertIs(study.validate_training_result(value, cfg, 'c' * 64), value)
        raw = canonical(value) + b'\n'
        with patch.object(study, 'read', return_value=raw) as source:
            actual, sha = study.training_result(cfg, 'c' * 64)
        source.assert_called_once_with('/training-result.json')
        self.assertEqual(actual, value)
        self.assertEqual(sha, hashlib.sha256(raw).hexdigest())
        self.assertNotIn('selected', actual)

    def test_primary_count_recipe_tags_policy_and_byte_bindings_refuse_changes(self):
        import study
        cfg = fixture_configuration()
        for field, value in [('format', 'eidos-owned-linear-training-receipt-v1'),
                             ('training_rows', 40), ('training_rows', True),
                             ('class_counts', {'A': 20, 'B': 20}),
                             ('class_counts', {'A': True, 'B': 5}),
                             ('epochs', 4), ('seed', 114),
                             ('checkpoint_selection', 'best_development_checkpoint'),
                             ('input_sha256', 'f' * 64), ('model_promoted', True)]:
            primary = authored_primary(cfg)
            primary['training_receipt'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                study.validate_training_result(primary, cfg, 'c' * 64)
        primary = authored_primary(cfg)
        primary['training_receipt']['additional_field'] = 'unbound'
        with self.assertRaisesRegex(ValueError, 'primary_training_receipt_byte_binding'):
            study.validate_training_result(primary, cfg, 'c' * 64)

    def test_bound_model_cannot_be_replaced_after_diagnostics(self):
        import study
        cfg = fixture_configuration()
        primary = authored_primary(cfg)
        profile = types.SimpleNamespace(identity={**primary['model_identity'], 'sha256': 'f' * 64})
        with patch.object(study, 'training_result', return_value=(primary, 'b' * 64)), \
             patch.object(study, 'model', return_value=(object(), profile, primary['registry_files'])):
            with self.assertRaisesRegex(ValueError, 'primary_artifact_pin'):
                study.bound_model(cfg, 'c' * 64)

    def test_train_calls_fixed_owned_builder_with_training_inputs_only(self):
        import study
        from eidos import train_sparse
        cfg = fixture_configuration()
        primary = authored_primary(cfg)
        profile = types.SimpleNamespace(identity=primary['model_identity'])
        with patch.object(train_sparse, 'build', return_value=primary['training_receipt']) as builder, \
             patch.object(study, 'pinned', return_value=b'opaque-authored-input') as pins, \
             patch.object(study, 'model', return_value=(object(), profile, primary['registry_files'])), \
             patch.object(study, 'write') as output:
            study.train(cfg, 'c' * 64)
        builder.assert_called_once_with('/data/training.jsonl', '/data/provenance.json',
                                         '/output/registry', 'dbpedia_scaled_sparse', epochs=3, seed=113)
        self.assertEqual([call.args[0] for call in pins.call_args_list],
                         ['/data/training.jsonl', '/data/provenance.json'])
        output.assert_called_once()
        self.assertEqual(output.call_args.args[1]['training_receipt']['format'],
                         'eidos-owned-sparse-training-receipt-v1')

    def test_development_reports_only_diagnostics_without_selection(self):
        import study
        cfg = fixture_configuration()
        values = [{'id': 'dev-a', 'text': 'Original Alpha diagnostic sentence', 'label': 'A'},
                  {'id': 'dev-b', 'text': 'Original Beta diagnostic sentence', 'label': 'B'}]
        raw = b''.join(canonical(value) + b'\n' for value in values)
        predictions = [prediction(value['id']) for value in values]
        with patch.object(study, 'bound_model', return_value=(object(), object(), {}, 'b' * 64)), \
             patch.object(study, 'pinned', return_value=raw), \
             patch.object(study, 'owned_predict', return_value=predictions), \
             patch.object(study, 'write') as output:
            study.development(cfg, 'c' * 64)
        result = output.call_args.args[1]
        self.assertEqual(result['policy'], 'diagnostic_only_no_selection_or_quality_based_stop')
        self.assertIs(result['quality_based_stop'], False)
        self.assertIs(result['model_selection_replacement_fit_calibration_or_promotion'], False)
        self.assertIs(result['test_inputs_or_gold_available'], False)
        self.assertNotIn('selected', result)


class ScoreBindingTests(unittest.TestCase):
    def attempt(self, mode):
        import study
        cfg = fixture_configuration()
        cfg['authored_fixture_only'] = False  # Exercise native receipt checks directly, no model runs.
        config_sha = 'c' * 64
        primary = authored_primary(cfg, config_sha)
        primary_raw = canonical(primary) + b'\n'
        streams = {name: b''.join(canonical(prediction(case, name)) + b'\n' for case in ('a', 'b'))
                   for name in ('eidos', 'laya')}
        receipts = {name: {'complete': True, 'stage': name, 'config_sha256': config_sha, 'rows': 2,
                          'predictions_sha256': hashlib.sha256(streams[name]).hexdigest(),
                          'input_sha256': cfg['cohort_files']['test-inputs.jsonl']['sha256'],
                          'fit_or_scoring_performed': False} for name in streams}
        receipts['eidos'].update(primary_receipt_sha256=hashlib.sha256(primary_raw).hexdigest(),
                                 model_files=primary['registry_files'], model_identity=primary['model_identity'],
                                 gold_or_development_available=False, operational_acceptance_enabled=False)
        receipts['laya'].update(gold_or_peer_predictions_available=False, adapter=copy.deepcopy(cfg['reference']),
                               reference_api=study.REFERENCE_API, all_choices_distinct_for_every_case=True,
                               actual_windows=2, truncated_window_count_reported_by_public_API=0)
        if mode == 'checkpoint':
            receipts['laya']['adapter']['weight_sha256'] = 'f' * 64
        elif mode == 'api':
            receipts['laya']['reference_api'] = 'a_different_controller'
        elif mode == 'choices':
            receipts['laya']['all_choices_distinct_for_every_case'] = False
        elif mode == 'windows':
            receipts['laya']['actual_windows'] = 9
        elif mode == 'truncation':
            receipts['laya']['truncated_window_count_reported_by_public_API'] = 1
        elif mode == 'primary':
            receipts['eidos']['primary_receipt_sha256'] = 'f' * 64
        files = {'/training-result.json': primary_raw}
        for name in streams:
            files['/' + name + '/predictions.jsonl'] = streams[name]
            files['/' + name + '/result.json'] = canonical(receipts[name])
        truth = b''.join(canonical({'id': case, 'label': label}) + b'\n'
                         for case, label in [('a', 'A'), ('b', 'B')])
        groups = b''.join(canonical({'id': case, 'group_id': 'group-' + case}) + b'\n' for case in ('a', 'b'))
        with patch.object(study, 'read', side_effect=lambda path, *_args: files[path]), \
             patch.object(study, 'pinned', side_effect=lambda path, *_args: truth if 'gold' in path else groups), \
             patch('eidos.paired_metrics.bootstrap') as metric, patch.object(study, 'write') as output:
            expected = 'primary_prediction_binding' if mode == 'primary' else 'reference_identity_api_and_window_contract'
            with self.assertRaisesRegex(ValueError, expected):
                study.score(cfg, config_sha)
            metric.assert_not_called()
            output.assert_not_called()

    def test_reference_checkpoint_api_choices_window_and_truncation_fail_before_metrics(self):
        for mode in ('checkpoint', 'api', 'choices', 'windows', 'truncation'):
            with self.subTest(mode=mode):
                self.attempt(mode)

    def test_changed_primary_receipt_fails_before_metrics(self):
        self.attempt('primary')


if __name__ == '__main__':
    unittest.main()

