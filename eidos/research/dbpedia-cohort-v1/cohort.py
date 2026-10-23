"""Original deterministic cohort mechanics; importing this file reads no corpus."""
import hashlib
import heapq
import json
import re
import unicodedata

LABELS = ('Company', 'EducationalInstitution', 'Artist', 'Athlete', 'OfficeHolder',
          'MeanOfTransportation', 'Building', 'NaturalPlace', 'Village', 'Animal',
          'Plant', 'Album', 'Film', 'WrittenWork')
SALT = 'eidos-dbpedia-cohort-v1:2026-10-23'
MAX_SELECTED_CHARS = 200_000


class CohortError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise CohortError(reason)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def normalized(text):
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', text).casefold()))


def fingerprints(text):
    """Both punctuation-preserving and application-token normalization."""
    value = unicodedata.normalize('NFKC', text).casefold()
    return {digest('whitespace:' + ' '.join(value.split())),
            digest('tokens:' + normalized(text))}


def rank(identifier):
    return int(digest(SALT + '\n' + identifier), 16)


def select_records(records, split, labels=LABELS, quota=20, expected_per_label=None,
                   observer=None):
    """Scan every record; retain the smallest predeclared ID hashes per class.

    No feature, title, quality outcome or historic-overlap result affects ranking.
    Official train supplies quota supports then quota development records/class.
    An invalid row, count or later overlap rejects the entire attempted cohort.
    """
    require(split in ('train', 'test'), 'split')
    require(type(quota) is int and 1 <= quota <= 20, 'quota')
    require(type(labels) is tuple and len(set(labels)) == len(labels) and 2 <= len(labels) <= 14,
            'labels')
    require(all(type(x) is str and x for x in labels), 'label_strings')
    keep = quota * (2 if split == 'train' else 1)
    counts = [0] * len(labels)
    heaps = [[] for _ in labels]
    total_chars = 0
    total = 0
    for index, value in enumerate(records):
        require(index < 630_000, 'source_row_limit')
        require(type(value) is dict and set(value) == {'label', 'title', 'content'}, 'source_schema')
        label, title, content = (value[k] for k in ('label', 'title', 'content'))
        require(type(label) is int and 0 <= label < len(labels), 'source_label')
        require(type(title) is str and 1 <= len(title) <= 2048 and title.strip(), 'source_title')
        require(type(content) is str and 1 <= len(content) <= 64_000 and content.strip(), 'source_content')
        try:
            (title + content).encode('utf-8', 'strict')
        except UnicodeError as error:
            raise CohortError('source_unicode') from error
        total_chars += len(title) + len(content)
        require(total_chars <= 300_000_000, 'source_character_limit')
        identifier = 'dbp-' + split + '-' + str(index).zfill(8)
        row = {'id': identifier, 'label': labels[label], 'title': title,
               'content': content, 'text': title + '\n' + content}
        if observer is not None:
            observer(row)
        counts[label] += 1
        total += 1
        entry = (-rank(identifier), identifier, row)
        if len(heaps[label]) < keep:
            heapq.heappush(heaps[label], entry)
        elif entry[0] > heaps[label][0][0]:
            heapq.heapreplace(heaps[label], entry)
    require(all(c >= keep for c in counts), 'insufficient_class_records')
    if expected_per_label is not None:
        require(type(expected_per_label) is int and counts == [expected_per_label] * len(labels),
                'unexpected_source_class_counts')
    ordered = [[e[2] for e in sorted(h, key=lambda e: (-e[0], e[1]))] for h in heaps]
    roles = {'support': [], 'development': []} if split == 'train' else {'test': []}
    for rows in ordered:
        if split == 'train':
            roles['support'].extend(rows[:quota])
            roles['development'].extend(rows[quota:])
        else:
            roles['test'].extend(rows)
    return roles, {'rows': total, 'class_counts': dict(zip(labels, counts)), 'characters': total_chars}


