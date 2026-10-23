"""Original predeclared larger-cohort mechanics; import performs no I/O.

Eligibility deliberately restricts the target population to articles whose
normalized title, full feature and content are globally unique across both
official splits. It is decided before neutral-ID ranking, never from outcomes.
"""
from dataclasses import dataclass
import collections
import hashlib
import heapq
import json
import re
import unicodedata

LABELS = ('Company', 'EducationalInstitution', 'Artist', 'Athlete', 'OfficeHolder',
          'MeanOfTransportation', 'Building', 'NaturalPlace', 'Village', 'Animal',
          'Plant', 'Album', 'Film', 'WrittenWork')
SALT = 'eidos-dbpedia-scaled-cohort-v1:2026-10-23'
QUOTAS = {'training': 1000, 'development': 100, 'test': 100}
EXPECTED = {'train': 40_000, 'test': 5_000}
ROLE_CHARACTERS = {'training': 10_000_000, 'development': 1_000_000, 'test': 1_000_000}
ROLE_BYTES = {'training': 32 * 1024**2, 'development': 4 * 1024**2, 'test': 4 * 1024**2}
EXPOSURE_ID = re.compile(r'dbp-(train|test)-[0-9]{8}\Z', re.ASCII)
SHA = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)


class CohortError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise CohortError(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def normalized(text):
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', text).casefold()))


def fingerprints(text):
    value = unicodedata.normalize('NFKC', text).casefold()
    return {digest('whitespace:' + ' '.join(value.split())),
            digest('tokens:' + normalized(text))}


def row_keys(row):
    return tuple(digest(normalized(row[field])) for field in ('title', 'text', 'content'))


def rank(identifier):
    return int(digest(SALT + '\n' + identifier), 16)


def settings(labels, quotas=None, expected=None):
    require(type(labels) is tuple and 2 <= len(labels) <= 14 and len(set(labels)) == len(labels), 'labels')
    require(all(type(x) is str and x and len(x) <= 128 for x in labels), 'label_strings')
    if quotas is not None:
        require(type(quotas) is dict and set(quotas) == set(QUOTAS), 'quotas')
        require(all(type(quotas[k]) is int and 1 <= quotas[k] <= QUOTAS[k] for k in QUOTAS), 'quota_bounds')
    if expected is not None:
        require(type(expected) is dict and set(expected) == {'train', 'test'}, 'expected_counts')
        require(all(type(v) is int and 1 <= v <= 40_000 for v in expected.values()), 'expected_counts')


def validate_source(value, split, index, labels):
    require(split in ('train', 'test') and type(index) is int and 0 <= index < 630_000, 'source_row_limit')
    require(type(value) is dict and set(value) == {'label', 'title', 'content'}, 'source_schema')
    label, title, content = (value[k] for k in ('label', 'title', 'content'))
    require(type(label) is int and 0 <= label < len(labels), 'source_label')
    require(type(title) is str and 1 <= len(title) <= 2048 and title.strip(), 'source_title')
    require(type(content) is str and 1 <= len(content) <= 64_000 and content.strip(), 'source_content')
    try:
        (title + content).encode('utf-8', 'strict')
    except UnicodeError as error:
        raise CohortError('source_unicode') from error
    return {'id': f'dbp-{split}-{index:08}', 'label': labels[label],
            'title': title, 'content': content, 'text': title + '\n' + content}


def source_rows(factory, split, labels):
    total_characters = 0
    for index, value in enumerate(factory(split)):
        row = validate_source(value, split, index, labels)
        total_characters += len(row['title']) + len(row['content'])
        require(total_characters <= 300_000_000, 'source_character_limit')
        yield row


@dataclass
class Census:
    labels: tuple
    expected: dict
    counts: tuple
    source_stats: dict
    exposures: dict
    exposure_keys: tuple
    exposure_fingerprints: set


def validate_exposures(exposures):
    require(type(exposures) is dict and len(exposures) <= 840, 'exposure_inventory')
    require(all(type(k) is str and EXPOSURE_ID.fullmatch(k) and
                type(v) is str and SHA.fullmatch(v) for k, v in exposures.items()), 'exposure_binding')


def scan_sources(factory, exposures, labels=LABELS, expected_per_label=EXPECTED):
    """First complete pass: global duplicate census and old-case reconstruction.

    Prior cases are bound by ID and raw full-feature SHA. Their actual pinned
    source rows provide title/content boundaries, without guessing from newlines
    or reading their gold labels, predictions or quality measurements.
    """
    settings(labels, expected=expected_per_label)
    validate_exposures(exposures)
    counts = tuple(collections.Counter() for _ in range(3))
    old_keys = tuple(set() for _ in range(3))
    old_fingerprints, resolved, stats = set(), set(), {}
    for split in ('train', 'test'):
        class_counts, characters, sequence = collections.Counter(), 0, hashlib.sha256()
        for row in source_rows(factory, split, labels):
            class_counts[row['label']] += 1
            characters += len(row['title']) + len(row['content'])
            sequence.update(canonical(row) + b'\n')
            keys = row_keys(row)
            for counter, key in zip(counts, keys):
                counter[key] += 1
                require(len(counter) <= 630_000, 'global_key_limit')
            if row['id'] in exposures:
                require(digest(row['text']) == exposures[row['id']], 'exposure_source_text_mismatch')
                resolved.add(row['id'])
                for output, key in zip(old_keys, keys):
                    output.add(key)
                old_fingerprints.update(fingerprints(row['text']) | fingerprints(row['content']))
        require(class_counts == {label: expected_per_label[split] for label in labels},
                'unexpected_source_class_counts')
        stats[split] = {'rows': sum(class_counts.values()), 'class_counts': dict(class_counts),
                        'characters': characters, 'ordered_validated_rows_sha256': sequence.hexdigest()}
    require(resolved == set(exposures), 'unresolved_exposure_id')
    return Census(labels, dict(expected_per_label), counts, stats, dict(exposures),
                  old_keys, old_fingerprints)


def eligibility(row, census, historical):
    """Return every predeclared exclusion reason; counts overlap intentionally."""
    keys = row_keys(row)
    reasons = []
    for name, counter, key in zip(('title', 'full_feature', 'content'), census.counts, keys):
        if counter[key] != 1:
            reasons.append('non_singleton_' + name)
    if row['id'] in census.exposures or any(key in old for key, old in zip(keys, census.exposure_keys)):
        reasons.append('previously_selected_id_or_component')
    row_fingerprints = fingerprints(row['text']) | fingerprints(row['content'])
    if row_fingerprints & historical or row_fingerprints & census.exposure_fingerprints:
        reasons.append('historical_full_or_content_fingerprint')
    return reasons


def select_sources(factory, census, historical, quotas=QUOTAS, labels=LABELS):
    """Second full pass: establish eligibility, then hash-rank eligible IDs.

    Source-wide eligibility exclusions are part of the declared target
    population. Nothing is replaced/dropped after selection if a later bound or
    validation fails. Ordered source hashes bind both passes to the same rows.
    """
    settings(labels, quotas=quotas)
    require(isinstance(census, Census) and census.labels == labels, 'census_binding')
    require(type(historical) is set and all(type(x) is str and SHA.fullmatch(x) for x in historical),
            'historical_fingerprints')
    roles, selection_stats = {'training': [], 'development': [], 'test': []}, {}
    for split in ('train', 'test'):
        keep = quotas['training'] + quotas['development'] if split == 'train' else quotas['test']
        heaps = {label: [] for label in labels}
        class_counts, eligible_counts, exclusions = collections.Counter(), collections.Counter(), collections.Counter()
        sequence, characters, excluded_rows = hashlib.sha256(), 0, 0
        for row in source_rows(factory, split, labels):
            class_counts[row['label']] += 1
            characters += len(row['title']) + len(row['content'])
            sequence.update(canonical(row) + b'\n')
            reasons = eligibility(row, census, historical)
            if reasons:
                exclusions.update(reasons)
                excluded_rows += 1
                continue
            eligible_counts[row['label']] += 1
            entry = (-rank(row['id']), row['id'], row)
            heap = heaps[row['label']]
            if len(heap) < keep:
                heapq.heappush(heap, entry)
            elif entry[0] > heap[0][0]:
                heapq.heapreplace(heap, entry)
        original = census.source_stats[split]
        require(class_counts == original['class_counts'] and characters == original['characters'] and
                sequence.hexdigest() == original['ordered_validated_rows_sha256'], 'source_changed_between_passes')
        require(all(eligible_counts[label] >= keep for label in labels), 'insufficient_eligible_class_records')
        for label in labels:
            ordered = [entry[2] for entry in sorted(heaps[label], key=lambda entry: (-entry[0], entry[1]))]
            if split == 'train':
                roles['training'].extend(ordered[:quotas['training']])
                roles['development'].extend(ordered[quotas['training']:])
            else:
                roles['test'].extend(ordered)
        selection_stats[split] = {'source_rows': sum(class_counts.values()),
                                 'eligible_per_class': dict(eligible_counts),
                                 'excluded_rows': excluded_rows,
                                 'overlapping_exclusion_reason_counts': dict(exclusions),
                                 'source_changed_between_passes': False}
    return roles, selection_stats


def check_cohort(roles, census, historical, quotas=QUOTAS, labels=LABELS):
    settings(labels, quotas=quotas)
    require(set(roles) == set(QUOTAS), 'cohort_roles')
    seen_ids, selected_keys, groups = set(), tuple(set() for _ in range(3)), {}
    total = 0
    for role in ('training', 'development', 'test'):
        values = roles[role]
        require(collections.Counter(row['label'] for row in values) ==
                {label: quotas[role] for label in labels}, 'selected_role_class_counts')
        require(sum(len(row['text']) for row in values) <= ROLE_CHARACTERS[role], 'role_character_limit')
        feature_bytes = b''.join(canonical({k: row[k] for k in ('id', 'text', 'label')}) + b'\n' for row in values)
        require(len(feature_bytes) <= ROLE_BYTES[role], 'role_file_size_limit')
        for row in values:
            require(row['id'] not in seen_ids, 'duplicate_selected_id')
            require(1 <= len(row['text']) <= 12_000 and normalized(row['text']) and
                    normalized(row['title']) and normalized(row['content']), 'selected_text_limit')
            require(not eligibility(row, census, historical), 'selected_ineligible_row')
            keys = row_keys(row)
            for key, seen in zip(keys, selected_keys):
                require(key not in seen, 'selected_component_overlap')
                seen.add(key)
            seen_ids.add(row['id'])
            groups[row['id']] = 'dbp-scaled-group-' + digest(row['id'])
            total += 1
    return groups, {'selected_rows': total, 'components': total, 'max_component_rows': 1,
                    'cross_role_components': 0, 'historical_exact_overlaps': 0,
                    'resolved_previous_exposure_cases': len(census.exposures),
                    'selection_population': 'globally_singleton_title_full_feature_content_and_unexposed',
                    'semantic_template_speaker_or_pretraining_independence_verified': False}


def unique_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda _x: (_ for _ in ()).throw(CohortError('nonfinite_json')))


