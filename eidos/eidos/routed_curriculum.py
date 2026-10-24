"""Original question-routing curriculum; data generation only, never model use.

Two named relations, unique properties and fixed-subject depth/direction contrasts
separate question control from context/option matching. All future composition
operator lexemes occur in training; this is not unknown-meaning generalization.
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

from .contextual_challenge import ChallengeError, canonical, digest
from .decision_contract import validate_request
from .decision_artifact import regular_bytes, strict_json
from .decision_neural import tokenize

FORMAT = 'eidos-original-routed-curriculum-v1'
ROLES = ('training', 'fresh_world', 'novel_entities', 'new_composition', 'joint')
FAMILIES = ('property', 'points_forward_one', 'points_forward_two',
            'points_incoming_one', 'cites_forward_one', 'cites_forward_two',
            'cites_incoming_one', 'binding')
KNOWN_NAMES = ('Alven', 'Bodar', 'Cemir', 'Dorak', 'Esnil',
               'Famir', 'Govel', 'Hadrin', 'Isven', 'Jomar')
NOVEL_NAMES = ('Kudrin', 'Lemvo', 'Mafrin', 'Nodex', 'Olmir',
               'Pezra', 'Qodim', 'Ravel', 'Seldin', 'Tovek')
HUES = ('coral', 'denim', 'hazel', 'mauve', 'sepia')
CONTEXT = (
    ('Node {name} has hue {hue}.', 'Node {name} points to {points}.',
     'Node {name} cites {cites}.'),
    ('For node {name}, the hue is {hue}.',
     'From node {name}, the points link goes to node {points}.',
     'From node {name}, the cites link goes to node {cites}.'),
    ('The hue for node {name} is {hue}.',
     'The points link from node {name} goes to node {points}.',
     'The cites link from node {name} goes to node {cites}.'),
)
PROPERTY_QUESTIONS = ('What hue does {subject} have?', 'Return the hue for {subject}.',
                      'For {subject}, return the hue.')
BINDING_QUESTIONS = ('Which node has hue {hue}?', 'Return the node for hue {hue}.',
                     'For hue {hue}, return the node.')
WALK_QUESTIONS = (
    'Follow {number} {direction} {relation} link from {subject}. What hue is there?',
    'Starting from {subject}, return the hue after {number} {direction} {relation} link.',
    'From {subject}, return the hue after {number} {direction} {relation} link.',
)
PROVENANCE = {
    'source': 'Original paired five-node worlds with two sampled named cycles conditioned on different successors, hue rotations and question-only depth/direction/relation contrasts authored in routed_curriculum.py',
    'license': 'Original authored synthetic material; no third-party material or new blanket public license grant',
    'citation': 'EIDOS original routed question-control curriculum v1, 2026',
    'rights_basis': 'Authored without external corpus, pretrained checkpoint, vendor output or previous challenge rows; declaration is not an exclusive legal ownership or production rights certificate',
}
MAX_OUTPUT_BYTES = 8 * 1024**2
MAX_EXCLUSION_BYTES = 1024**2


def _need(condition, reason):
    if not condition:
        raise ChallengeError(reason)


@dataclass(frozen=True)
class Node:
    name: str
    hue: str
    points: str
    cites: str


@dataclass(frozen=True)
class World:
    nodes: tuple[Node, ...]

    def node(self, name):
        return next(n for n in self.nodes if n.name == name)

    def as_dict(self):
        return {'nodes': [dict(name=n.name, hue=n.hue, points=n.points, cites=n.cites)
                          for n in sorted(self.nodes, key=lambda n: n.name)]}

    def structural(self):
        ordered = sorted(self.nodes, key=lambda n: n.name)
        position = {n.name: i for i, n in enumerate(ordered)}
        return [(n.hue, position[n.points], position[n.cites]) for n in ordered]

    def projected_prior_structure(self):
        """Byte-compatible old one-relation structure for exposure exclusion."""
        ordered = sorted(self.nodes, key=lambda n: n.name)
        position = {n.name: i for i, n in enumerate(ordered)}
        return [(n.hue, position[n.points]) for n in ordered]


def _cycle(rng, names):
    order = list(names)
    rng.shuffle(order)
    return dict(zip(order, order[1:] + order[:1]))


def _world(rng, role, pair_index):
    pool = NOVEL_NAMES if role in ('novel_entities', 'joint') else KNOWN_NAMES
    # The first two training groups cover every known entity lexeme; later
    # composition slices therefore do not accidentally introduce new names.
    names = list(KNOWN_NAMES[pair_index*5:(pair_index+1)*5]) if role == 'training' and pair_index < 2 else rng.sample(pool, 5)
    hues = rng.sample(HUES, 5)
    points = _cycle(rng, names)
    for _ in range(1000):
        cites = _cycle(rng, names)
        if all(points[name] != cites[name] for name in names):
            break
    else:
        raise ChallengeError('distinct_relation_cycle_search_exhausted')
    return World(tuple(Node(name, hue, points[name], cites[name]) for name, hue in zip(names, hues)))


def rotate_hues(world, shift=1):
    _need(type(shift) is int and 1 <= shift < 5, 'hue_rotation_outside_bounds')
    ordered = sorted(world.nodes, key=lambda n: n.name)
    rotated = {n.name: ordered[(i+shift) % 5].hue for i, n in enumerate(ordered)}
    return World(tuple(Node(n.name, rotated[n.name], n.points, n.cites) for n in world.nodes))


def oracle(world, task):
    if task['family'] == 'binding':
        return next(n.name for n in world.nodes if n.hue == task['hue'])
    node = world.node(task['subject'])
    relation, direction, steps = task.get('relation'), task.get('direction'), task.get('steps', 0)
    for _ in range(steps):
        node = world.node(getattr(node, relation)) if direction == 'outgoing' else next(n for n in world.nodes if getattr(n, relation) == node.name)
    return node.hue


def _tasks(world, subject):
    tasks = [{'family': 'property', 'subject': subject, 'steps': 0}]
    for relation in ('points', 'cites'):
        for suffix, direction, steps in (('forward_one', 'outgoing', 1),
                                         ('forward_two', 'outgoing', 2),
                                         ('incoming_one', 'incoming', 1)):
            tasks.append({'family': relation+'_'+suffix, 'subject': subject,
                          'relation': relation, 'direction': direction, 'steps': steps})
    tasks.append({'family': 'binding', 'hue': world.node(subject).hue})
    return tasks


def render(world, style, order):
    return ' '.join(template.format(**world.node(name).__dict__)
                    for name in order for template in CONTEXT[style])


def question(task, style):
    if task['family'] == 'property':
        return PROPERTY_QUESTIONS[style].format(**task)
    if task['family'] == 'binding':
        return BINDING_QUESTIONS[style].format(**task)
    return WALK_QUESTIONS[style].format(number='one' if task['steps'] == 1 else 'two', **task)


def _options(values, key):
    rng = random.Random(int(digest({'namespace': FORMAT, 'options': key})[:16], 16))
    ids = rng.sample(range(1_000_000, 9_999_999), len(values))
    options = [{'id': f'rx{identity}', 'description': value} for identity, value in zip(ids, sorted(values))]
    rng.shuffle(options)
    return options


def _counts(values):
    return dict(sorted(Counter(str(v) for v in values).items()))


def generate(training_pairs=4, slice_pairs=2, seed=47311, excluded_structural_hashes=()):
    """Create original rows/sidecars only, resampling by opaque exposure hashes.

    Exclusion checks both new two-relation and byte-compatible prior projected
    structures for BOTH counterfactual worlds. No target/model quality selects
    a world. This is an adaptive public finite curriculum, not a real benchmark.
    """
    for name, value, minimum, maximum in (('training_pairs', training_pairs, 2, 40),
                                         ('slice_pairs', slice_pairs, 0, 20),
                                         ('seed', seed, 0, 2**31-1)):
        _need(type(value) is int and minimum <= value <= maximum, name+'_outside_bounds')
    _need(type(excluded_structural_hashes) in (tuple, list, set, frozenset) and len(excluded_structural_hashes) <= 10000,
          'exclusion_inventory_outside_bounds')
    excluded = set(excluded_structural_hashes)
    _need(all(type(v) is str and len(v) == 64 and all(c in '0123456789abcdef' for c in v) for v in excluded),
          'invalid_exclusion_hash')
    seen_structures, seen_projected, seen_worlds, seen_groups = set(), set(), set(), set()
    roles, worlds, tasks, role_audits = {}, {}, {}, {}
    training_lexemes = set()
    for role in ROLES:
        rng = random.Random(int(digest({'namespace': FORMAT, 'seed': seed, 'role': role})[:16], 16))
        pair_count = training_pairs if role == 'training' else slice_pairs
        rows, records, specs = [], [], []
        attempts_total = 0
        for pair_index in range(pair_count):
            for _attempt in range(2000):
                attempts_total += 1
                base = _world(rng, role, pair_index)
                altered = rotate_hues(base, rng.randrange(1, 5))
                pair = (base, altered)
                structures = {digest(w.structural()) for w in pair}
                projected = {digest(w.projected_prior_structure()) for w in pair}
                world_hashes = {digest(w.as_dict()) for w in pair}
                if (len(structures) == len(projected) == len(world_hashes) == 2 and
                    structures.isdisjoint(excluded | seen_structures) and
                    projected.isdisjoint(excluded | seen_projected) and
                    world_hashes.isdisjoint(seen_worlds)):
                    break
            else:
                raise ChallengeError('exposure_disjoint_world_search_exhausted')
            seen_structures.update(structures);seen_projected.update(projected);seen_worlds.update(world_hashes)
            group = 'rx-world-'+digest(base.as_dict())[:24]
            _need(group not in seen_groups, 'duplicate_source_group');seen_groups.add(group)
            subject = sorted(n.name for n in base.nodes)[pair_index % 5]
            task_specs = _tasks(base, subject)
            order = [n.name for n in base.nodes];rng.shuffle(order)
            styles = (0, 1) if role == 'training' else ((2,) if role in ('new_composition', 'joint') else (pair_index % 2,))
            answer_pair = [[oracle(world, task) for task in task_specs] for world in pair]
            _need(all(a != b for a, b in zip(*answer_pair)), 'counterfactual_answer_did_not_change')
            for variant, world in enumerate(pair):
                records.append({'world_hash': digest(world.as_dict()), 'structural_hash': digest(world.structural()),
                    'projected_prior_structural_hash': digest(world.projected_prior_structure()),
                    'source_group': group, 'variant': variant, 'world': world.as_dict(),
                    'subject': subject, 'render_order': list(order), 'styles': list(styles)})
                for style in styles:
                    context = render(world, style, order)
                    candidates = {'hue': _options(HUES, [seed, role, group, variant, style, 'hue']),
                                  'entity': _options([n.name for n in world.nodes], [seed, role, group, variant, style, 'entity'])}
                    for task_index, task in enumerate(task_specs):
                        request_id = f'rx4-{role}-{len(rows):05d}'
                        kind = 'entity' if task['family'] == 'binding' else 'hue'
                        options = [dict(o) for o in candidates[kind]]
                        answer = oracle(world, task)
                        request = {'format': 'eidos-decision-request-v2', 'request_id': request_id,
                            'context': context, 'prompt': question(task, style), 'kind': 'CHOICE', 'options': options}
                        validate_request(request)
                        target = next(o['id'] for o in options if o['description'] == answer)
                        rows.append({'request': request, 'target_option_id': target, 'source_group': group})
                        specs.append({'request_id': request_id, 'source_group': group, 'world_hash': digest(world.as_dict()),
                            'task': dict(task), 'answer': answer, 'variant': variant, 'style': style,
                            'paired_task_key': f'{group}-style{style}-task{task_index}',
                            'contrast_set_id': f'{group}-v{variant}-style{style}-{kind}',
                            'depth_contrast_id': f'{group}-v{variant}-style{style}-pointsdepth' if task['family'] in ('property','points_forward_one','points_forward_two') else None,
                            'direction_contrast_id': f'{group}-v{variant}-style{style}-pointsdirection' if task['family'] in ('points_forward_one','points_incoming_one') else None,
                            'relation_contrast_id': f'{group}-v{variant}-style{style}-onerelation' if task['family'] in ('points_forward_one','cites_forward_one') else None})
                        if role == 'training':
                            training_lexemes.update(tokenize(context));training_lexemes.update(tokenize(request['prompt']))
            for style in styles:
                _need(Counter(tokenize(render(base, style, order))) == Counter(tokenize(render(altered, style, order))),
                      'counterfactual_token_multiset_changed')
        _need(len(rows) <= 2000, 'role_row_budget_exceeded')
        shortcut = {}
        for family in FAMILIES:
            selected = [(row, spec) for row, spec in zip(rows, specs) if spec['task']['family'] == family]
            shortcut[family] = {'answer_counts': _counts(spec['answer'] for row, spec in selected),
                'correct_display_rank_zero_based': _counts(next(i for i, o in enumerate(row['request']['options']) if o['id'] == row['target_option_id']) for row, spec in selected),
                'correct_description_length': _counts(len(spec['answer']) for row, spec in selected),
                'full_balance_or_shortcut_freedom_proven': False}
        role_audits[role] = {'rows': len(rows), 'relational_source_groups': pair_count, 'total_source_groups': pair_count,
            'relational_worlds': len(records), 'family_counts': _counts(s['task']['family'] for s in specs),
            'styles': sorted({s['style'] for s in specs}), 'entity_inventory': 'novel' if role in ('novel_entities','joint') else 'known',
            'all_relational_counterfactual_pair_task_answers_change': True,
            'world_search_attempts_hash_selection_only': attempts_total,
            'row_jsonl_sha256': hashlib.sha256(b''.join(canonical(row)+b'\n' for row in rows)).hexdigest(),
            'shortcut_diagnostics': shortcut,
            'maximum_context_tokens': max((len(tokenize(row['request']['context'])) for row in rows), default=0)}
        roles[role], worlds[role], tasks[role] = rows, records, specs
    _need(sum(map(len, roles.values())) <= 4000, 'total_row_budget_exceeded')
    known_entities = {name.casefold() for name in KNOWN_NAMES}
    novel_entities = {name.casefold() for name in NOVEL_NAMES}
    missing = {}
    for role in ROLES[1:]:
        observed = {token for row in roles[role] for text in (row['request']['context'],row['request']['prompt']) for token in tokenize(text)}
        # Semantic/operator lexemes must have supervised occurrence, even when
        # the role intentionally introduces unseen entity spellings.
        unseen_nonentities = observed - training_lexemes - known_entities - novel_entities
        _need(not unseen_nonentities, 'heldout_operator_lexeme_not_supervised')
        missing[role] = len(unseen_nonentities)
    audit = {'format': FORMAT, 'seed': seed, 'roles': role_audits,
        'exclusion_inventory_size': len(excluded), 'exclusion_inventory_sha256': digest(sorted(excluded)),
        'new_two_relation_and_old_projected_structure_hashes_excluded_for_both_variants': True,
        'exact_world_group_and_both_structures_disjoint_across_roles': True,
        'all_worlds_have_five_nodes_and_two_different_outgoing_cycle_destinations': True,
        'all_five_hue_bindings_rotate_counterfactually': True,
        'same_context_and_literal_options_for_question_only_hue_contrasts': True,
        'points_depth_zero_one_two_have_distinct_answers': True,
        'forward_incoming_and_relation_selector_one_step_answers_differ': True,
        'heldout_unseen_nonentity_operator_lexeme_counts': missing,
        'new_composition_is_known_lexemes_new_order_not_unknown_synonym_meaning': True,
        'all_task_heads': ['CHOICE'], 'BINARY_SCORE_semantic_quality_measured': False,
        'source_groups_must_not_be_split': True,
        'model_fit_inference_scoring_or_activation_performed': False,
        'pristine_real_world_or_LAYA_JEV_benchmark': False,
        'limits': ['Public finite two-relation five-cycle ontology, not arbitrary graphs or real language.',
                   'Counterfactuals, question contrasts and renderings are dependent within source group.',
                   'New composition uses supervised lexemes and is a different target from the prior unknown-wording challenge.',
                   'Projection exclusion checks exact name-erased ordered assignments, not graph-isomorphism independence.',
                   'Finite label/rank/length imbalances remain audited; no complete shortcut-freedom proof.',
                   'Relation and hue vocabularies are shared intentionally; all forecasts remain uncalibrated research outputs.']}
    return {'roles': roles, 'worlds': worlds, 'tasks': tasks, 'sequences': {role: [] for role in ROLES},
            'audit': audit, 'provenance': dict(PROVENANCE)}


def materialize(output, **settings):
    destination = Path(output).absolute()
    _need('..' not in destination.parts, 'parent_path_component_refused')
    _need(not destination.exists() and not destination.is_symlink(), 'output_already_exists')
    _need(destination.parent.is_dir(), 'output_parent_required')
    _need(not any(p.is_symlink() for p in (destination.parent, *destination.parent.parents)), 'symlinked_output_ancestor_refused')
    generated = generate(**settings)
    files = {f'{role}.jsonl': b''.join(canonical(row)+b'\n' for row in rows) for role, rows in generated['roles'].items() if rows}
    files.update({'provenance.json': canonical(generated['provenance'])+b'\n',
        'ORACLE.json': canonical({'format': FORMAT, 'worlds': generated['worlds'], 'tasks': generated['tasks'], 'sequences': generated['sequences']})+b'\n',
        'AUDIT.json': canonical(generated['audit'])+b'\n'})
    _need(sum(map(len, files.values())) <= MAX_OUTPUT_BYTES, 'output_byte_budget_exceeded')
    manifest = {'format': FORMAT, 'files': {name: {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in sorted(files.items())},
        'model_fit_inference_scoring_or_activation_performed': False, 'pristine_real_world_or_LAYA_JEV_benchmark': False}
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
        for path in written:path.unlink()
        destination.rmdir();raise
    return {'format': FORMAT, 'complete': True,
        'role_rows': {role: len(rows) for role,rows in generated['roles'].items()},
        'role_groups': {role: generated['audit']['roles'][role]['total_source_groups'] for role in ROLES},
        'file_count':len(files), 'manifest_sha256':hashlib.sha256(files['MANIFEST.json']).hexdigest(),
        'model_fit_inference_scoring_or_activation_performed':False,'pristine_real_world_or_LAYA_JEV_benchmark':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--training-pairs',type=int,default=4)
    parser.add_argument('--slice-pairs',type=int,default=2)
    parser.add_argument('--seed',type=int,default=47311)
    parser.add_argument('--exclude-hashes',help='Optional JSON list of opaque structural SHA256 strings only')
    args=parser.parse_args(argv)
    try:
        exclusions=() if args.exclude_hashes is None else strict_json(regular_bytes(args.exclude_hashes, MAX_EXCLUSION_BYTES), MAX_EXCLUSION_BYTES)
        result=materialize(args.output,training_pairs=args.training_pairs,slice_pairs=args.slice_pairs,seed=args.seed,excluded_structural_hashes=exclusions)
    except (ChallengeError,OSError,ValueError,StopIteration) as error:
        reason=str(error) if isinstance(error,ChallengeError) else 'routed_curriculum_write_or_validation_failed'
        print(json.dumps({'complete':False,'error':reason}),file=sys.stderr);return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':
    raise SystemExit(main())
