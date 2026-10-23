"""Eight offline roles using original authored fixtures; no real corpus/native model."""
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import time

from run_dbpedia_study_v1 import ROOT, STUDY, read, role, sha, write

OUT = ROOT / 'evaluation/production/dbpedia-study-v1/authored-fixture-v3'


def main():
    assert os.getuid() == 1000 and Path.cwd() == ROOT
    OUT.mkdir(mode=0o700, parents=True, exist_ok=False)
    resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
    resource.setrlimit(resource.RLIMIT_AS, (768*1024**2,)*2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64*1024**2,)*2)
    active = [None]
    def deadline(_signum, _frame):
        if active[0] is not None and active[0].poll() is None: os.killpg(active[0].pid, signal.SIGKILL)
        raise TimeoutError('Authored fixture deadline')
    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 120)
    started = time.monotonic()
    source = OUT / 'controls'; source.mkdir(mode=0o700)
    before = {p.name: sha(p) for p in STUDY.glob('*.py')}
    for name in before:
        shutil.copyfile(STUDY / name, source / name); (source / name).chmod(0o400)
    data = OUT / 'authored-data'; data.mkdir(mode=0o700)
    def jsonl(name, values):
        with (data/name).open('xb') as f:
            for value in values:f.write(json.dumps(value,sort_keys=True).encode()+b'\n')
        (data/name).chmod(0o400)
    for split in ('support','development','test'):
        values = [{'id': f'{split}-{label}-{i:02d}',
                   'text': f'{word} help request {split} original example number {i}', 'label': label}
                  for label,word in (('A','Alpha'),('B','Beta')) for i in range(20)]
        if split == 'test':
            jsonl('test-inputs.jsonl', [{k:r[k] for k in ('id','text')} for r in values])
            jsonl('test-gold.jsonl', [{k:r[k] for k in ('id','label')} for r in values])
            jsonl('test-groups.jsonl', [{'id':r['id'],'group_id':'group-'+r['id']} for r in values])
        else:jsonl(split+'.jsonl', values)
    write(data/'provenance.json', {'source':'Original authored Alpha/Beta mechanics fixtures',
          'license':'CC0-1.0','citation':'EIDOS original authored fixture','rights_basis':'Original authored material; not DBpedia or LAYA outputs'})
    config = OUT / 'config.json'
    write(config, {'authored_fixture_only':True,'labels':['A','B'],'n':40,
          'reference_aliases':{'A':'authored_alpha','B':'authored_beta'},
          'recipe_order':['memory','linear_gd','linear_adam'],
          'recipes':{'memory':{},'linear_gd':{'optimizer':'gradient_descent','epochs':12},
                     'linear_adam':{'optimizer':'adam','epochs':12}},
          'cohort_files':{p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in data.iterdir()}})
    tree={'/owned-app':{'root':str(STUDY/'owned-app'),'manifest':str(STUDY/'owned-app-manifest.json'),
                       'sha256':sha(STUDY/'owned-app-manifest.json')}}
    summaries = [role('unit', OUT, source, config, [], tree, (30,45,768), active, script='test_contracts.py')]
    for name in ('memory','linear_gd','linear_adam','select','eidos','laya','score'):
        mounts = []
        if name in ('memory','linear_gd','linear_adam'):
            mounts=[(data/p,'/data/'+p) for p in ('support.jsonl','provenance.json')]
        elif name=='select':
            mounts=[(data/'development.jsonl','/data/development.jsonl')]
            mounts += [(OUT/p/'data/registry','/models/'+p) for p in ('memory','linear_gd','linear_adam')]
        elif name=='eidos':
            selection=OUT/'select/data/result.json';chosen=read(selection)['selected']
            mounts=[(selection,'/selection.json'),(OUT/chosen/'data/registry','/selected-registry'),
                    (data/'test-inputs.jsonl','/data/test-inputs.jsonl')]
        elif name=='laya':mounts=[(data/'test-inputs.jsonl','/data/test-inputs.jsonl')]
        else:
            mounts=[(data/p,'/data/'+p) for p in ('test-gold.jsonl','test-groups.jsonl')]
            mounts += [(OUT/'select/data/result.json','/selection.json')]
            mounts += [(OUT/p/'data','/'+p) for p in ('eidos','laya')]
        summaries.append(role(name, OUT, source, config, mounts, tree, (90,100,768), active,
                              script='authored_reference_fixture.py' if name=='laya' else 'study.py'))
    result=read(OUT/'score/data/result.json')
    assert result['complete'] and result['n']==40 and result['paired']['groups']==40
    assert result['paired']['numeric_gates']['both_pass'] is False
    assert before=={p.name:sha(p) for p in STUDY.glob('*.py')}
    summary={'complete':True,'stages':summaries,'elapsed_seconds':time.monotonic()-started,
             'authored_test_methods':16,'actual_owned_training_selection_prediction_and_paired_score':True,
             'reference_was_explicit_authored_substitute_not_LAYA':True,'real_corpus_or_native_reference_mounted':False,
             'source_files_unchanged':len(before),'corpus_quality_or_LAYA_claim':False,
             'whole_wall_seconds_limit':120,'per_process_limits_not_aggregate_cgroup':True}
    write(OUT/'execution-summary.json',summary)
    print(json.dumps(summary))


if __name__=='__main__':main()
