"""New source-bound direct seven-option reference path; never automatic execution."""
import collections,hashlib,json,math,resource,sys,time
from pathlib import Path
N=1821


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while block:=f.read(1024**2):h.update(block)
    return h.hexdigest()
def write(path,value):
    with Path(path).open('xb') as f:f.write(canonical(value)+b'\n')
def rows(path):
    assert Path(path).stat().st_size<16*1024**2
    result=[json.loads(v) for v in Path(path).read_bytes().splitlines()]
    assert len(result)==len({r['id'] for r in result})==N;return result
def cpu():
    r=resource.getrusage(resource.RUSAGE_SELF);return r.ru_utime+r.ru_stime
def check_response(probabilities,prediction,labels):
    assert len(probabilities)==len(labels)==7 and prediction in labels
    assert all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in probabilities)
    assert abs(math.fsum(probabilities)-1)<1e-10


def predict():
    import re
    from primary_predict import verify_large_tree,validate_rights
    pins=json.loads(Path('/pins.json').read_bytes());inventories={}
    for name,pin in pins['trees'].items():
        inventories[name]=verify_large_tree('/'+name,'/'+name+'-manifest.json',pin)['manifest']
    validate_rights(json.loads(Path('/rights.json').read_bytes()),inventories['model'],inventories['laya-source'])
    assert not any(Path(p).exists() for p in ('/gold.jsonl','/eidos.jsonl','/workspace','/root'))
    for path,pin in pins['files'].items():assert sha(path)==pin
    labels=json.loads(Path('/labels.json').read_bytes());assert len(labels)==len(set(labels))==7 and labels==sorted(labels)
    inputs=rows('/inputs.jsonl');assert all(set(r)=={'id','text','group_hash'} for r in inputs)
    sys.path[:0]=['/application','/laya-source','/deps']
    from benchmarks.predict import Laya,decode_laya_choice
    from laya import common
    adapter=Laya('/model','/laya-source','/rights.json',threads=1)
    assert adapter.agent.cfg['max_len']==512 and adapter.agent.cfg['head_max_len']==192
    descriptions={c:re.sub(r'(?<=[a-z0-9])(?=[A-Z])',' ',c).replace('_',' ').casefold() for c in labels}
    definition={'intent':{'type':'choice','instructions':'Classify this utterance','criteria':descriptions}}
    with Path('/output/predictions.jsonl').open('xb') as f:
        for i,row in enumerate(inputs):
            state={'utterance':row['text']};question={'t':'choice','ins':'Classify this utterance','crit':descriptions}
            _,markers,stats,truncation=common.build_sequence(adapter.agent.tok,state,question,max_len=512,head_max_len=192,return_stats=True,return_truncation_stats=True)
            assert len(markers)==stats['options']==stats['options_distinct']==7
            assert truncation['state_tokens_dropped']==0 and not truncation['truncated']
            response=adapter.agent.predict(state,definition,lang='en');p,choice,answered,diagnostics=decode_laya_choice(response,labels)
            check_response(p,choice,labels);assert not diagnostics['usage'].get('options') and not diagnostics['usage'].get('truncated')
            f.write(canonical({'id':row['id'],'input_row_sha256':hashlib.sha256(canonical(row)).hexdigest(),
                'prediction':choice,'probabilities':p,'public_answered':answered})+b'\n');f.flush()
            if (i+1)%20==0 or i+1==N:
                progress={'completed':i+1,'total':N,'cpu_seconds':cpu(),'quality_scored':False}
                Path('/output/progress.tmp').write_bytes(canonical(progress)+b'\n');Path('/output/progress.tmp').replace('/output/progress.json')
                print(json.dumps(progress),flush=True)
    write('/output/result.json',{'complete':True,'n':N,'predictions_sha256':sha('/output/predictions.jsonl'),'pins_sha256':sha('/pins.json'),
        'public_direct_forward_calls':N,'all_seven_options_and_state_preserved':True,'adapter':adapter.metadata,
        'gold_or_peer_outputs_available':False,'quality_scored':False,'cpu_seconds':cpu(),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})


def score():
    from statistics import macro,bootstrap
    assert not any(Path(p).exists() for p in ('/inputs.jsonl','/model','/deps','/laya-source'))
    pins=json.loads(Path('/pins.json').read_bytes())
    for path,pin in pins['files'].items():assert sha(path)==pin
    receipt=json.loads(Path('/prediction-result.json').read_bytes());assert receipt['complete'] and receipt['n']==N and receipt['predictions_sha256']==sha('/laya.jsonl')
    labels=json.loads(Path('/labels.json').read_bytes());gold=rows('/gold.jsonl');eidos=rows('/eidos.jsonl');laya=rows('/laya.jsonl')
    assert dict(collections.Counter(r['label'] for r in gold))==pins['gold_class_counts']
    predictions={a:[] for a in ('linear','memory','laya')}
    for g,e,l in zip(gold,eidos,laya,strict=True):
        assert g['id']==e['id']==l['id'] and g['input_row_sha256']==l['input_row_sha256'];check_response(l['probabilities'],l['prediction'],labels)
        predictions['laya'].append(l['prediction'])
        for arm in ('linear','memory'):
            d=e['results']['seven'][arm];assert d['status']=='abstained' and d['review_required'] and d['candidate_value'] in labels
            predictions[arm].append(d['candidate_value'])
    truth=[r['label'] for r in gold];metrics={};paired={}
    for arm,p in predictions.items():
        counts=collections.Counter(zip(truth,p));correct=sum(a==b for a,b in zip(truth,p))
        metrics[arm]={'n':N,'correct':correct,'accuracy':correct/N,'fixed_seven_macro_f1':macro(truth,p,labels),
            'confusion':{'labels':labels,'counts':[[counts[g,v] for v in labels] for g in labels]}}
    for arm in ('linear','memory'):
        a=[p==g for p,g in zip(predictions[arm],truth)];b=[p==g for p,g in zip(predictions['laya'],truth)]
        intervals=bootstrap(gold,predictions[arm],predictions['laya'],labels)
        differences={k:metrics[arm][k]-metrics['laya'][k] for k in ('accuracy','fixed_seven_macro_f1')}
        paired[arm]={'both_correct':sum(x and y for x,y in zip(a,b)),'eidos_only_correct':sum(x and not y for x,y in zip(a,b)),
            'laya_only_correct':sum(y and not x for x,y in zip(a,b)),'both_wrong':sum(not x and not y for x,y in zip(a,b)),
            'differences_eidos_minus_laya':differences,'approximate_component_bootstrap':intervals,
            'both_scoped_gates_passed':all(v>0 for v in differences.values()) and intervals['accuracy_difference_95_percentile'][0]>0 and intervals['fixed_seven_macro_f1_difference_95_percentile'][0]>0}
    write('/output/result.json',{'complete':True,'n':N,'metrics':metrics,'paired':paired,'primary_arm':'linear',
        'posthoc_previously_revealed_eidos_cohort':True,'pilot_quality_seen_before_followup_plan':True,'confirmatory_or_global_error_control_claimed':False,'matched_adaptation':False,'eidos_support_per_label':20,'laya_zero_shot':True,
        'new_jev_comparison':False,'general_superiority_claimed':False,'model_promoted':False,'mission_complete':False,'production_certified':False,'cpu_seconds':cpu()})


if __name__=='__main__':{'predict':predict,'score':score}[sys.argv[1]]()
