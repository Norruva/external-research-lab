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
import time
import urllib.error
import urllib.parse
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
ASSET_HEADER = 'X-AASForge-Asset'              # set by the data plane from the asset's data address
REGISTRY_ASSET = 'aasforge-digital-twin-registry'
REGISTRY_READ = ('/shell-descriptors', '/lookup/shells')


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
        """Requests from the provider data plane act for the consumer: its BPN and agreement come from
        the headers the data plane adds, and the request is confined to the EDC asset it was made
        for (``X-AASForge-Asset``, set through the asset's data address). Requests without the asset
        header may use the registry only. The data plane key's own claims (tiers, roles) are not
        passed on."""
        subject = request.subject
        if not subject.claims.get('edc_dataplane'):
            return
        bpn = (request.headers.get('Edc-Bpn') or '').strip()
        asset = (request.headers.get(ASSET_HEADER) or REGISTRY_ASSET).strip()
        self._check_scope(request, asset)
        claims = {'sub': f'edc:{bpn}' if bpn else 'edc:unknown', 'edc_dataplane': True, 'edc_asset': asset}
        if bpn:
            claims['bpn'] = bpn
        if request.headers.get('Edc-Contract-Agreement-Id'):
            claims['edc_agreement'] = request.headers['Edc-Contract-Agreement-Id']
        request.subject = security.Subject(claims, anonymous=False)

    @staticmethod
    def _check_scope(request, asset):
        path = request.path
        for base in ('/api/v3.1', '/api/v3.0', '/api/v3.2'):
            if path == base or path.startswith(base + '/'):
                path = path[len(base):] or '/'
                break
        segments = path.split('/')
        if any(s in ('..', '.') or '%2e' in s.lower() for s in segments):
            raise PermissionError('path not allowed through the EDC data plane')
        method = request.method
        if asset == REGISTRY_ASSET:
            allowed = (method == 'GET' and (path == '/description' or path.startswith(REGISTRY_READ))) or \
                (method == 'POST' and path in ('/lookup/shellsByAssetLink', '/query/shell-descriptors'))
        else:
            own = f'/submodels/{canonical.b64url_encode(asset)}'
            allowed = method == 'GET' and (path == own or path.startswith(own + '/'))
        if not allowed:
            raise PermissionError(f'the EDC asset {asset!r} does not cover {method} {path}')

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
# Policy shapes follow the Tractus-X EDC management API walkthrough (DSP 2025-1, Catena-X 2025/9
# policy context): an *access* policy decides who sees an offer in the catalog, a *usage*
# (contract) policy is what the consumer agrees to.
POLICY_CONTEXT = ['https://w3id.org/dspace/2025/1/odrl-profile.jsonld',
                  'https://w3id.org/catenax/2025/9/policy/context.jsonld', {'@vocab': EDC_NS}]
DSP_PROTOCOL = 'dataspace-protocol-http:2025-1'


def bpn_policy(policy_id: str, bpns: list[str], action: str = 'access') -> dict:
    """Only these Business Partner Numbers (checked against their BPN credential)."""
    return _policy(policy_id, [{'action': action, 'constraint': {
        'leftOperand': 'BusinessPartnerNumber', 'operator': 'isAnyOf', 'rightOperand': list(bpns)}}])


def membership_policy(policy_id: str, framework: str | None = 'DataExchangeGovernance:1.0',
                      purpose: str | None = None, action: str = 'use') -> dict:
    """Active Catena-X membership, optionally a framework agreement and a usage purpose."""
    constraints = [{'leftOperand': 'Membership', 'operator': 'eq', 'rightOperand': 'active'}]
    if framework:
        constraints.append({'leftOperand': 'FrameworkAgreement', 'operator': 'eq', 'rightOperand': framework})
    if purpose:
        constraints.append({'leftOperand': 'UsagePurpose', 'operator': 'isAnyOf', 'rightOperand': [purpose]})
    constraint = constraints[0] if len(constraints) == 1 else {'and': constraints}
    return _policy(policy_id, [{'action': action, 'constraint': [constraint]}])


def _policy(policy_id, permissions):
    return {'@context': POLICY_CONTEXT, '@type': 'PolicyDefinition', '@id': policy_id,
            'policy': {'@type': 'Set', 'permission': permissions}}