def reject_training_overlap(test_rows):
    """Check selected test titles AND token-normalized full features vs all train."""
    titles = {digest(normalized(r['title'])) for r in test_rows}
    texts = {digest(normalized(r['text'])) for r in test_rows}

    def check(row):
        require(digest(normalized(row['title'])) not in titles, 'test_title_in_official_train')
        require(digest(normalized(row['text'])) not in texts, 'test_text_in_official_train')
    return check


def check_cohort(roles, historical):
    """No row removal, replacement, quality inference or outcome-dependent retry."""
    require(set(roles) == {'support', 'development', 'test'}, 'cohort_roles')
    require(type(historical) is set and all(re.fullmatch('[0-9a-f]{64}', x) for x in historical),
            'historical_fingerprints')
    rows, owners, seen_ids = [], [], set()
    for role in ('support', 'development', 'test'):
        require(bool(roles[role]), 'empty_role')
        require(sum(len(r['text']) for r in roles[role]) <= MAX_SELECTED_CHARS, 'role_character_limit')
        seen_text = set()
        for row in roles[role]:
            require(row['id'] not in seen_ids, 'duplicate_id')
            seen_ids.add(row['id'])
            require(1 <= len(row['text']) <= 12_000 and normalized(row['text']), 'selected_text_limit')
            token_text = normalized(row['text'])
            require(token_text not in seen_text, 'within_role_duplicate_text')
            seen_text.add(token_text)
            require(not (fingerprints(row['text']) | fingerprints(row['content'])) & historical,
                    'historical_exact_text_overlap')
            rows.append(row)
            owners.append(role)
    parent = list(range(len(rows)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    lookups = ({}, {})
    for i, row in enumerate(rows):
        for lookup, key in zip(lookups, (normalized(row['title']), normalized(row['text']))):
            require(bool(key), 'empty_normalized_title_or_text')
            if key in lookup:
                parent[root(i)] = root(lookup[key])
            else:
                lookup[key] = i
    members = {}
    for i, row in enumerate(rows):
        members.setdefault(root(i), []).append(i)
    groups = {}
    for indexes in members.values():
        require(len({owners[i] for i in indexes}) == 1, 'cross_role_title_or_text_component')
        group_id = 'dbp-group-' + digest('\n'.join(sorted(rows[i]['id'] for i in indexes)))
        for i in indexes:
            groups[rows[i]['id']] = group_id
    return groups, {'selected_rows': len(rows), 'components': len(members),
                    'cross_role_components': 0, 'historical_exact_overlaps': 0,
                    'max_component_rows': max(map(len, members.values()))}


def unique_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda _x: (_ for _ in ()).throw(CohortError('nonfinite_json')))


def historical_texts(raw, kind):
    """Extract text only. No prediction, outcome, answer-key or metric input."""
    require(len(raw) <= 32 * 1024**2, 'history_file_limit')
    if kind == 'jsonl':
        for line in raw.decode('utf-8', 'strict').splitlines():
            row = unique_json(line)
            require(type(row) is dict and type(row.get('text')) is str, 'history_schema')
            require(1 <= len(row['text']) <= 12_000, 'history_text_limit')
            yield row['text']
    elif kind == 'snips':
        # Same lossless paired CESU-8 handling used for the pinned public source.
        text = raw.decode('utf-8-sig', 'surrogatepass').encode('utf-16', 'surrogatepass').decode('utf-16')
        value = unique_json(text)
        require(type(value) is dict and len(set(value) - {'language'}) == 1, 'snips_top_schema')
        require(value.get('language', 'en') == 'en', 'snips_language')
        items = value[next(k for k in value if k != 'language')]
        require(type(items) is list and 1 <= len(items) <= 20_000, 'snips_count')
        for row in items:
            require(type(row) is dict and set(row) == {'data'} and type(row['data']) is list, 'snips_row')
            chunks = []
            for segment in row['data']:
                require(type(segment) is dict and 'text' in segment and
                        set(segment) <= {'text', 'entity', 'slot_name'} and type(segment['text']) is str,
                        'snips_segment')
                chunks.append(segment['text'])
            result = ''.join(chunks).strip()
            require(1 <= len(result) <= 12_000, 'snips_text_limit')
            yield result
    else:
        raise CohortError('history_kind')
