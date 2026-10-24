"""Independent finite-language label checks; no training or model execution."""
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest

from eidos import contextual_challenge as challenge
from eidos.decision_contract import validate_request


def parse_facts(context):
    """Parse the authored literal language without generator task/oracle data."""
    patterns = (
        r'(?P<name>\w+) has hue (?P<hue>\w+) and points to (?P<successor>\w+)',
        r'(?P<name>\w+) points to (?P<successor>\w+) and has hue (?P<hue>\w+)',
        r'Node (?P<name>\w+) carries (?P<hue>\w+) hue and links onward to (?P<successor>\w+)',
        r'Node (?P<name>\w+) links onward to (?P<successor>\w+) while carrying (?P<hue>\w+) hue',
    )
    facts = {}
    for sentence in context.rstrip('.').split('. '):
        matches = [m for pattern in patterns if (m := re.fullmatch(pattern, sentence))]
        if len(matches) != 1:
            raise AssertionError('context has no unique literal parse')
        record = matches[0].groupdict()
        if record['name'] in facts:
            raise AssertionError('duplicate entity fact')
        facts[record['name']] = record
    if len(facts) not in (3, 5) or len({r['hue'] for r in facts.values()}) != len(facts):
        raise AssertionError('expected unique three/five-node hue assignment')
    if {r['successor'] for r in facts.values()} != set(facts):
        raise AssertionError('directed successors must be a permutation')
    return facts


def solve_rendered(request):
    """Solve context and actual prompt, without generator templates/task specs."""
    prompt = request['prompt'].casefold()
    if prompt in {'which symbol appears first?', 'select the first symbol.',
                  'return the opening symbol.', 'name the leading symbol.'}:
        tokens = request['context'].rstrip('.').split()
        if len(tokens) != 4:
            raise AssertionError('first-symbol challenge must have four tokens')
        return tokens[0]
    facts = parse_facts(request['context'])
    by_lower = {name.casefold(): name for name in facts}
    patterns = (
        (r'what hue does (\w+) have\?', 'property', 0),
        (r'select the hue of (\w+)\.', 'property', 0),
        (r'return the shade carried by (\w+)\.', 'property', 0),
        (r"identify (\w+)'s recorded shade\.", 'property', 0),
        (r'which node has hue (\w+)\?', 'binding', 0),
        (r'select the node with hue (\w+)\.', 'binding', 0),
        (r'name the node carrying shade (\w+)\.', 'binding', 0),
        (r'the shade (\w+) belongs to which node\?', 'binding', 0),
        (r'follow one points-to link from (\w+)\. what hue is there\?', 'walk', 1),
        (r'what hue does the node (\w+) points to have\?', 'walk', 1),
        (r'from (\w+), move onward once and return the shade\.', 'walk', 1),
        (r"return the shade at (\w+)'s immediate link destination\.", 'walk', 1),
        (r'follow two points-to links from (\w+)\. what hue is there\?', 'walk', 2),
        (r'what hue does the node reached after two points-to links from (\w+) have\?', 'walk', 2),
        (r'from (\w+), move onward twice and return the shade\.', 'walk', 2),
        (r'return the shade after two outward links starting at (\w+)\.', 'walk', 2),
    )
    matches = [(m.group(1), kind, steps) for pattern, kind, steps in patterns
               if (m := re.fullmatch(pattern, prompt))]
    if len(matches) != 1:
        raise AssertionError('question has no unique independent literal parse')
    value, kind, steps = matches[0]
    if kind == 'binding':
        found = [name for name, row in facts.items() if row['hue'] == value]
        if len(found) != 1:
            raise AssertionError('hue does not identify one entity')
        return found[0]
    current = by_lower[value]
    for _ in range(steps):
        current = facts[current]['successor']
    return facts[current]['hue']


def offered_gold(row):
    return next(option['description'] for option in row['request']['options']
                if option['id'] == row['target_option_id'])


