"""Independent literal-prose labels and routing contrast checks, no model use."""
from collections import Counter,defaultdict
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from eidos import routed_curriculum as curriculum
from eidos.decision_contract import validate_request


def solve_rendered(request):
    """Parse actual prose/question without generator oracle/task/template data."""
    facts=defaultdict(dict)
    for sentence in request['context'].rstrip('.').split('. '):
        patterns=(
            (r'Node (\w+) has hue (\w+)', 'hue'),
            (r'Node (\w+) points to (\w+)', 'points'),
            (r'Node (\w+) cites (\w+)', 'cites'),
            (r'For node (\w+), the hue is (\w+)', 'hue'),
            (r'From node (\w+), the points link goes to node (\w+)', 'points'),
            (r'From node (\w+), the cites link goes to node (\w+)', 'cites'),
            (r'The hue for node (\w+) is (\w+)', 'hue'),
            (r'The points link from node (\w+) goes to node (\w+)', 'points'),
            (r'The cites link from node (\w+) goes to node (\w+)', 'cites'),
        )
        found=[(m,field) for pattern,field in patterns if (m:=re.fullmatch(pattern,sentence))]
        if len(found)!=1:raise AssertionError('no unique independent context parse')
        match,field=found[0];name,value=match.groups()
        if field in facts[name]:raise AssertionError('duplicate named relation/property')
        facts[name][field]=value
    if len(facts)!=5 or any(set(row)!= {'hue','points','cites'} for row in facts.values()):
        raise AssertionError('five complete entities required')
    if len({row['hue'] for row in facts.values()})!=5:raise AssertionError('unique hue values required')
    for relation in ('points','cites'):
        if {row[relation] for row in facts.values()}!=set(facts):raise AssertionError('relation must be a permutation')
    by_lower={name.casefold():name for name in facts};prompt=request['prompt'].casefold()
    bind=(r'which node has hue (\w+)\?',r'return the node for hue (\w+)\.',r'for hue (\w+), return the node\.')
    for pattern in bind:
        if match:=re.fullmatch(pattern,prompt):
            return next(name for name,fields in facts.items() if fields['hue']==match[1])
    direct=(r'what hue does (\w+) have\?',r'return the hue for (\w+)\.',r'for (\w+), return the hue\.')
    for pattern in direct:
        if match:=re.fullmatch(pattern,prompt):return facts[by_lower[match[1]]]['hue']
    walks=(
        (r'follow (one|two) (outgoing|incoming) (points|cites) link from (\w+)\. what hue is there\?',(3,0,1,2)),
        (r'starting from (\w+), return the hue after (one|two) (outgoing|incoming) (points|cites) link\.',(0,1,2,3)),
        (r'from (\w+), return the hue after (one|two) (outgoing|incoming) (points|cites) link\.',(0,1,2,3)),
    )
    for pattern,(subject_index,number_index,direction_index,relation_index) in walks:
        if match:=re.fullmatch(pattern,prompt):
            groups=match.groups();current=by_lower[groups[subject_index]]
            relation=groups[relation_index];incoming=groups[direction_index]=='incoming'
            for _ in range(1 if groups[number_index]=='one' else 2):
                current=next(name for name,fields in facts.items() if fields[relation]==current) if incoming else facts[current][relation]
            return facts[current]['hue']
    raise AssertionError('no independent question parse')


