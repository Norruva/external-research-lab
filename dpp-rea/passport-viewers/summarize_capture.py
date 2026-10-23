"""Summarize one capture_live.sh output directory into a compact, committable JSON receipt.

Usage: python3 summarize_capture.py <capture-dir> <label> [screenshot.png] > receipt.json

Keeps request methods, URLs without query values, resource types and statuses, console/page-error counts,
final URL, visible accessibility text (for field-coverage review) and REA evidence identities.
Writes the final screenshot to the optional PNG path instead of the receipt; drops header values
and bodies.
"""
import base64
import collections
import json
import re
import sys
from pathlib import Path


def last_record(path):
    records = [json.loads(line) for line in path.read_text().splitlines()
               if line.strip() and 'rea_progress' not in line]
    return records[-1] if records else {}


def artifact(step, name):
    value = step.get('artifacts', {}).get(name, {})
    return value.get('value') if value.get('state') == 'captured' else None


def main():
    directory, label = Path(sys.argv[1]), sys.argv[2]
    scenario = last_record(directory / 'scenario-evidence.json')
    capture = scenario.get('normalized_result') or {}
    events = capture.get('events', {}).get('items', [])
    responses = {}
    for event in events:
        if event.get('kind') in ('response', 'request-failed'):
            responses[event['transaction_id']] = event.get('status') or event.get('failure')
    requests = []
    for event in events:
        if event.get('kind') != 'request':
            continue
        url = event['url']
        requests.append({'method': event['method'], 'type': event.get('resource_type'),
                         'url': (url.get('url') or '').split('?')[0], 'origin': url.get('origin'),
                         'query_parameter_names': url.get('query_parameter_names', []),
                         'status': responses.get(event['transaction_id'])})
    kinds = collections.Counter(event.get('kind') for event in events)
    steps = capture.get('steps', [])
    final = steps[-1] if steps else {}
    final_url = artifact(final, 'url') or {}
    tree = artifact(final, 'accessibility') or {}
    screenshot = artifact(final, 'screenshot')
    if screenshot and len(sys.argv) > 3:
        Path(sys.argv[3]).write_bytes(base64.b64decode(screenshot['data_base64']))
    bundle = last_record(directory / 'bundle.json')
    bundle_result = bundle.get('normalized_result') or {}
    receipt = {
        'label': label,
        'start_url': json.loads((directory / 'scenario.json').read_text())['start_url']['url'],
        'final_url': final_url.get('url'),
        'scenario_evidence_id': scenario.get('evidence_id'),
        'scenario_error': scenario.get('code'),
        'bundle_evidence_id': bundle.get('evidence_id'),
        'bundle_error': bundle.get('code'),
        'bundle_capture': {k: bundle_result.get('capture', {}).get(k)
                           for k in ('scripts_observed', 'scripts_analyzed')},
        'bundle_inferences': len(bundle_result.get('inferences', [])),
        'event_counts': dict(kinds),
        'origins': sorted({r['origin'] for r in requests if r['origin']}),
        'data_requests': [r for r in requests if r['type'] in ('fetch', 'xhr', 'document')],
        'request_types': dict(collections.Counter(r['type'] for r in requests)),
        'accessibility_sha256': tree.get('sha256'),
        'screenshot_sha256': screenshot['sha256'] if screenshot else None,
        'visible_text': [re.sub(r'\?\S*', '?<redacted>', line) if '/url:' in line else line
                         for line in tree.get('text', '').splitlines()],
    }
    json.dump(receipt, sys.stdout, indent=1, ensure_ascii=False)


main()
