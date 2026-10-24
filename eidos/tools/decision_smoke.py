"""Exercise original authored decision training, CLI and signed loopback API.

Visible authored tasks establish integration only, never independent semantic
quality. Keys/configuration are generated here and deleted without being printed.
The output retains this run's owned mechanical model and aggregate receipts.
"""
import argparse
import copy
import hashlib
import hmac
import http.client
import json
import math
import os
import re
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eidos.decision import DecisionEngine
from eidos.decision_contract import canonical_request
from eidos.decision_epistemic import assess_forecast


def need(value, reason):
    if not value:
        raise RuntimeError(reason)


def run(*args, payload=None, timeout=30):
    result = subprocess.run([sys.executable, '-S', '-B', *args], cwd=ROOT,
                            input=payload, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        reason = 'redacted_failure'
        try:
            candidate = json.loads(result.stderr).get('reason')
            if type(candidate) is str and re.fullmatch(r'[a-z0-9_]{1,100}', candidate):
                reason = candidate
        except (ValueError, AttributeError, TypeError):
            pass
        raise RuntimeError('authored_command_failed_' + str(args[:2]) + '_' + reason)
    need(len(result.stdout) <= 2_000_000, 'command_response_bound')
    return json.loads(result.stdout)


def headers(key, key_id, method, path, body):
    timestamp = str(int(time.time())); nonce = secrets.token_hex(16)
    message = '\n'.join((key_id, timestamp, nonce, method, path,
                           hashlib.sha256(body).hexdigest())).encode()
    mac = hmac.new(bytes.fromhex(key), message, 'sha256').hexdigest()
    return {'X-Eidos-Key': key_id, 'X-Eidos-Time': timestamp, 'X-Eidos-Nonce': nonce,
            'X-Eidos-Signature': mac, 'Content-Type': 'application/json'}


def call(port, method, path, body=b'', signed=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        request_headers = {'Content-Type': 'application/json'} if method == 'POST' else {}
        request_headers.update(signed or {})
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse(); raw = response.read(2_000_001)
        need(len(raw) <= 2_000_000, 'http_response_bound')
        return response.status, raw
    finally:
        connection.close()


def check(result, kind, count):
    need(result['format'] == 'eidos-decision-response-v2' and result['kind'] == kind,
         'wrong_response_contract')
    need(result['status'] == 'review_required' and result['value'] is None and
         result['review_required'] and result['calibrated_confidence'] is None,
         'unsafe_acceptance_or_calibration_claim')
    need(result['authorization'] == 'recommendation_only_no_action_authorized', 'unexpected_authority')
    p = result['distribution']
    need(len(p) == count and abs(math.fsum(r['probability'] for r in p) - 1) < 1e-12,
         'complete_normalized_choices_required')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True); args = parser.parse_args()
    output = Path(args.output).absolute(); output.mkdir(mode=0o700, exist_ok=False)
    fixtures = ROOT/'examples/decision-owned-demo'
    checked = []
    trained = run('-m', 'eidos.train_decision', '--input', str(fixtures/'training.jsonl'),
                  '--provenance', str(fixtures/'provenance.json'), '--output', str(output/'model'),
                  '--model-id', 'owned-question-decision-demo-v1', '--epochs', '10', '--seed', '113', timeout=700)
    checked.append('actual_from_scratch_training_CLI_fixed_recipe')
    receipt = json.loads((output/'model/TRAINING-RECEIPT.json').read_bytes())
    # Authoring examples are visible; these numbers are training diagnostics.
    diagnostic = receipt.get('diagnostics', receipt.get('training_diagnostics', {}))
    engine = DecisionEngine.from_registry(output/'model')
    checked.append('checked_private_artifact_loaded_by_real_engine')
    cases = [json.loads(line) for line in (fixtures/'smoke.jsonl').read_text().splitlines()]
    rows = []
    exact_choices, total_choices, score_absolute = 0, 0, []
    for case in cases:
        result = engine.decide(case['request']); check(result, case['request']['kind'], len(case['request']['options']))
        rows.append(result)
        target = case['target_option_id']
        if case['request']['kind'] == 'SCORE':
            gold = next(o['value'] for o in case['request']['options'] if o['id'] == target)
            score_absolute.append(abs(result['candidate_value'] - gold))
        else:
            target_value = target == 'true' if case['request']['kind'] == 'BINARY' else target
            total_choices += 1; exact_choices += int(result['candidate_value'] == target_value)
    need({row['kind'] for row in rows} == {'CHOICE', 'BINARY', 'SCORE'}, 'all_typed_heads_required')
    checked.append('actual_choice_binary_and_numeric_inference_review_only')
    assessment = assess_forecast(rows[0], evidence_positive=True, evidence_negative=True)
    need(assessment['four_valued_evidence']['state'] == 'both' and
         assessment['review_required'] and not assessment['action_authority'], 'evidence_assessment_changed_authority')
    checked.append('actual_forecast_evidence_assessment_preserves_contradiction_and_review')
    first = cases[0]['request']; response = rows[0]
    cli = run('-m', 'eidos.decision', '--registry', str(output/'model'), payload=canonical_request(first))
    need(cli == response, 'CLI_library_parity')
    checked.append('actual_CLI_library_exact_parity')
    permuted = copy.deepcopy(first); permuted['options'].reverse()
    need(engine.decide(permuted) == response, 'option_order_changed_mapped_result')
    renamed = copy.deepcopy(first)
    for i, option in enumerate(renamed['options']): option['id'] = 'opaqueRenamed' + str(i)
    changed = engine.decide(renamed)
    by_meaning = {o['description']: o['id'] for o in renamed['options']}
    original_meaning = {o['id']: o['description'] for o in first['options']}
    a = {by_meaning[original_meaning[r['id']]]: r['probability'] for r in response['distribution']}
    b = {r['id']: r['probability'] for r in changed['distribution']}
    need(a == b, 'opaque_ID_changed_meaning_probability')
    checked.append('actual_trained_option_order_and_ID_independence')
    many = copy.deepcopy(first)
    many['options'] = [{'id': 'option' + str(i), 'description': 'authored alternative ' + str(i)} for i in range(255)]
    large = engine.decide(many); check(large, 'CHOICE', 255)
    checked.append('actual_255_option_complete_scoring_mechanics_not_quality')
    private_config = output/'fresh-service-config.json'
    started = time.monotonic(); process = None
    try:
        run('-m', 'eidos.decision_service', '--init-local', '--config', str(private_config),
            '--registry', str(output/'model'))
        need(private_config.stat().st_mode & 0o777 == 0o600, 'config_not_private')
        config = json.loads(private_config.read_bytes())
        with socket.socket() as port_probe:
            port_probe.bind(('127.0.0.1', 0)); port = port_probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-S', '-B', '-m', 'eidos.decision_service',
                     '--config', str(private_config), '--port', str(port)], cwd=ROOT,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while True:
            need(process.poll() is None, 'service_exited')
            try:
                status, body = call(port, 'GET', '/healthz')
                if status == 200: break
            except OSError: pass
            need(time.monotonic() < deadline, 'service_start_timeout')
            time.sleep(.05)
        checked.append('actual_loopback_service_start_and_health')
        status, _ = call(port, 'POST', '/v2/decisions', canonical_request(first))
        need(status == 403, 'unauthenticated_decision_accepted')
        body = canonical_request(first); signed = headers(config['secret'], config['key_id'], 'POST', '/v2/decisions', body)
        status, raw = call(port, 'POST', '/v2/decisions', body, signed)
        need(status == 200 and json.loads(raw) == response, 'signed_HTTP_library_parity')
        checked.append('signed_HTTP_exact_parity_and_unauthenticated_refusal')
        status, _ = call(port, 'POST', '/v2/decisions', body, signed)
        need(status in (403, 409), 'replayed_signature_accepted')
        checked.append('actual_HTTP_signature_replay_refused')
        status, raw = call(port, 'GET', '/v2/model', signed=headers(config['secret'],config['key_id'],'GET','/v2/model',b''))
        need(status == 200 and b'"secret"' not in raw, 'model_endpoint_or_secret_disclosure')
        checked.append('authenticated_model_metadata_without_key_disclosure')
        for path, marker in (('/', b'EIDOS'), ('/decision.js', b'crypto.subtle'), ('/style.css', b':root')):
            status, raw = call(port, 'GET', path)
            need(status == 200 and marker in raw, 'local_browser_asset_unavailable')
        checked.append('real_local_browser_assets_served_no_CDN_or_external_model')
    finally:
        if process is not None:
            if process.poll() is None: process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        if private_config.exists(): private_config.unlink()
    result = {'format': 'eidos-owned-decision-authored-smoke-v1', 'complete': True,
              'checks': checked, 'check_count': len(checked), 'authored_training_rows': 144, 'authored_fixed_epochs': 10,
              'epoch_count_selected_by_pre_fit_work_budget_not_quality': True,
              'authored_visible_smoke_rows': len(cases), 'training_diagnostics': diagnostic,
              'visible_mechanical_fixture_observation': {'forced_choice_and_binary_correct':exact_choices,
                  'forced_choice_and_binary_total':total_choices,'ordinal_absolute_errors':score_absolute,
                  'independent_semantic_quality_or_LAYA_JEV_estimate':False},
              'model': engine.identity, 'registry_sha256': engine.registry_sha256,
              'operational_acceptance_enabled': False,'new_private_config_deleted':True,
              'loopback_service_stopped': True, 'no_existing_credentials_or_external_service': True,
              'production_certified': False, 'JEV_or_general_LAYA_parity_established': False,
              'service_phase_elapsed_seconds': time.monotonic() - started}
    (output/'result.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True))


if __name__ == '__main__':
    main()
