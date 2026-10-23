"""Compare original AAS JSON files with tool re-serializations and report lost/added/changed paths.

Usage:
  python3 roundtrip_diff.py basyx-write <corpus-dir> <out-dir>    # re-serialize JSON inputs with BaSyx Python
  python3 roundtrip_diff.py diff <corpus-dir> <roundtrip-dir> <tool-label> > diff.json

Paths are normalised: list indices are replaced by the element's idShort (or key value) where available,
so element reordering does not count as a change.
"""
import json
import logging
import sys
from pathlib import Path

logging.disable(logging.CRITICAL)


def label(item, index):
    if isinstance(item, dict):
        for key in ('idShort', 'id', 'language', 'value'):
            if isinstance(item.get(key), str):
                return f'[{key}={item[key][:40]}]'
        if isinstance(item.get('keys'), list) and item['keys']:
            return f"[ref={str(item['keys'][-1].get('value'))[:40]}]"
    return f'[{index}]'


def flatten(node, prefix='', out=None):
    out = {} if out is None else out
    if isinstance(node, dict):
        for key, value in node.items():
            flatten(value, f'{prefix}.{key}', out)
    elif isinstance(node, list):
        seen = {}
        for index, item in enumerate(node):
            tag = label(item, index)
            seen[tag] = seen.get(tag, 0) + 1
            if seen[tag] > 1:
                tag = f'{tag}#{seen[tag]}'
            flatten(item, prefix + tag, out)
    else:
        out[prefix] = node
    return out


def generalise(path):
    """Collapse element-specific labels so findings group by field kind."""
    import re
    return re.sub(r'\[[^\]]*\]', '[]', path)


def diff(original, roundtrip):
    a, b = flatten(original), flatten(roundtrip)
    lost = sorted(set(a) - set(b))
    added = sorted(set(b) - set(a))
    changed = sorted(p for p in set(a) & set(b) if a[p] != b[p] and str(a[p]) != str(b[p]))
    return lost, added, changed, a, b


def basyx_write(corpus, out):
    from basyx.aas.adapter.json import read_aas_json_file, write_aas_json_file
    for path in sorted(corpus.rglob('*.json')):
        if path.name == 'probes-manifest.json':
            continue
        target = out / path.relative_to(corpus)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            store = read_aas_json_file(str(path), failsafe=False)
            write_aas_json_file(str(target), store)
        except Exception:  # noqa: BLE001 - reading failures are reported by run_python_tools.py
            continue


def report(corpus, roundtrip_dir, tool):
    rows = []
    for path in sorted(corpus.rglob('*.json')):
        if path.name == 'probes-manifest.json':
            continue
        rt = roundtrip_dir / path.relative_to(corpus)
        if not rt.exists():
            continue
        lost, added, changed, a, b = diff(json.loads(path.read_text()), json.loads(rt.read_text()))
        group = lambda paths: sorted({generalise(p) for p in paths})
        rows.append({'file': str(path.relative_to(corpus)), 'tool': tool, 'leaf_values': len(a),
                     'lost': len(lost), 'added': len(added), 'changed': len(changed),
                     'lost_kinds': group(lost)[:15], 'added_kinds': group(added)[:15],
                     'changed_examples': [f'{p}: {a[p]!r} -> {b[p]!r}' for p in changed[:5]]})
    json.dump(rows, sys.stdout, indent=1, ensure_ascii=False)


if sys.argv[1] == 'basyx-write':
    basyx_write(Path(sys.argv[2]), Path(sys.argv[3]))
else:
    report(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
