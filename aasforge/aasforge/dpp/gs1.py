"""GS1 Digital Link (identification of the data carrier target) and resolver link sets.

The unique product identifier of a passport is a GS1 Digital Link URI
(``https://<resolver>/01/<GTIN-14>[/10/<batch>][/21/<serial>]``) whose GTIN check digit is
validated. The resolver answers with a redirect to the passport page or, for
``Accept: application/linkset+json`` / ``?linkType=all``, an RFC 9264 link set.
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote

DEFAULT_BASE = 'https://id.example.com'
AI_ORDER = ('22', '10', '21')          # GS1 Digital Link key-qualifier order after 01
AI_NAMES = {'10': 'batchId', '21': 'serialNumber', '22': 'consumerProductVariant'}
LINK_TYPES = {
    'gs1:defaultLink': 'https://gs1.org/voc/defaultLink', 'gs1:pip': 'https://gs1.org/voc/pip',
    'gs1:sustainabilityInfo': 'https://gs1.org/voc/sustainabilityInfo',
    'gs1:certificationInfo': 'https://gs1.org/voc/certificationInfo',
    'aas:shell': 'https://admin-shell.io/aas/3/1/AssetAdministrationShell',
    'dpp:credential': 'https://www.w3.org/ns/credentials#VerifiableCredential',
}


class DigitalLinkError(ValueError):
    pass


def check_digit(body: str) -> int:
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def normalise_gtin(gtin: str) -> str:
    digits = re.sub(r'\D', '', str(gtin))
    if len(digits) not in (8, 12, 13, 14):
        raise DigitalLinkError(f'GTIN must have 8, 12, 13 or 14 digits, got {gtin!r}')
    digits = digits.zfill(14)
    if check_digit(digits[:-1]) != int(digits[-1]):
        raise DigitalLinkError(f'GTIN {gtin!r} has an invalid check digit (expected {check_digit(digits[:-1])})')
    return digits


def digital_link(base: str, gtin: str, *, serial=None, batch=None, variant=None) -> str:
    uri = f'{base.rstrip("/")}/01/{normalise_gtin(gtin)}'
    for ai, value in (('22', variant), ('10', batch), ('21', serial)):
        if value:
            uri += f'/{ai}/{quote(str(value), safe="")}'
    return uri


def parse_path(path: str) -> dict:
    """Parse ``/01/<gtin>/10/<lot>/21/<serial>`` into its parts."""
    segments = [s for s in path.split('/') if s]
    if len(segments) < 2 or segments[0] != '01':
        raise DigitalLinkError('a GS1 Digital Link path starts with /01/<GTIN>')
    result = {'gtin': normalise_gtin(segments[1])}
    pairs = segments[2:]
    if len(pairs) % 2:
        raise DigitalLinkError('key qualifiers must come in AI/value pairs')
    seen = []
    for ai, value in zip(pairs[::2], pairs[1::2]):
        if ai not in AI_NAMES:
            raise DigitalLinkError(f'unsupported key qualifier AI {ai}')
        seen.append(ai)
        result[AI_NAMES[ai]] = unquote(value)
    if seen != [ai for ai in AI_ORDER if ai in seen]:
        raise DigitalLinkError('key qualifiers must appear in the order 22, 10, 21')
    return result


def linkset(anchor: str, links: dict) -> dict:
    """RFC 9264 linkset+json with GS1 link-type relations. ``links``: link type -> [link dicts]."""
    entry = {'anchor': anchor}
    for link_type, items in links.items():
        entry[LINK_TYPES.get(link_type, link_type)] = items
    return {'linkset': [entry]}


def link_header(links: dict) -> str:
    parts = []
    for link_type, items in links.items():
        for item in items:
            parts.append(f'<{item["href"]}>; rel="{LINK_TYPES.get(link_type, link_type)}"'
                         + (f'; type="{item["type"]}"' if item.get('type') else ''))
    return ', '.join(parts)
