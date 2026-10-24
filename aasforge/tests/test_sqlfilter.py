"""The SQL prefilter must never drop a match: for generated queries over the test corpus, the
objects the exact evaluator accepts are a subset of what the compiled SQL selects."""
import random
import unittest

from helpers import FIXTURES, database

import battery_passport

from aasforge import elements, formula, sqlfilter
from aasforge.app import Config, Forge
from aasforge.dpp import passport, tiers

OPS = ['$eq', '$ne', '$lt', '$gt', '$le', '$ge', '$contains', '$starts-with', '$ends-with', '$regex']


def f(path):
    return {'$field': path}


class Corpus:
    def __init__(self):
        self.forge = Forge(Config(database=database()))
        repo = self.forge.repository
        for name in sorted(p.name for p in FIXTURES.glob('*.aasx')):
            repo.import_bytes((FIXTURES / name).read_bytes(), name)
        passport.create(repo, battery_passport.spec())
        self.store = self.forge.store
        self.objects = {kind: list(self.store.iter_all(kind)) for kind in ('shell', 'submodel', 'concept-description')}

    def submodels_of(self, shell):
        return self.forge.api._submodels_of(shell)

    def shells_of(self, submodel):
        return self.forge.api._shells_of(submodel)

    def exact(self, kind, expression, claims=None):
        out = set()
        for obj in self.objects[kind]:
            ctx = formula.Context(kind, obj, self.submodels_of, self.shells_of, claims or {})
            if formula.matches(ctx, expression):
                out.add(obj['id'])
        return out

    def prefiltered(self, kind, expression, claims=None):
        where = sqlfilter.compile_condition(expression, kind, self.store.db, claims)
        return {o['id'] for o in self.store.iter_all(kind, where)}, where


class Generator:
    def __init__(self, corpus, seed):
        self.rng = random.Random(seed)
        self.samples = []      # (path, element) over all submodels
        for submodel in corpus.objects['submodel']:
            for path, element in elements.walk_elements(submodel):
                self.samples.append((path, element))
        self.corpus = corpus

    def literal(self, element):
        value = element.get('value')
        if isinstance(value, list):
            value = next((v.get('text') for v in value if isinstance(v, dict) and v.get('text')), None)
        if not isinstance(value, str):
            value = self.rng.choice(['', 'kg', '1', 'Battery', 'urn:'])
        roll = self.rng.random()
        if roll < 0.35:
            try:
                return {'$numVal': float(value) + self.rng.choice([-1, 0, 0.5, 1])}
            except ValueError:
                pass
        if roll < 0.7 and value:
            cut = self.rng.randint(0, len(value))
            return {'$strVal': self.rng.choice([value, value[:cut], value[cut:]])}
        return {'$strVal': self.rng.choice([value, value.upper(), 'zzz', ''])}

    def comparison(self, kind):
        path, element = self.rng.choice(self.samples)
        op = self.rng.choice(OPS)
        choice = self.rng.random()
        pattern = path.replace('[0]', self.rng.choice(['[0]', '[]'])) if '[0]' in path else path
        if choice < 0.45:
            field = f(f'$sme.{pattern}#value')
            literal = self.literal(element)
        elif choice < 0.6:
            field = f('$sme#value')
            literal = self.literal(element)
        elif choice < 0.7:
            field = f(f'$sme.{pattern}#semanticId')
            keys = (element.get('semanticId') or {}).get('keys') or [{'value': 'none'}]
            literal = {'$strVal': keys[0]['value']}
        elif choice < 0.8:
            submodel = self.rng.choice(self.corpus.objects['submodel'])
            field = f(self.rng.choice(['$sm#idShort', '$sm#semanticId', '$sm#id']))
            literal = {'$strVal': self.rng.choice([submodel.get('idShort') or '', submodel['id']])}
        elif choice < 0.9:
            shell = self.rng.choice(self.corpus.objects['shell'])
            field = f(self.rng.choice(['$aas#idShort', '$aas#id', '$aas#assetInformation.globalAssetId',
                                       '$aas#assetInformation.specificAssetIds[].value']))
            ids = (shell.get('assetInformation') or {}).get('specificAssetIds') or [{'value': 'x'}]
            literal = {'$strVal': self.rng.choice([shell.get('idShort') or '', shell['id'], ids[0]['value']])}
        else:
            field = {'$strCast': f(f'$sme.{pattern}#value')}
            literal = self.literal(element)
        operands = [field, literal] if self.rng.random() < 0.8 else [literal, field]
        if op in ('$contains', '$starts-with', '$ends-with', '$regex') and '$numVal' in literal:
            literal = {'$strVal': str(literal['$numVal'])}
            operands = [field, literal]
        return {op: operands}

    def expression(self, kind, depth=0):
        roll = self.rng.random()
        if depth >= 2 or roll < 0.45:
            return self.comparison(kind)
        if roll < 0.65:
            return {'$and': [self.expression(kind, depth + 1) for _ in range(self.rng.randint(2, 3))]}
        if roll < 0.8:
            return {'$or': [self.expression(kind, depth + 1) for _ in range(2)]}
        if roll < 0.9:
            return {'$not': self.expression(kind, depth + 1)}
        return {'$match': [self.comparison(kind) for _ in range(2)]}


class PrefilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus()

    @classmethod
    def tearDownClass(cls):
        cls.corpus.store.db.drop_schema()

    def test_generated_queries_never_lose_matches(self):
        generator = Generator(self.corpus, seed=20261024)
        checked = constrained = 0
        for i in range(600):
            kind = ('submodel', 'shell', 'concept-description')[i % 3] if i % 10 else 'shell'
            expression = generator.expression(kind)
            exact = self.corpus.exact(kind, expression)
            pre, where = self.corpus.prefiltered(kind, expression)
            self.assertLessEqual(exact, pre, f'{kind} {expression} -> {where}')
            checked += 1
            constrained += where is not None
        self.assertEqual(checked, 600)
        self.assertGreater(constrained, 300)    # most generated queries do reach SQL

    def test_selective_query_reads_few_candidates(self):
        query = {'$and': [{'$eq': [f('$sm#semanticId'),
                                   {'$strVal': 'https://admin-shell.io/idta/CarbonFootprint/CarbonFootprint/1/0'}]},
                          {'$ge': [f('$sme#value'), {'$numVal': 60}]}]}
        exact = self.corpus.exact('submodel', query)
        pre, where = self.corpus.prefiltered('submodel', query)
        self.assertTrue(exact)
        self.assertLessEqual(exact, pre)
        self.assertLess(len(pre), len(self.corpus.objects['submodel']) / 4)

    def test_tier_rules_prefilter_keeps_every_readable_submodel(self):
        rules = tiers.access_rules(self.corpus.objects['submodel'])
        from aasforge import security
        engine = security.AccessRules(rules)
        for claims, anonymous in (({}, True), ({'dpp_tier': 'legitimate-interest'}, False),
                                  ({'dpp_tier': 'authority'}, False)):
            subject = security.Subject(claims, anonymous)
            formulas, merged = engine.candidate_formulas(subject, 'READ')
            where = sqlfilter.any_of(sqlfilter.compile_condition(x, 'submodel', self.corpus.store.db, merged)
                                     for x in formulas)
            pre = {o['id'] for o in self.corpus.store.iter_all('submodel', where)}
            readable = {s['id'] for s in self.corpus.objects['submodel'] if engine.decide(
                subject, 'READ', security.Target('/submodels/x', 'submodel', s)).allowed}
            self.assertLessEqual(readable, pre, claims)

    def test_unconstrained_constructs(self):
        db = self.corpus.store.db
        for expression in ({'$not': {'$eq': [f('$sm#idShort'), {'$strVal': 'x'}]}},
                           {'$regex': [f('$sm#idShort'), {'$strVal': '^C'}]},
                           {'$ne': [f('$sm#idShort'), {'$strVal': 'x'}]},
                           {'$or': [{'$eq': [f('$sm#idShort'), {'$strVal': 'x'}]}, {'$boolean': True}]},
                           {'$eq': [f('$sm#idShort'), f('$sm#id')]}):
            self.assertIsNone(sqlfilter.compile_condition(expression, 'submodel', db), expression)
        self.assertEqual(sqlfilter.compile_condition({'$boolean': False}, 'submodel', db), sqlfilter.FALSE)
        constant = {'$eq': [{'$attribute': {'CLAIM': 'role'}}, {'$strVal': 'auditor'}]}
        self.assertIsNone(sqlfilter.compile_condition(constant, 'submodel', db, {'role': 'auditor'}))
        self.assertEqual(sqlfilter.compile_condition(constant, 'submodel', db, {'role': 'guest'}), sqlfilter.FALSE)


if __name__ == '__main__':
    unittest.main()