def exposure_rows(raw, kind):
    """Read prior IDs/text only; label values are deliberately never accessed."""
    require(kind in ('labeled_training_text', 'unlabeled_test_text') and
            1 <= len(raw) <= 4 * 1024**2, 'exposure_source_kind_or_size')
    expected = {'id', 'text', 'label'} if kind == 'labeled_training_text' else {'id', 'text'}
    for line in raw.decode('utf-8', 'strict').splitlines():
        row = unique_json(line)
        require(type(row) is dict and set(row) == expected, 'exposure_source_schema')
        require(type(row['id']) is str and EXPOSURE_ID.fullmatch(row['id']), 'exposure_source_id')
        require(type(row['text']) is str and 1 <= len(row['text']) <= 12_000, 'exposure_source_text')
        row['text'].encode('utf-8', 'strict')
        yield row['id'], digest(row['text'])


def historical_texts(raw, kind):
    """Reviewed old source-text extraction semantics, copied as local stdlib code."""
    require(len(raw) <= 32 * 1024**2, 'history_file_limit')
    if kind == 'jsonl':
        for line in raw.decode('utf-8', 'strict').splitlines():
            row = unique_json(line)
            require(type(row) is dict and type(row.get('text')) is str, 'history_schema')
            require(1 <= len(row['text']) <= 12_000, 'history_text_limit')
            yield row['text']
    elif kind == 'snips':
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
