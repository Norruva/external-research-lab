"""Exercise the source checkout using only a fresh authored demo and local state.

No benchmark/reference inference, existing configuration, external service,
dependency installation or browser automation. Temporary keys are never printed.
"""
import argparse
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import resource
import secrets
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
TENANT = 'owned-training-demo'
MODEL = 'eidos-owned-demo-v1'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(*args, payload=None):
    result = subprocess.run([sys.executable, '-S', '-B', *args], cwd=ROOT,
                            input=payload, capture_output=True, timeout=15)
    require(result.returncode == 0, 'Demo command failed: ' + ' '.join(args[:3]))
    require(len(result.stdout) <= 2_000_000, 'Oversized command response')
    return json.loads(result.stdout)


def request(port, method, path, body=b'', config=None):
    headers = {}
    if method == 'POST':
        headers['Content-Type'] = 'application/json'
    if config is not None:
        key_id = 'demo-operator'
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = '\n'.join((key_id, timestamp, nonce, method, path,
                             hashlib.sha256(body).hexdigest())).encode()
        mac = hmac.new(bytes.fromhex(config['keys'][key_id]['secret']), message, 'sha256').hexdigest()
        headers.update({'X-Eidos-Key': key_id, 'X-Eidos-Time': timestamp,
                        'X-Eidos-Nonce': nonce, 'X-Eidos-Signature': mac})
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read(2_000_001)
        require(len(payload) <= 2_000_000, 'Oversized HTTP response')
        return response.status, payload
    finally:
        connection.close()


def child_limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Match the existing worker's fixed 120-second CPU limit. A lower inherited
    # hard limit would prevent the worker from applying its own reviewed limit.
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))


def start(port, config, stderr):
    process = subprocess.Popen([sys.executable, '-S', '-B', '-m', 'eidos.service',
                                '--config', str(config), '--port', str(port)],
                               cwd=ROOT, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=stderr,
                               preexec_fn=child_limits)
    deadline = time.monotonic() + 12
    try:
        while time.monotonic() < deadline:
            require(process.poll() is None, 'Local demo service exited during startup')
            try:
                status, value = request(port, 'GET', '/healthz')
                if status == 200 and json.loads(value)['status'] == 'ok':
                    return process
            except OSError:
                pass
            time.sleep(0.05)
        raise RuntimeError('Local demo service startup timed out')
    except BaseException:
        stop(process)
        raise


def stop(process):
    if process is not None:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def check_decision(response):
    decision = response['decisions'][0]
    require(response['authorization'] == 'recommendation_only_no_action_authorized', 'Unexpected action authority')
    require(decision['status'] == 'abstained' and decision['value'] is None, 'New model accepted an operational action')
    require(decision['review_required'] and decision['calibrated_confidence'] is None, 'Missing review/uncalibrated policy')
    require(decision['model']['id'] == MODEL and len(decision['distribution']) == 2, 'Wrong demo model or class inventory')
    require(abs(sum(item['probability'] for item in decision['distribution']) - 1) < 1e-10, 'Invalid probability distribution')


