"""Only authored numeric/text fixtures; no real data, native imports or network."""
import copy
import hashlib
import math
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from contracts import (align, canonical, choose_candidate, inputs, parse, pinned,
                       read, rows, validate_prediction, write)


LABELS = ['A', 'B']


def prediction(identifier, engine='eidos', choice='A'):
    value = {'id': identifier, 'text_sha256': '0'*64, 'predicted': choice,
             'probabilities': [.5, .5], 'answered': False}
    if engine == 'eidos': value['logits'] = [0., 0.]
    return value


def metric(f1=.6, accuracy=.7, nll=.5):
    return {'macro_f1_fixed_offered_labels': f1, 'candidate_accuracy': accuracy,
            'negative_log_likelihood': nll}


class NumericTests(unittest.TestCase):
    def test_laya_actual_choice_survives_rounded_tie(self):
        row = prediction('authored', 'laya', 'B')
        self.assertEqual(validate_prediction(row, LABELS, 'laya')['predicted'], 'B')
        with self.assertRaises(ValueError): validate_prediction(prediction('authored', choice='B'), LABELS, 'eidos')

    def test_invalid_numeric_distribution_refused(self):
        for probabilities in ([True, 0.], [math.nan, .5], [-.1, 1.1], [.4, .4], [.5]):
            row = prediction('authored'); row['probabilities'] = probabilities
            with self.subTest(p=probabilities), self.assertRaises(ValueError):
                validate_prediction(row, LABELS, 'eidos')
        row = prediction('authored'); row['logits'] = [1., 0.]
        with self.assertRaises(ValueError): validate_prediction(row, LABELS, 'eidos')

    def test_policy_ids_and_text_pin_are_strict(self):
        for key, value in (('answered', True), ('answered', 0), ('id', '../bad'),
                           ('text_sha256', 'bad'), ('predicted', 'absent')):
            row = prediction('authored'); row[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_prediction(row, LABELS, 'eidos')

    def test_selection_tiebreaks_in_frozen_order(self):
        order = ['memory', 'linear_gd', 'linear_adam']
        values = {k: metric() for k in order}
        self.assertEqual(choose_candidate(values, order), 'memory')
        values['linear_gd'] = metric(nll=.4)
        self.assertEqual(choose_candidate(values, order), 'linear_gd')
        values['linear_adam'] = metric(accuracy=.8, nll=.8)
        self.assertEqual(choose_candidate(values, order), 'linear_adam')
        values['memory'] = metric(f1=.8, accuracy=.1, nll=2.)
        self.assertEqual(choose_candidate(values, order), 'memory')

    def test_missing_or_nonfinite_development_metrics_refused(self):
        with self.assertRaises(ValueError): choose_candidate({'memory': metric()}, ['memory', 'other'])
        with self.assertRaises(ValueError): choose_candidate({'memory': metric(nll=math.inf)}, ['memory'])


class PairTests(unittest.TestCase):
    def setUp(self):
        self.left = [prediction('a'), prediction('b')]
        self.right = [prediction('a', 'laya', 'B'), prediction('b', 'laya', 'B')]
        self.truth = [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}]
        self.groups = [{'id': 'a', 'group_id': 'group-a'}, {'id': 'b', 'group_id': 'group-b'}]

    def check(self): return align(self.left, self.right, self.truth, self.groups, LABELS, 2)

    def test_complete_pairs_preserve_both_choices(self):
        left, right, groups = self.check()
        self.assertEqual([v['predicted'] for v in left], ['A', 'A'])
        self.assertEqual([v['predicted'] for v in right], ['B', 'B'])
        self.assertEqual(groups, ['group-a', 'group-b'])

    def test_incomplete_reordered_and_text_tampered_pairs_refused(self):
        original = copy.deepcopy(self.right)
        for mode in ('missing', 'reorder', 'text', 'duplicate'):
            self.right = copy.deepcopy(original)
            if mode == 'missing': self.right.pop()
            if mode == 'reorder': self.right.reverse()
            if mode == 'text': self.right[0]['text_sha256'] = '1'*64
            if mode == 'duplicate': self.right[1]['id'] = 'a'
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.check()

    def test_single_group_and_unknown_gold_refused(self):
        self.groups[1]['group_id'] = 'group-a'
        with self.assertRaises(ValueError): self.check()
        self.groups[1]['group_id'] = 'group-b'; self.truth[0]['label'] = 'absent'
        with self.assertRaises(ValueError): self.check()


