"""Measure the SQL prefilter: the same Query Language request over N generated submodels, with the
prefilter on and off (results must be identical).

    python tools/bench_query.py [--submodels 5000] [--database postgresql://...]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aasforge import sqlfilter  # noqa: E402
from aasforge.app import Config, Forge  # noqa: E402
from aasforge.http import Request  # noqa: E402

QUERIES = {
    'semanticId + numeric value': {'$condition': {'$and': [
        {'$eq': [{'$field': '$sm#semanticId'}, {'$strVal': 'urn:bench:sem:7'}]},
        {'$gt': [{'$field': '$sme.Mass#value'}, {'$numVal': 990}]}]}, '$select': 'id'},
    'recursive value match': {'$condition': {'$eq': [{'$field': '$sme#value'}, {'$strVal': 'serial-04242'}]},
                              '$select': 'id'},
    'starts-with on idShort': {'$condition': {'$starts-with': [{'$field': '$sm#idShort'}, {'$strVal': 'Bench0001'}]},
                               '$select': 'id'},
}


def submodel(i: int) -> dict:
    return {'modelType': 'Submodel', 'id': f'urn:bench:sm:{i:05d}', 'idShort': f'Bench{i:05d}',
            'semanticId': {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': f'urn:bench:sem:{i % 10}'}]},
            'submodelElements': [
                {'modelType': 'Property', 'idShort': 'Mass', 'valueType': 'xs:double', 'value': str(i % 1000)},
                {'modelType': 'Property', 'idShort': 'Serial', 'valueType': 'xs:string', 'value': f'serial-{i:05d}'},
                {'modelType': 'SubmodelElementCollection', 'idShort': 'Cells', 'value': [
                    {'modelType': 'Property', 'idShort': f'Cell{j}', 'valueType': 'xs:double', 'value': str(3.6 + j / 100)}
                    for j in range(16)]}]}


def run(forge, query, prefilter: bool):
    original = sqlfilter.compile_condition
    if not prefilter:
        sqlfilter.compile_condition = lambda *a, **k: None
    try:
        request = Request('POST', '/query/submodels', {}, {}, __import__('json').dumps(query).encode())
        request.subject = forge.authenticator.authenticate({})
        start = time.perf_counter()
        response = forge.api.query(request, 'submodel')
        return time.perf_counter() - start, response.body
    finally:
        sqlfilter.compile_condition = original


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--submodels', type=int, default=5000)
    parser.add_argument('--database', default=':memory:')
    args = parser.parse_args(argv)
    forge = Forge(Config(database=args.database, dpp=False, signing=False, verification='off'))
    start = time.perf_counter()
    with forge.store.transaction():
        for i in range(args.submodels):
            forge.store.put('submodel', submodel(i))
    print(f'loaded {args.submodels} submodels ({forge.store.db.dialect}) in {time.perf_counter() - start:.1f} s')
    print(f'{"query":32} {"full scan":>10} {"prefilter":>10} {"speed-up":>9}')
    for name, query in QUERIES.items():
        slow, slow_body = run(forge, query, False)
        fast, fast_body = run(forge, query, True)
        assert slow_body == fast_body, name
        print(f'{name:32} {slow * 1000:8.0f}ms {fast * 1000:8.1f}ms {slow / fast:8.0f}x')
    forge.store.db.drop_schema()


if __name__ == '__main__':
    main()
