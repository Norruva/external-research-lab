"""Owned v4 training/CLI/signed-HTTP mechanics on four new authored tasks.

This is not semantic evaluation. Fresh configuration keys are never printed and
are deleted after the loopback service stops. No existing model or key is read.
"""
import argparse
import copy
import http.client
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eidos.routed import RoutedEngine
from eidos.decision_contract import canonical_request
from eidos.service import signature


def need(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def command(*args, payload=None, timeout=30):
    result = subprocess.run([sys.executable, '-S', '-B', *args], input=payload,
                            cwd=ROOT, capture_output=True, timeout=timeout)
    need(result.returncode == 0, 'owned_routed_command_failed')
    need(len(result.stdout) <= 2_000_000, 'command_output_bound')
    return json.loads(result.stdout)


def signed(config, method, path, body):
    timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
    return {'X-Eidos-Key': config['key_id'], 'X-Eidos-Time': timestamp, 'X-Eidos-Nonce': nonce,
            'X-Eidos-Signature': signature(bytes.fromhex(config['secret']), config['key_id'],
                                         timestamp, nonce, method, path, body)}


def call(port, method, path, body=b'', auth=None):
    client = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        headers = {'Content-Type': 'application/json'} if method == 'POST' else {}
        headers.update(auth or {})
        client.request(method, path, body=body, headers=headers)
        response = client.getresponse(); raw = response.read(2_000_001)
        need(len(raw) <= 2_000_000, 'HTTP_output_bound')
        return response.status, raw
    finally:
        client.close()


def check(response, kind, count):
    need(response['format'] == 'eidos-decision-response-v2' and response['kind'] == kind,
         'typed_response_mismatch')
    need(response['review_required'] and response['value'] is None and
         response['calibrated_confidence'] is None and
         response['authorization'] == 'recommendation_only_no_action_authorized', 'unsafe_decision')
    need(len(response['distribution']) == count and math.isclose(math.fsum(
        row['probability'] for row in response['distribution']), 1., abs_tol=1e-12), 'incomplete_distribution')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True); args = parser.parse_args()
    os.umask(0o077)
    output = Path(args.output).absolute()
    need('..' not in output.parts and not any(p.is_symlink() for p in (output, *output.parents)), 'output_path_refused')
    output.mkdir(mode=0o700, exist_ok=False)
    base = {'format': 'eidos-decision-request-v2', 'request_id': 'new-routed-choice',
            'context': 'Sela has ruby color. Tovan has teal color.', 'prompt': 'What color does Sela have?',
            'kind': 'CHOICE', 'options': [{'id': 'r', 'description': 'ruby'}, {'id': 't', 'description': 'teal'}]}
    training = []
    for variant in range(2):
        for person, target in [('Sela', 'r' if variant == 0 else 't'), ('Tovan', 't' if variant == 0 else 'r')]:
            q = copy.deepcopy(base); q['request_id'] = f'routed-training-{variant}-{person}'
            q['context'] = base['context'] if variant == 0 else 'Sela has teal color. Tovan has ruby color.'
            q['prompt'] = f'What color does {person} have?'
            training.append({'request': q, 'target_option_id': target, 'source_group': 'new-adapter-pair'})
    provenance = {'source': 'Four original routed transport mechanics tasks authored in routed_smoke.py',
        'license': 'Original authored fixture; no third-party material or new public license grant',
        'citation': 'EIDOS routed adapter integration mechanics, 2026',
        'rights_basis': 'Original finite authored facts; no external corpus, vendor output or pretrained model'}
    (output/'training.jsonl').write_text(''.join(json.dumps(row, sort_keys=True)+'\n' for row in training))
    (output/'provenance.json').write_text(json.dumps(provenance, sort_keys=True))
    receipt = command('-m', 'eidos.train_routed', '--input', str(output/'training.jsonl'),
        '--provenance', str(output/'provenance.json'), '--output', str(output/'model'),
        '--model-id', 'owned-routed-adapter-mechanics-v1', '--epochs', '1', '--seed', '113', timeout=700)
    checks = ['actual_new_authored_training_CLI_one_epoch']
    engine = RoutedEngine.from_registry(output/'model')
    checks.append('checked_routed_registry_loaded')
    binary = copy.deepcopy(base); binary.update(request_id='new-routed-binary', kind='BINARY',
        prompt='Is Sela ruby?', options=[{'id':'false','description':'The proposition is false.'},
                                       {'id':'true','description':'The proposition is true.'}])
    score = copy.deepcopy(base); score.update(request_id='new-routed-score', kind='SCORE',
        prompt='Select an offered ordinal anchor.', minimum=0, maximum=2,
        options=[{'id':str(i),'description':word,'value':i} for i,word in enumerate(('low','middle','high'))])
    requests = [base, binary, score]; responses = [engine.decide(q) for q in requests]
    for q, response in zip(requests, responses):
        check(response, q['kind'], len(q['options']))
        need(command('-m', 'eidos.routed', '--registry', str(output/'model'),
                     payload=canonical_request(q)) == response, 'CLI_library_parity_failed')
    checks.extend(['all_three_typed_heads_review_only', 'actual_CLI_library_parity'])
    permuted = copy.deepcopy(base); permuted['options'].reverse()
    need(engine.decide(permuted) == responses[0], 'option_order_changed_output')
    checks.append('actual_option_order_independence')
    many = copy.deepcopy(base); many['options'] = [{'id':f'o{i}', 'description':f'additional answer {i}'} for i in range(255)]
    try:
        check(engine.decide(many), 'CHOICE', 255)
        many_status = 'all_255_scored'
    except ValueError as error:
        need(re.fullmatch(r'[a-z_]*(?:budget|limit|bound)[a-z_]*', str(error)) is not None,
             'unexpected_many_option_failure')
        many_status = 'explicit_model_budget_refusal'
    checks.append('all_255_options_or_explicit_budget_refusal_no_truncation')
    config_path = output/'fresh-config.json'; process = None
    try:
        initialized = command('-m', 'eidos.routed_service', '--init-local', '--config', str(config_path),
                              '--registry', str(output/'model'))
        need(not initialized['key_values_printed'] and config_path.stat().st_mode & 0o777 == 0o600,
             'private_configuration_failed')
        config = json.loads(config_path.read_bytes())
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable,'-S','-B','-m','eidos.routed_service',
            '--config',str(config_path),'--port',str(port)], cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic()+10
        while True:
            need(process.poll() is None, 'routed_service_exited')
            try:
                if call(port, 'GET', '/healthz')[0] == 200: break
            except OSError: pass
            need(time.monotonic() < deadline, 'service_start_deadline'); time.sleep(.05)
        checks.extend(['fresh_configuration_pins_routed_registry', 'actual_loopback_service_health'])
        need(call(port,'POST','/v2/decisions',canonical_request(base))[0] == 403, 'unsigned_request_accepted')
        for q, expected in zip(requests, responses):
            body = canonical_request(q); auth = signed(config,'POST','/v2/decisions',body)
            status, raw = call(port,'POST','/v2/decisions',body,auth)
            need(status == 200 and json.loads(raw) == expected, 'HTTP_library_parity_failed')
        checks.append('signed_HTTP_parity_three_heads_and_unsigned_refusal')
        need(call(port,'POST','/v2/decisions',body,auth)[0] == 403, 'signature_replay_accepted')
        checks.append('actual_HTTP_replay_refusal')
        status, raw = call(port,'GET','/v2/model',auth=signed(config,'GET','/v2/model',b''))
        meta=json.loads(raw)
        need(status==200 and meta['model']==engine.identity and meta['registry_sha256']==engine.registry_sha256
             and 'secret' not in meta, 'metadata_identity_or_privacy_failed')
        checks.append('authenticated_pinned_model_metadata')
        for path,marker in [('/',b'EIDOS'),('/decision.js',b'crypto.subtle'),('/style.css',b':root')]:
            status,raw=call(port,'GET',path);need(status==200 and marker in raw,'local_asset_unavailable')
        checks.append('local_browser_assets_served')
    finally:
        if process is not None:
            if process.poll() is None: process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
        config_path.unlink(missing_ok=True)
    result={'format':'eidos-routed-adapter-smoke-v1','complete':True,'checks':checks,'check_count':len(checks),
        'authored_training_rows':4,'fixed_epochs':1,'model':engine.identity,'registry_sha256':engine.registry_sha256,
        'training_diagnostics':receipt.get('diagnostics',{}),'many_option_status':many_status,
        'private_config_deleted':not config_path.exists(),'service_stopped':process is None or process.poll() is not None,
        'semantic_quality_scored':False,'real_benchmark_or_vendor_access':False,
        'operational_acceptance_enabled':False,'production_certified':False,'JEV_LAYA_parity_established':False}
    (output/'result.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True))


if __name__ == '__main__':
    main()
