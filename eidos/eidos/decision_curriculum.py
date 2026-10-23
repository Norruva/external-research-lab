"""Author bounded original synthetic supervision; never train or score a model.

This finite four-person language is a curriculum and challenge fixture, not a
pristine benchmark or evidence of general language, LAYA or JEV performance.
Counterfactual worlds stay grouped. Public templates and a public generator
cannot establish an independently sealed real-world holdout.
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

FORMAT = 'eidos-original-decision-curriculum-v1'
ROLES = ('training', 'development', 'evaluation')
FAMILIES = ('property', 'binding', 'negation', 'comparison', 'two_step', 'score')
NAMES = {'training': ('Aven', 'Bora', 'Ciri', 'Demu'),
         'development': ('Elvi', 'Faro', 'Gima', 'Hunu'),
         'evaluation': ('Iset', 'Jora', 'Kavi', 'Lumo')}
COLORS = ('amber', 'azure', 'ivory', 'ochre')
INTERVALS = ((1, 3), (4, 6), (7, 9), (10, 12))
MAX_WORLDS_PER_ROLE = 166
MAX_TOTAL_WORLDS = 240
MAX_OUTPUT_BYTES = 16 * 1024**2
CONTEXT_TEMPLATES = {
    'training': ('{name} is {color} and weighs {mass} grams and follows {successor}.',
                 '{name} follows {successor} and weighs {mass} grams and is {color}.'),
    'development': ('The {color} person {name} follows {successor} and weighs {mass} grams.',
                    '{name} follows {successor} with {mass} grams mass and {color} coloring.'),
    'evaluation': ('{name} has {color} color and {mass} grams mass following {successor}.',
                   '{name} has mass {mass} grams with {color} color and follows {successor}.'),
}
QUESTIONS = {
    'training': {
        'property': ('What color is {subject}?', 'Identify the color of {subject}.'),
        'binding': ('Who is {color}?', 'Which person has color {color}?'),
        'negation': ('Is it true that {proposition}?', 'Is this correct: {proposition}?'),
        'comparison': ('Who is heavier: {subject} or {other}?',
                       'Who is lighter: {subject} or {other}?'),
        'two_step': ('What color is the person followed by the person {subject} follows?',
                     'Starting at {subject}, follow twice. What color is that person?'),
        'score': ("Which mass interval contains {subject}?",
                  'Rate the mass of {subject} using the offered intervals.'),
    },
    'development': {
        'property': ("State {subject}'s color.", 'Which color belongs to {subject}?'),
        'binding': ('Name the person whose color is {color}.',
                    'The {color} color identifies which person?'),
        'negation': ('Evaluate this claim: {proposition}. Is it correct?',
                     'Does the record support that {proposition}?'),
        'comparison': ('Between {subject} and {other}, select the person with greater mass.',
                       'Between {subject} and {other}, select the person with smaller mass.'),
        'two_step': ("Find {subject}'s successor's successor. Return that person's color.",
                     'Follow two edges from {subject}. Select the destination color.'),
        'score': ("Select the offered mass range covering {subject}.",
                  "Map {subject}'s mass to its displayed interval."),
    },
    'evaluation': {
        'property': ('Select the hue assigned to {subject}.',
                     "Return {subject}'s recorded color."),
        'binding': ('Select the individual recorded as {color}.',
                    'Which named individual is colored {color}?'),
        'negation': ("Should '{proposition}' be marked true?",
                     "Do the stated facts make '{proposition}' true?"),
        'comparison': ('Choose the heavier individual among {subject} and {other}.',
                       'Choose the lighter individual among {subject} and {other}.'),
        'two_step': ('After two follows links beginning at {subject}, identify the final color.',
                     'Which color is reached by following from {subject} and following again?'),
        'score': ('Choose the mass bracket into which {subject} falls.',
                  'Assign {subject} to the appropriate displayed mass bracket.'),
    },
}
PROVENANCE = {
    'source': 'Original finite relational worlds, language templates and exact synthetic task labels in decision_curriculum.py',
    'license': 'Original authored synthetic material; no third-party material or new blanket public license grant',
    'citation': 'EIDOS original relational decision curriculum v1, 2026',
    'rights_basis': 'Authored without external corpus, vendor output or pretrained checkpoint; declaration is not an exclusive-ownership or production-data rights certificate',
}


class CurriculumError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _need(condition, reason):
    if not condition:
        raise CurriculumError(reason)


@dataclass(frozen=True)
class Person:
    name: str
    color: str
    mass: int
    successor: str


@dataclass(frozen=True)
class World:
    people: tuple[Person, ...]

    def as_dict(self):
        return {'people': [dict(name=p.name, color=p.color, mass=p.mass,
                                successor=p.successor)
                           for p in sorted(self.people, key=lambda p: p.name)]}

    def person(self, name):
        return next(p for p in self.people if p.name == name)

    def structural(self):
        """Erase proper names to detect equal assignment/edge skeletons."""
        ordered = sorted(self.people, key=lambda p: p.name)
        positions = {p.name: i for i, p in enumerate(ordered)}
        return [(p.color, p.mass, positions[p.successor]) for p in ordered]


def _world(rng, role):
    names = list(NAMES[role])
    colors = list(COLORS)
    masses = [rng.randint(low, high) for low, high in INTERVALS]
    cycle = list(names)
    rng.shuffle(colors)
    rng.shuffle(masses)
    rng.shuffle(cycle)
    successors = dict(zip(cycle, cycle[1:] + cycle[:1]))
    return World(tuple(Person(name, color, mass, successors[name])
                       for name, color, mass in zip(names, colors, masses)))


def counterfactual(world, first, second):
    """Swap two people's color/mass only; preserve names and relation edges."""
    _need(first != second, 'counterfactual_requires_distinct_people')
    a, b = world.person(first), world.person(second)
    return World(tuple(Person(p.name, b.color, b.mass, p.successor) if p.name == first
                       else Person(p.name, a.color, a.mass, p.successor) if p.name == second
                       else p for p in world.people))