def submodel_asset(submodel: dict, public_url: str, api_key: str, asset_id: str | None = None) -> dict:
    semantic = ((submodel.get('semanticId') or {}).get('keys') or [{}])[0].get('value')
    properties = {'dct:type': {'@id': 'cx-taxo:Submodel'}, 'cx-common:version': '3.0',
                  'name': submodel.get('idShort') or submodel['id']}
    if semantic:
        properties['aas-semantics:semanticId'] = {'@id': semantic}
    return _asset(asset_id or submodel['id'], properties,
                  f'{public_url}/api/v3.1/submodels/{canonical.b64url_encode(submodel["id"])}', api_key, write=False)


def registry_asset(public_url: str, api_key: str, asset_id: str = REGISTRY_ASSET) -> dict:
    return _asset(asset_id, {'dct:type': {'@id': 'cx-taxo:DigitalTwinRegistry'}, 'cx-common:version': '3.0',
                             'name': 'Digital Twin Registry'}, f'{public_url}/api/v3.1', api_key, write=True)


def _asset(asset_id, properties, base_url, api_key, write):
    return {'@context': {'@vocab': EDC_NS, 'dct': 'http://purl.org/dc/terms/', 'cx-taxo': CX_TAXONOMY,
                         'cx-common': 'https://w3id.org/catenax/ontology/common#',
                         'aas-semantics': 'https://admin-shell.io/aas/3/0/HasSemantics/'},
            '@type': 'Asset', '@id': asset_id, 'properties': properties,
            'dataAddress': {'@type': 'DataAddress', 'type': 'HttpData', 'baseUrl': base_url, 'proxyPath': 'true',
                            'proxyQueryParams': 'true', 'proxyMethod': 'true' if write else 'false',
                            'proxyBody': 'true' if write else 'false', 'authKey': 'X-API-Key', 'authCode': api_key,
                            f'header:{ASSET_HEADER}': asset_id}}


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
        contract_policy = contract_policy or membership_policy(f'{access_policy["@id"]}-usage')
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


# -- consumer side: fetching partners' twins through EDC -------------------------------------------------
def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _term(obj: dict, name: str):
    """A JSON-LD property whether compacted (``dct:type``, ``type``) or expanded (full IRI)."""
    for key, value in obj.items():
        if key == name or key.endswith((f':{name}', f'/{name}', f'#{name}')):
            return value
    return None


