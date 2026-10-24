"""DPP HTTP routes, GS1 Digital Link resolver, dynamic tier rules and the MCP tool endpoint."""
from __future__ import annotations

import json
import threading
from urllib.parse import quote

from .. import canonical, formula, security, smt
from ..http import HttpError, Response
from ..repository import ValidationFailed
from ..store import Conflict, NotFound
from . import catalog, credential, gs1, passport, tiers, viewer

MCP_PROTOCOL = '2025-06-18'


class DynamicRules:
    """Access rules regenerated from the tier model whenever submodels change."""

    def __init__(self, store):
        self.store = store
        self.enabled = True
        self._compiled = None
        self._lock = threading.Lock()
        store.subscribe(self._invalidate)

    def _invalidate(self, event):
        if event['kind'] in ('submodel', 'shell'):
            self._compiled = None

    def document(self):
        return tiers.access_rules(self.store.iter_all('submodel'))

    def decide(self, subject, right, target, now=None):
        with self._lock:
            if self._compiled is None:
                self._compiled = security.AccessRules(self.document())
            rules = self._compiled
        return rules.decide(subject, right, target, now)


class DppService:
    def __init__(self, forge):
        self.forge = forge
        self.store = forge.store
        self.repo = forge.repository
        self.api = forge.api
        self.base = forge.config.base_url
        if forge.config.access_rules == 'dpp':
            forge.api.rules = DynamicRules(self.store)

    # -- helpers -------------------------------------------------------------------------------
    def tier(self, request):
        subject = request.subject
        return tiers.subject_tier(subject.claims, subject.anonymous)

    def _shell(self, request):
        try:
            shell = self.store.get('shell', canonical.b64url_decode(request.params['aas']))
        except (NotFound, ValueError) as error:
            raise HttpError(404, 'passport not found') from error
        self.api.allow(request, 'READ', 'shell', shell)
        if not passport.is_passport(self.store, shell):
            raise HttpError(404, 'this shell has no passport submodels')
        return shell

    def _require_operator(self, request):
        if self.forge.config.access_rules and self.tier(request) != 'economic-operator':
            raise PermissionError('only economic operators may create or change passports')

    # -- routes -----------------------------------------------------------------------------------
    def register(self, router):
        r = router.add
        r('GET', '/dpp/attributes', self.attributes)
        r('GET', '/dpp/templates', self.templates)
        r('GET', '/dpp/templates/{key}', self.template)
        r('GET', '/dpp/access-rules', self.access_rules)
        r('GET', '/dpp/passports', self.list_passports)
        r('POST', '/dpp/passports', self.create_passport)
        r('GET', '/dpp/passports/{aas}', self.get_passport)
        r('PATCH', '/dpp/passports/{aas}/values', self.update_passport)
        r('GET', '/dpp/passports/{aas}/completeness', self.completeness)
        r('GET', '/dpp/passports/{aas}/template-check', self.template_check)
        r('GET', '/dpp/passports/{aas}/credential', self.credential)
        r('GET', '/dpp/passports/{aas}/registry-payload', self.registry_payload)
        r('GET', '/dpp/passports/{aas}/carbon-rollup', self.carbon_rollup)
        r('GET', '/01/{rest:path}', self.resolve)
        r('GET', '/.well-known/gs1resolver', self.resolver_description)
        r('POST', '/mcp', self.mcp)

    def attributes(self, request):
        category = request.arg('category')
        rows = []
        for attribute in catalog.attributes():
            if category and attribute['applicability'].get(category) not in ('x', 'o'):
                continue
            mapping = catalog.attribute_map()[attribute['id']]
            rows.append({**attribute, 'mapping': mapping['method'], 'elements': mapping['elements']})
        return Response.json({'source': 'Battery Pass Data Attribute Longlist v1.2 (CC-BY-4.0)', 'attributes': rows})

    def templates(self, request):
        return Response.json({k: v for k, v in catalog.manifest().items()})

    def template(self, request):
        key = request.params['key']
        if key not in catalog.TEMPLATE_KEYS:
            raise HttpError(404, 'unknown template')
        return Response.json(catalog.template(key))

    def access_rules(self, request):
        return Response.json(tiers.access_rules(self.store.iter_all('submodel')))

    def list_passports(self, request):
        result = []
        for shell in self.store.iter_all('shell'):
            if not self.api.visible(request, 'shell', shell) or not passport.is_passport(self.store, shell):
                continue
            info = shell.get('assetInformation') or {}
            result.append({'aasId': shell['id'], 'name': shell.get('idShort'), 'globalAssetId': info.get('globalAssetId'),
                           'links': {'json': f'{self.base}/dpp/passports/{canonical.b64url_encode(shell["id"])}'}})
        return Response.json({'result': result})

    def create_passport(self, request):
        self._require_operator(request)
        spec = request.json()
        spec.setdefault('base', self.base)
        try:
            result = passport.create(self.repo, spec, actor=self.api.actor(request))
        except (passport.PassportError, gs1.DigitalLinkError) as error:
            raise HttpError(400, str(error)) from error
        except Conflict as error:
            raise HttpError(409, str(error)) from error
        except ValidationFailed as error:
            raise HttpError(400, str(error), details=error.errors) from error
        result['links'] = {'json': f'{self.base}/dpp/passports/{canonical.b64url_encode(result["aasId"])}',
                           'digitalLink': result['digitalLink']}
        return Response.json(result, 201)

    def get_passport(self, request):
        shell = self._shell(request)
        data = passport.view(self.store, shell, self.tier(request), self.base)
        wants_html = request.arg('format') == 'html' or (
            'text/html' in (request.headers.get('Accept') or '') and request.arg('format') != 'json')
        if wants_html:
            response = Response.text(viewer.render(data), content_type='text/html; charset=utf-8')
            response.headers.update({'Content-Security-Policy': viewer.CSP, 'X-Content-Type-Options': 'nosniff',
                                     'Referrer-Policy': 'no-referrer'})
            return response
        return Response.json(data)

    def update_passport(self, request):
        self._require_operator(request)
        shell = self._shell(request)
        try:
            return Response.json(passport.update_values(self.repo, shell['id'], request.json(), self.api.actor(request)))
        except passport.PassportError as error:
            raise HttpError(400, str(error)) from error
        except ValidationFailed as error:
            raise HttpError(400, str(error), details=error.errors) from error

    def completeness(self, request):
        shell = self._shell(request)
        found, _ = passport.submodels_of(self.store, shell)
        category = request.arg('category') or passport.detect_category(found)
        return Response.json(passport.completeness(found, category))

    def template_check(self, request):
        shell = self._shell(request)
        found, _ = passport.submodels_of(self.store, shell)
        return Response.json({key: {s['id']: smt.check(s, catalog.template(key)) for s in submodels}
                              for key, submodels in found.items()})

    def credential(self, request):
        if not self.forge.signer:
            raise HttpError(501, 'signing is not configured')
        shell = self._shell(request)
        data = passport.view(self.store, shell, self.tier(request), self.base)
        vc = credential.build(data, self.base, 'aasforge DPP issuer')
        if request.arg('format') == 'json':
            return Response.json(vc)
        return Response.text(credential.sign(vc, self.forge.signer), content_type='application/vc+jwt')

    def registry_payload(self, request):
        shell = self._shell(request)
        return Response.json(credential.registry_payload(passport.view(self.store, shell, 'public', self.base)))

    def carbon_rollup(self, request):
        return Response.json(passport.carbon_rollup(self.store, self._shell(request)))

    # -- GS1 Digital Link resolver ------------------------------------------------------------------
    def _find_by_link(self, parts):
        best, level = None, None
        for shell in self.store.iter_all('shell'):
            info = shell.get('assetInformation') or {}
            ids = {s.get('name'): s.get('value') for s in info.get('specificAssetIds') or []}
            gtin = ids.get('gtin') or ids.get('GTIN') or ids.get('01')
            if gtin is None and info.get('globalAssetId', '').count('/01/'):
                try:
                    gtin = gs1.parse_path(info['globalAssetId'].split('://', 1)[-1].split('/', 1)[1])['gtin']
                except (gs1.DigitalLinkError, IndexError):
                    gtin = None
            if not gtin or gs1.normalise_gtin(gtin) != parts['gtin']:
                continue
            serial, batch = ids.get('serialNumber'), ids.get('batchId')
            if parts.get('serialNumber'):
                if serial == parts['serialNumber']:
                    return shell, 'item'
                if serial is None and batch is None and best is None:
                    best, level = shell, 'model'
            elif parts.get('batchId'):
                if batch == parts['batchId'] and serial is None:
                    return shell, 'batch'
                if serial is None and batch is None and best is None:
                    best, level = shell, 'model'
            elif serial is None and batch is None:
                return shell, 'model'
        return best, level

    def resolve(self, request):
        path = '/01/' + request.params['rest']
        try:
            parts = gs1.parse_path(path)
        except gs1.DigitalLinkError as error:
            raise HttpError(400, str(error)) from error
        shell, level = self._find_by_link(parts)
        if shell is None:
            raise HttpError(404, f'no passport is registered for {path}')
        b64 = canonical.b64url_encode(shell['id'])
        anchor = f'{self.base}{path}'
        links = {
            'gs1:defaultLink': [{'href': f'{self.base}/dpp/passports/{b64}?format=html', 'type': 'text/html',
                                 'title': 'Digital Product Passport'}],
            'gs1:pip': [{'href': f'{self.base}/dpp/passports/{b64}?format=html', 'type': 'text/html'}],
            'gs1:sustainabilityInfo': [{'href': f'{self.base}/dpp/passports/{b64}', 'type': 'application/json'}],
            'aas:shell': [{'href': f'{self.base}/api/v3.1/shells/{b64}', 'type': 'application/json'}],
            'dpp:credential': [{'href': f'{self.base}/dpp/passports/{b64}/credential', 'type': 'application/vc+jwt'}],
        }
        headers = {'Link': f'<{anchor}?linkType=all>; rel="linkset"; type="application/linkset+json"',
                   'X-DPP-Granularity': level}
        link_type = request.arg('linkType')
        accept = request.headers.get('Accept') or ''
        if link_type == 'all' or 'application/linkset+json' in accept:
            response = Response.json(gs1.linkset(anchor, links), headers=headers)
            response.content_type = 'application/linkset+json'
            return response
        target = links.get(link_type, links['gs1:defaultLink']) if link_type else links['gs1:defaultLink']
        if link_type and link_type not in links:
            target = links['gs1:defaultLink']
        headers['Location'] = target[0]['href']
        return Response(307, b'', '', headers)

    def resolver_description(self, request):
        return Response.json({'name': 'aasforge GS1 Digital Link resolver', 'resolverRoot': self.base,
                              'supportedPrimaryKeys': ['01'], 'supportedQualifiers': ['22', '10', '21'],
                              'linkTypes': sorted(gs1.LINK_TYPES.values())})

    # -- MCP (Model Context Protocol) tool endpoint -------------------------------------------------
    TOOLS = [
        {'name': 'find_passport', 'description': 'Find a passport by GTIN and optional serial or batch number.',
         'inputSchema': {'type': 'object', 'properties': {'gtin': {'type': 'string'}, 'serial': {'type': 'string'},
                                                          'batch': {'type': 'string'}}, 'required': ['gtin']}},
        {'name': 'get_passport', 'description': 'Return the passport visible to the caller for an AAS id.',
         'inputSchema': {'type': 'object', 'properties': {'aasId': {'type': 'string'}}, 'required': ['aasId']}},
        {'name': 'passport_completeness', 'description': 'Battery Regulation completeness report for a passport.',
         'inputSchema': {'type': 'object', 'properties': {'aasId': {'type': 'string'},
                                                          'category': {'type': 'string', 'enum': list(catalog.CATEGORIES)}},
                         'required': ['aasId']}},
        {'name': 'query_submodels', 'description': 'Run an AAS Query Language (IDTA-01002) JSON query over submodels.',
         'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'object'}}, 'required': ['query']}},
        {'name': 'list_attributes', 'description': 'List Battery Regulation passport attributes for a battery category.',
         'inputSchema': {'type': 'object', 'properties': {'category': {'type': 'string', 'enum': list(catalog.CATEGORIES)}}}},
    ]

    def _tool(self, request, name, args):
        if name == 'find_passport':
            parts = {'gtin': gs1.normalise_gtin(args['gtin'])}
            if args.get('serial'):
                parts['serialNumber'] = args['serial']
            if args.get('batch'):
                parts['batchId'] = args['batch']
            shell, level = self._find_by_link(parts)
            if shell is None or not self.api.visible(request, 'shell', shell):
                return {'found': False}
            return {'found': True, 'aasId': shell['id'], 'granularity': level,
                    'passport': f'{self.base}/dpp/passports/{canonical.b64url_encode(shell["id"])}'}
        if name in ('get_passport', 'passport_completeness'):
            shell = self.store.get('shell', args['aasId'])
            self.api.allow(request, 'READ', 'shell', shell)
            if name == 'get_passport':
                return passport.view(self.store, shell, self.tier(request), self.base)
            found, _ = passport.submodels_of(self.store, shell)
            report = passport.completeness(found, args.get('category') or passport.detect_category(found))
            report['missing'] = [a['name'] for a in report.pop('attributes') if a['mandatory'] and a['status'] != 'present']
            return report
        if name == 'query_submodels':
            candidates = [self.api.readable_submodel(request, s) for s in self.store.iter_all('submodel')
                          if self.api.visible(request, 'submodel', s)]
            candidates = [tiers.filter_for_tier(s, self.tier(request)) or {} for s in candidates]
            results = formula.run_query(args['query'], 'submodel', [c for c in candidates if c])
            return {'count': len(results), 'result': results[:50]}
        if name == 'list_attributes':
            category = args.get('category')
            return [{'id': a['id'], 'name': a['attribute'], 'access': a['access'],
                     'mandatory': a['applicability'].get(category) == 'x' if category else None}
                    for a in catalog.attributes() if not category or a['applicability'].get(category) in ('x', 'o')]
        raise KeyError(name)

    def mcp(self, request):
        try:
            message = request.json()
        except HttpError:
            return Response.json({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}})
        identifier, method = message.get('id'), message.get('method')
        if identifier is None:   # notification
            return Response(202, b'', '')
        try:
            if method == 'initialize':
                result = {'protocolVersion': MCP_PROTOCOL, 'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': {'name': 'aasforge-dpp', 'version': '0.1.0'},
                          'instructions': 'Read-only access to Digital Product Passports, filtered by the caller\'s '
                                          'Battery Regulation access tier.'}
            elif method == 'tools/list':
                result = {'tools': self.TOOLS}
            elif method == 'tools/call':
                params = message.get('params') or {}
                data = self._tool(request, params.get('name'), params.get('arguments') or {})
                result = {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}],
                          'structuredContent': data if isinstance(data, dict) else {'items': data}, 'isError': False}
            elif method == 'ping':
                result = {}
            else:
                return Response.json({'jsonrpc': '2.0', 'id': identifier,
                                      'error': {'code': -32601, 'message': f'method not found: {method}'}})
        except (KeyError, NotFound, gs1.DigitalLinkError, formula.FormulaError, PermissionError) as error:
            result = {'content': [{'type': 'text', 'text': f'{type(error).__name__}: {error}'}], 'isError': True}
        return Response.json({'jsonrpc': '2.0', 'id': identifier, 'result': result})
