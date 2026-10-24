"""Catena-X: Digital Twin Registry (DTR) behaviour and EDC provisioning.

**DTR access semantics.** These follow eclipse-tractusx/sldt-digital-twin-registry,
docs/architecture/6-crosscutting-concepts.md (the "classic" model). When installed, they apply to
the registry, discovery and the shells of the repository:

* a caller's Business Partner Number (BPN) comes from the ``bpn`` claim of its token or API key;
  a caller authenticated as the provider EDC data plane (claim ``edc_dataplane: true``) also
  passes on the consumer's BPN in the ``Edc-Bpn`` header (Tractus-X "additional headers");
* the owner (its BPN is ``bpn`` of this registry, or its tier is economic-operator) sees
  everything;
* a ``specificAssetId`` is visible only to the BPNs listed in its ``externalSubjectId`` (closed by
  default), and a visible one shows only the matching keys of the ``externalSubjectId``;
* ``PUBLIC_READABLE`` makes a specificAssetId visible to everyone, but only for
  ``manufacturerPartId`` and ``assetLifecyclePhase`` (enforced on every write);
* a reader who sees one specificAssetId through their BPN sees the whole shell descriptor. A
  reader who sees only PUBLIC_READABLE ones gets ``id``, those specificAssetIds and the
  ``submodelDescriptors``. Anyone else does not see the shell at all, and discovery matches only
  the specificAssetIds the caller can see;
* standalone submodel descriptors are visible only to the owner (consumers reach submodels through
  the shell descriptors).

**Submodel descriptors** get the Catena-X DSP endpoint (``subprotocol: DSP``,
``subprotocolBody: id=<asset id>;dspEndpoint=<control plane DSP URL>``, ``href`` the data plane's
public URL), next to the direct AAS endpoint.

**EDC provisioning** (:class:`EdcClient`) registers each submodel and the registry as EDC assets,
with policy and contract definitions, through the EDC Management API v3 (JSON-LD, Tractus-X EDC
0.7 and later). The data plane reaches this server with an API key whose claims mark it as
``edc_dataplane``.
"""
from __future__ import annotations

import copy
import json
import urllib.error
import urllib.request

from . import canonical, security
from .dpp import tiers

PUBLIC_READABLE = 'PUBLIC_READABLE'
PUBLIC_READABLE_TYPES = ('manufacturerPartId', 'assetLifecyclePhase')
EDC_NS = 'https://w3id.org/edc/v0.0.1/ns/'
ODRL = 'http://www.w3.org/ns/odrl/2/'
CX_POLICY = 'https://w3id.org/catenax/policy/'
CX_TAXONOMY = 'https://w3id.org/catenax/taxonomy#'
PUBLIC_FIELDS = ('id', 'specificAssetIds', 'submodelDescriptors')


def _subject_values(asset_id: dict) -> list[str]:
    reference = asset_id.get('externalSubjectId') or {}
    return [k.get('value') for k in reference.get('keys') or [] if k.get('value')]