class EdcConsumer:
    """Consumer-side EDC Management API: catalog, EDR negotiation (``/v3/edrs``) and data access
    through the EDR's ``endpoint`` and ``authorization`` (Tractus-X EDC management API walkthrough)."""

    def __init__(self, client: EdcClient, poll_interval: float = 0.5, timeout: float = 60):
        self.client, self.poll_interval, self.timeout = client, poll_interval, timeout

    def catalog(self, dsp_url: str, counter_party_id: str, filters: list | None = None,
                protocol: str = DSP_PROTOCOL) -> list[dict]:
        body = {'@context': {'@vocab': EDC_NS}, '@type': 'CatalogRequest', 'counterPartyAddress': dsp_url,
                'counterPartyId': counter_party_id, 'protocol': protocol,
                'querySpec': {'@type': 'QuerySpec', 'offset': 0, 'limit': 100, 'filterExpression': filters or []}}
        status, catalog = self.client.request('POST', '/v3/catalog/request', body)
        if status != 200 or not isinstance(catalog, dict):
            raise EdcError(f'catalog request to {dsp_url} failed: HTTP {status}')
        return _as_list(_term(catalog, 'dataset'))

    def find(self, datasets: list[dict], asset_id: str | None = None, dct_type: str | None = None) -> dict:
        for dataset in datasets:
            identifier = dataset.get('@id') or _term(dataset, 'id')
            kind = _term(dataset, 'type')
            kind = kind.get('@id') if isinstance(kind, dict) else kind
            if asset_id and identifier == asset_id:
                return dataset
            if dct_type and kind and str(kind).endswith(dct_type.split(':')[-1].split('#')[-1]):
                return dataset
        raise EdcError(f'no offer for {asset_id or dct_type} in the partner catalog')

    def negotiate(self, dsp_url: str, provider_id: str, dataset: dict, protocol: str = DSP_PROTOCOL) -> str:
        """Start contract negotiation and EDR transfer for the dataset's first offer; returns its asset id."""
        offer = copy.deepcopy(_as_list(_term(dataset, 'hasPolicy'))[0])
        asset_id = dataset.get('@id') or _term(dataset, 'id')
        offer.update({'@type': 'Offer', 'assigner': provider_id, 'target': asset_id})
        for key in ('prohibition', 'obligation'):
            offer.setdefault(key, [])
        body = {'@context': POLICY_CONTEXT, '@type': 'ContractRequest', 'counterPartyAddress': dsp_url,
                'protocol': protocol, 'policy': offer, 'callbackAddresses': []}
        status, _ = self.client.request('POST', '/v3/edrs', body)
        if status not in (200, 201):
            raise EdcError(f'EDR negotiation for {asset_id} was refused: HTTP {status}')
        return asset_id

    def data_address(self, asset_id: str) -> dict:
        """Wait for the EDR of an asset and return its data address (``endpoint``, ``authorization``)."""
        query = {'@context': {'@vocab': EDC_NS}, '@type': 'QuerySpec',
                 'filterExpression': [{'operandLeft': 'assetId', 'operator': '=', 'operandRight': asset_id}]}
        deadline = time.monotonic() + self.timeout
        while True:
            status, entries = self.client.request('POST', '/v3/edrs/request', query)
            if status == 200 and entries:
                process = _term(entries[-1], 'transferProcessId')
                status, address = self.client.request(
                    'GET', f'/v3/edrs/{urllib.parse.quote(str(process), safe="")}/dataaddress?auto_refresh=true')
                if status == 200 and isinstance(address, dict) and _term(address, 'endpoint'):
                    return {'endpoint': _term(address, 'endpoint'), 'authorization': _term(address, 'authorization')}
            if time.monotonic() > deadline:
                raise EdcError(f'no EDR for {asset_id} within {self.timeout:g} s')
            time.sleep(self.poll_interval)

    def access(self, dsp_url: str, provider_id: str, asset_id: str | None = None, dct_type: str | None = None) -> dict:
        dataset = self.find(self.catalog(dsp_url, provider_id), asset_id, dct_type)
        return self.data_address(self.negotiate(dsp_url, provider_id, dataset))

    @staticmethod
    def call(address: dict, path: str = '', method: str = 'GET', body=None, timeout: float = 30):
        """A request through the provider data plane with the EDR token."""
        url = address['endpoint'].rstrip('/') + path
        data = None if body is None else json.dumps(body).encode()
        headers = {'Authorization': address['authorization'], 'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data, headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:   # noqa: S310 - EDR endpoint
                payload = response.read()
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as error:
            raise EdcError(f'data plane returned HTTP {error.code} for {path or "/"}') from error


class PartnerTwins:
    """A partner's Digital Twin Registry and submodels, reached through EDC.

        twins = PartnerTwins(EdcConsumer(EdcClient(url, key)), dsp_url, partner_bpn)
        ids = twins.lookup([{'name': 'manufacturerPartId', 'value': 'MPN-77'}])
        descriptor = twins.shell(ids[0])
        submodel = twins.submodel(descriptor, 'urn:samm:io.catenax.battery.battery_pass:6.1.0#BatteryPass')
    """

    def __init__(self, consumer: EdcConsumer, dsp_url: str, partner_bpn: str):
        self.consumer, self.dsp_url, self.bpn = consumer, dsp_url, partner_bpn
        self._registry = None

    def registry(self) -> dict:
        if self._registry is None:
            self._registry = self.consumer.access(self.dsp_url, self.bpn, dct_type='DigitalTwinRegistry')
        return self._registry

    def lookup(self, asset_ids: list[dict]) -> list[str]:
        result = EdcConsumer.call(self.registry(), '/lookup/shellsByAssetLink', 'POST', asset_ids)
        return (result or {}).get('result', [])

    def shell(self, aas_id: str) -> dict:
        return EdcConsumer.call(self.registry(), f'/shell-descriptors/{canonical.b64url_encode(aas_id)}')

    def submodel(self, shell_descriptor: dict, semantic_id: str, value_only: bool = False) -> dict:
        """Negotiate and fetch the submodel of ``shell_descriptor`` with ``semantic_id`` (DSP endpoint)."""
        for descriptor in shell_descriptor.get('submodelDescriptors') or []:
            keys = (descriptor.get('semanticId') or {}).get('keys') or []
            if not keys or keys[0].get('value') != semantic_id:
                continue
            for endpoint in descriptor.get('endpoints') or []:
                info = endpoint.get('protocolInformation') or {}
                if info.get('subprotocol') != 'DSP':
                    continue
                body = dict(part.split('=', 1) for part in (info.get('subprotocolBody') or '').split(';') if '=' in part)
                address = self.consumer.access(body.get('dspEndpoint') or self.dsp_url, self.bpn, asset_id=body.get('id'))
                return EdcConsumer.call(address, '/$value' if value_only else '')
        raise EdcError(f'the partner twin has no DSP submodel endpoint for {semantic_id}')
