"""Build ``aasforge/data/samm-units.json`` from the SAMM unit catalogue (units.ttl).

    python tools/build_samm_units.py path/to/esmf-semantic-aspect-meta-model/.../samm/unit/2.1.0/units.ttl

The catalogue is part of eclipse-esmf/esmf-semantic-aspect-meta-model (MPL-2.0); only each unit's
symbol, English name and UN/CEFACT common code are kept.
"""
import json
import sys
from pathlib import Path

import rdflib

SAMM = 'urn:samm:org.eclipse.esmf.samm:meta-model:2.1.0#'


def main(source):
    graph = rdflib.Graph().parse(source, format='turtle')
    samm = rdflib.Namespace(SAMM)
    units = {}
    for unit in graph.subjects(rdflib.RDF.type, samm.Unit):
        local = str(unit).split('#', 1)[1]
        entry = {k: str(v) for k, v in (('symbol', graph.value(unit, samm.symbol)),
                                        ('name', graph.value(unit, samm.preferredName)),
                                        ('commonCode', graph.value(unit, samm.commonCode))) if v is not None}
        units[local] = entry
    out = Path(__file__).resolve().parents[1] / 'aasforge' / 'data' / 'samm-units.json'
    out.write_text(json.dumps({'_source': 'eclipse-esmf/esmf-semantic-aspect-meta-model samm/unit/2.1.0/units.ttl '
                                          '(MPL-2.0, https://mozilla.org/MPL/2.0/); symbol, name and commonCode only',
                               'units': dict(sorted(units.items()))}, ensure_ascii=False, separators=(',', ':')))
    print(f'{len(units)} units -> {out}')


if __name__ == '__main__':
    main(sys.argv[1])