class CatenaX:
    def __init__(self, forge, bpn: str, dataplane_url: str | None = None, dsp_url: str | None = None,
                 public_readable_types=PUBLIC_READABLE_TYPES):
        if not bpn:
            raise ValueError('Catena-X mode needs the BPN of this registry (e.g. BPNL000000000001)')
        self.forge, self.bpn = forge, bpn
        self.dataplane_url, self.dsp_url = dataplane_url, dsp_url
        self.public_readable_types = tuple(public_readable_types)

    def install(self):
        api, repository = self.forge.api, self.forge.repository
        api.subject_hooks.append(self.subject_hook)
        api.descriptor_filters.append(self.descriptor_view)
        api.shell_filters.append(self.shell_view)
        repository.validators.append(self.validate)
        repository.endpoint_hooks.append(self.endpoints)
        return self

    # -- identity ------------------------------------------------------------------------------------
    def subject_hook(self, request):
        """The provider data plane forwards the consumer's BPN (and agreement) as headers."""
        subject = request.subject
        if not subject.claims.get('edc_dataplane'):
            return
        headers = {k.lower(): v for k, v in request.headers.items()}
        bpn = headers.get('edc-bpn')
        claims = dict(subject.claims)
        claims.pop('bpn', None)
        if bpn:
            claims['bpn'] = bpn.strip()
            claims['sub'] = f'edc:{claims["bpn"]}'
        if headers.get('edc-contract-agreement-id'):
            claims['edc_agreement'] = headers['edc-contract-agreement-id']
        request.subject = security.Subject(claims, anonymous=False)

    def is_owner(self, request) -> bool:
        claims = request.subject.claims if request.subject else {}
        return claims.get('bpn') == self.bpn or tiers.subject_tier(claims, request.subject.anonymous) == 'economic-operator'

    def caller_bpn(self, request):
        return (request.subject.claims or {}).get('bpn') if request.subject else None

    # -- visibility ------------------------------------------------------------------------------------
    def visible_asset_ids(self, asset_ids, bpn):
        """(visible specificAssetIds, any visible through the caller's own BPN)."""
        visible, by_bpn = [], False
        for asset_id in asset_ids or []:
            subjects = _subject_values(asset_id)
            matched = [s for s in subjects if bpn and s == bpn]
            public = PUBLIC_READABLE in subjects and asset_id.get('name') in self.public_readable_types
            if not matched and not public:
                continue
            item = copy.deepcopy(asset_id)
            keep = set(matched) | ({PUBLIC_READABLE} if public else set())
            reference = item.get('externalSubjectId')
            if reference:
                reference['keys'] = [k for k in reference.get('keys') or [] if k.get('value') in keep]
            visible.append(item)
            by_bpn = by_bpn or bool(matched)
        return visible, by_bpn

    def descriptor_view(self, request, kind, descriptor):
        if self.is_owner(request):
            return descriptor
        if kind == 'sm':
            return None
        visible, by_bpn = self.visible_asset_ids(descriptor.get('specificAssetIds'), self.caller_bpn(request))
        if not visible:
            return None
        result = copy.deepcopy(descriptor)
        result['specificAssetIds'] = visible
        if not by_bpn:
            result = {k: v for k, v in result.items() if k in PUBLIC_FIELDS}
        return result

    def shell_view(self, request, shell):
        if self.is_owner(request):
            return shell
        info = shell.get('assetInformation') or {}
        if not info.get('specificAssetIds'):
            return shell
        result = copy.deepcopy(shell)
        result['assetInformation']['specificAssetIds'] = self.visible_asset_ids(
            info['specificAssetIds'], self.caller_bpn(request))[0]
        if not result['assetInformation']['specificAssetIds']:
            del result['assetInformation']['specificAssetIds']
        return result

    def validate(self, kind, body):
        if kind == 'shell':
            asset_ids = (body.get('assetInformation') or {}).get('specificAssetIds')
        elif kind == 'aas-descriptor':
            asset_ids = body.get('specificAssetIds')
        else:
            return
        for asset_id in asset_ids or []:
            if PUBLIC_READABLE in _subject_values(asset_id) and asset_id.get('name') not in self.public_readable_types:
                raise ValueError(f'specificAssetId {asset_id.get("name")!r} may not be {PUBLIC_READABLE}; only '
                                 f'{", ".join(self.public_readable_types)} may be (Catena-X DTR rule)')

    # -- endpoints ----------------------------------------------------------------------------------
    def asset_id(self, submodel_id: str) -> str:
        return submodel_id

    def endpoints(self, kind, obj):
        if kind != 'submodel' or not (self.dataplane_url and self.dsp_url):
            return []
        return [{'interface': 'SUBMODEL-3.0', 'protocolInformation': {
            'href': self.dataplane_url, 'endpointProtocol': 'HTTP', 'endpointProtocolVersion': ['1.1'],
            'subprotocol': 'DSP', 'subprotocolBody': f'id={self.asset_id(obj["id"])};dspEndpoint={self.dsp_url}',
            'subprotocolBodyEncoding': 'plain',
            'securityAttributes': [{'type': 'NONE', 'key': 'NONE', 'value': 'NONE'}]}}]


# -- EDC Management API v3 ----------------------------------------------------------------------------
def bpn_policy(policy_id: str, bpns: list[str]) -> dict:
    """Access/usage policy: the consumer's BPN must be one of ``bpns``."""
    constraints = [{'@type': 'Constraint', 'leftOperand': f'{CX_POLICY}BusinessPartnerNumber', 'operator': 'eq',
                    'rightOperand': bpn} for bpn in bpns]
    constraint = constraints[0] if len(constraints) == 1 else {'@type': 'LogicalConstraint', 'or': constraints}
    return _policy(policy_id, [{'action': 'use', 'constraint': constraint}])


def membership_policy(policy_id: str, framework: str | None = None, purpose: str | None = None) -> dict:
    """Catena-X membership, optionally a framework agreement and a usage purpose."""
    constraints = [{'@type': 'Constraint', 'leftOperand': f'{CX_POLICY}Membership', 'operator': 'eq',
                    'rightOperand': 'active'}]
    if framework:
        constraints.append({'@type': 'Constraint', 'leftOperand': f'{CX_POLICY}FrameworkAgreement', 'operator': 'eq',
                            'rightOperand': framework})
    if purpose:
        constraints.append({'@type': 'Constraint', 'leftOperand': f'{CX_POLICY}UsagePurpose', 'operator': 'eq',
                            'rightOperand': purpose})
    constraint = constraints[0] if len(constraints) == 1 else {'@type': 'LogicalConstraint', 'and': constraints}
    return _policy(policy_id, [{'action': 'use', 'constraint': constraint}])


def _policy(policy_id, permissions):
    return {'@context': {'@vocab': EDC_NS, 'odrl': ODRL}, '@type': 'PolicyDefinition', '@id': policy_id,
            'policy': {'@context': 'http://www.w3.org/ns/odrl.jsonld', '@type': 'Set', 'permission': permissions,
                       'prohibition': [], 'obligation': []}}


