"""Exact authored curriculum checks; no model fits or quality predictions."""
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import random
import re
import tempfile
import unittest

from eidos import decision_curriculum as curriculum
from eidos.decision_contract import validate_request
from eidos.train_decision import parse_training


def parse_rendered(text):
    """Independent literal-language parser; never invokes the typed oracle."""
    patterns = (
        r'(?P<name>\w+) is (?P<color>\w+) and weighs (?P<mass>\d+) grams and follows (?P<successor>\w+)',
        r'(?P<name>\w+) follows (?P<successor>\w+) and weighs (?P<mass>\d+) grams and is (?P<color>\w+)',
        r'The (?P<color>\w+) person (?P<name>\w+) follows (?P<successor>\w+) and weighs (?P<mass>\d+) grams',
        r'(?P<name>\w+) follows (?P<successor>\w+) with (?P<mass>\d+) grams mass and (?P<color>\w+) coloring',
        r'(?P<name>\w+) has (?P<color>\w+) color and (?P<mass>\d+) grams mass following (?P<successor>\w+)',
        r'(?P<name>\w+) has mass (?P<mass>\d+) grams with (?P<color>\w+) color and follows (?P<successor>\w+)',
    )
    records = {}
    for sentence in text.rstrip('.').split('. '):
        matches = [re.fullmatch(pattern, sentence) for pattern in patterns]
        matches = [match for match in matches if match]
        if len(matches) != 1:
            raise AssertionError('literal context sentence is missing or ambiguous')
        row = matches[0].groupdict()
        row['mass'] = int(row['mass'])
        records[row['name']] = row
    if len(records) != 4:
        raise AssertionError('four explicit persons required')
    return records


def independent_answer(facts, spec):
    """Recompute all finite labels from parsed rendered facts and task spec."""
    task = spec['task']
    family = task['family']
    current = facts[task['subject']]
    if family == 'property':
        return current['color']
    if family == 'binding':
        return next(name for name, row in facts.items() if row['color'] == task['color'])
    if family == 'negation':
        return (current['color'] != task['color']) if task['negated'] else (current['color'] == task['color'])
    if family == 'comparison':
        names = [task['subject'], task['other']]
        return sorted(names, key=lambda name: facts[name]['mass'],
                      reverse=task['direction'] == 'heavier')[0]
    if family == 'two_step':
        name = task['subject']
        for _ in range(2):
            name = facts[name]['successor']
        return facts[name]['color']
    if family == 'score':
        return (current['mass'] - 1) // 3
    raise AssertionError('unknown family')


def answer_rendered_request(request):
    """Solve the actual rendered finite question without generator task specs.

    This is a test oracle for the authored language only. It is deliberately
    separate from the generator's template table and typed-world oracle.
    """
    facts = parse_rendered(request['context'])
    prompt = request['prompt'].casefold()
    names = {name.casefold(): name for name in facts}
    named = re.findall(r'\b(?:' + '|'.join(names) + r')\b', prompt)
    colors = re.findall(r'\b(?:amber|azure|ivory|ochre)\b', prompt)
    if request['kind'] == 'BINARY':
        proposition = re.search(r'\b(' + '|'.join(names) +
            r') is (not )?(amber|azure|ivory|ochre)\b', prompt)
        if proposition is None:
            raise AssertionError('binary prompt has no explicit proposition')
        name, negative, color = proposition.groups()
        equal = facts[names[name]]['color'] == color
        return not equal if negative else equal
    if request['kind'] == 'SCORE':
        if len(named) != 1 or 'mass' not in prompt:
            raise AssertionError('score prompt has no unique mass subject')
        mass = facts[names[named[0]]]['mass']
        containing = []
        for option in request['options']:
            interval = re.fullmatch(r'(\d+) to (\d+) grams', option['description'])
            if interval is None:
                raise AssertionError('missing explicit score rubric')
            low, high = map(int, interval.groups())
            if low <= mass <= high:
                containing.append(option['value'])
        if len(containing) != 1:
            raise AssertionError('mass rubric has overlap or missing range')
        return containing[0]
    if any(word in prompt for word in ('heavier', 'lighter', 'greater mass', 'smaller mass')):
        if len(named) != 2:
            raise AssertionError('comparison requires exactly two named people')
        reverse = 'heavier' in prompt or 'greater mass' in prompt
        return sorted((names[name] for name in named),
                      key=lambda name: facts[name]['mass'], reverse=reverse)[0]
    if any(phrase in prompt for phrase in ('follow twice', 'follow two edges', 'two follows links',
            'following again', "successor's successor", 'followed by the person')):
        if len(named) != 1:
            raise AssertionError('two-step query requires one starting person')
        name = names[named[0]]
        for _ in range(2):
            name = facts[name]['successor']
        return facts[name]['color']
    if colors:
        if len(colors) != 1 or named:
            raise AssertionError('binding query requires one color and no supplied answer name')
        return next(name for name, row in facts.items() if row['color'] == colors[0])
    if len(named) == 1 and ('color' in prompt or 'hue' in prompt):
        return facts[names[named[0]]]['color']
    raise AssertionError('rendered prompt is not in independently understood finite language')


