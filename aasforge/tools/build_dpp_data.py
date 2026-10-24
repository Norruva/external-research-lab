"""Build the DPP data bundle shipped in ``aasforge/data``.

Usage: python3 tools/build_dpp_data.py <submodel-templates/published> <battery-attributes-full.json>

* copies the latest IDTA 02099 (DPP Part 1) and IDTA 02035 (Digital Battery Passport parts 1-7)
  templates as AAS 3.1 JSON environments (parts only published as AASX are converted with the
  aasforge AASX reader; repairs are recorded);
* writes ``battery-attributes.json``: the Battery Pass consortium attribute longlist v1.2
  (CC-BY-4.0) with applicability per battery category, access tier and DIN DKE SPEC 99100 chapter;
* writes ``attribute-map.json``: an exact join of attributes to template elements, using the
  DIN DKE SPEC 99100 chapter numbers that IDTA 02035 cites in element and concept descriptions.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aasforge import aasx, elements, versioning  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / 'aasforge' / 'data'
TEMPLATES = {
    'dpp-metadata': 'Digital Product Passport/Digital Product Passport Part-1',
    'dbp-nameplate': 'Digital Battery Passport/1_Digital Nameplate',
    'dbp-handover': 'Digital Battery Passport/2_Handover Documentation',
    'dbp-carbon-footprint': 'Digital Battery Passport/3_Product Carbon Footprint',
    'dbp-technical-data': 'Digital Battery Passport/4_Technical Data',
    'dbp-product-condition': 'Digital Battery Passport/5_Product Condition',
    'dbp-material-composition': 'Digital Battery Passport/6_Material Composition',
    'dbp-circularity': 'Digital Battery Passport/7_Circularity',
}
TIER = {'Public': 'public', 'persons with a legitimate interest': 'legitimate-interest',
        'persons with a legitimate interest and the Commission': 'legitimate-interest',
        'Notified bodies, market surveillance authorities and the Commission': 'authority'}
# Reviewed overrides where the templates do not cite the exact DIN DKE SPEC 99100 sub-chapter.
# "where" selects the list entry whose sibling property has the given value.
PC, CI, HO, CF = 'dbp-product-condition', 'dbp-circularity', 'dbp-handover', 'dbp-carbon-footprint'
CURATED = {
    20: [(CF, 'ProductCarbonFootprints[].LifeCyclePhases', None)],
    28: [(HO, 'Documents[]', None)], 29: [(HO, 'Documents[]', None)],
    40: [(CI, 'RecycledContentInformation[].PreConsumerShare', {'RecycledMaterial': 'Nickel'})],
    41: [(CI, 'RecycledContentInformation[].PreConsumerShare', {'RecycledMaterial': 'Cobalt'})],
    42: [(CI, 'RecycledContentInformation[].PreConsumerShare', {'RecycledMaterial': 'Lithium'})],
    43: [(CI, 'RecycledContentInformation[].PreConsumerShare', {'RecycledMaterial': 'Lead'})],
    44: [(CI, 'RecycledContentInformation[].PostConsumerShare', {'RecycledMaterial': 'Nickel'})],
    45: [(CI, 'RecycledContentInformation[].PostConsumerShare', {'RecycledMaterial': 'Cobalt'})],
    46: [(CI, 'RecycledContentInformation[].PostConsumerShare', {'RecycledMaterial': 'Lithium'})],
    47: [(CI, 'RecycledContentInformation[].PostConsumerShare', {'RecycledMaterial': 'Lead'})],
    50: [(CI, 'EndOfLifeInformation', None)],
    87: [(PC, 'TemperatureInformation.TimeExtremeHighTemp', None)],
    88: [(PC, 'TemperatureInformation.TimeExtremeLowTemp', None)],
    89: [(PC, 'TemperatureInformation.TimeExtremeHighTempCharging', None)],
    90: [(PC, 'TemperatureInformation.TimeExtremeLowTempCharging', None)],
    84: [(PC, 'TemperatureInformation.MeasuredTemp', None)],
    79: [('dbp-technical-data', 'TechnicalPropertyAreas.Lifetime', None)],
}
# Known gaps: IDTA 02035 has no element for these (voluntary supply-chain indices; deep-discharge and
# overcharge counters are only expressible as NegativeEvents entries).
GAPS = {30: None, 91: [(PC, 'NegativeEvents', None)], 92: [(PC, 'NegativeEvents', None)]}

CHAPTER = re.compile(r'(?<![\d.])(6\.\d+\.\d+(?:\.\d+)?|6\.\d+)(?![\d])')


def version_key(path: Path):
    return [int(p) for p in path.parts if p.isdigit()]


def latest(directory: Path):
    candidates = [p for p in directory.rglob('*') if p.suffix in ('.json', '.aasx')
                  and 'without' not in p.name.lower() and 'sample' not in p.name.lower()]
    best = max(version_key(p.relative_to(directory)) for p in candidates)
    chosen = [p for p in candidates if version_key(p.relative_to(directory)) == best]
    json_files = [p for p in chosen if p.suffix == '.json']
    return (json_files or chosen)[0]


def load_env(path: Path):
    if path.suffix == '.json':
        return versioning.parse_json(path.read_bytes()).environment, []
    package = aasx.read(path.read_bytes())
    return package.normalised.environment, package.repairs


def chapters_of(element, cds):
    texts = [json.dumps(element.get('description', []))]
    semantic = ((element.get('semanticId') or {}).get('keys') or [{}])[0].get('value')
    if semantic in cds:
        texts.append(json.dumps(cds[semantic].get('embeddedDataSpecifications', [])))
        texts.append(json.dumps(cds[semantic].get('description', [])))
    return set(CHAPTER.findall(' '.join(texts)))


def main():
    published, attributes_path = Path(sys.argv[1]), Path(sys.argv[2])
    (DATA / 'templates').mkdir(parents=True, exist_ok=True)
    manifest = {}
    element_index = []   # (template, submodel semanticId, path, element semanticId, chapters, modelType)
    for key, folder in TEMPLATES.items():
        source = latest(published / folder)
        env, repairs = load_env(source)
        (DATA / 'templates' / f'{key}.json').write_text(json.dumps(env, indent=1, ensure_ascii=False))
        submodel = env['submodels'][0]
        semantic = ((submodel.get('semanticId') or {}).get('keys') or [{}])[0].get('value')
        manifest[key] = {'source': str(source.relative_to(published)), 'repairs': repairs, 'idShort': submodel['idShort'],
                         'semanticId': semantic, 'elements': len(elements.all_paths(submodel))}
        cds = {c['id']: c for c in env.get('conceptDescriptions', [])}
        for path, element in elements.walk_elements(submodel):
            element_index.append({'template': key, 'submodelSemanticId': semantic,
                                  'path': re.sub(r'\[\d+\]', '[]', path), 'modelType': element['modelType'],
                                  'semanticId': ((element.get('semanticId') or {}).get('keys') or [{}])[0].get('value'),
                                  'chapters': sorted(chapters_of(element, cds))})
    manifest['_license'] = 'IDTA submodel templates, CC-BY-4.0 (admin-shell-io/submodel-templates)'
    (DATA / 'templates' / 'manifest.json').write_text(json.dumps(manifest, indent=1))

    rows = json.loads(attributes_path.read_text())
    attributes = []
    for row in rows:
        attributes.append({
            'id': int(row['#']), 'attribute': ' '.join(row['Attribute'].split()),
            'category': row['Attribute category'], 'subcategory': row['Attribute sub-category'],
            'din_chapter': row['DIN DKE Spec 99100 Chapter'],
            'applicability': {'ev': row['EV'], 'lmt': row['LMT'], 'industrial': row['Other Industrial >2kWh'],
                              'stationary': row['Stationary >2kWh']},
            'regulation_reference': ' '.join(row['Regulation Reference'].split()),
            'access': TIER[row['Access rights according to Battery Regulation']],
            'dynamic': row['Data behavioural characteristic: static vs. dynamic'].lower().startswith('dynamic'),
            'granularity': row['Granularity level I: Model vs individual battery'],
            'unit': row['Unit of attribute or sign (if applicable)'], 'data_format': row['Data format']})
    (DATA / 'battery-attributes.json').write_text(json.dumps({
        'source': 'Battery Pass consortium, Battery Passport Data Attribute Longlist v1.2 (January 2025)',
        'license': 'CC-BY-4.0', 'changes': 'columns selected and renamed; access tiers normalised',
        'attributes': attributes}, indent=1, ensure_ascii=False))

    def name_tokens(text):
        words = re.findall(r'[A-Za-z]+', re.sub(r'\(.*?\)', '', text))
        stop = {'of', 'the', 'and', 'in', 'on', 'to', 'for', 'a', 'information', 'battery', 'share', 'number'}
        return [w.lower() for w in words if w.lower() not in stop]

    def template_of_chapter(chapter):
        owners = {e['template'] for e in element_index if any(c.startswith(chapter.rsplit('.', 1)[0]) for c in e['chapters'])}
        return owners

    mapping = []
    for attribute in attributes:
        chapter = attribute['din_chapter']
        method = 'chapter'
        hits = [e for e in element_index if chapter in e['chapters']]
        if not hits:
            tokens = name_tokens(attribute['attribute'])
            owners = template_of_chapter(chapter)
            by_name = [e for e in element_index if e['template'] in owners and tokens and
                       all(t in e['path'].split('.')[-1].replace('[]', '').lower() for t in tokens[:3])]
            if by_name:
                hits, method = by_name, 'idShort'
        if not hits and chapter.count('.') >= 2:
            parent = chapter.rsplit('.', 1)[0]
            hits = [e for e in element_index if parent in e['chapters']]
            method = 'parent-chapter'
        # Prefer the outermost element of each template that cites the chapter (children inherit it).
        chosen = []
        for hit in hits:
            if not any(other is not hit and other['template'] == hit['template'] and
                       (hit['path'].startswith(other['path'] + '.') or hit['path'].startswith(other['path'] + '['))
                       for other in hits):
                chosen.append(hit)
        override = CURATED.get(attribute['id']) or GAPS.get(attribute['id'])
        if override:
            index = {(e['template'], e['path']): e for e in element_index}
            chosen = [dict(index[(t, path)], where=where) for t, path, where in override]
            method = 'curated' if attribute['id'] in CURATED else 'partial'
        mapping.append({'attribute': attribute['id'], 'din_chapter': chapter, 'access': attribute['access'],
                        'method': method if chosen else None, 'elements': [{k: e.get(k) for k in ('template', 'submodelSemanticId', 'path', 'semanticId', 'modelType', 'where')
                                      if e.get(k) is not None}
                                     for e in chosen]})
    mapped = sum(1 for m in mapping if m['elements'])
    by_method = {}
    for m in mapping:
        by_method[m['method']] = by_method.get(m['method'], 0) + 1
    print('mapping methods:', by_method)
    (DATA / 'attribute-map.json').write_text(json.dumps({
        'method': 'exact join on DIN DKE SPEC 99100 chapter numbers cited by IDTA 02035 element/concept descriptions',
        'mapped': mapped, 'total': len(mapping), 'map': mapping}, indent=1, ensure_ascii=False))
    print(json.dumps({k: (v['elements'] if isinstance(v, dict) and 'elements' in v else v)
                      for k, v in manifest.items() if not k.startswith('_')}, indent=1))
    print(f'mapped {mapped} of {len(mapping)} attributes')
    for m in mapping:
        if not m['elements']:
            a = attributes[m['attribute'] - 1]
            print('  unmapped', a['id'], a['din_chapter'], a['attribute'][:60])


main()