def oracle(world, task):
    """Compute an answer from typed facts, never rendered text, IDs or order."""
    family = task['family']
    subject = world.person(task['subject'])
    if family == 'property':
        return subject.color
    if family == 'binding':
        return next(p.name for p in world.people if p.color == task['color'])
    if family == 'negation':
        equal = subject.color == task['color']
        return not equal if task['negated'] else equal
    if family == 'comparison':
        other = world.person(task['other'])
        if task['direction'] == 'heavier':
            return subject.name if subject.mass > other.mass else other.name
        return subject.name if subject.mass < other.mass else other.name
    if family == 'two_step':
        return world.person(world.person(subject.successor).successor).color
    if family == 'score':
        return next(index for index, (low, high) in enumerate(INTERVALS)
                    if low <= subject.mass <= high)
    raise CurriculumError('unknown_task_family')


def _tasks(world, actors, rng):
    a, b = actors[:2]
    negated_first = bool(rng.getrandbits(1))
    alleged = world.person(a).color
    tasks = []
    for family in FAMILIES:
        for index, subject in enumerate((a, b)):
            task = {'family': family, 'subject': subject, 'template_index': index}
            if family == 'binding':
                task['color'] = world.person(subject).color
            elif family == 'negation':
                task.update(subject=a, color=alleged,
                            negated=negated_first if index == 0 else not negated_first)
            elif family == 'comparison':
                task.update(subject=a, other=b,
                            direction=('heavier', 'lighter')[index])
            tasks.append(task)
    return tasks


def render(world, role, template_index, order):
    """Render every complete person's facts in one explicit-subject sentence."""
    template = CONTEXT_TEMPLATES[role][template_index]
    return ' '.join(template.format(**world.person(name).__dict__) for name in order)


