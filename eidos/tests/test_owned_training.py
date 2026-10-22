"""Authored mechanical fixtures only: artifact, input and review-policy tests."""
import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from eidos.contracts import ContractError
from eidos.engine import Engine
from eidos.model import canonical
from eidos.train_support import TrainingError,build,parse_training


def fixture():
    values=[]
    for label,stem in [('billing_help','help with invoice payment reference'),('music_help','play my song music selection')]:
        for i in range(20):values.append({'id':label+'-'+str(i),'text':stem+' '+str(i),'label':label})
    return values


PROVENANCE=canonical({'source':'Original authored test fixture','license':'CC0-1.0','citation':'EIDOS mechanical test only','rights_basis':'Original synthetic sentences; no benchmark or private data'})
def raw(values):return b''.join(canonical(v)+b'\n' for v in values)


class OwnedTraining(unittest.TestCase):
    def files(self,directory,values=None):
        root=Path(directory);a=root/'input.jsonl';b=root/'provenance.json'
        a.write_bytes(raw(fixture() if values is None else values));b.write_bytes(PROVENANCE);return a,b

    def test_round_trip_actual_engine_review_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            a,b=self.files(directory);out=Path(directory)/'model';r=build(a,b,out,'owned-demo')
            self.assertEqual(r['input_sha256'],hashlib.sha256(a.read_bytes()).hexdigest())
            self.assertEqual(r['provenance_sha256'],hashlib.sha256(PROVENANCE).hexdigest())
            engine=Engine.from_registry(out,tenants=('demo',))
            request={'schema_version':'1.0','input_version':'authored-test','request_id':'authored-only','tenant_id':'demo','language':'en',
                'state':{'utterance':'help me with an invoice'},'sources':[],'questions':[{'id':'q','kind':'CHOICE','profile':'owned-demo',
                    'prompt':'Classify this utterance','state_path':['utterance'],'choices':[{'id':c,'class_id':c} for c in ('billing_help','music_help')]}]}
            response=engine.decide(request);d=response['decisions'][0]
            self.assertEqual(d['status'],'abstained');self.assertIsNone(d['value']);self.assertTrue(d['review_required'])
            self.assertIsNone(d['calibrated_confidence']);self.assertEqual(len(d['distribution']),2)
            self.assertEqual(response['authorization'],'recommendation_only_no_action_authorized')
            model=engine.models['owned-demo'];self.assertEqual(model.data['training']['declaration_status'],'caller_supplied_not_independently_verified')
            self.assertEqual(out.stat().st_mode&0o777,0o700)
            self.assertTrue(all(p.stat().st_mode&0o777==0o400 for p in out.iterdir()))

    def test_byte_for_byte_reproduction(self):
        with tempfile.TemporaryDirectory() as directory:
            a,b=self.files(directory);root=Path(directory)
            first=build(a,b,root/'first','owned-demo');second=build(a,b,root/'second','owned-demo')
            self.assertEqual(first,second)
            for p in (root/'first').iterdir():self.assertEqual(p.read_bytes(),(root/'second'/p.name).read_bytes())

    def test_input_order_preserves_numerical_artifact_and_tracks_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            a,b=self.files(directory);root=Path(directory);build(a,b,root/'first','owned-demo')
            a.write_bytes(raw(list(reversed(fixture()))));build(a,b,root/'second','owned-demo')
            self.assertEqual((root/'first/support.sparse').read_bytes(),(root/'second/support.sparse').read_bytes())
            left=json.loads((root/'first/model.json').read_bytes());right=json.loads((root/'second/model.json').read_bytes())
            for key in ('vocabulary','idf','support_ids'):self.assertEqual(left[key],right[key])
            self.assertNotEqual(left['training']['input_sha256'],right['training']['input_sha256'])

    def test_exact_balanced_support_quotas(self):
        values=fixture();values[-1]['label']='billing_help'
        with self.assertRaisesRegex(TrainingError,'exactly_20_rows_per_class_required'):parse_training(raw(values),PROVENANCE)

    def test_duplicate_ids_and_normalized_text_refused(self):
        for field in ('id','text'):
            values=fixture();values[1][field]=values[0][field]
            if field=='text':values[1][field]='  '+values[0][field].upper()+'!!! '
            with self.assertRaises(TrainingError):parse_training(raw(values),PROVENANCE)

    def test_strict_row_schema_and_json(self):
        values=fixture();values[0]['extra']='not accepted'
        with self.assertRaises(TrainingError):parse_training(raw(values),PROVENANCE)
        duplicate=b'{"id":"x","id":"y","text":"a","label":"billing_help"}\n'+raw(fixture()[1:])
        with self.assertRaises(ContractError):parse_training(duplicate,PROVENANCE)
        with self.assertRaises(TrainingError):parse_training(raw(fixture())+b'\n',PROVENANCE)

    def test_provenance_is_explicit_bounded_declaration(self):
        for p in (b'{}',canonical({'source':'x','license':'CC0-1.0','citation':'demo','rights_basis':' '}),PROVENANCE.replace(b'"source":',b'"source":"duplicate","source":')):
            with self.assertRaises((TrainingError,ContractError)):parse_training(raw(fixture()),p)

    def test_malformed_unicode_and_zero_word_text(self):
        values=fixture();values[0]['text']='🧬🛰🫧'
        with self.assertRaises(TrainingError):parse_training(raw(values),PROVENANCE)
        malformed=raw(fixture()).replace(b'help with invoice',b'help\xffwith invoice',1)
        with self.assertRaises(UnicodeError):parse_training(malformed,PROVENANCE)
        escaped=raw(fixture()).replace(b'help with invoice',b'help\\ud800with invoice',1)
        with self.assertRaises(UnicodeError):parse_training(escaped,PROVENANCE)

    def test_input_and_aggregate_text_bounds(self):
        with self.assertRaises(TrainingError):parse_training(b' '*(2*1024**2+1),PROVENANCE)
        values=fixture()
        for row in values:row['text']+=' lorem'*980
        with self.assertRaisesRegex(TrainingError,'aggregate_text_limit'):parse_training(raw(values),PROVENANCE)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            a,b=self.files(directory);out=Path(directory)/'keep';out.mkdir();(out/'sentinel').write_bytes(b'preserve this')
            with self.assertRaisesRegex(TrainingError,'output_already_exists'):build(a,b,out,'owned-demo')
            self.assertEqual((out/'sentinel').read_bytes(),b'preserve this');self.assertEqual(len(list(out.iterdir())),1)

    def test_input_and_output_symlink_refusals(self):
        with tempfile.TemporaryDirectory() as directory:
            a,b=self.files(directory);root=Path(directory);alias=root/'alias';alias.symlink_to(a)
            with self.assertRaises(OSError):build(alias,b,root/'out','owned-demo')
            output_alias=root/'out-alias';output_alias.symlink_to(root/'missing')
            with self.assertRaisesRegex(TrainingError,'output_already_exists'):build(a,b,output_alias,'owned-demo')

    def test_invalid_identity_fails_before_artifact_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            a,b=self.files(directory);out=Path(directory)/'out'
            for ident in ('','../escape','bad name','a'*129):
                with self.assertRaisesRegex(TrainingError,'invalid_model_id_or_version'):build(a,b,out,ident)
                self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
