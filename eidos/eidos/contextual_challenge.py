"""Original finite binding/order challenges; generate data, never run a model.

The public generator supports separately described synthetic shift slices. It
does not establish a pristine benchmark, real-domain quality, calibration or
LAYA/JEV parity. Related counterfactual variants always share one source group.
"""
import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import random
import sys

from .decision_contract import validate_request

FORMAT = 'eidos-original-contextual-challenge-v1'
ROLES = ('training', 'fresh_world', 'novel_entities', 'new_wording', 'joint')
RELATIONAL = ('property', 'binding', 'one_hop', 'two_hop')
KNOWN_NAMES = ('Nexal', 'Pevin', 'Rukor', 'Sadil', 'Tezum')
NOVEL_NAMES = ('Vekim', 'Wusor', 'Xadin', 'Yelun', 'Zopar')
HUES = ('coral', 'denim', 'hazel', 'mauve', 'sepia')
KNOWN_SYMBOLS = ('lunex', 'merov', 'nifal', 'porin', 'suxel',
                 'tavir', 'vorem', 'wazik', 'xelun', 'zorav')
NOVEL_SYMBOLS = ('bexor', 'cuvan', 'dorel', 'fexim', 'gavor',
                 'hisel', 'javor', 'kexul', 'leriv', 'moxel')
CONTEXT = {
    'known': ('{name} has hue {hue} and points to {successor}.',
              '{name} points to {successor} and has hue {hue}.'),
    'shifted': ('Node {name} carries {hue} hue and links onward to {successor}.',
                'Node {name} links onward to {successor} while carrying {hue} hue.'),
}
QUESTIONS = {
    'known': {
        'property': ('What hue does {subject} have?', 'Select the hue of {subject}.'),
        'binding': ('Which node has hue {hue}?', 'Select the node with hue {hue}.'),
        'one_hop': ('Follow one points-to link from {subject}. What hue is there?',
                    'What hue does the node {subject} points to have?'),
        'two_hop': ('Follow two points-to links from {subject}. What hue is there?',
                    'What hue does the node reached after two points-to links from {subject} have?'),
        'sequence_first': ('Which symbol appears first?', 'Select the first symbol.'),
    },
    'shifted': {
        'property': ('Return the shade carried by {subject}.',
                     "Identify {subject}'s recorded shade."),
        'binding': ('Name the node carrying shade {hue}.',
                    'The shade {hue} belongs to which node?'),
        'one_hop': ('From {subject}, move onward once and return the shade.',
                    "Return the shade at {subject}'s immediate link destination."),
        'two_hop': ('From {subject}, move onward twice and return the shade.',
                    'Return the shade after two outward links starting at {subject}.'),
        'sequence_first': ('Return the opening symbol.', 'Name the leading symbol.'),
    },
}
PROVENANCE = {
    'source': 'Original three/five-node finite graphs, counterfactual hue bindings and first-symbol sequences authored in contextual_challenge.py',
    'license': 'Original authored synthetic material; no third-party material or new blanket public license grant',
    'citation': 'EIDOS original contextual binding/order challenge v1, 2026',
    'rights_basis': 'Authored without external corpus, pretrained checkpoint, vendor output or previous challenge rows; declaration is not an exclusive legal ownership or production rights certificate',
}
MAX_TRAINING_PAIRS = 40
MAX_SLICE_PAIRS = 20
MAX_ORDERING_PAIRS = 8
MAX_OUTPUT_BYTES = 8 * 1024**2


