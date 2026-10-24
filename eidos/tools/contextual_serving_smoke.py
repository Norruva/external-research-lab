"""Check a caller-supplied v3 artifact through CLI/signed local HTTP, no fit/gold."""
import argparse
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from eidos.contextual import ContextualEngine
from eidos.decision_contract import canonical_request
from tools.contextual_smoke import call,check,command,need,signed


def main():
    p=argparse.ArgumentParser();p.add_argument('--registry',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();os.umask(0o077)
    out=Path(args.output).absolute();registry=Path(args.registry).absolute()
    need('..' not in out.parts and not any(x.is_symlink() for x in (out,*out.parents)),'output_refused')
    out.mkdir(mode=0o700)
    engine=ContextualEngine.from_registry(registry)
    base={'format':'eidos-decision-request-v2','request_id':'serving-v3-choice',
          'context':'Ena carries jade. Fenn carries violet.','prompt':'What does Ena carry?',
          'kind':'CHOICE','options':[{'id':'j','description':'jade'},{'id':'v','description':'violet'}]}
    binary=copy.deepcopy(base);binary.update(request_id='serving-v3-binary',kind='BINARY',
        prompt='Does Ena carry jade?',options=[{'id':'false','description':'The claim is false.'},
                                             {'id':'true','description':'The claim is true.'}])
    score=copy.deepcopy(base);score.update(request_id='serving-v3-score',kind='SCORE',minimum=0,maximum=2,
        prompt='Select an ordinal band.',options=[{'id':str(i),'description':v,'value':i}
                                                  for i,v in enumerate(('low','medium','high'))])
    requests=[base,binary,score];responses=[engine.decide(q) for q in requests]
    checks=['actual_frozen_contextual_artifact_loaded']
    for q,response in zip(requests,responses):
        check(response,q['kind'],len(q['options']))
        need(command('-m','eidos.contextual','--registry',str(registry),
                     '--registry-sha256',engine.registry_sha256,payload=canonical_request(q))==response,
             'CLI_library_parity_failed')
    checks.extend(['three_review_only_typed_heads','actual_CLI_library_exact_parity'])
    config_path=out/'fresh-config.json';process=None
    try:
        initialized=command('-m','eidos.contextual_service','--init-local','--config',str(config_path),
                            '--registry',str(registry))
        need(not initialized['key_values_printed'] and initialized['registry_sha256']==engine.registry_sha256,
             'configuration_identity_failed')
        config=json.loads(config_path.read_bytes())
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        process=subprocess.Popen([sys.executable,'-S','-B','-m','eidos.contextual_service',
            '--config',str(config_path),'--port',str(port)],cwd=ROOT,stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+10
        while True:
            need(process.poll() is None,'service_exited')
            try:
                if call(port,'GET','/healthz')[0]==200:break
            except OSError:pass
            need(time.monotonic()<deadline,'service_start_deadline');time.sleep(.03)
        checks.extend(['fresh_configuration_pins_frozen_registry','actual_loopback_service_health'])
        need(call(port,'POST','/v2/decisions',canonical_request(base))[0]==403,'unsigned_accepted')
        for q,expected in zip(requests,responses):
            body=canonical_request(q);auth=signed(config,'POST','/v2/decisions',body)
            status,raw=call(port,'POST','/v2/decisions',body,auth)
            need(status==200 and json.loads(raw)==expected,'HTTP_library_parity_failed')
        checks.append('signed_HTTP_three_head_parity_and_unsigned_refusal')
        need(call(port,'POST','/v2/decisions',body,auth)[0]==403,'replay_accepted')
        checks.append('actual_signature_replay_refused')
        status,raw=call(port,'GET','/v2/model',auth=signed(config,'GET','/v2/model',b''))
        metadata=json.loads(raw)
        need(status==200 and metadata['model']==engine.identity and
             metadata['registry_sha256']==engine.registry_sha256 and 'secret' not in metadata,
             'metadata_pin_mismatch')
        checks.append('authenticated_model_identity_pinned')
        for path,marker in [('/',b'EIDOS'),('/decision.js',b'crypto.subtle'),('/style.css',b':root')]:
            status,raw=call(port,'GET',path);need(status==200 and marker in raw,'asset_unavailable')
        checks.append('unchanged_local_browser_assets_served')
    finally:
        if process is not None:
            if process.poll() is None:process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        config_path.unlink(missing_ok=True)
    result={'format':'eidos-contextual-frozen-serving-smoke-v1','complete':True,
        'checks':checks,'check_count':len(checks),'model':engine.identity,'registry_sha256':engine.registry_sha256,
        'model_fit_or_gold_quality_scoring':False,'new_original_unlabeled_mechanics_requests':3,
        'private_configuration_deleted':not config_path.exists(),
        'service_stopped':process is None or process.poll() is not None,
        'production_certified':False,'calibration_fitted':False,'model_promoted':False}
    (out/'result.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
