"""Real Chromium UI mechanics for an explicitly supplied owned decision registry.

No fitting, vendor model/service, benchmark data or semantic-quality assertion.
Fresh operator configuration and browser profile are private and removed. Only
aggregate check names, input identities and resource-independent timing survive.
Run inside an isolated offline development environment with existing Chromium.
"""
import argparse
import base64
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = Path('/usr/lib/chromium/chromium')
MAX_CDP = 2_000_000


class BrowserSmokeError(RuntimeError):
    pass


def need(condition, code):
    if not condition:
        raise BrowserSmokeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class CDP:
    """Original stdlib local WebSocket transport, from EIDOS's owned UI control."""
    def __init__(self, url):
        parts = urlsplit(url)
        need(parts.scheme == 'ws' and parts.hostname == '127.0.0.1' and parts.port
             and not parts.username and not parts.password and not parts.query
             and not parts.fragment, 'CDP_loopback_required')
        self.socket = socket.create_connection(('127.0.0.1', parts.port), timeout=15)
        self.id = 0
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        request = (f'GET {parts.path} HTTP/1.1\r\nHost: 127.0.0.1:{parts.port}\r\n'
                   'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                   f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.socket.sendall(request.encode())
        response = bytearray()
        while not response.endswith(b'\r\n\r\n'):
            response.extend(self.exact(1))
            need(len(response) < 4096, 'CDP_handshake_bound')
        expected = base64.b64encode(hashlib.sha1(
            (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        need(response.startswith(b'HTTP/1.1 101') and expected in response,
             'CDP_handshake_refused')

    def exact(self, count):
        result = bytearray()
        while len(result) < count:
            block = self.socket.recv(count - len(result))
            need(bool(block), 'CDP_connection_stopped')
            result.extend(block)
        return bytes(result)

    def send_frame(self, data, opcode=1):
        need(len(data) <= MAX_CDP, 'CDP_send_bound')
        mask = secrets.token_bytes(4)
        if len(data) < 126:
            header = bytes((0x80 | opcode, 0x80 | len(data)))
        elif len(data) < 65536:
            header = bytes((0x80 | opcode, 0xfe)) + struct.pack('!H', len(data))
        else:
            header = bytes((0x80 | opcode, 0xff)) + struct.pack('!Q', len(data))
        self.socket.sendall(header + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(data)))

    def read(self):
        while True:
            first, second = self.exact(2)
            need(first & 0x80 and not first & 0x70 and not second & 0x80,
                 'CDP_frame_refused')
            count = second & 127
            if count == 126:
                count = struct.unpack('!H', self.exact(2))[0]
            elif count == 127:
                count = struct.unpack('!Q', self.exact(8))[0]
            need(count <= MAX_CDP, 'CDP_receive_bound')
            data = self.exact(count)
            opcode = first & 15
            if opcode == 9:
                need(len(data) <= 125, 'CDP_control_frame_bound')
                self.send_frame(data, opcode=10)
                continue
            need(opcode == 1, 'CDP_message_refused')
            value = json.loads(data)
            need(type(value) is dict, 'CDP_object_required')
            return value

    def call(self, method, params=None):
        self.id += 1
        self.send_frame(json.dumps({'id': self.id, 'method': method,
                                    'params': params or {}}, separators=(',', ':')).encode())
        while True:
            result = self.read()
            if result.get('id') == self.id:
                # No exception or protocol body is echoed: expressions can carry
                # the one newly generated operator secret in process memory.
                need('error' not in result and type(result.get('result')) is dict,
                     'CDP_command_refused')
                return result['result']

    def close(self):
        self.socket.close()


def clear_environment(**extra):
    return {'PATH': '/usr/bin:/bin', 'HOME': '/home/research', 'LC_ALL': 'C.UTF-8', **extra}


def stop(process, *, interrupt=False):
    if process is None:
        return True
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGINT if interrupt else signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
    return process.poll() is not None


def launch_service(config):
    process = subprocess.Popen([sys.executable, '-S', '-B', '-m', 'eidos.decision_service',
                                '--config', str(config), '--port', '0'], cwd=ROOT,
                               env=clear_environment(), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            need(selector.select(timeout=15), 'service_start_timeout')
            raw = process.stdout.readline(4097)
        need(0 < len(raw) <= 4096 and raw.endswith(b'\n'), 'service_ready_bound')
        value = json.loads(raw)
        need(value.get('service') == 'eidos-decision-service-v2' and
             value.get('bind') == '127.0.0.1' and type(value.get('port')) is int and
             0 < value['port'] <= 65535, 'service_ready_contract')
        return process, value['port']
    except BaseException:
        stop(process, interrupt=True)
        raise


def chrome_target(profile, browser):
    deadline = time.monotonic() + 15
    marker = profile / 'DevToolsActivePort'
    while not marker.is_file():
        need(time.monotonic() < deadline and browser.poll() is None, 'Chromium_start_timeout')
        time.sleep(.05)
    raw = marker.read_text()
    need(len(raw) < 4096, 'Chromium_port_bound')
    port = int(raw.splitlines()[0])
    need(0 < port <= 65535, 'Chromium_port_refused')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET', '/json/list')
        response = connection.getresponse()
        raw = response.read(1_000_001)
        need(response.status == 200 and len(raw) <= 1_000_000, 'Chromium_target_bound')
        targets = json.loads(raw)
    finally:
        connection.close()
    need(type(targets) is list and 1 <= len(targets) <= 20, 'Chromium_targets_refused')
    pages = [row for row in targets if row.get('type') == 'page']
    need(len(pages) == 1, 'Chromium_page_required')
    return pages[0]['webSocketDebuggerUrl']


def read_authored_tasks():
    path = ROOT / 'examples/decision-owned-demo/smoke.jsonl'
    raw = path.read_bytes()
    need(0 < len(raw) <= 256_000 and not path.is_symlink(), 'authored_fixture_bound')
    rows = [json.loads(line) for line in raw.splitlines()]
    selected = {}
    for row in rows:
        request = row['request']
        if request['kind'] in {'CHOICE', 'BINARY', 'SCORE'}:
            selected.setdefault(request['kind'], request)
    need(set(selected) == {'CHOICE', 'BINARY', 'SCORE'}, 'authored_typed_cases_required')
    return [selected[kind] for kind in ('CHOICE', 'BINARY', 'SCORE')], sha(raw)


SCRIPT = r'''(async () => {
  const checks=[];
  let phase='initial_ui';
  const el=id=>document.getElementById(id);
  const need=(value)=>{if(!value)throw Error('check_failed');};
  const wait=async fn=>{
    for(let i=0;i<240;i++){
      if(fn())return;
      await new Promise(resolve=>setTimeout(resolve,50));
    }
    throw Error('UI_timeout');
  };
  const parsed=()=>{try{return JSON.parse(el('result').textContent);}catch{return null;}};
  const verify=(request,result)=>{
    need(result.format==='eidos-decision-response-v2'&&result.kind===request.kind);
    need(result.request_id===request.request_id&&result.status==='review_required');
    need(result.value===null&&result.review_required===true&&result.calibrated_confidence===null);
    need(result.probability_semantics==='uncalibrated_conditional_on_offered_options');
    need(result.authorization==='recommendation_only_no_action_authorized');
    const rows=result.distribution;
    need(Array.isArray(rows)&&rows.length===request.options.length);
    need(new Set(rows.map(r=>r.id)).size===request.options.length);
    need(request.options.every(o=>rows.some(r=>r.id===o.id)));
    let sum=0;
    for(const row of rows){need(Number.isFinite(row.probability)&&row.probability>=0&&row.probability<=1);sum+=row.probability;}
    need(Math.abs(sum-1)<1e-10);
    if(request.kind==='CHOICE')need(rows.some(r=>r.id===result.candidate_value));
    if(request.kind==='BINARY'){
      need(typeof result.candidate_value==='boolean');
      need(Number.isFinite(result.probability_true));
      need(Math.abs(result.probability_true-rows.find(r=>r.id==='true').probability)<1e-12);
    }
    if(request.kind==='SCORE'){
      need(Number.isFinite(result.candidate_value)&&result.candidate_value>=request.minimum&&result.candidate_value<=request.maximum);
      const mean=rows.reduce((total,row)=>total+row.probability*row.value,0);
      need(Math.abs(result.candidate_value-mean)<1e-10);
    }
  };
  const submit=async request=>{
    el('request').value=JSON.stringify(request);
    el('result').textContent='Waiting for browser smoke result';
    el('decide').click();
    await wait(()=>{const result=parsed();return result&&result.request_id===request.request_id;});
    const result=parsed();verify(request,result);
    await wait(()=>!el('decide').disabled);
    return result;
  };
  try {
    need(el('connect')&&el('disconnect')&&el('request')&&el('decide'));
    need(el('decide').disabled&&el('operator-key').value==='');
    need(document.querySelector('.notice').textContent.includes('uncalibrated'));
    need(typeof crypto.subtle.importKey==='function');
    checks.push('real_browser_loaded_original_ui_and_WebCrypto');
    phase='connect';
    el('key-id').value=KEY_ID;
    el('operator-key').value=OPERATOR_SECRET;
    el('connect').click();
    await wait(()=>!el('decide').disabled&&el('session-status').textContent.includes('Connected'));
    const model=parsed();
    need(model&&model.format==='eidos-decision-model-response-v2'&&model.review_required===true);
    need(model.registry_sha256===REGISTRY_SHA);
    need(el('operator-key').value==='');
    need(!el('result').textContent.includes(OPERATOR_SECRET));
    checks.push('browser_WebCrypto_HMAC_authenticated_model_identity');
    checks.push('operator_key_input_cleared_after_connect');
    for(const original of AUTHORED_TASKS){
      const request=JSON.parse(JSON.stringify(original));
      request.request_id='original-browser-'+request.kind.toLowerCase();
      phase='typed_'+request.kind.toLowerCase();
      await submit(request);
      checks.push('real_UI_'+request.kind+'_finite_normalized_typed_review_only');
    }
    phase='many_options';
    const many=JSON.parse(JSON.stringify(AUTHORED_TASKS[0]));
    many.request_id='original-browser-255-mechanics';
    many.options=Array.from({length:255},(_,i)=>({id:'opaque-'+String(i).padStart(3,'0'),description:'authored alternative '+i}));
    const large=await submit(many);
    need(large.distribution.length===255&&el('result').textContent.includes('opaque-254'));
    checks.push('all255_options_scored_and_displayed_mechanics_only');
    phase='duplicate_json';
    const valid=JSON.stringify(AUTHORED_TASKS[0]);
    el('request').value='{"format":"duplicate-private-placeholder",'+valid.slice(1);
    el('result').textContent='Waiting for duplicate refusal';
    el('decide').click();
    await wait(()=>el('result').textContent.includes('HTTP 400'));
    need(!el('result').textContent.includes('duplicate-private-placeholder'));
    need(!el('result').textContent.includes(OPERATOR_SECRET));
    checks.push('raw_duplicate_JSON_reaches_server_and_is_redacted_refused');
    phase='storage';
    need(localStorage.length===0&&sessionStorage.length===0);
    checks.push('no_browser_localStorage_or_sessionStorage');
    phase='disconnect';
    el('disconnect').click();
    need(el('decide').disabled&&el('operator-key').value==='');
    need(el('session-status').textContent==='Disconnected');
    need(el('result').textContent==='Disconnected');
    checks.push('disconnect_clears_session_input_and_disables_requests');
    return {ok:true,checks,typed_cases:3,maximum_options:255,semantic_correctness_asserted:false};
  }catch{
    try{el('disconnect').click();}catch{}
    return {ok:false,checks,failed_phase:phase,semantic_correctness_asserted:false};
  }
})()'''


def main():
    parser = argparse.ArgumentParser(description='Real Chromium mechanics on an explicit owned decision registry')
    parser.add_argument('--registry', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).absolute()
    need(not any(p.is_symlink() for p in (output, *output.parents)), 'output_symlink_refused')
    output.mkdir(mode=0o700, exist_ok=False)
    started = time.monotonic()
    service = browser = cdp = None
    complete = False
    phase = 'preflight'
    checks = []
    failure = None
    model_pin = fixture_pin = None
    cleanup = {'service_stopped': True, 'browser_stopped': True, 'private_state_removed': False}
    try:
        need(sys.version_info >= (3, 12) and os.name == 'posix' and
             sys.flags.no_site and sys.flags.dont_write_bytecode, 'isolated_Python_required')
        need(CHROMIUM.is_file(), 'existing_Chromium_required')
        registry = Path(args.registry).absolute()
        need(not any(p.is_symlink() for p in (registry, *registry.parents)), 'registry_symlink_refused')
        tasks, fixture_pin = read_authored_tasks()
        with tempfile.TemporaryDirectory(prefix='eidos-decision-browser-', dir='/tmp') as temporary:
            private = Path(temporary)
            config = private / 'fresh-config.json'
            phase = 'fresh_configuration'
            initialized = subprocess.run([sys.executable, '-S', '-B', '-m', 'eidos.decision_service',
                '--config', str(config), '--registry', str(registry), '--init-local'], cwd=ROOT,
                env=clear_environment(), stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
            need(initialized.returncode == 0 and len(initialized.stdout) <= 4096,
                 'fresh_configuration_refused')
            status = config.stat()
            need(stat.S_ISREG(status.st_mode) and status.st_mode & 0o777 == 0o600,
                 'fresh_configuration_not_private')
            settings = json.loads(config.read_bytes())
            model_pin = settings['registry_sha256']
            need(re.fullmatch(r'[0-9a-f]{64}', model_pin) and
                 re.fullmatch(r'[0-9a-f]{64}', settings['secret']), 'fresh_configuration_contract')
            checks.append('new_private_configuration_only_no_existing_keys')
            phase = 'service_start'
            service, service_port = launch_service(config)
            checks.append('explicit_registry_signed_service_started_on_loopback_dynamic_port')
            profile = private / 'profile'
            profile.mkdir(mode=0o700)
            fonts = private / 'fonts.conf'
            fonts.write_text('<?xml version="1.0"?><fontconfig><dir>/usr/share/fonts</dir>'
                             '<cachedir>' + str(private / 'fontcache') + '</cachedir></fontconfig>')
            phase = 'Chromium_start'
            browser = subprocess.Popen([str(CHROMIUM), '--headless', '--no-sandbox', '--disable-gpu',
                '--no-first-run', '--disable-dev-shm-usage', '--disable-extensions',
                '--disable-background-networking', '--disable-sync', '--disable-component-update',
                '--disable-default-apps', '--disable-breakpad', '--password-store=basic',
                '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                '--user-data-dir=' + str(profile), 'about:blank'],
                env=clear_environment(FONTCONFIG_FILE=str(fonts)), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                cdp = CDP(chrome_target(profile, browser))
                phase = 'browser_navigation'
                cdp.call('Page.enable')
                cdp.call('Page.navigate', {'url': f'http://127.0.0.1:{service_port}/'})
                deadline = time.monotonic() + 10
                while True:
                    result = cdp.call('Runtime.evaluate', {'expression':
                        "document.readyState==='complete'&&document.getElementById('connect')!==null",
                        'returnByValue': True})
                    if result.get('result', {}).get('value') is True:
                        break
                    need(time.monotonic() < deadline, 'browser_navigation_timeout')
                    time.sleep(.05)
                script = SCRIPT.replace('KEY_ID', json.dumps(settings['key_id']))
                script = script.replace('OPERATOR_SECRET', json.dumps(settings['secret']))
                script = script.replace('REGISTRY_SHA', json.dumps(model_pin))
                script = script.replace('AUTHORED_TASKS', json.dumps(tasks, ensure_ascii=True))
                phase = 'browser_checks'
                result = cdp.call('Runtime.evaluate', {'expression': script, 'awaitPromise': True,
                                                       'returnByValue': True, 'timeout': 85000})
                # Never persist raw CDP responses or exception details.
                value = result.get('result', {}).get('value')
                need(type(value) is dict and type(value.get('checks')) is list,
                     'browser_result_contract')
                allowed_check = re.compile(r'[A-Za-z0-9_]{1,100}\Z')
                need(all(type(name) is str and allowed_check.fullmatch(name) for name in value['checks']),
                     'browser_check_names_refused')
                checks.extend(value['checks'])
                if value.get('ok') is not True:
                    failed = value.get('failed_phase', 'unknown')
                    phase = failed if type(failed) is str and allowed_check.fullmatch(failed) else 'browser_checks'
                    raise BrowserSmokeError('browser_check_failed')
                need(value['typed_cases'] == 3 and value['maximum_options'] == 255 and
                     value['semantic_correctness_asserted'] is False, 'browser_scope_contract')
                phase = 'reload'
                cdp.call('Page.reload', {'ignoreCache': True})
                deadline = time.monotonic() + 10
                while True:
                    result = cdp.call('Runtime.evaluate', {'expression':
                        "document.readyState==='complete'&&document.getElementById('decide')!==null&&"
                        "document.getElementById('decide').disabled&&document.getElementById('operator-key').value===''&&"
                        "document.getElementById('session-status').textContent==='Disconnected'&&"
                        "localStorage.length===0&&sessionStorage.length===0", 'returnByValue': True})
                    if result.get('result', {}).get('value') is True:
                        break
                    need(time.monotonic() < deadline, 'browser_reload_timeout')
                    time.sleep(.05)
                checks.append('page_reload_has_no_operator_session_or_persistent_browser_storage')
                complete = True
            finally:
                if cdp is not None:
                    cdp.close()
                    cdp = None
                cleanup['browser_stopped'] = stop(browser)
                browser = None
                cleanup['service_stopped'] = stop(service, interrupt=True)
                service = None
                settings.clear()
        cleanup['private_state_removed'] = not private.exists()
    except Exception as error:
        failure = type(error).__name__
    finally:
        if cdp is not None:
            cdp.close()
        cleanup['browser_stopped'] = stop(browser) and cleanup['browser_stopped']
        cleanup['service_stopped'] = stop(service, interrupt=True) and cleanup['service_stopped']
    complete = complete and all(cleanup.values())
    receipt = {'format': 'eidos-decision-real-browser-smoke-v1', 'complete': complete,
        'phase': 'complete' if complete else phase, 'failure_category': failure,
        'checks': checks, 'checks_passed': len(checks), 'elapsed_seconds': time.monotonic() - started,
        'source_sha256': sha(Path(__file__).read_bytes()), 'registry_sha256': model_pin,
        'authored_fixture_sha256': fixture_pin, 'existing_Chromium_path': str(CHROMIUM),
        'real_Chromium_WebCrypto_signed_HTTP_exercised': complete,
        'semantic_correctness_or_independent_quality_asserted': False,
        'fit_installation_download_vendor_service_or_existing_credentials_accessed': False,
        'new_private_operator_key_or_raw_request_or_CDP_payload_recorded': False,
        'automatic_acceptance_or_action_authority': False, 'cleanup': cleanup,
        'resource_note': 'External per-process limits required; no aggregate cgroup or browser address-space guarantee.'}
    fd = os.open(output / 'result.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'complete': complete, 'checks_passed': len(checks),
                      'phase': receipt['phase'], 'output': str(output / 'result.json'),
                      'semantic_quality_asserted': False}))
    return 0 if complete else 2


if __name__ == '__main__':
    raise SystemExit(main())