def _request(world, task, role, context, request_id, rng):
    answer = oracle(world, task)
    family = task['family']
    substitutions = dict(task)
    if family == 'negation':
        substitutions['proposition'] = (task['subject'] + ' is ' +
                                       ('not ' if task['negated'] else '') + task['color'])
    prompt = QUESTIONS[role][family][task['template_index']].format(**substitutions)
    if family == 'negation':
        kind = 'BINARY'
        options = [{'id': 'false', 'description': 'The proposition is false.'},
                   {'id': 'true', 'description': 'The proposition is true.'}]
        target = 'true' if answer else 'false'
    else:
        kind = 'SCORE' if family == 'score' else 'CHOICE'
        if family in ('property', 'two_step'):
            candidates = list(COLORS)
        elif family == 'binding':
            candidates = sorted(p.name for p in world.people)
        elif family == 'comparison':
            candidates = [task['subject'], task['other']]
        else:
            candidates = list(range(len(INTERVALS)))
        identifiers = rng.sample(range(1_000_000, 9_999_999), len(candidates))
        options, target = [], None
        for value, ident in zip(candidates, identifiers):
            if family == 'score':
                low, high = INTERVALS[value]
                row = {'id': f'c{ident}', 'description': f'{low} to {high} grams', 'value': value}
            else:
                row = {'id': f'c{ident}', 'description': value}
            options.append(row)
            if value == answer:
                target = row['id']
        _need(target is not None, 'oracle_answer_not_offered')
    rng.shuffle(options)
    request = {'format': 'eidos-decision-request-v2', 'request_id': request_id,
               'context': context, 'prompt': prompt, 'kind': kind, 'options': options}
    if kind == 'SCORE':
        request.update(minimum=0, maximum=3)
    validate_request(request)
    return request, target, answer


def _distribution(values):
    return dict(sorted(Counter(str(v) for v in values).items()))


