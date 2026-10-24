"""Server-rendered passport page: no JavaScript, no third-party requests, strict CSP, tier-aware.

The public teardown (dpp-rea/passport-viewers) found trackers firing on a public battery passport
before consent; this page loads nothing but itself.
"""
from __future__ import annotations

import html
import json

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'; " \
      "frame-ancestors 'none'"
TIER_LABEL = {'public': 'Public view', 'legitimate-interest': 'Legitimate-interest view', 'authority': 'Authority view',
              'economic-operator': 'Economic-operator view'}

STYLE = '''
:root{--bg:#f7f7f5;--card:#fff;--ink:#16181d;--muted:#5b616e;--line:#e3e4e8;--accent:#0b6e4f;--warn:#9a5b00;--chip:#eef6f2}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--card:#1b1e23;--ink:#eceef2;--muted:#a2a8b5;--line:#2c3038;--accent:#4fc39a;--warn:#e0a84a;--chip:#1f2c27}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:920px;margin:0 auto;padding:24px 16px 64px}header.hero{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px}
h1{margin:4px 0 6px;font-size:1.6rem;line-height:1.2}h2{font-size:1.05rem;margin:0 0 10px}.eyebrow{color:var(--muted);font-size:.8rem;letter-spacing:.06em;text-transform:uppercase}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.chip{background:var(--chip);color:var(--accent);border-radius:999px;padding:2px 10px;font-size:.8rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:14px}.kv{border-top:1px solid var(--line);padding-top:6px}
.k{color:var(--muted);font-size:.78rem}.v{word-break:break-word}section.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-top:14px}
dl{margin:0}dt{color:var(--muted);font-size:.8rem;margin-top:8px}dd{margin:0 0 0 0;word-break:break-word}.nested{border-left:2px solid var(--line);padding-left:12px;margin-top:4px}
.bar{height:8px;background:var(--line);border-radius:6px;overflow:hidden;margin:8px 0}.bar span{display:block;height:100%;background:var(--accent)}
.notice{border:1px dashed var(--warn);color:var(--warn);border-radius:10px;padding:10px 12px;margin-top:14px;font-size:.9rem}
.lock{font-size:.75rem;color:var(--warn);margin-left:6px}footer{color:var(--muted);font-size:.8rem;margin-top:28px}a{color:var(--accent)}
code{font-size:.85em;word-break:break-all}
'''


def _value(value):
    if isinstance(value, list):
        if value and all(isinstance(v, dict) and len(v) == 1 for v in value):
            return ', '.join(f'{html.escape(str(t))} <span class="k">({html.escape(lang)})</span>'
                             for item in value for lang, t in item.items())
        return ', '.join(_value(v) for v in value)
    if isinstance(value, dict):
        if 'keys' in value:
            keys = value.get('keys') or []
            return f'<code>{html.escape(str(keys[-1].get("value") if keys else ""))}</code>'
        if 'contentType' in value and 'value' in value:
            return f'<code>{html.escape(str(value.get("value")))}</code> <span class="k">{html.escape(str(value.get("contentType")))}</span>'
        if set(value) <= {'min', 'max'}:
            return f'{html.escape(str(value.get("min")))} – {html.escape(str(value.get("max")))}'
        return html.escape(json.dumps(value, ensure_ascii=False))
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    text = html.escape(str(value))
    return f'<a href="{text}" rel="noopener nofollow">{text}</a>' if text.startswith(('https://', 'http://')) else text


def _fields(fields):
    out = ['<dl>']
    for field in fields:
        lock = '' if field.get('access') == 'public' else f'<span class="lock">{html.escape(field["access"])}</span>'
        out.append(f'<dt>{html.escape(field["label"])}{lock}</dt>')
        if field.get('children'):
            out.append(f'<dd class="nested">{_fields(field["children"])}</dd>')
        else:
            out.append(f'<dd>{_value(field.get("value"))}</dd>')
    out.append('</dl>')
    return ''.join(out)


def render(view: dict) -> str:
    title = html.escape(view.get('name') or 'Digital Product Passport')
    completeness = view.get('completeness') or {}
    score = completeness.get('score')
    ids = [('Passport ID', view.get('passportId')), ('Product identifier', view.get('uniqueProductIdentifier')),
           ('Granularity', view.get('granularity')), ('Status', view.get('status')),
           ('Last update', view.get('lastUpdate')), ('Economic operator', view.get('economicOperatorId')),
           ('Facility', view.get('facilityId'))]
    ids += [(s['name'], s['value']) for s in view.get('specificAssetIds', [])]
    grid = ''.join(f'<div class="kv"><div class="k">{html.escape(k)}</div><div class="v">{_value(v)}</div></div>'
                   for k, v in ids if v)
    chips = [TIER_LABEL.get(view.get('viewerTier'), view.get('viewerTier'))]
    if view.get('category'):
        chips.append(f'{view["category"].upper()} battery')
    if view.get('schemaVersion'):
        chips.append(view['schemaVersion'])
    sections = ''.join(f'<section class="card" aria-labelledby="s{i}"><h2 id="s{i}">{html.escape(s["title"])}</h2>'
                       f'{_fields(s["fields"]) if s["fields"] else "<p class=k>No data published.</p>"}</section>'
                       for i, s in enumerate(view.get('sections', [])))
    notice = ''
    if view.get('restrictedTiersHidden') and view.get('viewerTier') == 'public':
        notice = ('<p class="notice">Some passport data is available only to persons with a legitimate interest or to '
                  'authorities (Regulation (EU) 2023/1542, Art. 77(2)). Sign in with an authorised role to see it.</p>')
    bar = ''
    if score is not None:
        bar = (f'<section class="card"><h2>Completeness</h2><div class="bar" role="img" aria-label="{score} percent">'
               f'<span style="width:{score}%"></span></div><div class="k">{completeness.get("present")} of '
               f'{completeness.get("mandatory")} mandatory Battery Regulation attributes present'
               f'{" for this battery category" if view.get("category") else ""}.</div></section>')
    links = view.get('links', {})
    jsonld = json.dumps({'@context': 'https://schema.org', '@type': 'Product', 'name': view.get('name'),
                         'identifier': view.get('uniqueProductIdentifier'),
                         'subjectOf': {'@type': 'Dataset', 'name': 'Digital Product Passport', 'url': links.get('json'),
                                       'dateModified': view.get('lastUpdate')}}, ensure_ascii=False).replace('</', '<\\/')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<title>{title} · Digital Product Passport</title><style>{STYLE}</style>
<script type="application/ld+json">{jsonld}</script></head><body><main>
<header class="hero"><div class="eyebrow">Digital Product Passport</div><h1>{title}</h1>
<div class="chips">{''.join(f'<span class="chip">{html.escape(c)}</span>' for c in chips if c)}</div>
<div class="grid">{grid}</div></header>{notice}{bar}{sections}
<footer>Machine-readable: <a href="{html.escape(links.get('json', ''))}">passport JSON</a> ·
<a href="{html.escape(links.get('aas', ''))}">AAS API</a> · <a href="{html.escape(links.get('credential', ''))}">verifiable credential</a>
· <a href="{html.escape(links.get('signedShell', ''))}">signed shell (JWS)</a>. This page loads no scripts and no third-party resources.</footer>
</main></body></html>'''
