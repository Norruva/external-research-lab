"""Private self-contained original source/demo release; no inference or activation."""
from pathlib import Path
import hashlib,io,json,os,tarfile
R=Path('/workspace/eidos');A=Path('/workspace/external-research-lab/eidos');B=R/'evaluation/production/decision-model-v1';PREFIX='eidos-owned-question-decision-v1'
def sha(b):return hashlib.sha256(b).hexdigest()
def main():
 check=json.loads((B/'final-source-consistency-v1.json').read_bytes());assert check['complete']
 run=json.loads((B/'endtoend-v5/data/demo/result.json').read_bytes());assert run['complete']
 assert json.loads((B/'browser-network-review-v1.json').read_bytes())['complete']
 files={}
 selected=[*sorted((A/'eidos').glob('*.py')),A/'eidos/decision-api.schema.json']
 selected += [A/'web'/n for n in ('decision.html','decision.js','style.css')]
 selected += [*sorted((A/'examples/decision-owned-demo').iterdir())]
 selected += [A/'docs'/n for n in ('DECISION-MODEL.md','DECISION-DATA.md','DECISION-EPISTEMIC.md','DECISION-BUILD-RESULT.md')]
 for p in selected:
  assert p.is_file() and not p.is_symlink();files[p.relative_to(A).as_posix()]=p.read_bytes()
 model=B/'endtoend-v5/data/demo/model'
 for p in sorted(model.iterdir()):
  assert p.is_file() and not p.is_symlink() and p.stat().st_mode & 0o777==0o400
  files['models/owned-demo/'+p.name]=p.read_bytes()
 files['README.md']=b'''# Original owned EIDOS decision-model demonstration\n\nThis private release includes the original question/context/option-conditioned model implementation, its locally fitted original144-task mechanical demo model, the local API/browser and finite evidence mathematics. Python3.12+/POSIX with standard SQLite/OpenSSL is required; inference needs no pip/npm package, hosted model, vendor checkpoint or internet.\n\nFrom the extracted directory, query the included model:\n\n```sh\npython3 -S -B -m eidos.decision --registry models/owned-demo < examples/decision-owned-demo/request-choice.json\n```\n\nFor the local browser, explicitly create a NEW private config:\n\n```sh\npython3 -S -B -m eidos.decision_service --init-local --config local-config.json --registry models/owned-demo\npython3 -S -B -m eidos.decision_service --config local-config.json --port 8766\n```\n\nOpen http://127.0.0.1:8766/ on the same machine, using the new config's decision-operator key. The packager never runs these commands or creates a key. This is a loopback development service, not a public production deployment.\n\nThe model is a weak compact demonstration:4/10 visible choice/binary smoke answers correct, not an independent semantic-quality estimate. It is not a JEV/LAYA replacement, and it does not inherit the separate fixed-class DBpedia score. Every result remains uncalibrated,review-only,value=null,noactions. Read docs/DECISION-BUILD-RESULT.md for the retained failures and full limits.\n\nThis archive contains original authored synthetic tasks and their demonstration weights, no real benchmark data/gold/per-case predictions, LAYA/JEV tensors/implementation, existing keys/configuration or state. Original source has no new blanket public-license grant. Commodity infrastructure retains its own terms; rights declarations are not an exclusive-ownership certification. Keep this artifact private.\n'''
 manifest={'format':'eidos-owned-question-decision-release-v1','source_parent_commit':'6b82539672f7fead65353dba4feebcfde0432b75','files':{n:{'bytes':len(b),'sha256':sha(b)} for n,b in sorted(files.items())},'model':run['model'],'registry_sha256':run['registry_sha256'],'source_parameter_or_training_data_ownership_legally_certified':False,'original_author_fixture_not_benchmark_data':True,'new_public_code_license_granted':False,'quality_fixture_correct':4,'quality_fixture_total':10,'independent_quality_or_LAYA_JEV_parity':False,'model_promoted':False,'mission_complete':False,'production_certified':False}
 files['MANIFEST.json']=json.dumps(manifest,sort_keys=True,indent=2).encode()+b'\n'
 p=R/(PREFIX+'.tar.gz');fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
 with os.fdopen(fd,'wb') as stream:
  with tarfile.open(fileobj=stream,mode='w:gz',format=tarfile.USTAR_FORMAT) as arc:
   for n,b in sorted(files.items()):
    t=tarfile.TarInfo(PREFIX+'/'+n);t.mode=0o400;t.size=len(b);t.mtime=0;arc.addfile(t,io.BytesIO(b))
 with tarfile.open(p,'r:gz') as arc:assert {m.name.removeprefix(PREFIX+'/'):arc.extractfile(m).read() for m in arc.getmembers()}==files
 receipt={'format':'eidos-owned-question-decision-packaging-v1','complete':True,'file':str(p),'bytes':p.stat().st_size,'sha256':sha(p.read_bytes()),'files_verified':len(files),'manifest_sha256':sha(files['MANIFEST.json']),'model':run['model'],'authored_demo_model_included':True,'raw_benchmark_vendor_model_existing_credentials_or_state_included':False,'packaging_performed_training_inference_activation_or_network':False,'private_release_not_public_weights_distribution':True,'mission_complete':False,'production_certified':False}
 q=R/(PREFIX+'-receipt.json');fd=os.open(q,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
 with os.fdopen(fd,'w') as out:out.write(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
 print(json.dumps(receipt))
if __name__=='__main__':main()