def submodel_asset(submodel: dict, public_url: str, api_key: str, asset_id: str | None = None) -> dict:
    semantic = ((submodel.get('semanticId') or {}).get('keys') or [{}])[0].get('value')
    properties = {'dct:type': {'@id': 'cx-taxo:Submodel'}, 'cx-common:version': '3.0',
                  'name': submodel.get('idShort') or submodel['id']}
    if semantic:
        properties['aas-semantics:semanticId'] = {'@id': semantic}
    return _asset(asset_id or submodel['id'], properties,
                  f'{public_url}/api/v3.1/submodels/{canonical.b64url_encode(submodel["id"])}', api_key, write=False)


def registry_asset(public_url: str, api_key: str, asset_id: str = 'aasforge-digital-twin-registry') -> dict:
    return _asset(asset_id, {'dct:type': {'@id': 'cx-taxo:DigitalTwinRegistry'}, 'cx-common:version': '3.0',
                             'name': 'Digital Twin Registry'}, f'{public_url}/api/v3.1', api_key, write=True)


def _asset(asset_id, properties, base_url, api_key, write):
    return {'@context': {'@vocab': EDC_NS, 'dct': 'http://purl.org/dc/terms/', 'cx-taxo': CX_TAXONOMY,
                         'cx-common': 'https://w3id.org/catenax/ontology/common#',
                         'aas-semantics': 'https://admin-shell.io/aas/3/0/HasSemantics/'},
            '@type': 'Asset', '@id': asset_id, 'properties': properties,
            'dataAddress': {'@type': 'DataAddress', 'type': 'HttpData', 'baseUrl': base_url, 'proxyPath': 'true',
                            'proxyQueryParams': 'true', 'proxyMethod': 'true' if write else 'false',
                            'proxyBody': 'true' if write else 'false', 'authKey': 'X-API-Key', 'authCode': api_key}}


def contract_definition(definition_id: str, asset_id: str, access_policy: str, contract_policy: str) -> dict:
    return {'@context': {'@vocab': EDC_NS}, '@type': 'ContractDefinition', '@id': definition_id,
            'accessPolicyId': access_policy, 'contractPolicyId': contract_policy,
            'assetsSelector': [{'@type': 'Criterion', 'operandLeft': f'{EDC_NS}id', 'operator': '=',
                                'operandRight': asset_id}]}


class EdcError(RuntimeError):
    pass


class EdcClient:
    """Minimal EDC Management API v3 client (``X-Api-Key`` authentication)."""

    def __init__(self, management_url: str, api_key: str, timeout: float = 30):
        self.base, self.api_key, self.timeout = management_url.rstrip('/'), api_key, timeout

    def request(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f'{self.base}{path}', data, {'Content-Type': 'application/json',
                                                                       'X-Api-Key': self.api_key}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:   # noqa: S310 - configured URL
                payload = response.read()
                return response.status, json.loads(payload) if payload else None
        except urllib.error.HTTPError as error:
            return error.code, None
        except urllib.error.URLError as error:
            raise EdcError(f'EDC management API unreachable: {error.reason}') from error

    def upsert(self, collection: str, body: dict) -> str:
        """Create, or update when it exists (409 on create); returns 'created' or 'updated'."""
        status, _ = self.request('POST', f'/v3/{collection}', body)
        if status in (200, 201, 204):
            return 'created'
        if status == 409:
            status, _ = self.request('PUT', f'/v3/{collection}', body)
            if status in (200, 204):
                return 'updated'
        raise EdcError(f'EDC rejected {collection} {body.get("@id")!r}: HTTP {status}')

    def provision(self, forge, catenax: CatenaX | None, public_url: str, dataplane_key: str, access_policy: dict,
                  contract_policy: dict | None = None, submodel_ids=None, registry: bool = True) -> dict:
        """Register submodels (all, or ``submodel_ids``) and the registry as EDC assets with
        contracts. Idempotent: running it again updates what exists."""
        contract_policy = contract_policy or access_policy
        report = {'policies': {}, 'assets': {}, 'contracts': {}}
        for policy in {access_policy['@id']: access_policy, contract_policy['@id']: contract_policy}.values():
            report['policies'][policy['@id']] = self.upsert('policydefinitions', policy)
        assets = []
        for submodel in forge.store.iter_all('submodel'):
            if submodel_ids is None or submodel['id'] in submodel_ids:
                asset_id = catenax.asset_id(submodel['id']) if catenax else submodel['id']
                assets.append(submodel_asset(submodel, public_url, dataplane_key, asset_id))
        if registry:
            assets.append(registry_asset(public_url, dataplane_key))
        for asset in assets:
            report['assets'][asset['@id']] = self.upsert('assets', asset)
            definition = contract_definition(f'{asset["@id"]}#contract', asset['@id'], access_policy['@id'],
                                             contract_policy['@id'])
            report['contracts'][definition['@id']] = self.upsert('contractdefinitions', definition)
        return report
