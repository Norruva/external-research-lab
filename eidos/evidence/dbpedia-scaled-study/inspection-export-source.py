"""Export an explicit public source/aggregate inspection subset without model or case data."""
from pathlib import Path
import hashlib, io, json, os, tarfile
ROOT=Path('/workspace/eidos')
REPO=Path('/workspace/external-research-lab')
PREFIX='eidos-dbpedia-scaled-result-review-v1'

def sha(raw): return hashlib.sha256(raw).hexdigest()

def main():
    review=json.loads((ROOT/'evaluation/production/dbpedia-scaled-study-v1/completion-review-v1.json').read_bytes())
    assert review['complete'] and review['study_completed_successfully']
    selected=[p.relative_to(REPO).as_posix() for p in sorted((REPO/'eidos/eidos').iterdir()) if p.is_file()]
    assert len(selected)==27
    selected += ['eidos/research/dbpedia-scaled-cohort-v1/'+n for n in ('cohort.py','prepare.py','test_cohort.py')]
    selected += ['eidos/research/dbpedia-scaled-study-v1/'+n for n in ('contracts.py','study.py','tree_review.py','run_dbpedia_scaled_study_v1.py','plan.json','PLAN.md','prepared-manifest-v1.json','owned-app-manifest.json')]
    selected += ['eidos/README.md','eidos/evidence/README.md','eidos/docs/DBPEDIA-SCALED-RESULT.md','eidos/docs/DBPEDIA-SCALED-ATTRIBUTION.md','eidos/docs/EVALUATION.md','eidos/docs/SCALABLE-TRAINING.md']
    names=('score.json','completion-review.json','execution-summary.json','cohort-readiness.json','history-readiness.json','training.json','eidos-prediction-receipt.json','laya-prediction-receipt.json','reference-feasibility.json','primary-freeze.json','authorization.json','approval-provenance.json','private-owned-package-receipt.json','completion-review-source.py','owned-packaging-source.py','passive-rea-source.py','history-execution.json','corpus-execution.json','train-execution.json','development-execution.json','eidos-execution.json','laya-execution.json','score-execution.json')
    selected += ['eidos/evidence/dbpedia-scaled-study/'+n for n in names]
    assert len(selected)==len(set(selected))==67
    files={}
    for name in sorted(selected):
        p=REPO/name
        assert p.is_file() and not any(q.is_symlink() for q in (p,*p.parents))
        raw=p.read_bytes(); assert 0<len(raw)<=16_000_000
        files[name]=raw
    manifest={'format':'eidos-dbpedia-scaled-result-review-manifest-v1','scope':'explicit_public_source_documentation_and_completed_aggregate_evidence_subset','files':{n:{'bytes':len(r),'sha256':sha(r)} for n,r in files.items()},'target_execution':False,'quality_recalculated':False,'raw_cases_gold_groups_percase_predictions_model_vocabulary_weights_native_credentials_state_included':False,'whole_application_security_rights_or_quality_certificate':False}
    files['MANIFEST.json']=json.dumps(manifest,sort_keys=True,indent=2).encode()+b'\n'
    assert len(files)==68 and sum(map(len,files.values()))<=32*1024**2
    artifact=ROOT/(PREFIX+'.tar.gz')
    fd=os.open(artifact,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
    with os.fdopen(fd,'wb') as stream:
        with tarfile.open(fileobj=stream,mode='w:gz',format=tarfile.USTAR_FORMAT) as arc:
            for name,raw in sorted(files.items()):
                item=tarfile.TarInfo(PREFIX+'/'+name);item.mode=0o400;item.mtime=0;item.size=len(raw)
                arc.addfile(item,io.BytesIO(raw))
    with tarfile.open(artifact,'r:gz') as arc:
        assert {m.name.removeprefix(PREFIX+'/'):arc.extractfile(m).read() for m in arc.getmembers()}==files
    receipt={'format':'eidos-dbpedia-scaled-result-review-export-v1','complete':True,'file':str(artifact),'bytes':artifact.stat().st_size,'sha256':sha(artifact.read_bytes()),'files_verified':len(files),'manifest_sha256':sha(files['MANIFEST.json']),'inspection_subset_not_complete_application':True,'raw_case_gold_predictions_or_model_weights_included':False,'fit_prediction_scoring_installation_activation_or_network_performed':False}
    p=ROOT/(PREFIX+'-receipt.json')
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
    with os.fdopen(fd,'w') as out:out.write(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    print(json.dumps(receipt))

if __name__=='__main__':main()
