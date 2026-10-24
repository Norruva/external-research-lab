"""Materialize original authored mechanics fixtures; no model or external data access.

The scenarios and finite templates below are deliberately transparent. Their
labels follow their declared facts. They are not independent semantic-quality
or broad language-understanding evidence. Running this authoring script writes
only the three new demo files beside it and refuses existing files.
"""
from pathlib import Path
import json
import random

HERE = Path(__file__).resolve().parent
COLORS = ('red', 'blue', 'green', 'yellow', 'white', 'black')
OWNERS = ('Mira', 'Noel', 'Iris', 'Omar', 'Tess', 'Luca')
PLACES = ('workshop', 'office', 'kitchen', 'library', 'garage', 'garden')
STATUSES = ('rejected', 'pending', 'approved')
# Each pair declares item, color, owner, storage location and processing status.
SCENARIOS = (
    (('lantern','red','Mira','workshop','approved'), ('parcel','blue','Noel','office','pending')),
    (('bottle','green','Iris','kitchen','rejected'), ('notebook','yellow','Omar','library','approved')),
    (('helmet','white','Tess','garage','pending'), ('basket','black','Luca','garden','rejected')),
    (('folder','blue','Omar','office','rejected'), ('toolbox','red','Mira','workshop','pending')),
    (('scarf','yellow','Noel','library','pending'), ('mug','green','Iris','kitchen','approved')),
    (('bucket','black','Luca','garden','approved'), ('jacket','white','Tess','garage','rejected')),
    (('radio','green','Mira','garage','pending'), ('envelope','black','Iris','office','approved')),
    (('plate','red','Omar','kitchen','approved'), ('album','white','Luca','library','rejected')),
    (('cart','blue','Tess','garden','rejected'), ('case','yellow','Noel','workshop','pending')),
    (('bag','white','Iris','library','approved'), ('clock','green','Omar','office','rejected')),
    (('vase','black','Noel','kitchen','pending'), ('bench','red','Tess','garden','approved')),
    (('drill','yellow','Luca','workshop','rejected'), ('locker','blue','Mira','garage','pending')),
    (('pouch','red','Tess','office','pending'), ('tray','yellow','Iris','kitchen','rejected')),
    (('map','blue','Luca','library','approved'), ('shovel','white','Noel','garden','pending')),
    (('box','green','Omar','garage','rejected'), ('brush','black','Mira','workshop','approved')),
    (('canister','yellow','Mira','garden','approved'), ('camera','red','Luca','office','pending')),
    (('journal','white','Noel','library','rejected'), ('apron','blue','Tess','kitchen','approved')),
    (('pump','black','Iris','garage','pending'), ('saw','green','Omar','workshop','rejected')),
    (('ruler','blue','Iris','workshop','approved'), ('suitcase','white','Mira','office','rejected')),
    (('kettle','black','Tess','kitchen','rejected'), ('atlas','green','Noel','library','pending')),
    (('trolley','yellow','Omar','garden','pending'), ('boot','red','Luca','garage','approved')),
    (('rope','white','Omar','workshop','pending'), ('stamp','black','Tess','office','approved')),
    (('bowl','blue','Luca','kitchen','approved'), ('globe','red','Iris','library','rejected')),
    (('spade','green','Mira','garden','rejected'), ('coat','yellow','Noel','garage','pending')),
)
SMOKE_SCENARIOS = (
    (('satchel','green','Tess','library','pending'), ('flask','red','Omar','kitchen','approved')),
    (('clipboard','black','Luca','office','approved'), ('scoop','white','Mira','garden','rejected')),
)


def context(pair, style):
    a, b = pair
    def sentence(x):
        item, color, owner, place, status = x
        return f'{owner} owns the {color} {item}. The {item} is in the {place}. Its status is {status}.'
    if style % 4 == 0:
        return sentence(a) + ' ' + sentence(b)
    if style % 4 == 1:
        return '\n'.join(f'Item: {x[0]}; color: {x[1]}; owner: {x[2]}; location: {x[3]}; status: {x[4]}.' for x in pair)
    if style % 4 == 2:
        return ' '.join(f'The {x[0]} belongs to {x[2]}. It is {x[1]}, located in the {x[3]}, and marked {x[4]}.' for x in (b,a))
    return ' '.join(f'In the {x[3]} there is a {x[1]} {x[0]}, owned by {x[2]}, with status {x[4]}.' for x in pair)