def main():
    parser = argparse.ArgumentParser(description='Exercise a fresh authored-demo registry and loopback service')
    parser.add_argument('--trainer', choices=('memory', 'linear', 'adam'), default='memory')
    args = parser.parse_args()
    require(sys.version_info >= (3, 12) and os.name == 'posix', 'Python3.12+/POSIX required')
    require(sys.flags.no_site and sys.flags.dont_write_bytecode, 'Run python3 -S -B tools/smoke.py')
    started = time.monotonic()
    checks = []
    provenance = json.loads((ROOT / 'SOURCE-PROVENANCE.json').read_bytes())
    copied = provenance['copied_files']
    for name, pin in copied.items():
        require(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == pin['sha256'], 'Imported source identity changed: ' + name)
    checks.append('imported source hashes verified')
    with tempfile.TemporaryDirectory(prefix='eidos-source-smoke-') as temporary:
        private = Path(temporary)
        registry = private / 'registry'
        registry.mkdir(mode=0o700)
        (registry / 'models').mkdir(mode=0o700)
        model = registry / 'models/owned-demo'
        trainer_module = 'eidos.train_support' if args.trainer == 'memory' else 'eidos.train_linear_support'
        optimizer_args = ('--optimizer', 'adam') if args.trainer == 'adam' else ()
        command('-m', trainer_module, '--input', 'examples/owned-support-demo.jsonl',
                '--provenance', 'examples/owned-support-demo-provenance.json',
                '--output', str(model), '--model-id', MODEL, *optimizer_args)
        require(model.stat().st_mode & 0o777 == 0o700, 'Model directory is not private')
        require(len(list(model.iterdir())) == 4 and all(p.stat().st_mode & 0o777 == 0o400 for p in model.iterdir()), 'Model artifacts are not private')
        checks.append('actual training CLI and private artifact publication')
        payload = (ROOT / 'examples/owned-support-request.json').read_bytes()
        response = command('-m', 'eidos', '--registry', str(model), '--tenant', TENANT, payload=payload)
        check_decision(response)
        checks.append('actual Engine CLI, two-class distribution and review policy')
        if args.trainer == 'memory':
            explanation = command('-m', 'eidos.explain_support', '--registry', str(model), '--tenant', TENANT, payload=payload)
            check_decision(explanation['engine_response'])
            require(explanation['review_required'] and not explanation['action_authorized'], 'Explanation grants an action')
            require(all(len(c['neighbors']) == 5 and all(set(n) == {'support_id', 'cosine'} for n in c['neighbors'])
                        for c in explanation['evidence'][0]['classes']), 'Explanation exposes unexpected support fields')
            checks.append('actual opaque-support explanation CLI')
        config_path = private / 'config.json'
        command('-m', 'eidos.manage', 'init-local', '--config', str(config_path),
                '--state-dir', str(private / 'state'), '--registry-root', str(registry),
                '--release-path', 'models/owned-demo', '--tenant', TENANT)
        require(config_path.stat().st_mode & 0o777 == 0o600, 'Configuration is not private')
        config = json.loads(config_path.read_bytes())  # Only this freshly generated configuration.
        checks.append('fresh local configuration without printing key values')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        process = None
        with (private / 'service-stderr.log').open('wb') as stderr:
            try:
                process = start(port, config_path, stderr)
                checks.append('real loopback service health')
                for route, asset in [('/', 'index.html'), ('/eidos.js', 'eidos.js'),
                                     ('/transport.js', 'transport.js'), ('/ui.js', 'ui.js'), ('/style.css', 'style.css')]:
                    status, body = request(port, 'GET', route)
                    require(status == 200 and body == (ROOT / 'web' / asset).read_bytes(), 'Browser asset serving failed')
                checks.append('five exact browser assets served; no browser automation claim')
                status, body = request(port, 'GET', '/v1/models')
                require(status == 403 and json.loads(body)['error'] == 'authentication_required', 'Unsigned model inventory was not refused')
                checks.append('unsigned API access refused')
                status, body = request(port, 'GET', '/v1/models', config=config)
                inventory = json.loads(body)
                require(status == 200 and len(inventory['profiles']) == 1 and len(inventory['profiles'][0]['labels']) == 2, 'Wrong signed inventory')
                checks.append('signed API inventory from newly trained registry')
                # Readiness may warm in a background worker; only retry explicit 503.
                deadline = time.monotonic() + 12
                while True:
                    status, body = request(port, 'POST', '/v1/decisions', payload, config)
                    if status != 503 or time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
                require(status == 200, 'Signed decision failed')
                decision = json.loads(body)
                check_decision(decision)
                checks.append('signed real-service decision preserves review policy')
                status, repeated = request(port, 'POST', '/v1/decisions', payload, config)
                require(status == 200 and json.loads(repeated) == decision, 'Idempotent response changed')
                checks.append('identical request returns identical cached response')
                status, body = request(port, 'POST', '/v1/decisions', payload + b' ', config)
                require(status == 409 and json.loads(body)['error'] == 'idempotency_conflict', 'Conflicting body reused an idempotency key')
                checks.append('idempotency conflict refused')
                review = json.dumps({'request_id': decision['request_id'], 'action': 'accepted'}).encode()
                status, body = request(port, 'POST', '/v1/reviews', review, config)
                reviewed = json.loads(body)
                require(status == 200 and reviewed['review'] == 'accepted' and not reviewed['action_executed']
                        and not reviewed['training_data_created'], 'Review executed an action or trained a model')
                checks.append('review records approval without action or training')
                status, body = request(port, 'GET', '/v1/reviews', config=config)
                history = json.loads(body)
                require(status == 200 and not history['raw_input_retained'] and len(history['items']) == 1
                        and history['items'][0]['review'] == 'accepted', 'Review history persistence failed')
                checks.append('review history without raw input retention')
                stop(process)
                process = start(port, config_path, stderr)
                status, body = request(port, 'POST', '/v1/decisions', payload, config)
                require(status == 200 and json.loads(body) == decision, 'Service restart lost cached response')
                checks.append('restart preserves exact cached decision')
            finally:
                stop(process)
        maintenance = command('-m', 'eidos.manage', 'maintain', '--config', str(config_path))
        # One new decision and one review are audited. Authentication nonces,
        # reads, cached decisions and rejected conflicts do not add events.
        require(maintenance['audit']['events'] == 2, 'Audit chain did not record exactly the decision and review')
        checks.append('stopped-service audit-chain maintenance')
    for name, pin in copied.items():
        require(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == pin['sha256'], 'Imported source changed during smoke test')
    checks.append('source unchanged; temporary configuration and state removed')
    print(json.dumps({'complete': True, 'trainer': args.trainer, 'checks': checks, 'check_count': len(checks),
                      'not_applicable': ['support-memory explanation for linear profiles'] if args.trainer == 'linear' else [],
                      'elapsed_seconds': time.monotonic() - started,
                      'fresh_authored_demo_only': True, 'reference_or_quality_evaluation': False,
                      'headless_browser_or_backup_restore_repeated': False,
                      'preexisting_configuration_accessed': False,
                      'service_stopped': True, 'temporary_state_removed': True}, indent=2))


if __name__ == '__main__':
    main()
