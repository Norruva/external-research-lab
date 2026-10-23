"""Original bounded research contracts. No model, corpus or network import."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z', re.ASCII)
SHA = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError('nonfinite_json')
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    canonical(result)
    return result


def read(path, maximum=4 * 1024**2):
    p = Path(path)
    require(not any(a.is_symlink() for a in (p, *p.parents)), 'symlink_refused')
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum, 'regular_file_bound')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        require(len(raw) == before.st_size and
                (before.st_ino, before.st_mtime_ns, before.st_ctime_ns, before.st_size) ==
                (after.st_ino, after.st_mtime_ns, after.st_ctime_ns, after.st_size), 'file_changed')
    return raw


def pinned(path, pin, maximum=4 * 1024**2):
    raw = read(path, maximum)
    require(digest(raw) == pin['sha256'] and len(raw) == pin['bytes'], 'file_pin_mismatch')
    return raw


def write(path, value, jsonl=False):
    raw = (b''.join(canonical(row) + b'\n' for row in value) if jsonl else canonical(value) + b'\n')
    require(len(raw) <= 4 * 1024**2, 'output_byte_limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def rows(raw, fields, count):
    require(type(count) is int, 'integer_case_count')
    values = [parse(line) for line in raw.decode('utf-8').splitlines()]
    require(len(values) == count and 1 <= count <= 1_400, 'complete_case_count')
    require(all(type(v) is dict and set(v) == fields for v in values), 'row_schema')
    require(all(type(v['id']) is str and ID.fullmatch(v['id']) for v in values), 'invalid_id')
    require(len({v['id'] for v in values}) == count, 'duplicate_id')
    return values


def inputs(raw, count):
    values = rows(raw, {'id', 'text'}, count)
    require(all(type(v['text']) is str and 1 <= len(v['text']) <= 12_000 for v in values), 'text_bound')
    require(sum(len(v['text']) for v in values) <= 1_000_000, 'aggregate_text_bound')
    return values


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_prediction(row, labels, engine):
    fields = {'id', 'text_sha256', 'predicted', 'probabilities', 'answered'}
    require(engine in ('eidos', 'laya'), 'engine')
    require(type(row) is dict and set(row) == fields | ({'logits'} if engine == 'eidos' else set()), 'prediction_fields')
    require(type(row['id']) is str and ID.fullmatch(row['id']), 'prediction_id')
    require(type(row['text_sha256']) is str and SHA.fullmatch(row['text_sha256']), 'prediction_text_pin')
    require(type(row['predicted']) is str and row['predicted'] in labels and type(row['answered']) is bool, 'prediction_choice')
    probabilities = row['probabilities']
    require(type(probabilities) is list and len(probabilities) == len(labels) and
            all(number(p) and 0 <= p <= 1 for p in probabilities), 'prediction_probabilities')
    require(math.isclose(math.fsum(probabilities), 1., abs_tol=1e-12, rel_tol=0), 'probability_mass')
    if engine == 'eidos':
        logits = row['logits']
        require(type(logits) is list and len(logits) == len(labels) and
                all(number(z) and abs(z) <= 1e9 for z in logits), 'prediction_logits')
        maximum = max(logits)
        exponential = [math.exp(z - maximum) for z in logits]
        total = math.fsum(exponential)
        require(all(abs(p - v / total) <= 1e-12 for p, v in zip(probabilities, exponential)), 'logit_probability_mismatch')
        require(row['predicted'] == labels[max(range(len(labels)), key=probabilities.__getitem__)] and
                row['answered'] is False, 'review_only_candidate_rule')
    # LAYA's actual returned choice is authoritative, including rounded ties.
    return row


def align(left, right, truth, grouping, labels, count):
    require(len(left) == len(right) == len(truth) == len(grouping) == count, 'complete_paired_count')
    inventories = [[r['id'] for r in values] for values in (left, right, truth, grouping)]
    require(all(x == inventories[0] for x in inventories) and len(set(inventories[0])) == count,
            'complete_ordered_paired_inventory')
    for a, b, gold, group in zip(left, right, truth, grouping):
        validate_prediction(a, labels, 'eidos')
        validate_prediction(b, labels, 'laya')
        require(a['text_sha256'] == b['text_sha256'], 'paired_text_pin')
        require(set(gold) == {'id', 'label'} and type(gold['label']) is str and gold['label'] in labels, 'gold_schema')
        require(set(group) == {'id', 'group_id'} and type(group['group_id']) is str and
                ID.fullmatch(group['group_id']), 'group_schema')
    require(len({r['group_id'] for r in grouping}) >= 2, 'at_least_two_groups')
    return ([{**r, 'label': g['label']} for r, g in zip(left, truth)],
            [{**r, 'label': g['label']} for r, g in zip(right, truth)],
            [r['group_id'] for r in grouping])