class ContextualChallengeTests(unittest.TestCase):
    def tiny(self):
        return challenge.generate(training_pairs=1, slice_pairs=1, ordering_pairs=1)

    def test_default_counts_axes_and_all_rendered_labels(self):
        generated = challenge.generate()
        self.assertEqual({r: len(v) for r, v in generated['roles'].items()},
                         {'training': 72, 'fresh_world': 36, 'novel_entities': 36,
                          'new_wording': 36, 'joint': 36})
        self.assertEqual({r: a['total_source_groups'] for r, a in generated['audit']['roles'].items()},
                         {'training': 8, 'fresh_world': 4, 'novel_entities': 4,
                          'new_wording': 4, 'joint': 4})
        for role, rows in generated['roles'].items():
            specs = {spec['request_id']: spec for spec in generated['tasks'][role]}
            for row in rows:
                self.assertEqual(solve_rendered(row['request']), offered_gold(row))
                self.assertEqual(offered_gold(row), specs[row['request']['request_id']]['answer'])
                self.assertEqual(set(row), {'request', 'target_option_id', 'source_group'})
                self.assertEqual(validate_request(row['request']).kind, 'CHOICE')
        self.assertFalse(generated['audit']['pristine_real_world_or_LAYA_JEV_benchmark'])
        self.assertFalse(generated['audit']['BINARY_SCORE_semantic_quality_measured'])

    def test_hand_world_all_renderers_and_question_forms(self):
        names = ('Nexal', 'Pevin', 'Rukor', 'Sadil', 'Tezum')
        hues = ('coral', 'denim', 'hazel', 'mauve', 'sepia')
        world = challenge.World(tuple(challenge.Node(name, hues[index], names[(index+1) % 5])
                                      for index, name in enumerate(names)))
        cases = (({'family': 'property', 'subject': 'Nexal'}, 'coral'),
                 ({'family': 'binding', 'hue': 'denim'}, 'Pevin'),
                 ({'family': 'one_hop', 'subject': 'Nexal'}, 'denim'),
                 ({'family': 'two_hop', 'subject': 'Nexal'}, 'hazel'))
        for wording in ('known', 'shifted'):
            for context_index in range(2):
                context = challenge.render(world, wording, context_index, tuple(reversed(names)))
                self.assertEqual(set(parse_facts(context)), set(names))
                for task, expected in cases:
                    self.assertEqual(challenge.oracle(world, task), expected)
                    for question_index in range(2):
                        prompt = challenge.QUESTIONS[wording][task['family']][question_index].format(**task)
                        request = {'context': context, 'prompt': prompt}
                        self.assertEqual(solve_rendered(request), expected)

    def test_three_and_five_cycles_discriminate_relation_direction(self):
        for size in (3, 5):
            names = challenge.KNOWN_NAMES[:size]
            world = challenge.World(tuple(challenge.Node(name, challenge.HUES[index], names[(index+1) % size])
                                          for index, name in enumerate(names)))
            forward = challenge.oracle(world, {'family': 'two_hop', 'subject': names[0]})
            # Independently walk incoming edges twice, rather than invoking oracle.
            current = names[0]
            for _ in range(2):
                current = next(node.name for node in world.nodes if node.successor == current)
            backward = world.node(current).hue
            self.assertEqual(forward, challenge.HUES[2])
            self.assertNotEqual(forward, backward)
        generated = challenge.generate()
        for role in challenge.ROLES:
            self.assertEqual(set(generated['audit']['roles'][role]['relation_graph_sizes']), {'3', '5'})

    def test_all_relation_counterfactual_answers_flip_with_same_word_multiset(self):
        generated = challenge.generate()
        for role, rows in generated['roles'].items():
            pairs = generated['audit']['roles'][role]['relational_source_groups']
            for start in range(0, 16*pairs, 16):
                first, second = rows[start:start+8], rows[start+8:start+16]
                self.assertEqual(len({r['source_group'] for r in first+second}), 1)
                self.assertEqual(Counter(first[0]['request']['context'].split()),
                                 Counter(second[0]['request']['context'].split()))
                self.assertNotEqual(first[0]['request']['context'], second[0]['request']['context'])
                for before, after in zip(first, second):
                    self.assertEqual(before['request']['prompt'], after['request']['prompt'])
                    self.assertNotEqual(offered_gold(before), offered_gold(after))

    def test_sequence_pairs_keep_counts_and_first_moments_but_change_first(self):
        generated = self.tiny()
        for role, records in generated['sequences'].items():
            for index in range(0, len(records), 2):
                first, second = records[index:index+2]
                a, b = first['tokens'], second['tokens']
                self.assertEqual(first['source_group'], second['source_group'])
                self.assertEqual(Counter(a), Counter(b))
                for token in set(a):
                    self.assertEqual(sum(i for i, t in enumerate(a) if t == token),
                                     sum(i for i, t in enumerate(b) if t == token))
                self.assertNotEqual(a[0], b[0])
        self.assertTrue(generated['audit']['sequence_count_first_moment_collision_pattern_shared_intentionally'])

    def test_cross_role_exact_world_structures_and_sequence_groups_are_disjoint(self):
        generated = challenge.generate()
        for index, role in enumerate(challenge.ROLES):
            for other in challenge.ROLES[index+1:]:
                for key in ('world_hash', 'structural_hash', 'source_group'):
                    self.assertTrue({w[key] for w in generated['worlds'][role]}.isdisjoint(
                                     w[key] for w in generated['worlds'][other]))
                self.assertTrue({s['source_group'] for s in generated['sequences'][role]}.isdisjoint(
                                 s['source_group'] for s in generated['sequences'][other]))
        self.assertFalse(generated['audit']['all_roles_have_disjoint_entities_or_templates'])

    def test_shift_axes_change_only_declared_inventory_and_grammar(self):
        generated = challenge.generate()
        inventories = {}
        for role, records in generated['worlds'].items():
            inventories[role] = {n['name'] for world in records for n in world['world']['nodes']}
        self.assertEqual(inventories['training'], inventories['fresh_world'])
        self.assertEqual(inventories['training'], inventories['new_wording'])
        self.assertEqual(inventories['novel_entities'], inventories['joint'])
        self.assertTrue(inventories['training'].isdisjoint(inventories['novel_entities']))
        a = generated['audit']['roles']
        self.assertEqual([a[r]['wording_inventory'] for r in challenge.ROLES],
                         ['known', 'known', 'known', 'shifted', 'shifted'])
        self.assertTrue(set(challenge.CONTEXT['known']).isdisjoint(challenge.CONTEXT['shifted']))

    def test_no_sidecar_or_gold_features_in_requests_and_complete_distractors(self):
        generated = self.tiny()
        for role, rows in generated['roles'].items():
            for row in rows:
                request = row['request']
                self.assertEqual(set(request), {'format', 'request_id', 'context', 'prompt', 'kind', 'options'})
                self.assertIn(len(request['options']), (2, 3))
                for option in request['options']:
                    self.assertIn(option['description'], request['context'])
                    self.assertEqual(len(option['description']), 5)

    def test_opaque_option_identity_and_order_do_not_change_independent_gold(self):
        row = copy.deepcopy(self.tiny()['roles']['training'][0])
        before = solve_rendered(row['request'])
        row['request']['options'].reverse()
        for index, option in enumerate(row['request']['options']):
            option['id'] = f'opaque-{index}'
        validate_request(row['request'])
        self.assertEqual(solve_rendered(row['request']), before)

    def test_deterministic_generation_and_training_independent_of_slice_count(self):
        first = self.tiny()
        self.assertEqual(first, self.tiny())
        more_slices = challenge.generate(training_pairs=1, slice_pairs=2, ordering_pairs=1)
        self.assertEqual(first['roles']['training'], more_slices['roles']['training'])

    def test_invalid_resource_settings_are_refused(self):
        for settings in ({'training_pairs': 0}, {'training_pairs': 41},
                         {'slice_pairs': -1}, {'slice_pairs': 21}, {'ordering_pairs': 9},
                         {'seed': True}, {'seed': -1}, {'seed': 2**31}):
            with self.subTest(settings=settings), self.assertRaises(challenge.ChallengeError):
                challenge.generate(**settings)

    def test_private_materialization_manifest_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'new'
            receipt = challenge.materialize(output, training_pairs=1, slice_pairs=0, ordering_pairs=1)
            self.assertTrue(receipt['complete'])
            self.assertEqual(receipt['role_rows']['training'], 18)
            self.assertEqual(receipt['role_rows']['joint'], 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            manifest = json.loads((output/'MANIFEST.json').read_text())
            self.assertEqual(set(manifest['files']), {'training.jsonl', 'ORACLE.json', 'AUDIT.json', 'provenance.json'})
            for name, pin in manifest['files'].items():
                raw = (output/name).read_bytes()
                self.assertEqual(pin, {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
                self.assertEqual((output/name).stat().st_mode & 0o777, 0o400)
            self.assertEqual((output/'MANIFEST.json').stat().st_mode & 0o777, 0o400)
            with self.assertRaisesRegex(challenge.ChallengeError, 'output_already_exists'):
                challenge.materialize(output)

    def test_symlinked_ancestor_and_parent_escape_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            real = parent/'real';real.mkdir()
            (parent/'alias').symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(challenge.ChallengeError, 'symlinked_output_ancestor_refused'):
                challenge.materialize(parent/'alias'/'new')
            with self.assertRaisesRegex(challenge.ChallengeError, 'parent_path_component_refused'):
                challenge.materialize(real/'..'/'new')


if __name__ == '__main__':
    unittest.main()
