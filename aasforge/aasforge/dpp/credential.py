"""Passport export as a W3C Verifiable Credential 2.0 aligned with the UN Transparency Protocol
(UNTP) Digital Product Passport, secured as a JWS (VC-JOSE, ``typ: vc+jwt``) with the server's
x5c certificate chain, and the EU DPP registry registration payload (ESPR Art. 13)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

UNTP_CONTEXT = 'https://test.uncefact.org/vocabulary/untp/dpp/0.6.0/'


def _find(view, *names):
    wanted = {n.lower() for n in names}
    stack = [f for s in view['sections'] for f in s['fields']]
    while stack:
        field = stack.pop(0)
        if field['path'].split('.')[-1].split('[')[0].lower() in wanted and 'value' in field:
            value = field['value']
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return next(iter(value[0].values()))
            return value
        stack.extend(field.get('children', []))
    return None


def build(view: dict, issuer_id: str, issuer_name: str) -> dict:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    specific = {s['name']: s['value'] for s in view.get('specificAssetIds', [])}
    product = {'type': ['Product'], 'id': view.get('uniqueProductIdentifier') or view.get('globalAssetId'),
               'name': view.get('name'), 'idScheme': {'type': ['IdentifierScheme'], 'id': 'https://id.gs1.org/01/',
                                                      'name': 'Global Trade Item Number (GTIN)'}}
    if specific.get('serialNumber'):
        product['serialNumber'] = specific['serialNumber']
    if specific.get('batchId'):
        product['batchNumber'] = specific['batchId']
    if view.get('category'):
        product['productCategory'] = [{'type': ['Classification'], 'code': view['category'],
                                       'name': f'{view["category"].upper()} battery (Regulation (EU) 2023/1542)'}]
    subject = {
        'type': ['DigitalProductPassport', 'Product'],
        'id': view.get('passportId'),
        'product': product,
        'granularityLevel': (view.get('granularity') or '').lower() or None,
        'producedByParty': {'type': ['Party'], 'id': view.get('economicOperatorId')},
        'dppStatus': view.get('status'), 'lastUpdate': view.get('lastUpdate'),
        'aasEndpoint': view['links']['aas'],
    }
    footprint = _find(view, 'PcfCO2eq')
    if footprint is not None:
        subject['emissionsScorecard'] = {'type': ['EmissionsPerformance'], 'carbonFootprint': footprint,
                                         'declaredUnit': 'kgCO2e/kWh', 'operationalScope': 'CradleToGate'}
    renewable = _find(view, 'RenewableContent')
    if renewable is not None:
        subject['circularityScorecard'] = {'type': ['CircularityPerformance'], 'renewableContent': renewable}
    subject['completeness'] = view.get('completeness')
    subject = {k: v for k, v in subject.items() if v not in (None, '', [])}
    return {
        '@context': ['https://www.w3.org/ns/credentials/v2', UNTP_CONTEXT],
        'type': ['VerifiableCredential', 'DigitalProductPassport'],
        'id': f'urn:uuid:{uuid.uuid4()}',
        'issuer': {'type': ['CredentialIssuer'], 'id': issuer_id, 'name': issuer_name},
        'validFrom': now,
        'credentialSubject': subject,
    }


def sign(credential: dict, signer) -> str:
    return signer.sign(credential, typ='vc+jwt', extra={'cty': 'vc'})


def registry_payload(view: dict) -> dict:
    """Registration record for the EU DPP registry (ESPR Art. 13(1)): the unique product,
    operator and facility identifiers plus where the passport is reachable. The Commission's
    registry interface is specified by implementing act; field names follow ESPR wording."""
    return {
        'uniqueProductIdentifier': view.get('uniqueProductIdentifier'),
        'uniqueOperatorIdentifier': view.get('economicOperatorId'),
        'uniqueFacilityIdentifier': view.get('facilityId'),
        'digitalProductPassportIdentifier': view.get('passportId'),
        'granularity': view.get('granularity'),
        'productGroup': 'batteries' if view.get('category') else None,
        'dataCarrierTarget': view.get('uniqueProductIdentifier'),
        'passportEndpoints': {'humanReadable': view['links']['html'], 'machineReadable': view['links']['json'],
                              'aas': view['links']['aas'], 'verifiableCredential': view['links']['credential']},
        'status': view.get('status'), 'lastUpdate': view.get('lastUpdate'),
    }