class RoutedCurriculumTests(unittest.TestCase):
    def test_default_counts_roles_and_all_rendered_labels(self):
        generated=curriculum.generate()
        self.assertEqual({r:len(rows) for r,rows in generated['roles'].items()},
                         {'training':128,'fresh_world':32,'novel_entities':32,'new_composition':32,'joint':32})
        self.assertEqual(tuple(generated['roles']),curriculum.ROLES)
        for role,rows in generated['roles'].items():
            self.assertEqual(generated['audit']['roles'][role]['total_source_groups'],4 if role=='training' else 2)
            for row in rows:
                validate_request(row['request']);answer=solve_rendered(row['request'])
                self.assertEqual(next(o['description'] for o in row['request']['options'] if o['id']==row['target_option_id']),answer)

    def test_question_only_depth_direction_relation_contrasts(self):
        generated=curriculum.generate();types=('depth_contrast_id','direction_contrast_id','relation_contrast_id')
        for role,rows in generated['roles'].items():
            by_id={r['request']['request_id']:r for r in rows}
            for contrast_field in types:
                contrasts=defaultdict(list)
                for spec in generated['tasks'][role]:
                    if spec[contrast_field] is not None:contrasts[spec[contrast_field]].append(by_id[spec['request_id']])
                self.assertTrue(contrasts)
                for cases in contrasts.values():
                    self.assertEqual(len(cases),3 if contrast_field=='depth_contrast_id' else 2)
                    first=cases[0]['request']
                    self.assertTrue(all(c['request']['context']==first['context'] and c['request']['options']==first['options'] for c in cases))
                    self.assertEqual(len({solve_rendered(c['request']) for c in cases}),len(cases))

    def test_hue_rotation_changes_every_gold_and_keeps_context_multisets(self):
        generated=curriculum.generate()
        for role,rows in generated['roles'].items():
            by_id={r['request']['request_id']:r for r in rows};pairs=defaultdict(list)
            for spec in generated['tasks'][role]:pairs[spec['paired_task_key']].append(by_id[spec['request_id']])
            for pair in pairs.values():
                self.assertEqual(len(pair),2)
                self.assertNotEqual(solve_rendered(pair[0]['request']),solve_rendered(pair[1]['request']))
                self.assertEqual(Counter(re.findall(r'\w+',pair[0]['request']['context'].casefold())),
                                 Counter(re.findall(r'\w+',pair[1]['request']['context'].casefold())))
                self.assertEqual(pair[0]['request']['prompt'],pair[1]['request']['prompt'])

    def test_two_relation_cycles_are_complete_distinct_and_five_long(self):
        generated=curriculum.generate()
        for records in generated['worlds'].values():
            for record in records:
                nodes={n['name']:n for n in record['world']['nodes']};self.assertEqual(len(nodes),5)
                self.assertTrue(all(n['points']!=n['cites'] for n in nodes.values()))
                for relation in ('points','cites'):
                    for start in nodes:
                        visited=[];current=start
                        for _ in range(5):visited.append(current);current=nodes[current][relation]
                        self.assertEqual(current,start);self.assertEqual(len(set(visited)),5)

    def test_known_lexeme_new_composition_and_new_name_axis(self):
        generated=curriculum.generate()
        train_words=set(re.findall(r'\w+', ' '.join(r['request']['context']+' '+r['request']['prompt'] for r in generated['roles']['training']).casefold()))
        for role in ('fresh_world','new_composition'):
            words=set(re.findall(r'\w+', ' '.join(r['request']['context']+' '+r['request']['prompt'] for r in generated['roles'][role]).casefold()))
            self.assertFalse(words-train_words)
        self.assertTrue(set(curriculum.KNOWN_NAMES).isdisjoint(curriculum.NOVEL_NAMES))
        for role in ('novel_entities','joint'):
            names={node['name'] for record in generated['worlds'][role] for node in record['world']['nodes']}
            self.assertLessEqual(names,set(curriculum.NOVEL_NAMES));self.assertTrue(names.isdisjoint(curriculum.KNOWN_NAMES))
        self.assertEqual(generated['audit']['heldout_unseen_nonentity_operator_lexeme_counts'],{r:0 for r in curriculum.ROLES[1:]})

    def test_cross_role_hashes_and_prior_projection_exclusion(self):
        generated=curriculum.generate();seen={field:set() for field in ('world_hash','structural_hash','projected_prior_structural_hash','source_group')}
        for role in curriculum.ROLES:
            for field in seen:
                values={record[field] for record in generated['worlds'][role]}
                self.assertTrue(values.isdisjoint(seen[field]));seen[field].update(values)
        excluded={record[field] for records in generated['worlds'].values() for record in records for field in ('structural_hash','projected_prior_structural_hash')}
        fresh=curriculum.generate(excluded_structural_hashes=sorted(excluded))
        for records in fresh['worlds'].values():
            for record in records:
                self.assertNotIn(record['structural_hash'],excluded);self.assertNotIn(record['projected_prior_structural_hash'],excluded)
        for records in generated['worlds'].values():
            for record in records:
                ordered=sorted(record['world']['nodes'],key=lambda n:n['name']);position={n['name']:i for i,n in enumerate(ordered)}
                raw=json.dumps([(n['hue'],position[n['points']]) for n in ordered],sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
                self.assertEqual(hashlib.sha256(raw).hexdigest(),record['projected_prior_structural_hash'])

    def test_only_request_target_group_in_rows_no_oracle_metadata_features(self):
        generated=curriculum.generate();ids=[]
        for rows in generated['roles'].values():
            for row in rows:
                self.assertEqual(set(row),{'request','target_option_id','source_group'})
                self.assertEqual(set(row['request']),{'format','request_id','context','prompt','kind','options'})
                ids.append(row['request']['request_id'])
        self.assertEqual(len(ids),len(set(ids)))

    def test_option_id_and_order_do_not_change_independent_answer(self):
        generated=curriculum.generate()
        for role in curriculum.ROLES:
            request=copy.deepcopy(generated['roles'][role][0]['request']);expected=solve_rendered(request)
            request['options'].reverse()
            for i,option in enumerate(request['options']):option['id']=f'replacement-{i}'
            self.assertEqual(solve_rendered(request),expected)

    def test_fixed_seed_determinism_and_no_model_quality_claim(self):
        self.assertEqual(curriculum.generate(),curriculum.generate())
        audit=curriculum.generate()['audit']
        self.assertFalse(audit['model_fit_inference_scoring_or_activation_performed']);self.assertFalse(audit['pristine_real_world_or_LAYA_JEV_benchmark'])

    def test_invalid_settings_hashes_and_rotation_refused(self):
        for settings in ({'training_pairs':1},{'training_pairs':True},{'slice_pairs':21},{'seed':-1},
                         {'excluded_structural_hashes':['bad']},{'excluded_structural_hashes':'a'*64},
                         {'excluded_structural_hashes':['G'*64]}):
            with self.assertRaises(ValueError):curriculum.generate(**settings)
        world=curriculum.World(tuple(curriculum.Node(str(i),str(i),str((i+1)%5),str((i+2)%5)) for i in range(5)))
        for shift in (0,5,True,-1):
            with self.assertRaises(ValueError):curriculum.rotate_hues(world,shift)

    def test_private_materialization_manifest_and_existing_output_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            output=Path(parent)/'new';receipt=curriculum.materialize(output)
            self.assertTrue(receipt['complete']);self.assertEqual(receipt['file_count'],9)
            self.assertEqual(output.stat().st_mode&0o777,0o700)
            manifest=json.loads((output/'MANIFEST.json').read_bytes())
            for name,pin in manifest['files'].items():
                path=output/name;raw=path.read_bytes();self.assertEqual(pin,{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()});self.assertEqual(path.stat().st_mode&0o777,0o400)
            with self.assertRaises(ValueError):curriculum.materialize(output)

    def test_cli_exclusion_paths_refused_before_open_and_bounded_list_passed(self):
        with tempfile.TemporaryDirectory() as parent:
            path=Path(parent);target=path/'hashes.json'
            target.write_text(json.dumps(['a'*64]))
            oversized=path/'oversized.json'
            with oversized.open('wb') as stream:stream.truncate(curriculum.MAX_EXCLUSION_BYTES+1)
            linked=path/'linked.json';linked.symlink_to(target)
            directory=path/'directory';directory.mkdir()
            with mock.patch.object(curriculum,'materialize') as materialize:
                for invalid in (oversized,linked,directory):
                    with self.subTest(path=invalid.name), mock.patch('eidos.decision_artifact.os.open') as opener, mock.patch('sys.stderr',new=io.StringIO()):
                        self.assertEqual(curriculum.main(['--output',str(path/'new'),'--exclude-hashes',str(invalid)]),2)
                        opener.assert_not_called()
                materialize.assert_not_called()
                materialize.return_value={'complete':True}
                with mock.patch('sys.stdout',new=io.StringIO()):
                    self.assertEqual(curriculum.main(['--output',str(path/'new'),'--exclude-hashes',str(target)]),0)
                self.assertEqual(materialize.call_args.kwargs['excluded_structural_hashes'],['a'*64])

    def test_symlinked_ancestor_and_parent_path_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            path=Path(parent);(path/'real').mkdir();(path/'link').symlink_to(path/'real',target_is_directory=True)
            with self.assertRaises(ValueError):curriculum.materialize(path/'link'/'new')
            with self.assertRaises(ValueError):curriculum.materialize(path/'real'/'..'/'new')


if __name__=='__main__':
    unittest.main()
