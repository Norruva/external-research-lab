"""ConceptDescriptions from property dictionaries: ECLASS and IEC Common Data Dictionary (CDD)
tabular exports (CSV/TSV), mapped to the IEC 61360 data specification.

Both publishers ship tables whose column names differ but whose content matches IEC 61360, so
columns are recognised by name (case-insensitive, see ``COLUMNS``):

* ECLASS "Basic/Advanced" CSV (``ECLASS<v>_PR_<lang>.csv``: ``IrdiPR``, ``PreferredName``,
  ``ShortName``, ``Definition``, ``DataType``, ``IrdiUN``, ``ISOLanguageCode``, ...) together with
  the unit table (``..._UN_<lang>.csv``: ``IrdiUN``, ``ShortName``/``DINNotation``) to resolve
  unit symbols. Files of several languages are merged into one multilingual ConceptDescription
  per IRDI;
* IEC CDD exports (``IRDI``/``Code``, ``Preferred name``, ``Short name``, ``Definition``,
  ``Data type`` such as ``REAL_MEASURE_TYPE``, ``Unit``/``Primary unit``).

Value lists (ECLASS VA/VL tables, CDD enumerations) are not imported. ECLASS and CDD content is
licensed by its publishers; this module ships none of it.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

IEC61360 = 'https://admin-shell.io/DataSpecificationTemplates/DataSpecificationIec61360/3/0'
COLUMNS = {
    'id': ('irdipr', 'irdi', 'irdi_pr', 'property irdi', 'code', 'identifier'),
    'version': ('versionnumber', 'version'),
    'revision': ('revisionnumber', 'revision'),
    'name': ('preferredname', 'preferred name', 'preferred_name', 'name'),
    'short': ('shortname', 'short name', 'short_name'),
    'symbol': ('preferredsymbol', 'symbol', 'preferred symbol'),
    'definition': ('definition',),
    'source': ('sourceofdefinition', 'source of definition', 'definition source'),
    'note': ('note',),
    'datatype': ('datatype', 'data type', 'data_type', 'value format'),
    'unit': ('unit', 'primary unit', 'primaryunit', 'unit of measure'),
    'unit_irdi': ('irdiun', 'irdi_un', 'unit irdi', 'unit code', 'unitcode'),
    'language': ('isolanguagecode', 'language', 'language code', 'lang'),
}
UNIT_COLUMNS = {
    'id': ('irdiun', 'irdi', 'code'),
    'symbol': ('shortname', 'dinnotation', 'symbol', 'short name', 'preferredsymbol'),
    'name': ('preferredname', 'preferred name', 'name'),
}


class DictionaryError(ValueError):
    pass


def _read_rows(source) -> list[dict]:
    if isinstance(source, (str, Path)) and Path(source).exists():
        data = Path(source).read_bytes()
    elif isinstance(source, bytes):
        data = source
    else:
        data = str(source).encode()
    text = data.decode('utf-8-sig', errors='replace') if data[:3] == b'\xef\xbb\xbf' or _is_utf8(data) \
        else data.decode('cp1252', errors='replace')
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=';,\t|')
    except csv.Error:
        class dialect(csv.excel):          # never modify csv.excel itself
            delimiter = ';' if sample.count(';') > sample.count(',') else ','
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode('utf-8')
        return True
    except UnicodeDecodeError:
        return False


def _columns(header, spec):
    lowered = {h.strip().lower(): h for h in header or [] if h}
    found = {}
    for key, names in spec.items():
        for name in names:
            if name in lowered:
                found[key] = lowered[name]
                break
    return found


def iec_data_type(text: str | None) -> str | None:
    """ECLASS (``REAL_MEASURE``) or CDD (``REAL_MEASURE_TYPE``) data type -> DataTypeIec61360."""
    if not text:
        return None
    t = text.strip().upper().replace(' ', '_')
    if 'TRANSLATABLE' in t:
        return 'STRING_TRANSLATABLE'
    if 'BOOL' in t:
        return 'BOOLEAN'
    if 'DATE_TIME' in t or 'TIMESTAMP' in t or 'TIME_STAMP' in t:
        return 'TIMESTAMP'
    if 'DATE' in t:
        return 'DATE'
    if t.startswith('TIME'):
        return 'TIME'
    if 'IRDI' in t:
        return 'IRDI'
    if 'URL' in t or 'URI' in t or 'IRI' in t:
        return 'IRI'
    if 'RATIONAL' in t:
        return 'RATIONAL_MEASURE' if 'MEASURE' in t else 'RATIONAL'
    if 'BLOB' in t:
        return 'BLOB'
    if 'FILE' in t:
        return 'FILE'
    if 'HTML' in t:
        return 'HTML'
    if t.startswith('INT') or 'INTEGER' in t or t.startswith('ENUM_INT') or 'NATURAL' in t:
        return 'INTEGER_MEASURE' if 'MEASURE' in t else 'INTEGER_CURRENCY' if 'CURRENCY' in t else 'INTEGER_COUNT'
    if t.startswith('REAL') or 'FLOAT' in t or 'DOUBLE' in t or t.startswith('ENUM_REAL') or 'NUMBER' in t:
        return 'REAL_MEASURE' if 'MEASURE' in t else 'REAL_CURRENCY' if 'CURRENCY' in t else 'REAL_COUNT'
    return 'STRING'


def read_units(*sources) -> dict:
    """Unit IRDI -> symbol from ECLASS/CDD unit tables."""
    result = {}
    for source in sources:
        rows = _read_rows(source)
        columns = _columns(rows[0].keys() if rows else [], UNIT_COLUMNS)
        if 'id' not in columns:
            raise DictionaryError('unit table has no IRDI column (IrdiUN)')
        for row in rows:
            identifier = (row.get(columns['id']) or '').strip()
            symbol = (row.get(columns.get('symbol', ''), '') or '').strip() or \
                (row.get(columns.get('name', ''), '') or '').strip()
            if identifier and symbol:
                result[identifier] = symbol
    return result


def concept_descriptions(*sources, units: dict | None = None, language: str = 'en') -> list[dict]:
    """ConceptDescriptions from one or more property tables (several languages merge per IRDI)."""
    merged: dict = {}
    for source in sources:
        rows = _read_rows(source)
        if not rows:
            continue
        columns = _columns(rows[0].keys(), COLUMNS)
        if 'id' not in columns or 'name' not in columns:
            raise DictionaryError(f'no IRDI or preferred name column among {list(rows[0].keys())[:12]}')
        file_language = _language_of(source) or language
        for row in rows:
            def cell(key):
                return (row.get(columns[key]) or '').strip() if key in columns else ''
            identifier = cell('id')
            if not identifier:
                continue
            lang = (cell('language') or file_language).lower()[:2]
            entry = merged.setdefault(identifier, {'names': {}, 'shorts': {}, 'definitions': {}, 'version': cell('version'),
                                                   'revision': cell('revision'), 'datatype': None, 'unit': None,
                                                   'unit_irdi': None, 'source': None, 'symbol': None})
            if cell('name'):
                entry['names'][lang] = cell('name')
            if cell('short'):
                entry['shorts'][lang] = cell('short')
            if cell('definition'):
                entry['definitions'][lang] = cell('definition')
            entry['datatype'] = entry['datatype'] or iec_data_type(cell('datatype'))
            entry['unit'] = entry['unit'] or cell('unit') or None
            entry['unit_irdi'] = entry['unit_irdi'] or cell('unit_irdi') or None
            entry['source'] = entry['source'] or cell('source') or None
            entry['symbol'] = entry['symbol'] or cell('symbol') or None
    return [_cd(identifier, entry, units or {}) for identifier, entry in merged.items()]


def _language_of(source) -> str | None:
    if isinstance(source, (str, Path)):
        match = re.search(r'_([a-z]{2})(?:[-_][A-Z]{2})?\.(?:csv|tsv|txt)$', str(source))
        return match.group(1) if match else None
    return None


def _strings(values: dict, limit=None):
    items = [{'language': lang, 'text': text[:limit] if limit else text} for lang, text in values.items() if text]
    return sorted(items, key=lambda i: (i['language'] != 'en', i['language']))


def _cd(identifier, entry, units):
    names = _strings(entry['names'], 255)
    content = {'modelType': 'DataSpecificationIec61360', 'preferredName': names}
    shorts = {k: v for k, v in entry['shorts'].items() if len(v) <= 18}
    if shorts:
        content['shortName'] = _strings(shorts)
    if entry['definitions']:
        content['definition'] = _strings(entry['definitions'], 1023)
    if entry['datatype']:
        content['dataType'] = entry['datatype']
    unit = entry['unit'] or (units.get(entry['unit_irdi']) if entry['unit_irdi'] else None)
    if unit:
        content['unit'] = unit
    if entry['unit_irdi']:
        content['unitId'] = {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': entry['unit_irdi']}]}
    if entry['source']:
        content['sourceOfDefinition'] = entry['source']
    if entry['symbol']:
        content['symbol'] = entry['symbol']
    cd = {'modelType': 'ConceptDescription', 'id': identifier,
          'embeddedDataSpecifications': [{'dataSpecification': {'type': 'ExternalReference', 'keys': [
              {'type': 'GlobalReference', 'value': IEC61360}]}, 'dataSpecificationContent': content}]}
    short = (entry['shorts'].get('en') or next(iter(entry['shorts'].values()), '') or
             (entry['names'].get('en') or next(iter(entry['names'].values()), '')))
    id_short = re.sub(r'[^A-Za-z0-9_]', '', short.title().replace(' ', ''))
    if id_short and id_short[0].isalpha():
        cd['idShort'] = id_short[:128]
    if entry['version'] or entry['revision']:
        cd['administration'] = {k: v for k, v in (('version', entry['version'].lstrip('0') or '0' if entry['version'] else None),
                                                  ('revision', entry['revision'].lstrip('0') or '0' if entry['revision'] else None)) if v}
    return cd
