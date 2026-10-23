"""Original authored training/artifact checks; no benchmark or rival data.

The tiny tasks establish deterministic fitting and artifact integration only.
They do not establish held-out semantic accuracy, calibration or model parity.
"""
from array import array
import copy
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from eidos import train_decision as training
from eidos.decision_artifact import ArtifactError, canonical, digest, load_artifact, WEIGHTS


PROVENANCE = canonical({
    'source': 'Original authored decision training unit fixtures',
    'license': 'Original fixture; no third-party material or new public license grant',
    'citation': 'EIDOS decision training mechanics test',
    'rights_basis': 'Authored synthetic test material; caller declaration only',
})


def fixture():
    rows = []
    for index, (context, first, second, descriptions) in enumerate((
            ('Mira carries a silver key. Leon carries a green book.', 'Mira', 'Leon',
             ('A silver key', 'A green book')),
            ('Nora holds a blue parcel. Ivan holds a red letter.', 'Nora', 'Ivan',
             ('A blue parcel', 'A red letter')))):
        for question, person in enumerate((first, second)):
            rows.append({'request': {'format': 'eidos-decision-request-v2',
                'request_id': f'case-{index}-{question}', 'context': context,
                'prompt': f'What does {person} carry or hold?', 'kind': 'CHOICE',
                'options': [{'id': 'choice-A', 'description': descriptions[0]},
                            {'id': 'choice-B', 'description': descriptions[1]}]},
                'target_option_id': ('choice-A', 'choice-B')[question],
                'source_group': f'original-scene-{index}'})
    return rows


def raw(rows):
    return b''.join(canonical(row) + b'\n' for row in rows)