class ChallengeError(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise ChallengeError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _wording(role):
    return 'shifted' if role in ('new_wording', 'joint') else 'known'


def _names(role):
    return NOVEL_NAMES if role in ('novel_entities', 'joint') else KNOWN_NAMES


def _symbols(role):
    return NOVEL_SYMBOLS if role in ('novel_entities', 'joint') else KNOWN_SYMBOLS


@dataclass(frozen=True)
class Node:
    name: str
    hue: str
    successor: str


@dataclass(frozen=True)
class World:
    nodes: tuple[Node, ...]

    def node(self, name):
        return next(node for node in self.nodes if node.name == name)

    def as_dict(self):
        return {'nodes': [dict(name=n.name, hue=n.hue, successor=n.successor)
                          for n in sorted(self.nodes, key=lambda n: n.name)]}

    def structural(self):
        """An exact name-erased assignment, not a graph-isomorphism proof."""
        ordered = sorted(self.nodes, key=lambda n: n.name)
        positions = {n.name: index for index, n in enumerate(ordered)}
        return [(n.hue, positions[n.successor]) for n in ordered]


def _world(rng, role, size):
    names = rng.sample(_names(role), size)
    hues = rng.sample(HUES, size)
    cycle = list(names)
    rng.shuffle(cycle)
    successors = dict(zip(cycle, cycle[1:] + cycle[:1]))
    return World(tuple(Node(name, hue, successors[name]) for name, hue in zip(names, hues)))


def counterfactual(world, first, second):
    """Swap two hue assignments while retaining every entity and directed edge."""
    _need(first != second, 'counterfactual_needs_distinct_nodes')
    a, b = world.node(first), world.node(second)
    return World(tuple(Node(n.name, b.hue if n.name == first else
                           a.hue if n.name == second else n.hue, n.successor)
                       for n in world.nodes))


def oracle(world, task):
    """Compute a finite label from typed facts, independent of option identity."""
    family = task['family']
    if family == 'binding':
        return next(n.name for n in world.nodes if n.hue == task['hue'])
    current = world.node(task['subject'])
    if family == 'property':
        return current.hue
    if family in ('one_hop', 'two_hop'):
        for _ in range(1 if family == 'one_hop' else 2):
            current = world.node(current.successor)
        return current.hue
    raise ChallengeError('unknown_relational_family')


def render(world, wording, template_index, order):
    return ' '.join(CONTEXT[wording][template_index].format(**world.node(name).__dict__)
                    for name in order)


def _tasks(world, actors):
    predecessors = {node.successor: node.name for node in world.nodes}
    tasks = []
    for family in RELATIONAL:
        for index, destination in enumerate(actors):
            task = {'family': family, 'template_index': index}
            if family == 'binding':
                task['hue'] = world.node(destination).hue
            else:
                subject = destination
                for _ in range({'property': 0, 'one_hop': 1, 'two_hop': 2}[family]):
                    subject = predecessors[subject]
                task['subject'] = subject
            tasks.append(task)
    return tasks


def _request(context, prompt, values, answer, request_id, seed):
    rng = random.Random(int(digest({'seed': seed, 'request': request_id,
                                  'purpose': 'opaque-options'})[:16], 16))
    identifiers = rng.sample(range(1_000_000, 9_999_999), len(values))
    options = [{'id': f'x{identifier}', 'description': value}
               for identifier, value in zip(identifiers, values)]
    rng.shuffle(options)
    target = next(option['id'] for option in options if option['description'] == answer)
    request = {'format': 'eidos-decision-request-v2', 'request_id': request_id,
               'context': context, 'prompt': prompt, 'kind': 'CHOICE', 'options': options}
    validate_request(request)
    return request, target


def _counts(values):
    return dict(sorted(Counter(str(v) for v in values).items()))


def generate(training_pairs=4, slice_pairs=2, ordering_pairs=4, seed=27183):
    """Return source rows and private oracle sidecars without file/model access.

    Role RNGs are separately seeded. Global exact-structure exclusion may force
    downstream resampling when earlier role counts change; count independence
    of downstream files is not promised.
    """
    for name, value, minimum, maximum in (
            ('training_pairs', training_pairs, 1, MAX_TRAINING_PAIRS),
            ('slice_pairs', slice_pairs, 0, MAX_SLICE_PAIRS),
            ('ordering_pairs', ordering_pairs, 0, MAX_ORDERING_PAIRS),
            ('seed', seed, 0, 2**31 - 1)):
        _need(type(value) is int and minimum <= value <= maximum, name + '_outside_bounds')
    roles, worlds, sequences, tasks, role_audits = {}, {}, {}, {}, {}
    structural_seen, sequence_seen, request_ids = set(), set(), set()
    for role in ROLES:
        pair_count = training_pairs if role == 'training' else slice_pairs
        order_count = ordering_pairs if role == 'training' else min(ordering_pairs, slice_pairs)
        rng = random.Random(int(digest({'namespace': FORMAT, 'seed': seed, 'role': role})[:16], 16))
        rows, world_records, sequence_records, specs = [], [], [], []
        wording = _wording(role)
        for pair_index in range(pair_count):
            # Three and five distinguish following twice from reversing twice.
            size = (3, 5)[pair_index % 2]
            for _attempt in range(2000):
                base = _world(rng, role, size)
                actors = rng.sample([n.name for n in base.nodes], 2)
                altered = counterfactual(base, *actors)
                hashes = {digest(w.structural()) for w in (base, altered)}
                if len(hashes) == 2 and structural_seen.isdisjoint(hashes):
                    structural_seen.update(hashes)
                    break
            else:
                raise ChallengeError('unique_relational_world_search_exhausted')
            group = 'cx-world-' + digest(base.as_dict())[:24]
            task_list = _tasks(base, actors)
            order = [n.name for n in base.nodes]
            rng.shuffle(order)
            template_index = rng.randrange(2)
            rendered, answers = [], []
            for variant, world in enumerate((base, altered)):
                world_hash = digest(world.as_dict())
                context = render(world, wording, template_index, order)
                rendered.append(context)
                world_records.append({'world_hash': world_hash,
                    'structural_hash': digest(world.structural()), 'source_group': group,
                    'variant': variant, 'wording': wording, 'context_template': template_index,
                    'render_order': list(order), 'world': world.as_dict()})
                variant_answers = []
                for task_index, task in enumerate(task_list):
                    answer = oracle(world, task)
                    variant_answers.append(answer)
                    prompt = QUESTIONS[wording][task['family']][task['template_index']].format(**task)
                    values = sorted(n.name for n in world.nodes) if task['family'] == 'binding' else sorted(n.hue for n in world.nodes)
                    request_id = f'cx3-{role}-{len(rows):04d}'
                    request, target = _request(context, prompt, values, answer, request_id, seed)
                    rows.append({'request': request, 'target_option_id': target, 'source_group': group})
                    specs.append({'request_id': request_id, 'source_group': group,
                        'world_hash': world_hash, 'task': dict(task), 'answer': answer,
                        'paired_task_key': f'{group}-{task_index}'})
                answers.append(variant_answers)
            _need(Counter(rendered[0].split()) == Counter(rendered[1].split()),
                  'counterfactual_token_multiset_mismatch')
            _need(all(a != b for a, b in zip(*answers)), 'counterfactual_answer_did_not_change')
        for pair_index in range(order_count):
            for _attempt in range(1000):
                a, b = rng.sample(_symbols(role), 2)
                key = digest(sorted((a, b)))
                if key not in sequence_seen:
                    sequence_seen.add(key)
                    break
            else:
                raise ChallengeError('unique_sequence_search_exhausted')
            group = 'cx-sequence-' + key[:24]
            prompt_index = pair_index % 2
            prompt = QUESTIONS[wording]['sequence_first'][prompt_index]
            for variant, tokens in enumerate(((a, b, b, a), (b, a, a, b))):
                request_id = f'cx3-{role}-{len(rows):04d}'
                context = ' '.join(tokens) + '.'
                request, target = _request(context, prompt, sorted((a, b)), tokens[0], request_id, seed)
                rows.append({'request': request, 'target_option_id': target, 'source_group': group})
                sequence_records.append({'source_group': group, 'variant': variant,
                    'tokens': list(tokens), 'request_id': request_id})
                specs.append({'request_id': request_id, 'source_group': group,
                    'task': {'family': 'sequence_first', 'template_index': prompt_index},
                    'answer': tokens[0], 'paired_task_key': group})
        role_ids = [row['request']['request_id'] for row in rows]
        _need(len(role_ids) == len(set(role_ids)) and request_ids.isdisjoint(role_ids),
              'duplicate_request_identity')
        request_ids.update(role_ids)
        semantic_keys = [digest({'context': row['request']['context'],
                                'prompt': row['request']['prompt'],
                                'options': sorted(o['description'] for o in row['request']['options'])})
                         for row in rows]
        _need(len(semantic_keys) == len(set(semantic_keys)), 'duplicate_semantic_task')
        shortcuts = {}
        for family in (*RELATIONAL, 'sequence_first'):
            observations = []
            for row, spec in zip(rows, specs):
                if spec['task']['family'] != family:
                    continue
                options = row['request']['options']
                chosen = next(o for o in options if o['id'] == row['target_option_id'])
                observations.append((spec['answer'], options.index(chosen), len(chosen['description']), len(options)))
            shortcuts[family] = {'answer_counts': _counts(x[0] for x in observations),
                'correct_display_rank_zero_based': _counts(x[1] for x in observations),
                'correct_description_length': _counts(x[2] for x in observations),
                'candidate_counts': _counts(x[3] for x in observations),
                'full_balance_or_shortcut_freedom_proven': False}
        roles[role], worlds[role], sequences[role], tasks[role] = rows, world_records, sequence_records, specs
        role_audits[role] = {'rows': len(rows), 'relational_worlds': len(world_records),
            'relational_source_groups': pair_count, 'ordering_source_groups': order_count,
            'total_source_groups': pair_count + order_count,
            'family_counts': _counts(spec['task']['family'] for spec in specs),
            'relation_graph_sizes': _counts(len(w['world']['nodes']) for w in world_records),
            'entity_inventory': 'novel' if role in ('novel_entities', 'joint') else 'known',
            'wording_inventory': wording,
            'all_relational_counterfactual_pair_task_answers_change': True,
            'shortcut_diagnostics': shortcuts,
            'role_jsonl_sha256': hashlib.sha256(b''.join(canonical(row)+b'\n' for row in rows)).hexdigest(),
            'worlds_sha256': digest(world_records), 'tasks_sha256': digest(specs),
            'maximum_context_words': max((len(row['request']['context'].split()) for row in rows), default=0)}
    for index, role in enumerate(ROLES):
        for other in ROLES[index+1:]:
            for field in ('world_hash', 'structural_hash', 'source_group'):
                _need({w[field] for w in worlds[role]}.isdisjoint(w[field] for w in worlds[other]),
                      'cross_role_relational_' + field + '_overlap')
            _need({s['source_group'] for s in sequences[role]}.isdisjoint(s['source_group'] for s in sequences[other]),
                  'cross_role_sequence_group_overlap')
    _need(sum(len(rows) for rows in roles.values()) <= 2000, 'total_task_budget_exceeded')
    audit = {'format': FORMAT, 'seed': seed, 'roles': role_audits,
        'relational_world_group_and_exact_name_erased_structure_disjoint_across_roles': True,
        'sequence_exact_lexeme_pair_groups_disjoint_across_roles': True,
        'sequence_count_first_moment_collision_pattern_shared_intentionally': True,
        'all_roles_have_disjoint_entities_or_templates': False,
        'shift_axes': {'fresh_world': 'new exact worlds; known entity pool and literal grammar',
            'novel_entities': 'new exact worlds and entity pool; known literal grammar',
            'new_wording': 'new exact worlds and literal grammar; known entity pool',
            'joint': 'new exact worlds, entity pool and literal grammar'},
        'all_task_heads': ['CHOICE'], 'BINARY_SCORE_semantic_quality_measured': False,
        'source_groups_must_not_be_split': True,
        'model_fit_inference_scoring_or_activation_performed': False,
        'pristine_real_world_or_LAYA_JEV_benchmark': False,
        'limits': ['Public finite English generator; no real-source or domain independence claim.',
                   'Relation graphs are directed cycles of length three or five, not arbitrary graphs.',
                   'Exact name-erased ordered assignments are excluded; isomorphic graphs may remain.',
                   'First-symbol pairs do not test first-versus-last instruction discrimination.',
                   'Shared ontology, hue vocabulary and sequence collision pattern remain visible.',
                   'Few paired groups; individual rows are not independent observations.',
                   'Option identities/order are randomized; finite label/rank imbalances remain audited.']}
    return {'roles': roles, 'worlds': worlds, 'sequences': sequences, 'tasks': tasks,
            'audit': audit, 'provenance': dict(PROVENANCE)}


def materialize(output, **settings):
    """Write a new private corpus; never overwrite, follow symlinks or fit."""
    destination = Path(output).absolute()
    _need('..' not in destination.parts, 'parent_path_component_refused')
    _need(not destination.exists() and not destination.is_symlink(), 'output_already_exists')
    _need(destination.parent.is_dir(), 'output_parent_required')
    _need(not any(p.is_symlink() for p in (destination.parent, *destination.parent.parents)),
          'symlinked_output_ancestor_refused')
    generated = generate(**settings)
    files = {f'{role}.jsonl': b''.join(canonical(row)+b'\n' for row in rows)
             for role, rows in generated['roles'].items() if rows}
    files.update({'provenance.json': canonical(generated['provenance'])+b'\n',
        'ORACLE.json': canonical({'format': FORMAT, 'worlds': generated['worlds'],
            'sequences': generated['sequences'], 'tasks': generated['tasks']})+b'\n',
        'AUDIT.json': canonical(generated['audit'])+b'\n'})
    _need(sum(map(len, files.values())) <= MAX_OUTPUT_BYTES, 'output_byte_budget_exceeded')
    manifest = {'format': FORMAT, 'files': {name: {'bytes': len(raw),
        'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in sorted(files.items())},
        'pristine_real_world_or_LAYA_JEV_benchmark': False,
        'model_fit_inference_scoring_or_activation_performed': False}
    files['MANIFEST.json'] = canonical(manifest)+b'\n'
    destination.mkdir(mode=0o700)
    written = []
    try:
        for name, raw in files.items():
            path = destination/name
            fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o400)
            written.append(path)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
    except BaseException:
        for path in written:
            path.unlink()
        destination.rmdir()
        raise
    return {'format': FORMAT, 'complete': True,
        'role_rows': {role: len(rows) for role, rows in generated['roles'].items()},
        'role_groups': {role: generated['audit']['roles'][role]['total_source_groups'] for role in ROLES},
        'file_count': len(files), 'manifest_sha256': hashlib.sha256(files['MANIFEST.json']).hexdigest(),
        'model_fit_inference_scoring_or_activation_performed': False,
        'pristine_real_world_or_LAYA_JEV_benchmark': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--training-pairs', type=int, default=4)
    parser.add_argument('--slice-pairs', type=int, default=2)
    parser.add_argument('--ordering-pairs', type=int, default=4)
    parser.add_argument('--seed', type=int, default=27183)
    args = parser.parse_args(argv)
    try:
        result = materialize(args.output, training_pairs=args.training_pairs,
            slice_pairs=args.slice_pairs, ordering_pairs=args.ordering_pairs, seed=args.seed)
    except (ChallengeError, OSError, ValueError, StopIteration) as error:
        reason = str(error) if isinstance(error, ChallengeError) else 'challenge_write_or_validation_failed'
        print(json.dumps({'complete': False, 'error': reason}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