def generate(training_worlds=30, development_worlds=6, evaluation_worlds=6, seed=113):
    """Return generated roles plus audit/oracle sidecars; perform no I/O."""
    counts = dict(zip(ROLES, (training_worlds, development_worlds, evaluation_worlds)))
    _need(type(seed) is int and 0 <= seed <= 2**31 - 1, 'seed_outside_bounds')
    for role, count in counts.items():
        _need(type(count) is int and 0 <= count <= MAX_WORLDS_PER_ROLE and count % 2 == 0,
              'world_count_requires_even_bounded_integer')
        _need(role != 'training' or count >= 2, 'training_requires_counterfactual_pair')
    _need(sum(counts.values()) <= MAX_TOTAL_WORLDS, 'total_world_budget_exceeded')
    roles, worlds, task_records, structural_hashes = {}, {}, {}, set()
    role_audits = {}
    for role, count in counts.items():
        role_seed = int(digest({'seed': seed, 'role': role})[:16], 16)
        rng = random.Random(role_seed)
        rows, ledger, specs, pairs, semantics = [], [], [], [], set()
        for group_index in range(count // 2):
            for _attempt in range(1000):
                base = _world(rng, role)
                actors = list(NAMES[role])
                rng.shuffle(actors)
                altered = counterfactual(base, *actors[:2])
                hashes = {digest(w.structural()) for w in (base, altered)}
                if len(hashes) == 2 and hashes.isdisjoint(structural_hashes):
                    structural_hashes.update(hashes)
                    break
            else:
                raise CurriculumError('unique_world_search_exhausted')
            group = 'world-pair-' + digest(base.as_dict())[:24]
            tasks = _tasks(base, actors, rng)
            order = list(NAMES[role])
            rng.shuffle(order)
            context_index = rng.randrange(len(CONTEXT_TEMPLATES[role]))
            contexts = []
            for variant, world in enumerate((base, altered)):
                ordinal = group_index * 2 + variant
                world_hash = digest(world.as_dict())
                context = render(world, role, context_index, order)
                contexts.append(context)
                ledger.append({'world_sha256': world_hash, 'structural_sha256': digest(world.structural()),
                    'source_group': group, 'counterfactual_variant': variant,
                    'context_template': f'{role}-{context_index}', 'render_order': order,
                    'world': world.as_dict()})
                for task_index, task in enumerate(tasks):
                    request_id = f'{role}-{ordinal:04d}-{task_index:02d}'
                    # Administrative option sampling is independent of oracle values.
                    option_rng = random.Random(int(digest({'seed': seed, 'request_id': request_id,
                                                           'purpose': 'option-identities-order'})[:16], 16))
                    request, target, answer = _request(world, task, role, context, request_id, option_rng)
                    semantic = digest({'context': context, 'prompt': request['prompt'],
                        'kind': request['kind'], 'candidates': sorted(o['description'] for o in request['options'])})
                    _need(semantic not in semantics, 'duplicate_semantic_task')
                    semantics.add(semantic)
                    rows.append({'request': request, 'target_option_id': target, 'source_group': group})
                    specs.append({'request_id': request_id, 'world_sha256': world_hash,
                                  'source_group': group, 'task': task, 'oracle_answer': answer,
                                  'question_template': f'{role}-{task["family"]}-{task["template_index"]}'})
            _need(Counter(contexts[0].split()) == Counter(contexts[1].split()),
                  'counterfactual_token_multiset_mismatch')
            pairs.append({'source_group': group, 'context_token_multiset_equal': True,
                          'worlds': 2, 'all_pair_tasks_stay_in_same_role': True})
        roles[role], worlds[role], task_records[role] = rows, ledger, specs
        by_family = {family: [i for i, spec in enumerate(specs) if spec['task']['family'] == family]
                     for family in FAMILIES}
        shortcuts = {}
        for family, indices in by_family.items():
            observations = []
            for i in indices:
                row = rows[i]
                candidates = row['request']['options']
                correct = next(o for o in candidates if o['id'] == row['target_option_id'])
                observations.append((str(specs[i]['oracle_answer']), candidates.index(correct),
                                     len(correct['description']), len(candidates)))
            shortcuts[family] = {'gold_answer_counts': _distribution(x[0] for x in observations),
                'correct_display_rank_counts_zero_based': _distribution(x[1] for x in observations),
                'correct_description_length_counts': _distribution(x[2] for x in observations),
                'candidate_count_counts': _distribution(x[3] for x in observations),
                'shortcut_freedom_or_full_balance_proven': False}
        binary = [spec for spec in specs if spec['task']['family'] == 'negation']
        role_audits[role] = {'rows': len(rows), 'worlds': len(ledger), 'source_groups': len(pairs),
            'family_counts': {family: len(indices) for family, indices in by_family.items()},
            'kind_counts': _distribution(row['request']['kind'] for row in rows),
            'binary_polarity_truth_counts': _distribution(
                f'{"negated" if s["task"]["negated"] else "positive"}:{str(s["oracle_answer"]).lower()}' for s in binary),
            'counterfactual_pairs': pairs, 'shortcut_diagnostics': shortcuts,
            'max_context_words': max((len(row['request']['context'].split()) for row in rows), default=0),
            'role_jsonl_sha256': hashlib.sha256(b''.join(canonical(row) + b'\n' for row in rows)).hexdigest(),
            'worlds_sha256': digest(ledger), 'tasks_sha256': digest(specs)}
    for first_index, first in enumerate(ROLES):
        for second in ROLES[first_index + 1:]:
            _need(set(NAMES[first]).isdisjoint(NAMES[second]), 'entity_lexeme_overlap')
            _need({w['source_group'] for w in worlds[first]}.isdisjoint(w['source_group'] for w in worlds[second]),
                  'cross_role_group_overlap')
            _need(set(CONTEXT_TEMPLATES[first]).isdisjoint(CONTEXT_TEMPLATES[second]), 'context_template_overlap')
            first_q = {q for values in QUESTIONS[first].values() for q in values}
            second_q = {q for values in QUESTIONS[second].values() for q in values}
            _need(first_q.isdisjoint(second_q), 'question_template_overlap')
    audit = {'format': FORMAT, 'seed': seed, 'roles': role_audits,
        'world_group_and_anonymized_structural_hashes_pairwise_disjoint_across_roles': True,
        'entity_proper_name_inventories_pairwise_disjoint': True,
        'all_lexical_tokens_or_domains_disjoint': False,
        'context_render_and_question_literal_templates_pairwise_disjoint': True,
        'world_labels_computed_from_typed_facts_not_candidate_IDs_or_positions': True,
        'counterfactual_pairs_are_grouped_not_split': True,
        'independently_sealed_real_world_or_LAYA_JEV_benchmark': False,
        'rights_independence_semantic_coverage_or_general_quality_certified': False,
        'training_inference_or_model_scoring_performed': False,
        'limits': ['Finite four-person English synthetic language and public templates.',
                   'Two-step questions concern only two follows links in a four-cycle.',
                   'Mass scoring uses four explicit finite intervals, not continuous-score quality.',
                   'Option ID/order is randomized; finite label/rank/length imbalances remain audited.',
                   'Role shifts test this generator only, not external domains or pristine generalization.']}
    return {'roles': roles, 'worlds': worlds, 'tasks': task_records,
            'audit': audit, 'provenance': dict(PROVENANCE)}


def materialize(output, **settings):
    """Write a NEW private directory; refuse existing or symlinked paths."""
    destination = Path(output).absolute()
    _need('..' not in destination.parts, 'parent_path_component_refused')
    _need(not destination.exists() and not destination.is_symlink(), 'output_already_exists')
    _need(destination.parent.is_dir(), 'output_parent_required')
    _need(not any(p.is_symlink() for p in (destination.parent, *destination.parent.parents)),
          'symlinked_output_ancestor_refused')
    generated = generate(**settings)
    files = {f'{role}.jsonl': b''.join(canonical(row) + b'\n' for row in rows)
             for role, rows in generated['roles'].items() if rows}
    files.update({'provenance.json': canonical(generated['provenance']) + b'\n',
        'ORACLE.json': canonical({'format': FORMAT, 'worlds': generated['worlds'],
                                 'tasks': generated['tasks']}) + b'\n',
        'AUDIT.json': canonical(generated['audit']) + b'\n'})
    _need(sum(map(len, files.values())) <= MAX_OUTPUT_BYTES, 'output_byte_budget_exceeded')
    manifest = {'format': FORMAT, 'files': {name: {'bytes': len(raw),
        'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in sorted(files.items())},
        'model_fitted_scored_or_activated': False,
        'evaluation_is_public_generator_synthetic_not_sealed_real_benchmark': True}
    files['MANIFEST.json'] = canonical(manifest) + b'\n'
    destination.mkdir(mode=0o700)
    written = []
    try:
        for name, raw in files.items():
            path = destination / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
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
            'role_groups': {role: len(rows) // 24 for role, rows in generated['roles'].items()},
            'manifest_sha256': hashlib.sha256(files['MANIFEST.json']).hexdigest(),
            'file_count': len(files), 'training_inference_or_model_scoring_performed': False,
            'independently_sealed_real_world_or_LAYA_JEV_benchmark': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--training-worlds', type=int, default=30)
    parser.add_argument('--development-worlds', type=int, default=6)
    parser.add_argument('--evaluation-worlds', type=int, default=6)
    parser.add_argument('--seed', type=int, default=113)
    args = parser.parse_args(argv)
    try:
        result = materialize(args.output, training_worlds=args.training_worlds,
            development_worlds=args.development_worlds, evaluation_worlds=args.evaluation_worlds,
            seed=args.seed)
    except (CurriculumError, OSError, ValueError) as error:
        code = str(error) if isinstance(error, CurriculumError) else 'curriculum_write_or_validation_failed'
        print(json.dumps({'complete': False, 'error': code}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