class DecisionTraining(unittest.TestCase):
    def files(self, directory, rows=None):
        root = Path(directory)
        data, provenance = root / 'training.jsonl', root / 'provenance.json'
        data.write_bytes(raw(fixture() if rows is None else rows))
        provenance.write_bytes(PROVENANCE)
        return data, provenance

    def build(self, root, name='artifact', **settings):
        data, provenance = self.files(root)
        destination = Path(root) / name
        receipt = training.build(data, provenance, destination, 'original-decision-test',
                                 epochs=settings.pop('epochs', 1), **settings)
        return destination, receipt

    def test_proprietary_provenance_is_accepted_without_public_license(self):
        rows, declaration = training.parse_training(raw(fixture()), PROVENANCE)
        self.assertEqual(len(rows), 4)
        self.assertIn('no third-party', declaration['license'])
        self.assertEqual(len({row.source_group for row in rows}), 2)
        self.assertEqual(rows[0].request.prompt, 'What does Mira carry or hold?')

    def test_source_groups_can_repeat_but_request_ids_cannot(self):
        rows = fixture()
        rows[1]['request']['request_id'] = rows[0]['request']['request_id']
        with self.assertRaisesRegex(training.TrainingError, 'duplicate_training_request_id'):
            training.parse_training(raw(rows), PROVENANCE)

    def test_duplicate_task_refused_even_with_new_request_id_and_option_ids(self):
        rows = fixture()
        clone = copy.deepcopy(rows[0])
        clone['request']['request_id'] = 'administrative-new-id'
        clone['request']['options'][0]['id'] = 'unrelated-X'
        clone['request']['options'][1]['id'] = 'unrelated-Y'
        clone['target_option_id'] = 'unrelated-X'
        rows.append(clone)
        with self.assertRaisesRegex(training.TrainingError, 'duplicate_training_task'):
            training.parse_training(raw(rows), PROVENANCE)

    def test_target_must_be_offered_and_group_must_be_bounded(self):
        for key, value in (('target_option_id', 'not-offered'), ('source_group', '../escape')):
            with self.subTest(key=key):
                rows = fixture()
                rows[0][key] = value
                with self.assertRaises(training.TrainingError):
                    training.parse_training(raw(rows), PROVENANCE)

    def test_outer_extra_fields_and_duplicate_json_keys_refused(self):
        rows = fixture()
        rows[0]['teacher_response'] = 'unwanted extra field'
        with self.assertRaises(training.TrainingError):
            training.parse_training(raw(rows), PROVENANCE)
        line = canonical(fixture()[0]).decode()
        line = line[:-1] + ',"source_group":"duplicate"}'
        with self.assertRaises(ArtifactError):
            training.parse_training(line.encode() + b'\n' + canonical(fixture()[1]) + b'\n', PROVENANCE)

    def test_nonfinite_and_incomplete_provenance_refused(self):
        missing = json.loads(PROVENANCE)
        del missing['rights_basis']
        with self.assertRaises(training.TrainingError):
            training.parse_training(raw(fixture()), canonical(missing))
        rows = raw(fixture()).replace(b'"CHOICE"', b'NaN', 1)
        with self.assertRaises(ArtifactError):
            training.parse_training(rows, PROVENANCE)

    def test_time_and_work_refusals_do_not_publish_partial_models(self):
        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            with patch.object(training, 'MAX_WORK_UNITS', 1):
                with self.assertRaisesRegex(training.TrainingError, 'training_work_budget_exceeded'):
                    training.build(data, provenance, Path(directory)/'refused', 'bounded-test', epochs=1)
            self.assertFalse((Path(directory)/'refused').exists())
        with patch.object(training.time, 'process_time', return_value=601):
            with self.assertRaisesRegex(training.TrainingError, 'training_time_budget_exceeded'):
                training._tick((0., 0.))

    def test_boolean_settings_refused_before_reading_input(self):
        for kwargs in ({'epochs': True}, {'seed': True}, {'learning_rate': True}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(training.TrainingError):
                    training.build('not-read.jsonl', 'not-read.json', 'not-created', 'bounded-test', **kwargs)

    def test_fitting_roundtrip_private_modes_identity_and_unseen_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            destination, receipt = self.build(directory)
            artifact = load_artifact(destination)
            self.assertEqual(artifact.identity, receipt['model'])
            self.assertEqual(receipt['training']['training_rows'], 4)
            self.assertFalse(receipt['operational_acceptance_enabled'])
            self.assertFalse(receipt['training']['rights_verified'])
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            for path in destination.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            request = copy.deepcopy(fixture()[0]['request'])
            request['options'] = [{'id': 'new-opaque-one', 'description': 'A violet container'},
                                  {'id': 'new-opaque-two', 'description': 'A striped envelope'}]
            parsed = training.validate_request(request)
            result = artifact.model.forward(artifact.model.encode(parsed))
            self.assertEqual(len(result['probabilities']), 2)
            self.assertAlmostEqual(sum(result['probabilities']), 1.)
            self.assertTrue(all(math.isfinite(p) and 0 <= p <= 1 for p in result['probabilities']))

    def test_identical_fitting_reproduces_model_weights_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            one, first = self.build(directory, 'one')
            two, second = self.build(directory, 'two')
            for name in ('model.json', WEIGHTS, 'registry.json'):
                self.assertEqual((one/name).read_bytes(), (two/name).read_bytes())
            self.assertEqual(first['training'], second['training'])
            # Timings are honest diagnostics, not part of model identity.
            self.assertEqual(first['diagnostics']['optimizer_steps'], 4)

    def test_supervision_changes_numeric_parameters_not_only_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            data, provenance = self.files(directory)
            root = Path(directory)
            training.build(data, provenance, root/'original', 'target-effect', epochs=1)
            reversed_rows = fixture()
            for row in reversed_rows:
                row['target_option_id'] = ('choice-B' if row['target_option_id'] == 'choice-A'
                                           else 'choice-A')
            data.write_bytes(raw(reversed_rows))
            training.build(data, provenance, root/'opposite', 'target-effect', epochs=1)
            self.assertNotEqual((root/'original'/WEIGHTS).read_bytes(),
                                (root/'opposite'/WEIGHTS).read_bytes())

    def test_existing_outputs_and_symlink_inputs_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            destination, _ = self.build(directory)
            data, provenance = self.files(directory)
            before = (destination/'model.json').read_bytes()
            with self.assertRaises(training.TrainingError):
                training.build(data, provenance, destination, 'different', epochs=1)
            self.assertEqual(before, (destination/'model.json').read_bytes())
            linked = Path(directory)/'linked-input.jsonl'
            linked.symlink_to(data)
            with self.assertRaises(ArtifactError):
                training.build(linked, provenance, Path(directory)/'not-created', 'different', epochs=1)

    def test_loader_requires_complete_receipt_and_external_registry_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            destination, _ = self.build(directory)
            artifact = load_artifact(destination)
            self.assertEqual(load_artifact(destination, artifact.registry_sha256).identity, artifact.identity)
            with self.assertRaises(ArtifactError):
                load_artifact(destination, '0'*64)
            (destination/'TRAINING-RECEIPT.json').unlink()
            with self.assertRaises(ArtifactError):
                load_artifact(destination)

    def test_weight_tampering_and_symlink_ancestors_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            destination, _ = self.build(directory)
            path = destination/WEIGHTS
            original = path.read_bytes()
            path.chmod(0o600)
            path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            with self.assertRaises(ArtifactError):
                load_artifact(destination)
            link = Path(directory)/'linked-artifact'
            link.symlink_to(destination, target_is_directory=True)
            with self.assertRaises(ArtifactError):
                load_artifact(link)

    def test_previous_architecture_is_not_reinterpreted_as_v2(self):
        with tempfile.TemporaryDirectory() as directory:
            destination, receipt = self.build(directory)
            self.assertEqual(receipt['model']['feature_version'],
                             'eidos-question-conditioned-attention-mlp-v2')
            self.assertEqual(receipt['training']['recipe'],
                             'question-conditioned-attention-mlp-adam-v2')
            from eidos.decision_artifact import _network
            document = json.loads((destination/'model.json').read_bytes())
            document['network']['architecture'] = 'eidos-question-conditioned-attention-mlp-v1'
            with self.assertRaisesRegex(ArtifactError, 'unsupported_network_version'):
                _network(document, (destination/WEIGHTS).read_bytes())

    def test_nonfinite_weights_refused_even_if_metadata_hash_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            destination, _ = self.build(directory)
            from eidos.decision_artifact import _network
            document = json.loads((destination/'model.json').read_bytes())
            original = (destination/WEIGHTS).read_bytes()
            corrupt = struct.pack('<d', math.nan) + original[8:]
            document['weights']['sha256'] = digest(corrupt)
            with self.assertRaises(ArtifactError):
                _network(document, corrupt)


if __name__ == '__main__':
    unittest.main()