class DecisionCurriculum(unittest.TestCase):
    def tiny(self, seed=113):
        return curriculum.generate(training_worlds=4, development_worlds=2,
                                   evaluation_worlds=2, seed=seed)

    def test_hand_computed_world_all_families_and_rendering_templates(self):
        world = curriculum.World((
            curriculum.Person('Aven', 'amber', 1, 'Bora'),
            curriculum.Person('Bora', 'azure', 4, 'Ciri'),
            curriculum.Person('Ciri', 'ivory', 7, 'Demu'),
            curriculum.Person('Demu', 'ochre', 10, 'Aven'),
        ))
        expected_facts = {
            'Aven': {'name': 'Aven', 'color': 'amber', 'mass': 1, 'successor': 'Bora'},
            'Bora': {'name': 'Bora', 'color': 'azure', 'mass': 4, 'successor': 'Ciri'},
            'Ciri': {'name': 'Ciri', 'color': 'ivory', 'mass': 7, 'successor': 'Demu'},
            'Demu': {'name': 'Demu', 'color': 'ochre', 'mass': 10, 'successor': 'Aven'},
        }
        cases = [
            ({'family': 'property', 'subject': 'Aven', 'template_index': 0}, 'amber'),
            ({'family': 'binding', 'subject': 'Aven', 'color': 'azure', 'template_index': 0}, 'Bora'),
            ({'family': 'negation', 'subject': 'Aven', 'color': 'azure', 'negated': True,
              'template_index': 0}, True),
            ({'family': 'comparison', 'subject': 'Aven', 'other': 'Demu',
              'direction': 'heavier', 'template_index': 0}, 'Demu'),
            ({'family': 'comparison', 'subject': 'Aven', 'other': 'Demu',
              'direction': 'lighter', 'template_index': 1}, 'Aven'),
            ({'family': 'two_step', 'subject': 'Aven', 'template_index': 0}, 'ivory'),
            ({'family': 'score', 'subject': 'Demu', 'template_index': 0}, 3),
        ]
        for role in curriculum.ROLES:
            for template_index in range(len(curriculum.CONTEXT_TEMPLATES[role])):
                context = curriculum.render(world, role, template_index,
                                             ['Demu', 'Ciri', 'Bora', 'Aven'])
                self.assertEqual(parse_rendered(context), expected_facts)
                for index, (task, expected) in enumerate(cases):
                    with self.subTest(role=role, context_template=template_index, family=task['family']):
                        self.assertEqual(curriculum.oracle(world, task), expected)
                        request, _, answer = curriculum._request(world, task, role, context,
                            f'hand-{role}-{template_index}-{index}', random.Random(113))
                        self.assertEqual(answer, expected)
                        self.assertEqual(answer_rendered_request(request), expected)

    def test_default_shape_families_and_true_false_balance(self):
        generated = curriculum.generate()
        self.assertEqual({role: len(rows) for role, rows in generated['roles'].items()},
                         {'training': 360, 'development': 72, 'evaluation': 72})
        for role, audit in generated['audit']['roles'].items():
            rows = generated['roles'][role]
            self.assertEqual(set(audit['family_counts']), set(curriculum.FAMILIES))
            self.assertEqual(len(set(audit['family_counts'].values())), 1)
            self.assertEqual(set(audit['binary_polarity_truth_counts'].values()), {len(rows) // 24})
            self.assertFalse(audit['shortcut_diagnostics']['score']['shortcut_freedom_or_full_balance_proven'])
        self.assertFalse(generated['audit']['independently_sealed_real_world_or_LAYA_JEV_benchmark'])

    def test_generated_requests_and_exact_trainer_schema(self):
        generated = self.tiny()
        for role, rows in generated['roles'].items():
            raw = b''.join(curriculum.canonical(row) + b'\n' for row in rows)
            parsed, provenance = parse_training(raw, curriculum.canonical(generated['provenance']))
            self.assertEqual(len(parsed), len(rows))
            self.assertEqual(set(provenance), {'source', 'license', 'citation', 'rights_basis'})
            for row in rows:
                request = validate_request(row['request'])
                self.assertIn(row['target_option_id'], {o.id for o in request.options})
                self.assertEqual(set(row), {'request', 'target_option_id', 'source_group'})

    def test_every_label_independently_recomputed_from_rendered_facts(self):
        generated = self.tiny()
        for role, rows in generated['roles'].items():
            specs = {spec['request_id']: spec for spec in generated['tasks'][role]}
            for row in rows:
                request = row['request']
                spec = specs[request['request_id']]
                answer = independent_answer(parse_rendered(request['context']), spec)
                self.assertEqual(answer_rendered_request(request), answer)
                self.assertEqual(answer, spec['oracle_answer'])
                chosen = next(o for o in request['options'] if o['id'] == row['target_option_id'])
                if request['kind'] == 'BINARY':
                    self.assertEqual(chosen['id'] == 'true', answer)
                elif request['kind'] == 'SCORE':
                    self.assertEqual(chosen['value'], answer)
                    self.assertEqual(sorted(o['value'] for o in request['options']), [0, 1, 2, 3])
                else:
                    self.assertEqual(chosen['description'], answer)

    def test_counterfactual_pairs_share_group_and_token_multiset(self):
        generated = self.tiny()
        for role, rows in generated['roles'].items():
            for start in range(0, len(rows), 24):
                first, second = rows[start:start+12], rows[start+12:start+24]
                self.assertEqual(len({row['source_group'] for row in first + second}), 1)
                self.assertEqual(Counter(first[0]['request']['context'].split()),
                                 Counter(second[0]['request']['context'].split()))
                self.assertNotEqual(first[0]['request']['context'], second[0]['request']['context'])
                for a, b in zip(first, second):
                    self.assertEqual(a['request']['prompt'], b['request']['prompt'])
                for index in (0, 1, 2, 3, 4, 5, 6, 7, 10, 11):
                    a, b = first[index], second[index]
                    def answer(row):
                        chosen = next(o for o in row['request']['options'] if o['id'] == row['target_option_id'])
                        return chosen.get('value', chosen['description'])
                    self.assertNotEqual(answer(a), answer(b))

    def test_role_entity_groups_worlds_and_templates_are_disjoint(self):
        generated = self.tiny()
        for index, role in enumerate(curriculum.ROLES):
            for other in curriculum.ROLES[index+1:]:
                first, second = generated['worlds'][role], generated['worlds'][other]
                for key in ('world_sha256', 'structural_sha256', 'source_group'):
                    self.assertTrue({w[key] for w in first}.isdisjoint(w[key] for w in second))
                self.assertTrue(set(curriculum.NAMES[role]).isdisjoint(curriculum.NAMES[other]))
                self.assertTrue(set(curriculum.CONTEXT_TEMPLATES[role]).isdisjoint(curriculum.CONTEXT_TEMPLATES[other]))
                a = {q for variants in curriculum.QUESTIONS[role].values() for q in variants}
                b = {q for variants in curriculum.QUESTIONS[other].values() for q in variants}
                self.assertTrue(a.isdisjoint(b))

    def test_same_role_generation_is_deterministic_and_count_independent(self):
        first = self.tiny()
        again = self.tiny()
        self.assertEqual(first, again)
        changed = curriculum.generate(training_worlds=6, development_worlds=2,
                                      evaluation_worlds=2, seed=113)
        # Independent role RNGs preserve downstream roles unless an extremely
        # rare anonymized-world collision forces resampling; fixture has none.
        self.assertEqual(first['roles']['development'], changed['roles']['development'])
        self.assertNotEqual(first['roles']['training'], self.tiny(seed=114)['roles']['training'])

    def test_targets_have_distractors_and_no_oracle_sidecar_enters_requests(self):
        generated = self.tiny()
        for role, rows in generated['roles'].items():
            for row in rows:
                request = row['request']
                facts = parse_rendered(request['context'])
                self.assertFalse({'family', 'world', 'oracle_answer', 'target_option_id'} & set(request))
                if request['kind'] == 'CHOICE':
                    for option in request['options']:
                        self.assertIn(option['description'], set(facts) | {r['color'] for r in facts.values()})
                self.assertLessEqual(len(request['options']), 4)

    def test_option_identity_and_order_do_not_change_independent_gold(self):
        generated = self.tiny()
        row = copy.deepcopy(generated['roles']['training'][0])
        spec = generated['tasks']['training'][0]
        before = independent_answer(parse_rendered(row['request']['context']), spec)
        row['request']['options'].reverse()
        for i, option in enumerate(row['request']['options']):
            option['id'] = f'new-{i}'
        validate_request(row['request'])
        self.assertEqual(before, independent_answer(parse_rendered(row['request']['context']), spec))

    def test_invalid_settings_refused_without_output(self):
        for settings in ({'seed': True}, {'seed': -1}, {'training_worlds': 1},
                         {'development_worlds': True}, {'evaluation_worlds': 168},
                         {'training_worlds': 166, 'development_worlds': 166}):
            with self.subTest(settings=settings), self.assertRaises(curriculum.CurriculumError):
                curriculum.generate(**settings)

    def test_private_materialization_manifest_hashes_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'new'
            receipt = curriculum.materialize(root, training_worlds=2,
                                             development_worlds=0, evaluation_worlds=0)
            self.assertTrue(receipt['complete'])
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            manifest = json.loads((root / 'MANIFEST.json').read_text())
            self.assertEqual(set(manifest['files']), {'training.jsonl', 'ORACLE.json', 'AUDIT.json', 'provenance.json'})
            for name, pin in manifest['files'].items():
                path = root / name
                raw = path.read_bytes()
                self.assertEqual(pin, {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
                self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            with self.assertRaisesRegex(curriculum.CurriculumError, 'output_already_exists'):
                curriculum.materialize(root)

    def test_symlink_ancestor_and_parent_escape_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / 'actual'
            actual.mkdir()
            (root / 'alias').symlink_to(actual, target_is_directory=True)
            with self.assertRaisesRegex(curriculum.CurriculumError, 'symlinked_output_ancestor_refused'):
                curriculum.materialize(root / 'alias' / 'new')
            with self.assertRaisesRegex(curriculum.CurriculumError, 'parent_path_component_refused'):
                curriculum.materialize(root / 'actual' / '..' / 'new')


if __name__ == '__main__':
    unittest.main()
