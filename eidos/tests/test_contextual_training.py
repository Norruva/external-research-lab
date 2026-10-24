"""Owned v3 artifact and trainer checks using new tiny authored tasks only."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from eidos import contextual_artifact as artifacts
from eidos.contextual import ContextualEngine
from eidos.decision_artifact import load_artifact as load_v2
from eidos.decision_contextual import DecisionNetwork
from eidos.train_contextual import build, training_preflight
from eidos.train_decision import TrainingError


class ContextualTrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='authored-contextual-training-')
        cls.root = Path(cls.temp.name)
        cls.input = cls.root/'training.jsonl'
        cls.provenance = cls.root/'provenance.json'
        cls.rows = []
        for variant in range(2):
            colors = ('copper', 'indigo') if variant == 0 else ('indigo', 'copper')
            for person, color in zip(('Rin', 'Sol'), colors):
                q = {'format':'eidos-decision-request-v2',
                     'request_id':f'new-unit-{variant}-{person}',
                     'context':f'Rin bears {colors[0]}. Sol bears {colors[1]}.',
                     'prompt':f'What does {person} bear?', 'kind':'CHOICE',
                     'options':[{'id':'c','description':'copper'},
                                {'id':'i','description':'indigo'}]}
                cls.rows.append({'request':q,'target_option_id':'c' if color=='copper' else 'i',
                                 'source_group':'authored-contextual-unit-pair'})
        cls.input.write_text(''.join(json.dumps(row)+'\n' for row in cls.rows))
        cls.provenance.write_text(json.dumps({'source':'Original four-task unit fixture',
            'license':'Proprietary authored fixture; no public license grant',
            'citation':'Contextual trainer unit checks',
            'rights_basis':'Authored without external data or vendor model'}))
        cls.directory = cls.root/'baseline'
        cls.receipt = build(cls.input,cls.provenance,cls.directory,'unit-contextual-v3',epochs=1)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def working_copy(self, name):
        target=self.root/name
        shutil.copytree(self.directory,target)
        for path in target.iterdir():path.chmod(0o600)
        return target

    def test_preflight_performs_no_forward_loss_or_output(self):
        with patch.object(DecisionNetwork,'forward',side_effect=AssertionError('forward forbidden')), \
                patch.object(DecisionNetwork,'loss_and_grad',side_effect=AssertionError('fit forbidden')):
            result=training_preflight(self.input,self.provenance,epochs=1)
        self.assertTrue(result['within_work_budget'])
        self.assertEqual(result['training_rows'],4)
        self.assertFalse(result['model_forward_loss_fit_or_quality_scoring_performed'])

    def test_complete_private_artifact_loads_with_external_registry_pin(self):
        pin=hashlib.sha256((self.directory/'registry.json').read_bytes()).hexdigest()
        artifact=artifacts.load_artifact(self.directory,expected_registry_sha256=pin)
        self.assertEqual(artifact.identity,self.receipt['model'])
        self.assertEqual(artifact.registry_sha256,pin)
        self.assertEqual(self.directory.stat().st_mode&0o777,0o700)
        for path in self.directory.iterdir():self.assertEqual(path.stat().st_mode&0o777,0o400)
        self.assertFalse(artifact.training['rights_verified'])
        self.assertEqual(artifact.training['training_rows'],4)
        response=ContextualEngine.from_registry(self.directory).decide(self.rows[0]['request'])
        self.assertIsNone(response['value']);self.assertTrue(response['review_required'])
        self.assertEqual(len(response['distribution']),2)

    def test_all_parameters_are_not_just_metadata_after_supervision(self):
        model=artifacts.load_artifact(self.directory).model
        initial=DecisionNetwork.initialize(model.vocabulary,dimension=8,hidden=12,seed=113)
        self.assertTrue(any(abs(a-b)>1e-8 for a,b in zip(initial.parameters,model.parameters)))
        self.assertEqual(self.receipt['diagnostics']['optimizer_steps'],4)

    def test_repeat_recipe_has_identical_data_only_model_bytes(self):
        target=self.root/'reproducible'
        build(self.input,self.provenance,target,'unit-contextual-v3',epochs=1)
        for name in ('model.json','registry.json','weights.f64'):
            self.assertEqual((target/name).read_bytes(),(self.directory/name).read_bytes())

    def test_changed_targets_change_weights_with_identical_request_features(self):
        rows=copy.deepcopy(self.rows)
        for row in rows:row['target_option_id']='i' if row['target_option_id']=='c' else 'c'
        path=self.root/'reversed-targets.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        target=self.root/'changed-target-model'
        build(path,self.provenance,target,'unit-contextual-v3',epochs=1)
        self.assertNotEqual((target/'weights.f64').read_bytes(),(self.directory/'weights.f64').read_bytes())

    def test_wrong_external_registry_pin_is_refused(self):
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.load_artifact(self.directory,expected_registry_sha256='0'*64)

    def test_weights_tampering_is_refused(self):
        target=self.working_copy('tampered-weights');path=target/'weights.f64'
        raw=path.read_bytes();path.write_bytes(bytes([raw[0]^1])+raw[1:])
        with self.assertRaises(artifacts.ArtifactError):artifacts.load_artifact(target)

    def test_nan_weights_refused_even_after_updating_weight_and_registry_hashes(self):
        target=self.working_copy('nan-weights');path=target/'weights.f64'
        raw=struct.pack('<d',float('nan'))+path.read_bytes()[8:];path.write_bytes(raw)
        model=json.loads((target/'model.json').read_text())
        model['weights']['sha256']=hashlib.sha256(raw).hexdigest()
        model_raw=artifacts.canonical(model)+b'\n';(target/'model.json').write_bytes(model_raw)
        registry=json.loads((target/'registry.json').read_text())
        registry['model_sha256']=hashlib.sha256(model_raw).hexdigest()
        (target/'registry.json').write_bytes(artifacts.canonical(registry)+b'\n')
        with self.assertRaises(artifacts.ArtifactError):artifacts.load_artifact(target)

    def test_incomplete_or_altered_receipt_is_refused(self):
        target=self.working_copy('bad-receipt');path=target/'TRAINING-RECEIPT.json'
        receipt=json.loads(path.read_text());receipt['complete']=False
        path.write_bytes(artifacts.canonical(receipt)+b'\n')
        with self.assertRaises(artifacts.ArtifactError):artifacts.load_artifact(target)
        path.unlink()
        with self.assertRaises(artifacts.ArtifactError):artifacts.load_artifact(target)

    def test_v2_loader_refuses_v3_instead_of_reinterpreting_weights(self):
        with self.assertRaises(ValueError):load_v2(self.directory)

    def test_duplicate_metadata_fields_and_public_permissions_are_refused(self):
        target=self.working_copy('duplicate-registry');path=target/'registry.json'
        raw=path.read_text();path.write_text(raw.rstrip()[:-1]+',"format":"duplicate"}')
        with self.assertRaises(ValueError):artifacts.load_artifact(target)
        target=self.working_copy('public-file');(target/'weights.f64').chmod(0o644)
        with self.assertRaises(ValueError):artifacts.load_artifact(target)

    def test_existing_outputs_and_symlinked_inputs_are_refused(self):
        with self.assertRaises(TrainingError):
            build(self.input,self.provenance,self.directory,'unit-contextual-v3',epochs=1)
        alias=self.root/'input-alias.jsonl';alias.symlink_to(self.input)
        with self.assertRaises(ValueError):
            build(alias,self.provenance,self.root/'never-alias','unit-contextual-v3',epochs=1)
        self.assertFalse((self.root/'never-alias').exists())

    def test_work_and_time_refusal_publish_no_partial_artifact(self):
        with patch('eidos.train_contextual.MAX_WORK_UNITS',1),self.assertRaises(TrainingError):
            build(self.input,self.provenance,self.root/'never-work','unit-contextual-v3',epochs=1)
        self.assertFalse((self.root/'never-work').exists())
        with patch('eidos.train_contextual.CPU_SECONDS',-1),self.assertRaises(TrainingError):
            build(self.input,self.provenance,self.root/'never-time','unit-contextual-v3',epochs=1)
        self.assertFalse((self.root/'never-time').exists())


if __name__=='__main__':unittest.main()