class FileTests(unittest.TestCase):
    def test_duplicate_keys_nonfinite_and_gold_leaking_input_schema_refused(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
            with self.assertRaises(ValueError): parse(raw)
        raw = canonical({'id': 'a', 'text': 'Authored input', 'label': 'A'}) + b'\n'
        with self.assertRaises(ValueError): inputs(raw, 1)

    def test_exact_count_unique_id_and_bounds(self):
        raw = canonical({'id': 'a', 'text': 'Authored input'}) + b'\n'
        self.assertEqual(inputs(raw, 1)[0]['id'], 'a')
        for payload, count in ((raw, 2), (raw + raw, 2), (raw, True)):
            with self.assertRaises(ValueError): inputs(payload, count)
        with self.assertRaises(ValueError): inputs(canonical({'id': 'a', 'text': 'x'*12001}), 1)

    def test_private_exclusive_files_hash_bounds_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'artifact.json'
            write(path, {'authored': True})
            raw = read(path); pin = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            self.assertEqual(pinned(path, pin), raw)
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            with self.assertRaises(FileExistsError): write(path, {})
            with self.assertRaises(ValueError): pinned(path, {**pin, 'sha256': '0'*64})
            with self.assertRaises(ValueError): read(path, 1)
            alias = Path(directory) / 'link'; alias.symlink_to(path)
            with self.assertRaises(ValueError): read(alias)


class ReferenceControlTests(unittest.TestCase):
    def exercise(self, *, collapsed=False, maximum_windows=16):
        import study
        captured, forwards = {}, []
        cfg = {'labels': ['A', 'B'], 'n': 2, 'reference_aliases': {'A': 'alias_a', 'B': 'alias_b'},
               'prompt': 'Authored classifier fixture', 'max_windows_per_case': maximum_windows,
               'max_total_windows': 100,
               'cohort_files': {'test-inputs.jsonl': {}}}
        values = [{'id': 'short', 'text': 'a'*9}, {'id': 'long', 'text': 'b'*21}]
        raw = b''.join(canonical(v) + b'\n' for v in values)
        class Agent:
            cfg = {'max_len': 512, 'head_max_len': 192}
            tok = types.SimpleNamespace(mask_token='[MASK]')
            model = types.SimpleNamespace(parameters=lambda: [types.SimpleNamespace(dtype='float32')])
            def _check_question(self, name, question):
                self.question = question
                assert list(question['criteria']) == ['alias_a', 'alias_b']
            def _to_internal(self, question): return question
            def _encode_state(self, state, ids, internal):
                return [{'markers': [1, 2], 'options': {'options': 2, 'options_distinct': 1 if collapsed else 2}}]
            def predict_long(self, state, definition, **kwargs):
                assert kwargs == {'aggregate': 'auto', 'batch_size': 1, 'lang': 'en'}
                forwards.append(len(state['utterance']))
                return {'usage': {'windows': 1 if len(state['utterance']) == 9 else 4, 'truncated': 0}}
        class Adapter:
            def __init__(self, *_args, threads):
                assert threads == 1
                self.agent = Agent(); self.metadata = {'authored_mock_only': True}
        parent = types.ModuleType('benchmarks'); parent.__path__ = []
        adapter_module = types.ModuleType('benchmarks.predict'); adapter_module.Laya = Adapter
        adapter_module.decode_laya_choice = lambda _response, _aliases: ([.5, .5], 'alias_b', True, {})
        laya = types.ModuleType('laya'); laya.__path__ = []
        agent = types.ModuleType('laya.agent'); agent.window_budget = lambda *_args: (10, 5, 20)
        common = types.ModuleType('laya.common')
        common.serialize_state = lambda state: state['utterance']
        common.encode_text = lambda _tok, text, **_kw: {'input_ids': list(range(len(text)))}
        torch = types.ModuleType('torch'); torch.float32 = 'float32'
        torch.get_num_threads = torch.get_num_interop_threads = lambda: 1
        modules = {'benchmarks': parent, 'benchmarks.predict': adapter_module,
                   'laya': laya, 'laya.agent': agent, 'laya.common': common, 'torch': torch}
        def save(path, value, **kwargs): captured[path] = copy.deepcopy(value)
        def output_read(path, *_args):
            assert path == '/output/predictions.jsonl'
            return b''.join(canonical(row)+b'\n' for row in captured[path])
        with patch.dict('sys.modules', modules), patch.object(study, 'pinned', return_value=raw), \
             patch.object(study, 'write', side_effect=save), patch.object(study, 'read', side_effect=output_read):
            if collapsed or maximum_windows < 4:
                with self.assertRaises(ValueError): study.predict_laya(cfg, '0'*64)
                self.assertEqual(forwards, [])
            else:
                study.predict_laya(cfg, '0'*64)
                self.assertEqual(forwards, [9, 21])
                self.assertEqual(captured['/output/feasibility.json']['planned_windows'], 5)
                self.assertEqual(captured['/output/result.json']['actual_windows'], 5)
                self.assertEqual([p['predicted'] for p in captured['/output/predictions.jsonl']], ['B', 'B'])
                self.assertTrue(captured['/output/result.json']['gold_or_peer_predictions_available'] is False)

    def test_long_api_preflight_counts_short_and_overlapping_windows_and_keeps_returned_ties(self):
        self.exercise()

    def test_collapsed_choices_reject_before_any_forward(self):
        self.exercise(collapsed=True)

    def test_window_bound_rejects_whole_cohort_before_any_forward(self):
        self.exercise(maximum_windows=3)


class ScoreBindingTests(unittest.TestCase):
    def attempt(self, mode):
        import study
        cfg_sha, input_sha = 'c'*64, 'd'*64
        cfg = {'labels': LABELS, 'n': 2, 'authored_fixture_only': False, 'max_total_windows': 10,
               'cohort_files': {'test-inputs.jsonl': {'sha256': input_sha},
                                'test-gold.jsonl': {}, 'test-groups.jsonl': {}},
               'reference': {'source_commit': 'a'*40, 'model_revision': 'b'*40, 'weight_sha256': 'e'*64}}
        selection = {'complete': True, 'config_sha256': cfg_sha, 'selected': 'memory',
                     'models': {'memory': {'files': {}}}}
        selection_raw = canonical(selection)
        streams = {name: b''.join(canonical(prediction(identifier, name)) + b'\n' for identifier in ('a', 'b'))
                   for name in ('eidos', 'laya')}
        receipts = {name: {'complete': True, 'stage': name, 'config_sha256': cfg_sha, 'rows': 2,
                          'predictions_sha256': hashlib.sha256(streams[name]).hexdigest(),
                          'input_sha256': input_sha, 'fit_or_scoring_performed': False}
                    for name in streams}
        receipts['eidos'].update(selection_sha256=hashlib.sha256(selection_raw).hexdigest(),
                                 model_files={}, gold_available=False)
        receipts['laya'].update(gold_or_peer_predictions_available=False, adapter=copy.deepcopy(cfg['reference']),
                               reference_api='public_predict_long_default_window_stride_aggregate_auto_batch_one',
                               all_choices_distinct_for_every_case=True, actual_windows=2)
        if mode == 'checkpoint': receipts['laya']['adapter']['weight_sha256'] = 'f'*64
        elif mode == 'api': receipts['laya']['reference_api'] = 'authored_stub'
        elif mode == 'choices': receipts['laya']['all_choices_distinct_for_every_case'] = False
        elif mode == 'windows': receipts['laya']['actual_windows'] = 11
        elif mode == 'selected': receipts['eidos']['selection_sha256'] = '0'*64
        gold = b''.join(canonical({'id': x, 'label': 'A'})+b'\n' for x in ('a','b'))
        groups = b''.join(canonical({'id': x, 'group_id': 'group-'+x})+b'\n' for x in ('a','b'))
        files = {'/selection.json': selection_raw}
        for name in streams:
            files['/'+name+'/predictions.jsonl'] = streams[name]
            files['/'+name+'/result.json'] = canonical(receipts[name])
        with patch.object(study, 'read', side_effect=lambda p,*_args: files[p]), \
             patch.object(study, 'pinned', side_effect=lambda p,*_args: gold if 'gold' in p else groups), \
             patch.object(study, 'write') as output:
            expected = 'selected_prediction_binding' if mode == 'selected' else 'reference_identity_api_and_window_contract'
            with self.assertRaisesRegex(ValueError, expected): study.score(cfg, cfg_sha)
            output.assert_not_called()

    def test_wrong_reference_checkpoint_api_choices_and_window_inventory_refused_before_score(self):
        for mode in ('checkpoint', 'api', 'choices', 'windows'):
            with self.subTest(mode=mode): self.attempt(mode)

    def test_changed_selection_receipt_refused_before_score(self):
        self.attempt('selected')


if __name__ == '__main__': unittest.main()