def choices(values, seed):
    # ID assignment/order varies independently of the correct semantic value.
    rng = random.Random(seed)
    ids = rng.sample(range(100, 999), len(values))
    rows = [{'id': f'c{ident}', 'description': value} for ident, value in zip(ids, values)]
    rng.shuffle(rows)
    return rows


def choice_request(ident, text, prompt, values, correct, seed):
    options = choices(values, seed)
    request = {'format':'eidos-decision-request-v2', 'request_id':ident,
               'context':text, 'prompt':prompt, 'kind':'CHOICE', 'options':options}
    target = next(row['id'] for row in options if row['description'] == correct)
    return request, target


def scenario_rows(pair, number, prefix):
    text = context(pair, number)
    a, b = pair
    # Every scenario is asked several different questions with identical context.
    prompts = (
        (f'What color is the {a[0]}?', COLORS, a[1]),
        (f'Who owns the {b[0]}?', OWNERS, b[2]),
        (f'Where is the {a[0]} located?', PLACES, a[3]),
        (f'What is the status of the {b[0]}?', STATUSES, b[4]),
    )
    rows = []
    for index, (prompt, values, correct) in enumerate(prompts):
        request, target = choice_request(f'{prefix}-{number:02d}-{index+1}', text, prompt,
                                         values, correct, 7919*number + 104729*index + 113)
        rows.append({'request':request, 'target_option_id':target, 'source_group':f'{prefix}-scenario-{number:02d}'})
    # Balanced supported/contradicted propositions; every other pair uses negation.
    alleged = a[1] if number % 2 == 0 else COLORS[(COLORS.index(a[1])+1) % len(COLORS)]
    negated = number % 4 in (2,3)
    prompt = f'Is the {a[0]} ' + ('not ' if negated else '') + f'{alleged}?'
    truth = (a[1] != alleged) if negated else (a[1] == alleged)
    binary = {'format':'eidos-decision-request-v2', 'request_id':f'{prefix}-{number:02d}-5',
              'context':text, 'prompt':prompt, 'kind':'BINARY',
              'options':[{'id':'true','description':'Yes. The proposition is supported by the stated facts.'},
                         {'id':'false','description':'No. The proposition is contradicted by the stated facts.'}]}
    rows.append({'request':binary, 'target_option_id':'true' if truth else 'false',
                 'source_group':f'{prefix}-scenario-{number:02d}'})
    # A declared finite ordinal rubric, not an invented continuous-point density.
    anchors = choices(STATUSES, 65537*number + 113)
    for row in anchors:
        row['value'] = STATUSES.index(row['description'])
    score = {'format':'eidos-decision-request-v2', 'request_id':f'{prefix}-{number:02d}-6',
             'context':text, 'prompt':f'Rate the {a[0]} readiness using its status: rejected means 0, pending means 1, and approved means 2.',
             'kind':'SCORE', 'minimum':0, 'maximum':2, 'options':anchors}
    target = next(row['id'] for row in anchors if row['description'] == a[4])
    rows.append({'request':score, 'target_option_id':target, 'source_group':f'{prefix}-scenario-{number:02d}'})
    return rows


def main():
    training = [row for number, pair in enumerate(SCENARIOS, 1) for row in scenario_rows(pair, number, 'authored-train')]
    smoke = [row for number, pair in enumerate(SMOKE_SCENARIOS, 1) for row in scenario_rows(pair, number+100, 'authored-smoke')]
    provenance = {
        'source':'Original authored two-entity facts and decision-task templates in author_fixtures.py',
        'license':'Original authored fixture; no third-party material; no new public license grant',
        'citation':'EIDOS decision-model implementation mechanics fixtures, 2026',
        'rights_basis':'Authored for this implementation without public benchmark, private source, vendor output or pretrained model input; no exclusive legal ownership or production-data rights certification is asserted'
    }
    outputs = {'training.jsonl': ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',',':'))+'\n' for row in training),
               'smoke.jsonl': ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',',':'))+'\n' for row in smoke),
               'provenance.json':json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2)+'\n'}
    for name, text in outputs.items():
        with (HERE/name).open('x', encoding='utf-8') as stream:
            stream.write(text)
    print(json.dumps({'training_rows':len(training),'authored_smoke_rows':len(smoke),
                      'training_scenario_groups':len(SCENARIOS),'smoke_scenario_groups':len(SMOKE_SCENARIOS),
                      'kind_counts_training':{'CHOICE':96,'BINARY':24,'SCORE':24},
                      'model_fit_inference_or_quality_scoring':False}))


if __name__ == '__main__':
    main()
