"""AAS API Part 2 (IDTA-01002) HTTP/REST implementation, versions 3.1 and 3.2 operations.

Served under ``/api/v3.1`` (``/api/v3.0`` and ``/api/v3.2`` are accepted aliases).
"""
from __future__ import annotations

import copy
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from . import canonical, elements, formula, security, signing, sqlfilter, versioning
from .http import FILE_HEADERS, HttpError, Request, Response, Router, content_disposition, safe_filename
from .repository import Repository, ValidationFailed
from .store import Conflict, NotFound

PROFILES = [f'https://admin-shell.io/aas/API/3/1/{spec}' for spec in (
    'AssetAdministrationShellRepositoryServiceSpecification/SSP-001',
    'SubmodelRepositoryServiceSpecification/SSP-001',
    'ConceptDescriptionRepositoryServiceSpecification/SSP-001',
    'AssetAdministrationShellRegistryServiceSpecification/SSP-001',
    'SubmodelRegistryServiceSpecification/SSP-001',
    'DiscoveryServiceSpecification/SSP-001',
    'AasxFileServerServiceSpecification/SSP-001')]
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
RIGHT_FOR = {'GET': 'READ', 'HEAD': 'READ', 'POST': 'CREATE', 'PUT': 'UPDATE', 'PATCH': 'UPDATE', 'DELETE': 'DELETE'}


def _id(segment: str) -> str:
    try:
        return canonical.b64url_decode(segment)
    except ValueError as error:
        raise HttpError(400, str(error)) from error


def _paging(request: Request):
    try:
        limit = int(request.arg('limit', DEFAULT_LIMIT))
    except ValueError as error:
        raise HttpError(400, 'limit must be an integer') from error
    if not 1 <= limit <= MAX_LIMIT:
        raise HttpError(400, f'limit must be between 1 and {MAX_LIMIT}')
    return limit, request.arg('cursor')


def _paged(items, cursor, **extra):
    meta = {'cursor': cursor} if cursor else {}
    meta.update(extra)
    return {'paging_metadata': meta, 'result': items}


def _list_page(items, limit, cursor):
    """Keyset-style paging over an in-memory list (opaque cursor = base64url offset)."""
    start = int(canonical.b64url_decode(cursor)) if cursor else 0
    page = items[start:start + limit]
    nxt = canonical.b64url_encode(str(start + limit)) if start + limit < len(items) else None
    return page, nxt


def _level_extent(request: Request):
    level = request.arg('level', 'deep')
    extent = request.arg('extent', 'withoutBlobValue')
    if level not in ('deep', 'core') or extent not in ('withoutBlobValue', 'withBlobValue'):
        raise HttpError(400, 'level must be deep|core and extent withoutBlobValue|withBlobValue')
    return level, extent


def _last_key(reference) -> str | None:
    keys = (reference or {}).get('keys') or []
    return keys[-1].get('value') if keys else None


MAX_DELEGATE_RESPONSE = 16 * 1024 * 1024
SAFE_INLINE_IMAGES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _covers(grant: str, path: str) -> bool:
    return path == grant or path.startswith(grant + '.') or path.startswith(grant + '[')


def _file_response(data: bytes, content_type, filename, inline_images=False) -> Response:
    """Serve stored user files so a browser never runs them on the API origin."""
    content_type = (content_type or 'application/octet-stream').split(';')[0].strip().lower()
    if inline_images and content_type in SAFE_INLINE_IMAGES:
        headers = {'Content-Disposition': content_disposition(filename, 'inline')}
    else:
        headers = {'Content-Disposition': content_disposition(filename)}
    if not re.fullmatch(r'[a-z0-9.+-]+/[a-z0-9.+-]+', content_type):
        content_type = 'application/octet-stream'
    return Response(200, data, content_type, {**headers, **FILE_HEADERS})


class Api:
    def __init__(self, repository: Repository, authenticator: security.Authenticator,
                 rules: security.AccessRules, signer: signing.Signer | None = None,
                 trusted_certificates: list | None = None):
        self.repo = repository
        self.store = repository.store
        self.auth = authenticator
        self.rules = rules
        self.signer = signer
        self.trusted = trusted_certificates or []
        self.operations = {}      # semanticId -> callable(inputs: dict) -> dict
        # URL prefixes an Operation's invocationDelegation qualifier may point at (empty: none)
        self.delegation_prefixes: list[str] = []
        # callables (request, submodel) -> submodel | None applied after access rules on every read
        self.submodel_guards: list = []
        self.forge = None         # set by app.Forge; needed by exports that rebuild the environment
        self.router = Router()
        self._register()

    # -- auth helpers --------------------------------------------------------------------------
    def authenticate(self, request: Request):
        request.subject = self.auth.authenticate(request.headers)

    def allow(self, request: Request, right=None, kind=None, obj=None, path=None) -> security.Decision:
        right = right or RIGHT_FOR.get(request.method, 'READ')
        decision = self.rules.decide(request.subject, right, security.Target(request.path, kind, obj, path))
        if not decision.allowed:
            raise PermissionError(f'{request.subject.name} may not {right} {request.path}')
        if decision.referables is not None and right != 'READ':
            # the grant covers only some elements: a write must target one of them
            if path is None or not any(_covers(grant, elements.unquote(path)) for grant in decision.referables):
                raise PermissionError(f'{request.subject.name} may not {right} {path or "the whole object"}')
        return decision

    def read_condition(self, request: Request, kind: str):
        """SQL prefilter for objects this subject may read (``None``: no constraint)."""
        candidates = self.rules.candidate_formulas(request.subject, 'READ')
        if candidates is None:
            return None
        formulas, claims = candidates
        return sqlfilter.any_of(sqlfilter.compile_condition(f, kind, self.store.db, claims) for f in formulas)

    def visible(self, request: Request, kind: str, obj: dict) -> bool:
        if not self.rules.decide(request.subject, 'READ', security.Target(request.path, kind, obj)).allowed:
            return False
        return kind != 'submodel' or all(guard(request, obj) is not None for guard in self.submodel_guards)

    def readable_submodel(self, request: Request, submodel: dict) -> dict:
        decision = self.allow(request, 'READ', 'submodel', submodel)
        result = security.restrict_submodel(submodel, decision, request.subject.claims)
        for guard in self.submodel_guards:
            result = guard(request, result)
            if result is None:
                raise PermissionError(f'{request.subject.name} may not READ this submodel')
        return result

    def local_visible(self, request: Request, kind: str, identifier) -> bool:
        """Whether an id held in a descriptor, link or event may be shown: hidden if it names a local
        object the caller cannot read, or (when no longer stored) if the rules deny it by id."""
        if not identifier:
            return True
        try:
            obj = self.store.get(kind, identifier)
        except NotFound:
            if not self.rules.enabled:
                return True
            return self.rules.decide(request.subject, 'READ', security.Target(request.path, kind,
                                                                              {'id': identifier})).allowed
        return self.visible(request, kind, obj)

    def unrestricted(self, request: Request, kind: str, obj: dict) -> bool:
        """READ allowed without FILTERs or element grants (e.g. raw package bytes may be served)."""
        decision = self.rules.decide(request.subject, 'READ', security.Target(request.path, kind, obj))
        if not decision.allowed or decision.filters is not None or decision.referables is not None:
            return False
        return kind != 'submodel' or all(guard(request, obj) == obj for guard in self.submodel_guards)

    def actor(self, request: Request):
        return request.subject.name if request.subject else None

    # Under /api/v3.2 bodies are metamodel 3.2: 3.2-only fields are split into the sidecar on the way
    # in and restored on the way out; the other bases speak 3.1.
    def inbound(self, request, kind, body):
        if request.base == '/api/v3.2':
            return self.repo.from_32(kind, body)
        return body, None

    def outbound(self, request, kind, body):
        return self.repo.as_32(kind, body) if request.base == '/api/v3.2' and self.store.exists(kind, body['id']) else body

    # -- routes ----------------------------------------------------------------------------------
    def _register(self):
        r = self.router.add
        r('GET', '/description', self.description)
        r('GET', '/health', lambda q: Response.json({'status': 'UP', 'counts': self.store.counts()}))
        # AAS repository
        r('GET', '/shells', self.get_shells)
        r('POST', '/shells', self.post_shell)
        r('GET', '/shells/$reference', self.get_shells_reference)
        r('GET', '/shells/$recent-changes', lambda q: self.recent_changes(q, 'shell'))
        r('GET', '/shells/{aas}', self.get_shell)
        r('PUT', '/shells/{aas}', self.put_shell)
        r('DELETE', '/shells/{aas}', lambda q: self.delete_identifiable(q, 'shell', _id(q.params['aas'])))
        r('GET', '/shells/{aas}/$reference',
          lambda q: Response.json(elements.identifiable_reference('shell', self._shell(q)['id'])))
        r('GET', '/shells/{aas}/$history', lambda q: self.history(q, 'shell', _id(q.params['aas'])))
        r('GET', '/shells/{aas}/$signed', lambda q: self.signed(q, 'shell', _id(q.params['aas'])))
        r('GET', '/shells/{aas}/$audit', lambda q: self.audit(q, 'shell', _id(q.params['aas'])))
        r('GET', '/shells/{aas}/asset-information', lambda q: Response.json(self._shell(q).get('assetInformation')))
        r('PUT', '/shells/{aas}/asset-information', self.put_asset_information)
        r('GET', '/shells/{aas}/asset-information/thumbnail', self.get_thumbnail)
        r('PUT', '/shells/{aas}/asset-information/thumbnail', self.put_thumbnail)
        r('DELETE', '/shells/{aas}/asset-information/thumbnail', self.delete_thumbnail)
        r('GET', '/shells/{aas}/submodel-refs', self.get_submodel_refs)
        r('POST', '/shells/{aas}/submodel-refs', self.post_submodel_ref)
        r('DELETE', '/shells/{aas}/submodel-refs/{sm}', self.delete_submodel_ref)
        # Submodel repository and superpath
        r('GET', '/submodels', self.get_submodels)
        r('POST', '/submodels', self.post_submodel)
        for modifier in ('$metadata', '$value', '$reference', '$path'):
            r('GET', f'/submodels/{modifier}', lambda q, m=modifier: self.get_submodels(q, m))
        r('GET', '/submodels/$recent-changes', lambda q: self.recent_changes(q, 'submodel'))
        r('GET', '/submodels/{sm}/$history', lambda q: self.history(q, 'submodel', _id(q.params['sm'])))
        r('GET', '/submodels/{sm}/$signed', lambda q: self.signed(q, 'submodel', _id(q.params['sm'])))
        r('GET', '/submodels/{sm}/$audit', lambda q: self.audit(q, 'submodel', _id(q.params['sm'])))
        for prefix in ('/submodels/{sm}', '/shells/{aas}/submodels/{sm}'):
            r('GET', prefix, self.get_submodel)
            r('PUT', prefix, self.put_submodel)
            r('PATCH', prefix, self.patch_submodel)
            r('DELETE', prefix, self.delete_submodel)
            for modifier in ('$metadata', '$value', '$reference', '$path'):
                r('GET', f'{prefix}/{modifier}', lambda q, m=modifier: self.get_submodel(q, m))
            r('PATCH', f'{prefix}/$metadata', lambda q: self.patch_submodel(q, '$metadata'))
            r('PATCH', f'{prefix}/$value', lambda q: self.patch_submodel(q, '$value'))
            r('GET', f'{prefix}/submodel-elements', self.get_elements)
            r('POST', f'{prefix}/submodel-elements', self.post_element)
            for modifier in ('$metadata', '$value', '$reference', '$path'):
                r('GET', f'{prefix}/submodel-elements/{modifier}', lambda q, m=modifier: self.get_elements(q, m))
            element = f'{prefix}/submodel-elements/{{path}}'
            r('GET', element, self.get_element)
            r('POST', element, self.post_element)
            r('PUT', element, self.put_element)
            r('PATCH', element, self.patch_element)
            r('DELETE', element, self.delete_element)
            for modifier in ('$metadata', '$value', '$reference', '$path'):
                r('GET', f'{element}/{modifier}', lambda q, m=modifier: self.get_element(q, m))
            r('PATCH', f'{element}/$metadata', lambda q: self.patch_element(q, '$metadata'))
            r('PATCH', f'{element}/$value', lambda q: self.patch_element(q, '$value'))
            r('GET', f'{element}/attachment', self.get_attachment)
            r('PUT', f'{element}/attachment', self.put_attachment)
            r('DELETE', f'{element}/attachment', self.delete_attachment)
            r('POST', f'{element}/invoke', lambda q: self.invoke(q, False, False))
            r('POST', f'{element}/invoke/$value', lambda q: self.invoke(q, False, True))
            r('POST', f'{element}/invoke-async', lambda q: self.invoke(q, True, False))
            r('POST', f'{element}/invoke-async/$value', lambda q: self.invoke(q, True, True))
            r('GET', f'{element}/operation-status/{{handle}}', self.operation_status)
            r('GET', f'{element}/operation-results/{{handle}}', lambda q: self.operation_result(q, False))
            r('GET', f'{element}/operation-results/{{handle}}/$value', lambda q: self.operation_result(q, True))
        # Concept descriptions
        r('GET', '/concept-descriptions', self.get_cds)
        r('POST', '/concept-descriptions', lambda q: self.post_identifiable(q, 'concept-description'))
        r('GET', '/concept-descriptions/$recent-changes', lambda q: self.recent_changes(q, 'concept-description'))
        r('GET', '/concept-descriptions/{cd}', self.get_cd)
        r('PUT', '/concept-descriptions/{cd}', lambda q: self.put_identifiable(q, 'concept-description', _id(q.params['cd'])))
        r('DELETE', '/concept-descriptions/{cd}', lambda q: self.delete_identifiable(q, 'concept-description', _id(q.params['cd'])))
        r('GET', '/concept-descriptions/{cd}/$signed', lambda q: self.signed(q, 'concept-description', _id(q.params['cd'])))
        # Query
        r('POST', '/query/shells', lambda q: self.query(q, 'shell'))
        r('POST', '/query/submodels', lambda q: self.query(q, 'submodel'))
        r('POST', '/query/concept-descriptions', lambda q: self.query(q, 'concept-description'))
        r('POST', '/query/shell-descriptors', lambda q: self.query(q, 'aas-descriptor'))
        r('POST', '/query/submodel-descriptors', lambda q: self.query(q, 'sm-descriptor'))
        # Serialization, upload, packages
        r('GET', '/serialization', self.serialization)
        r('POST', '/upload', self.upload)
        r('GET', '/packages', self.get_packages)
        r('POST', '/packages', self.post_package)
        r('GET', '/packages/{pkg}', self.get_package)
        r('PUT', '/packages/{pkg}', self.put_package)
        r('DELETE', '/packages/{pkg}', self.delete_package)
        r('POST', '/packages-async', self.post_package_async)
        r('GET', '/packages-async/status/{handle}', self.async_status)
        r('GET', '/packages-async/result/{handle}', self.async_result)
        # Registry
        r('GET', '/shell-descriptors', lambda q: self.get_descriptors(q, 'aas'))
        r('POST', '/shell-descriptors', lambda q: self.post_descriptor(q, 'aas'))
        r('GET', '/shell-descriptors/{aas}', lambda q: self.get_descriptor(q, 'aas', _id(q.params['aas'])))
        r('PUT', '/shell-descriptors/{aas}', lambda q: self.put_descriptor(q, 'aas', _id(q.params['aas'])))
        r('DELETE', '/shell-descriptors/{aas}', lambda q: self.delete_descriptor(q, 'aas', _id(q.params['aas'])))
        r('GET', '/shell-descriptors/{aas}/submodel-descriptors', self.get_nested_descriptors)
        r('POST', '/shell-descriptors/{aas}/submodel-descriptors', self.post_nested_descriptor)
        r('GET', '/shell-descriptors/{aas}/submodel-descriptors/{sm}', self.get_nested_descriptor)
        r('PUT', '/shell-descriptors/{aas}/submodel-descriptors/{sm}', self.put_nested_descriptor)
        r('DELETE', '/shell-descriptors/{aas}/submodel-descriptors/{sm}', self.delete_nested_descriptor)
        r('GET', '/submodel-descriptors', lambda q: self.get_descriptors(q, 'sm'))
        r('POST', '/submodel-descriptors', lambda q: self.post_descriptor(q, 'sm'))
        r('GET', '/submodel-descriptors/{sm}', lambda q: self.get_descriptor(q, 'sm', _id(q.params['sm'])))
        r('PUT', '/submodel-descriptors/{sm}', lambda q: self.put_descriptor(q, 'sm', _id(q.params['sm'])))
        r('DELETE', '/submodel-descriptors/{sm}', lambda q: self.delete_descriptor(q, 'sm', _id(q.params['sm'])))
        for kind, route in (('aas', 'shell-descriptors'), ('sm', 'submodel-descriptors')):
            for method in ('POST', 'PUT', 'DELETE'):
                r(method, f'/bulk/{route}', lambda q, k=kind: self.bulk_descriptors(q, k))
        r('GET', '/bulk/status/{handle}', self.async_status)
        r('GET', '/bulk/result/{handle}', self.async_result)
        # Discovery
        r('GET', '/lookup/shells', self.lookup_shells)
        r('POST', '/lookup/shellsByAssetLink', self.lookup_by_links)
        r('GET', '/lookup/shells/{aas}', self.get_asset_links)
        r('POST', '/lookup/shells/{aas}', self.post_asset_links)
        r('DELETE', '/lookup/shells/{aas}', self.delete_asset_links)
        # Events and signatures
        r('GET', '/events', self.events)
        r('POST', '/signatures/verify', self.verify_signature)
        r('GET', '/opcua/nodeset', self.opcua_nodeset)
        r('GET', '/signatures/certificate', self.certificate)

    # -- description ------------------------------------------------------------------------------
    def description(self, request):
        return Response.json({'profiles': PROFILES, 'aasforge': {
            'metamodel': '3.1 (reads 3.0 and 3.2; 3.2 fields preserved in a sidecar)',
            'apiExtensions': ['$history', '$recent-changes', '$signed', '/query', '/events', '$audit', '/upload',
                              '/bulk', '/signatures/verify'], 'verification': self.repo.verification}})

    # -- shells ---------------------------------------------------------------------------------------
    def _shell(self, request, key='aas'):
        identifier = _id(request.params[key])
        try:
            shell = self.store.get('shell', identifier)
        except NotFound as error:
            raise HttpError(404, f'shell {identifier!r} not found') from error
        self.allow(request, 'READ' if request.method == 'GET' else None, 'shell', shell)
        return shell

    def _matches_asset_ids(self, shell, wanted):
        info = shell.get('assetInformation') or {}
        have = {(s.get('name'), s.get('value')) for s in info.get('specificAssetIds') or []}
        if info.get('globalAssetId'):
            have.add(('globalAssetId', info['globalAssetId']))
        return all((w.get('name'), w.get('value')) in have for w in wanted)

    def _asset_ids_arg(self, request):
        wanted = []
        for raw in request.args('assetIds'):
            try:
                decoded = json.loads(canonical.b64url_decode(raw))
            except (ValueError, json.JSONDecodeError) as error:
                raise HttpError(400, 'assetIds must be base64url-encoded SpecificAssetId JSON') from error
            wanted += decoded if isinstance(decoded, list) else [decoded]
        return wanted

    def get_shells(self, request):
        limit, cursor = _paging(request)
        wanted = self._asset_ids_arg(request)
        items, nxt = [], cursor
        while len(items) < limit:
            batch, nxt = self.store.list('shell', id_short=request.arg('idShort'), limit=limit, cursor=nxt,
                                         where=self.read_condition(request, 'shell'))
            items += [s for s in batch if self.visible(request, 'shell', s)
                      and (not wanted or self._matches_asset_ids(s, wanted))]
            if not nxt:
                break
        items = [self.outbound(request, 'shell', s) for s in items[:limit]]
        return Response.json(_paged(items, nxt if len(items) >= limit else None))

    def get_shells_reference(self, request):
        response = json.loads(self.get_shells(request).body)
        response['result'] = [elements.identifiable_reference('shell', s['id']) for s in response['result']]
        return Response.json(response)

    def get_shell(self, request):
        return Response.json(self.outbound(request, 'shell', self._shell(request)))

    def post_identifiable(self, request, kind):
        body, sidecar = self.inbound(request, kind, request.json())
        self.allow(request, 'CREATE', kind, body)
        try:
            _, _, warnings = self.repo.save(kind, body, actor=self.actor(request), must_exist=False, sidecar=sidecar)
        except Conflict as error:
            raise HttpError(409, str(error)) from error
        except ValidationFailed as error:
            raise HttpError(400, str(error), details=error.errors) from error
        headers = {'Location': f'{request.base}/{ {"shell": "shells", "submodel": "submodels"}.get(kind, "concept-descriptions")}'
                               f'/{canonical.b64url_encode(body["id"])}'}
        if warnings:
            headers['X-AAS-Verification-Warnings'] = str(len(warnings))
        return Response.json(body, 201, headers)

    def put_identifiable(self, request, kind, identifier):
        body, sidecar = self.inbound(request, kind, request.json())
        if body.get('id') != identifier:
            raise HttpError(400, 'id in body does not match the path')
        existing = self.store.exists(kind, identifier)
        if existing:
            self.allow(request, 'UPDATE', kind, self.store.get(kind, identifier))
        self.allow(request, 'UPDATE' if existing else 'CREATE', kind, body)
        try:
            created, _, _ = self.repo.save(kind, body, actor=self.actor(request), sidecar=sidecar)
        except ValidationFailed as error:
            raise HttpError(400, str(error), details=error.errors) from error
        return Response.json(body, 201) if created else Response.empty()

    def delete_identifiable(self, request, kind, identifier):
        try:
            obj = self.store.get(kind, identifier)
        except NotFound as error:
            raise HttpError(404, f'{kind} {identifier!r} not found') from error
        self.allow(request, 'DELETE', kind, obj)
        self.repo.remove(kind, identifier, actor=self.actor(request))
        return Response.empty()

    def post_shell(self, request):
        return self.post_identifiable(request, 'shell')

    def put_shell(self, request):
        return self.put_identifiable(request, 'shell', _id(request.params['aas']))

    def put_asset_information(self, request):
        shell = self._shell(request)
        info = request.json()
        self.allow(request, 'UPDATE', 'shell', shell)
        self._save_shell_change(request, shell['id'], lambda s: s.__setitem__('assetInformation', info))
        return Response.empty()

    def _save_shell_change(self, request, shell_id, mutate):
        try:
            self.repo.update_shell(shell_id, mutate, actor=self.actor(request))
        except ValidationFailed as error:
            raise HttpError(400, str(error), details=error.errors) from error

    def get_thumbnail(self, request):
        shell = self._shell(request)
        try:
            attachment = self.store.get_attachment('shell', shell['id'], 'thumbnail')
        except NotFound as error:
            raise HttpError(404, 'no thumbnail') from error
        return _file_response(attachment['data'], attachment['content_type'], attachment['filename'] or 'thumbnail',
                              inline_images=True)

    def put_thumbnail(self, request):
        shell = self._shell(request)
        self.allow(request, 'UPDATE', 'shell', shell)
        parts = request.multipart()
        upload = parts.get('file') or next(iter(parts.values()), None)
        if not upload:
            raise HttpError(400, 'multipart field "file" is required')
        filename = safe_filename((parts.get('fileName') or {}).get('data', b'').decode('utf-8', 'replace')
                                 or upload['filename'], 'thumbnail')
        self.store.put_attachment('shell', shell['id'], 'thumbnail', upload['data'], upload['content_type'], filename)

        def mutate(s):
            s['assetInformation']['defaultThumbnail'] = {'path': f'/aasx/thumbnails/{filename}',
                                                         'contentType': upload['content_type']}
        self._save_shell_change(request, shell['id'], mutate)
        return Response.empty()

    def delete_thumbnail(self, request):
        shell = self._shell(request)
        self.allow(request, 'DELETE', 'shell', shell)
        try:
            self.store.delete_attachment('shell', shell['id'], 'thumbnail')
        except NotFound as error:
            raise HttpError(404, 'no thumbnail') from error
        self._save_shell_change(request, shell['id'], lambda s: s['assetInformation'].pop('defaultThumbnail', None))
        return Response.empty()

    def get_submodel_refs(self, request):
        shell = self._shell(request)
        limit, cursor = _paging(request)
        page, nxt = _list_page(shell.get('submodels') or [], limit, cursor)
        return Response.json(_paged(page, nxt))

    def post_submodel_ref(self, request):
        shell = self._shell(request)
        reference = request.json()
        self.allow(request, 'UPDATE', 'shell', shell)
        if any(_last_key(r) == _last_key(reference) for r in shell.get('submodels') or []):
            raise HttpError(409, 'submodel reference already exists')
        self._save_shell_change(request, shell['id'], lambda s: s.setdefault('submodels', []).append(reference))
        return Response.json(reference, 201)

    def delete_submodel_ref(self, request):
        shell = self._shell(request)
        target = _id(request.params['sm'])
        self.allow(request, 'UPDATE', 'shell', shell)
        if not any(_last_key(r) == target for r in shell.get('submodels') or []):
            raise HttpError(404, 'submodel reference not found')

        def mutate(s):
            s['submodels'] = [r for r in s['submodels'] if _last_key(r) != target]
            if not s['submodels']:
                s.pop('submodels')
        self._save_shell_change(request, shell['id'], mutate)
        return Response.empty()

    # -- submodels ----------------------------------------------------------------------------
    def _submodel_id(self, request):
        submodel_id = _id(request.params['sm'])
        if 'aas' in request.params:
            shell = self._shell(request)
            if not any(_last_key(r) == submodel_id for r in shell.get('submodels') or []):
                raise HttpError(404, f'shell does not reference submodel {submodel_id!r}')
        return submodel_id

    def _submodel(self, request, *, raw=False):
        submodel_id = self._submodel_id(request)
        try:
            submodel = self.store.get('submodel', submodel_id)
        except NotFound as error:
            raise HttpError(404, f'submodel {submodel_id!r} not found') from error
        if raw:
            return submodel
        return self.readable_submodel(request, submodel)

    def _render_submodel(self, request, submodel, modifier):
        level, extent = _level_extent(request)
        if modifier == '$value':
            return elements.submodel_value_only(submodel, extent, level)
        if modifier == '$metadata':
            return elements.metadata(submodel)
        if modifier == '$reference':
            return elements.identifiable_reference('submodel', submodel['id'])
        if modifier == '$path':
            return elements.all_paths(submodel, level)
        return elements.apply_level_extent(self.outbound(request, 'submodel', submodel), level, extent)

    def get_submodels(self, request, modifier=None):
        limit, cursor = _paging(request)
        semantic = request.arg('semanticId')
        if semantic:
            semantic = _id(semantic)
        items, nxt = [], cursor
        while len(items) < limit:
            batch, nxt = self.store.list('submodel', id_short=request.arg('idShort'), semantic_id=semantic,
                                         limit=limit, cursor=nxt, where=self.read_condition(request, 'submodel'))
            for submodel in batch:
                if self.visible(request, 'submodel', submodel):
                    items.append(self._render_submodel(request, self.readable_submodel(request, submodel), modifier))
            if not nxt:
                break
        return Response.json(_paged(items[:limit], nxt if len(items) >= limit else None))

    def post_submodel(self, request):
        return self.post_identifiable(request, 'submodel')

    def get_submodel(self, request, modifier=None):
        return Response.json(self._render_submodel(request, self._submodel(request), modifier))

    def put_submodel(self, request):
        return self.put_identifiable(request, 'submodel', self._submodel_id(request))

    def patch_submodel(self, request, modifier=None):
        submodel_id = self._submodel_id(request)
        payload = request.json()
        current = self._submodel(request, raw=True)
        self.allow(request, 'UPDATE', 'submodel', current)

        def mutate(submodel):
            if modifier == '$value':
                elements.patch_submodel_value(submodel, payload)
            elif modifier == '$metadata':
                elements.patch_metadata(submodel, payload)
            else:
                if payload.get('id') not in (None, submodel['id']):
                    raise HttpError(400, 'id cannot be changed with PATCH')
                submodel.clear()
                submodel.update({**current, **payload, 'id': current['id']})
        self._mutate_submodel(request, submodel_id, mutate)
        return Response.empty()

    def delete_submodel(self, request):
        submodel_id = self._submodel_id(request)
        if 'aas' in request.params:
            shell = self._shell(request)
            self.allow(request, 'UPDATE', 'shell', shell)
        return self.delete_identifiable(request, 'submodel', submodel_id)

    def _mutate_submodel(self, request, submodel_id, mutate, path=None):
        try:
            return self.repo.update_submodel(submodel_id, mutate, actor=self.actor(request), path=path)[0]
        except elements.PathError as error:
            raise HttpError(404, str(error)) from error
        except FileExistsError as error:
            raise HttpError(409, f'element {error} already exists') from error
        except elements.ValueTypeError as error:
            raise HttpError(400, str(error)) from error
        except ValidationFailed as error:
            raise HttpError(400, str(error), details=error.errors) from error
        except NotFound as error:
            raise HttpError(404, str(error)) from error

    # -- submodel elements ----------------------------------------------------------------------------
    def _render_element(self, request, submodel, path, element, modifier):
        level, extent = _level_extent(request)
        if modifier == '$value':
            return {element['idShort']: elements.value_only(element, extent, level)} if element.get('idShort') \
                else elements.value_only(element, extent, level)
        if modifier == '$metadata':
            return elements.metadata(element)
        if modifier == '$reference':
            return elements.reference(submodel, path)
        if modifier == '$path':
            nested = {'submodelElements': [element]}
            return [path] + [f'{path}{p[len(element.get("idShort", "")):]}' for p in elements.all_paths(nested, level)[1:]]
        return elements.apply_level_extent(element, level, extent)

    def get_elements(self, request, modifier=None):
        submodel = self._submodel(request)
        limit, cursor = _paging(request)
        level, extent = _level_extent(request)
        top = submodel.get('submodelElements') or []
        page, nxt = _list_page(top, limit, cursor)
        if modifier == '$value':
            result = {e['idShort']: elements.value_only(e, extent, level) for e in page}
        elif modifier == '$path':
            paths = elements.all_paths(submodel, level)
            result, nxt = _list_page(paths, limit, cursor)
        else:
            result = [self._render_element(request, submodel, e.get('idShort'), e, modifier) for e in page]
        return Response.json(_paged(result, nxt))

    def _element(self, request, submodel):
        try:
            return elements.get(submodel, request.params['path'])
        except elements.PathError as error:
            raise HttpError(404, str(error)) from error

    def get_element(self, request, modifier=None):
        submodel = self._submodel(request)
        path = request.params['path']
        element = self._element(request, submodel)
        return Response.json(self._render_element(request, submodel, elements.unquote(path), element, modifier))

    def post_element(self, request):
        submodel_id = self._submodel_id(request)
        element = request.json()
        self.repo.check_element(element)
        self.allow(request, 'CREATE', 'submodel', self._submodel(request, raw=True))
        parent = request.params.get('path')
        new_path = self._mutate_submodel(request, submodel_id,
                                         lambda s: elements.insert(s, elements.unquote(parent) if parent else None, element),
                                         path=parent)
        return Response.json(element, 201, {'X-AAS-IdShortPath': new_path})

    def put_element(self, request):
        submodel_id = self._submodel_id(request)
        element = request.json()
        self.repo.check_element(element)
        path = elements.unquote(request.params['path'])
        self.allow(request, 'UPDATE', 'submodel', self._submodel(request, raw=True), path)
        self._mutate_submodel(request, submodel_id, lambda s: elements.replace(s, path, element), path=path)
        return Response.empty()

    def patch_element(self, request, modifier=None):
        submodel_id = self._submodel_id(request)
        payload = request.json()
        path = elements.unquote(request.params['path'])
        self.allow(request, 'UPDATE', 'submodel', self._submodel(request, raw=True), path)

        def mutate(submodel):
            element = elements.get(submodel, path)
            if modifier == '$value':
                if isinstance(payload, dict) and element.get('idShort') in payload and len(payload) == 1 \
                        and element.get('modelType') not in ('SubmodelElementCollection', 'Entity'):
                    elements.patch_value(element, payload[element['idShort']])
                else:
                    elements.patch_value(element, payload)
            elif modifier == '$metadata':
                elements.patch_metadata(element, payload)
            else:
                merged = {**element, **payload, 'modelType': element['modelType']}
                self.repo.check_element(merged)
                element.clear()
                element.update(merged)
        self._mutate_submodel(request, submodel_id, mutate, path=path)
        return Response.empty()

    def delete_element(self, request):
        submodel_id = self._submodel_id(request)
        path = elements.unquote(request.params['path'])
        self.allow(request, 'DELETE', 'submodel', self._submodel(request, raw=True), path)
        self._mutate_submodel(request, submodel_id, lambda s: elements.remove(s, path), path=path)
        try:
            self.store.delete_attachment('submodel', submodel_id, path)
        except NotFound:
            pass
        return Response.empty()

    def get_attachment(self, request):
        submodel = self._submodel(request)
        element = self._element(request, submodel)
        if element.get('modelType') != 'File':
            raise HttpError(400, 'attachments exist only for File elements')
        try:
            attachment = self.store.get_attachment('submodel', submodel['id'], elements.unquote(request.params['path']))
        except NotFound as error:
            raise HttpError(404, 'no attachment stored for this element') from error
        return _file_response(attachment['data'], attachment['content_type'] or element.get('contentType'),
                              attachment['filename'] or 'file')

    def put_attachment(self, request):
        submodel_id = self._submodel_id(request)
        path = elements.unquote(request.params['path'])
        submodel = self._submodel(request, raw=True)
        element = self._element(request, submodel)
        if element.get('modelType') != 'File':
            raise HttpError(400, 'attachments exist only for File elements')
        self.allow(request, 'UPDATE', 'submodel', submodel, path)
        parts = request.multipart()
        upload = parts.get('file') or next(iter(parts.values()), None)
        if not upload:
            raise HttpError(400, 'multipart field "file" is required')
        filename = safe_filename((parts.get('fileName') or {}).get('data', b'').decode('utf-8', 'replace')
                                 or upload['filename'], 'file')
        self.store.put_attachment('submodel', submodel_id, path, upload['data'], upload['content_type'], filename)

        def mutate(s):
            target = elements.get(s, path)
            target['value'] = f'/aasx/files/{filename}'
            target['contentType'] = upload['content_type'] or target.get('contentType') or 'application/octet-stream'
        self._mutate_submodel(request, submodel_id, mutate, path=path)
        return Response.empty()

    def delete_attachment(self, request):
        submodel_id = self._submodel_id(request)
        path = elements.unquote(request.params['path'])
        self.allow(request, 'DELETE', 'submodel', self._submodel(request, raw=True), path)
        try:
            self.store.delete_attachment('submodel', submodel_id, path)
        except NotFound as error:
            raise HttpError(404, 'no attachment stored for this element') from error
        self._mutate_submodel(request, submodel_id, lambda s: elements.get(s, path).pop('value', None), path=path)
        return Response.empty()

    # -- operations -------------------------------------------------------------------------------
    def register_operation(self, semantic_id: str, function):
        """Register a local implementation for Operations with this semanticId."""
        self.operations[semantic_id] = function

    def _run_operation(self, operation: dict, body: dict, value_only: bool):
        delegate = next((q.get('value') for q in operation.get('qualifiers') or []
                         if q.get('type') == 'invocationDelegation'), None)
        if delegate:
            return self._delegate(delegate, body)
        semantic = ((operation.get('semanticId') or {}).get('keys') or [{}])[0].get('value')
        function = self.operations.get(semantic)
        if function is None:
            raise HttpError(501, f'operation {operation.get("idShort")!r} has no implementation or invocationDelegation')
        if value_only:
            inputs = dict(body.get('inputArguments') or {})
            inputs.update(body.get('inoutputArguments') or {})
        else:
            inputs = {v['value']['idShort']: elements.value_only(v['value'])
                      for v in (body.get('inputArguments') or []) + (body.get('inoutputArguments') or [])}
        outputs = function(inputs) or {}
        if value_only:
            return {'executionState': 'Completed', 'success': True, 'outputArguments': outputs}
        variables = []
        for variable in operation.get('outputVariables') or []:
            element = copy.deepcopy(variable['value'])
            if element.get('idShort') in outputs:
                elements.patch_value(element, outputs[element['idShort']])
            variables.append({'value': element})
        return {'executionState': 'Completed', 'success': True, 'outputArguments': variables}

    def _delegate(self, url: str, body: dict):
        """Forward an invocation. Model data names the URL, so only configured prefixes are reachable,
        over http(s), without redirects, with a bounded response."""
        scheme = urllib.parse.urlsplit(url).scheme
        if scheme not in ('http', 'https') or not any(url.startswith(p) for p in self.delegation_prefixes):
            raise HttpError(403, 'invocationDelegation target is not in the configured allow-list')
        request = urllib.request.Request(url, json.dumps(body).encode(), {'Content-Type': 'application/json'},
                                         method='POST')
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=30) as response:
                data = response.read(MAX_DELEGATE_RESPONSE + 1)
        except urllib.error.HTTPError as error:
            raise HttpError(502, f'delegated operation returned HTTP {error.code}') from error
        except (urllib.error.URLError, OSError) as error:
            raise HttpError(502, 'delegated operation is unreachable') from error
        if len(data) > MAX_DELEGATE_RESPONSE:
            raise HttpError(502, 'delegated operation response is too large')
        try:
            return json.loads(data or b'{}')
        except json.JSONDecodeError as error:
            raise HttpError(502, 'delegated operation did not return JSON') from error

    def invoke(self, request, asynchronous: bool, value_only: bool):
        submodel = self._submodel(request, raw=True)
        operation = self._element(request, submodel)
        if operation.get('modelType') != 'Operation':
            raise HttpError(400, 'element is not an Operation')
        self.allow(request, 'EXECUTE', 'submodel', submodel, request.params['path'])
        body = request.json() if request.body else {}
        if not asynchronous:
            return Response.json(self._run_operation(operation, body, value_only))
        handle = self.store.new_operation('operation')

        def worker():
            try:
                self.store.finish_operation(handle, 'Completed', self._run_operation(operation, body, value_only))
            except Exception as error:  # noqa: BLE001 - reported through the handle
                self.store.finish_operation(handle, 'Failed', {'executionState': 'Failed', 'success': False,
                                                               'messages': [{'text': str(error)}]})
        threading.Thread(target=worker, daemon=True).start()
        location = f'{request.base}{request.path.rsplit("/invoke", 1)[0]}/operation-status/{handle}'
        return Response(202, b'', '', {'Location': location})

    def operation_status(self, request):
        self.allow(request, 'READ')
        try:
            operation = self.store.get_operation(request.params['handle'])
        except NotFound as error:
            raise HttpError(404, 'unknown handle') from error
        if operation['status'] != 'Running':
            location = f'{request.base}{request.path.replace("/operation-status/", "/operation-results/")}'
            return Response.json({'executionState': operation['status']}, 302, {'Location': location})
        return Response.json({'executionState': 'Running'})

    def operation_result(self, request, value_only):
        self.allow(request, 'READ')
        try:
            operation = self.store.get_operation(request.params['handle'])
        except NotFound as error:
            raise HttpError(404, 'unknown handle') from error
        return Response.json(operation['result'] or {'executionState': operation['status']})

    # -- concept descriptions --------------------------------------------------------------------
    def get_cds(self, request):
        limit, cursor = _paging(request)
        items, nxt = self.store.list('concept-description', id_short=request.arg('idShort'), limit=limit, cursor=cursor,
                                     where=self.read_condition(request, 'concept-description'))
        case_of = request.arg('isCaseOf')
        if case_of:
            target = _id(case_of)
            items = [c for c in items if any(_last_key(r) == target for r in c.get('isCaseOf') or [])]
        data_spec = request.arg('dataSpecificationRef')
        if data_spec:
            target = _id(data_spec)
            items = [c for c in items if any(_last_key(e.get('dataSpecification')) == target
                                             for e in c.get('embeddedDataSpecifications') or [])]
        return Response.json(_paged([c for c in items if self.visible(request, 'concept-description', c)], nxt))

    def get_cd(self, request):
        identifier = _id(request.params['cd'])
        try:
            cd = self.store.get('concept-description', identifier)
        except NotFound as error:
            raise HttpError(404, f'concept description {identifier!r} not found') from error
        self.allow(request, 'READ', 'concept-description', cd)
        return Response.json(self.outbound(request, 'concept-description', cd))

    # -- history, recent changes, audit, signatures ----------------------------------------------
    def history(self, request, kind, identifier):
        date = request.arg('date')
        if not date:
            raise HttpError(400, 'query parameter "date" (ISO 8601) is required')
        try:
            moment = datetime.fromisoformat(date.replace('Z', '+00:00'))
        except ValueError as error:
            raise HttpError(400, 'date must be ISO 8601') from error
        stamp = moment.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        try:
            obj = self.store.get(kind, identifier, at=stamp)
        except NotFound as error:
            raise HttpError(404, f'{kind} {identifier!r} did not exist at {date}') from error
        self.allow(request, 'READ', kind, obj)
        if kind == 'submodel':
            obj = self.readable_submodel(request, obj)
        return Response.json(obj)

    def audit(self, request, kind, identifier):
        try:
            obj = self.store.get(kind, identifier)
        except NotFound as error:
            raise HttpError(404, f'{kind} {identifier!r} not found') from error
        self.allow(request, 'READ', kind, obj)
        revisions = self.store.history(kind, identifier)
        writer = self.rules.decide(request.subject, 'UPDATE', security.Target(request.path, kind, obj))
        if not writer.allowed or writer.referables is not None:
            for revision in revisions:
                revision.pop('actor', None)      # who changed a passport is for those who may change it
        return Response.json({'id': identifier, 'revisions': revisions})

    def recent_changes(self, request, kind):
        limit, cursor = _paging(request)
        semantic = request.arg('semanticId')
        items, nxt = self.store.list(kind, id_short=request.arg('idShort'),
                                     semantic_id=_id(semantic) if semantic else None,
                                     created_from=request.arg('createdFrom'), updated_from=request.arg('updatedFrom'),
                                     limit=limit, cursor=cursor, order='updated')
        result = []
        for obj in items:
            if not self.visible(request, kind, obj):
                continue
            meta = self.store.meta(kind, obj['id'])
            change = {'id': obj['id'], 'createdAt': meta['created_at'], 'updatedAt': meta['updated_at']}
            if kind == 'shell' and (obj.get('assetInformation') or {}).get('globalAssetId'):
                change['globalAssetId'] = obj['assetInformation']['globalAssetId']
            if kind == 'submodel':
                for key in ('semanticId', 'supplementalSemanticIds'):
                    if obj.get(key):
                        change[key] = obj[key]
            result.append(change)
        return Response.json(_paged(result, nxt))

    def signed(self, request, kind, identifier):
        if not self.signer:
            raise HttpError(501, 'signing is not configured')
        try:
            obj = self.store.get(kind, identifier)
        except NotFound as error:
            raise HttpError(404, f'{kind} {identifier!r} not found') from error
        self.allow(request, 'READ', kind, obj)
        if kind == 'submodel':
            obj = self.readable_submodel(request, obj)
        return Response.text(self.signer.sign(obj), content_type='text/plain; charset=utf-8')

    def verify_signature(self, request):
        jws = request.body.decode('utf-8').strip()
        if jws.startswith('{'):
            jws = json.loads(jws).get('jws', '')
        try:
            result = signing.verify(jws, self.trusted + ([self.signer.certificate_pem()] if self.signer else []))
        except signing.SignatureError as error:
            return Response.json({'valid': False, 'error': str(error)}, 422)
        result['valid'] = True
        kind = {'AssetAdministrationShell': 'shell', 'Submodel': 'submodel',
                'ConceptDescription': 'concept-description'}.get(result['payload'].get('modelType'))
        if kind and result['payload'].get('id') and self.store.exists(kind, result['payload']['id']):
            current = self.store.get(kind, result['payload']['id'])
            if self.visible(request, kind, current):
                if kind == 'submodel':
                    current = self.readable_submodel(request, current)
                result['matchesCurrent'] = canonical.digest(current) == result['payload_digest']
        result.pop('payload')
        return Response.json(result)

    def opcua_nodeset(self, request):
        """OPC UA NodeSet2 (I4AAS) of what the caller may read; load it after Opc.Ua.I4AAS.NodeSet2.xml."""
        self.allow(request, 'READ')
        from . import opcua
        if self.forge is None:
            raise HttpError(501, 'OPC UA export is not available')
        try:
            data = opcua.export_nodeset(self.forge, None if request.subject.anonymous else request.subject.claims)
        except RuntimeError as error:
            raise HttpError(501, str(error)) from error
        return Response(200, data, 'application/xml', {'Content-Disposition': content_disposition('aasforge.NodeSet2.xml')})

    def certificate(self, request):
        if not self.signer:
            raise HttpError(501, 'signing is not configured')
        return Response.text(self.signer.certificate_pem(), content_type='application/x-pem-file')

    # -- query ----------------------------------------------------------------------------------
    def _submodels_of(self, shell):
        result = []
        for ref in shell.get('submodels') or []:
            try:
                result.append(self.store.get('submodel', _last_key(ref)))
            except NotFound:
                continue
        return result

    def _shells_of(self, submodel):
        return [s for s in self.store.iter_all('shell')
                if any(_last_key(r) == submodel['id'] for r in s.get('submodels') or [])]

    def query(self, request, kind):
        query = request.json()
        self.allow(request, 'READ')
        limit, cursor = _paging(request)
        if kind in ('aas-descriptor', 'sm-descriptor'):
            short = 'aas' if kind == 'aas-descriptor' else 'sm'
            candidates = [d for d in (self._descriptor_view(request, short, d)
                                      for d in self.store.list_descriptors(short)[0]) if d is not None]
        else:
            condition = query.get('$condition') if isinstance(query, dict) else None
            where = sqlfilter.combine(self.read_condition(request, kind), sqlfilter.compile_condition(
                condition, kind, self.store.db, request.subject.claims) if isinstance(condition, dict) else None)
            candidates = [o for o in self.store.iter_all(kind, where) if self.visible(request, kind, o)]
            if kind == 'submodel':
                candidates = [self.readable_submodel(request, s) for s in candidates]
        try:
            results = formula.run_query(
                query, kind, candidates, claims=request.subject.claims,
                submodels_of=lambda shell: [self.readable_submodel(request, s) for s in self._submodels_of(shell)
                                            if self.visible(request, 'submodel', s)],
                shells_of=lambda submodel: [s for s in self._shells_of(submodel) if self.visible(request, 'shell', s)])
        except formula.FormulaError as error:
            raise HttpError(400, f'invalid query: {error}') from error
        page, nxt = _list_page(results, limit, cursor)
        result_type = 'Identifier' if query.get('$select') == 'id' else {
            'shell': 'AssetAdministrationShell', 'submodel': 'Submodel', 'concept-description': 'ConceptDescription',
            'aas-descriptor': 'AssetAdministrationShellDescriptor', 'sm-descriptor': 'SubmodelDescriptor'}[kind]
        return Response.json(_paged(page, nxt, resultType=result_type))

    # -- serialization, upload and packages --------------------------------------------------------
    def serialization(self, request):
        self.allow(request, 'READ')
        aas_ids = [_id(i) for i in request.args('aasIds')]
        sm_ids = [_id(i) for i in request.args('submodelIds')]
        include_cds = request.arg('includeConceptDescriptions', 'true').lower() != 'false'
        version = request.arg('version', '3.1')
        accept = request.accept('application/json', 'application/xml', 'application/asset-administration-shell-package+xml',
                                'application/asset-administration-shell-package')
        try:
            env = self.repo.environment(aas_ids or None, sm_ids or None, include_cds, version=version)
        except NotFound as error:
            raise HttpError(404, f'{error} not found') from error
        env = self._filter_environment(request, env)
        if 'package' in accept:
            return Response(200, self.repo.package(environment=env, version=version),
                            'application/asset-administration-shell-package+xml')
        if accept == 'application/xml':
            return Response.text(versioning.to_xml(env, '3.0' if version == '3.0' else '3.1'),
                                 content_type='application/xml')
        return Response.json(env)

    def _filter_environment(self, request, env):
        env = dict(env)
        env['assetAdministrationShells'] = [s for s in env.get('assetAdministrationShells', [])
                                            if self.visible(request, 'shell', s)]
        env['submodels'] = [self.readable_submodel(request, s) for s in env.get('submodels', [])
                            if self.visible(request, 'submodel', s)]
        return {k: v for k, v in env.items() if v}

    def upload(self, request):
        self.allow(request, 'CREATE')
        content_type = request.headers.get('Content-Type') or ''
        if 'multipart/form-data' in content_type:
            parts = request.multipart()
            upload = parts.get('file') or next(iter(parts.values()))
            data, filename = upload['data'], upload['filename'] or ''
        else:
            data, filename = request.body, request.arg('fileName', '')
        try:
            report = self.repo.import_bytes(data, filename, content_type, actor=self.actor(request),
                                            overwrite=request.arg('ignore-duplicates', 'false') != 'true')
        except (versioning.VersionError, ValueError) as error:
            raise HttpError(400, str(error), details=getattr(error, 'errors', None)) from error
        return Response.json(report.as_dict(), 200 if report.ok else 207)

    def get_packages(self, request):
        self.allow(request, 'READ')
        limit, cursor = _paging(request)
        aas = request.arg('aasId')
        packages = [p for p in self.store.list_packages(_id(aas) if aas else None)
                    if all(self.local_visible(request, 'shell', i) for i in p['aasIds'])]
        page, nxt = _list_page([{'packageId': p['packageId'], 'aasIds': p['aasIds']} for p in packages], limit, cursor)
        return Response.json(_paged(page, nxt))

    def _store_package(self, request, package_id=None):
        parts = request.multipart()
        upload = parts.get('file')
        if not upload:
            raise HttpError(400, 'multipart field "file" is required')
        filename = (parts.get('fileName') or {}).get('data', b'').decode() or upload['filename'] or 'package.aasx'
        try:
            report = self.repo.import_bytes(upload['data'], filename, 'application/asset-administration-shell-package',
                                            actor=self.actor(request))
        except (versioning.VersionError, ValueError) as error:
            raise HttpError(400, str(error)) from error
        aas_ids = report.ids.get('shell', [])
        package_id = package_id or canonical.b64url_encode(str(uuid.uuid4()))
        filename = safe_filename(filename, 'package.aasx')
        self.store.put_package(package_id, filename, aas_ids, upload['data'], report.ids)
        return package_id, aas_ids, report

    def post_package(self, request):
        self.allow(request, 'CREATE')
        package_id, aas_ids, report = self._store_package(request)
        return Response.json({'packageId': package_id, 'aasIds': aas_ids, 'import': report.as_dict()}, 201,
                             {'Location': f'{request.base}/packages/{package_id}'})

    def put_package(self, request):
        self.allow(request, 'UPDATE')
        try:
            self.store.get_package(request.params['pkg'])
        except NotFound as error:
            raise HttpError(404, 'package not found') from error
        self._store_package(request, request.params['pkg'])
        return Response.empty()

    def get_package(self, request):
        self.allow(request, 'READ')
        try:
            package = self.store.get_package(request.params['pkg'])
        except NotFound as error:
            raise HttpError(404, 'package not found') from error
        contents = package['contents']
        stored = [(kind, i) for kind in ('shell', 'submodel', 'concept-description') for i in contents.get(kind, [])]
        if not all(self.local_visible(request, kind, i) for kind, i in stored):
            raise PermissionError(f'{request.subject.name} may not READ every object in this package')
        withheld = any(not self.unrestricted(request, kind, self.store.get(kind, i))
                       for kind, i in stored if self.store.exists(kind, i))
        if request.arg('fresh', 'false') == 'true' or withheld:
            # rebuilt from current data, with the caller's FILTERs applied
            env = self._filter_environment(request, self.repo.environment(
                [i for i in contents.get('shell', []) if self.store.exists('shell', i)] or None,
                [i for i in contents.get('submodel', []) if self.store.exists('submodel', i)] or None))
            data = self.repo.package(environment=env)
        else:
            data = package['data']
        return Response(200, data, 'application/asset-administration-shell-package+xml',
                        {'Content-Disposition': content_disposition(package['filename'] or 'package.aasx')})

    def delete_package(self, request):
        self.allow(request, 'DELETE')
        try:
            self.store.delete_package(request.params['pkg'])
        except NotFound as error:
            raise HttpError(404, 'package not found') from error
        return Response.empty()

    def post_package_async(self, request):
        self.allow(request, 'CREATE')
        handle = self.store.new_operation('package')
        try:
            package_id, aas_ids, report = self._store_package(request)
            self.store.finish_operation(handle, 'Completed', {'packageId': package_id, 'aasIds': aas_ids,
                                                              'import': report.as_dict()})
        except HttpError as error:
            self.store.finish_operation(handle, 'Failed', {'messages': [{'text': error.text}]})
        return Response(202, b'', '', {'Location': f'{request.base}/packages-async/status/{handle}'})

    def async_status(self, request):
        self.allow(request, 'READ')
        try:
            operation = self.store.get_operation(request.params['handle'])
        except NotFound as error:
            raise HttpError(404, 'unknown handle') from error
        if operation['status'] == 'Running':
            return Response.json({'executionState': 'Running'})
        prefix = request.path.split('/status/')[0]
        return Response.json({'executionState': operation['status']}, 302,
                             {'Location': f'{request.base}{prefix}/result/{request.params["handle"]}'})

    def async_result(self, request):
        self.allow(request, 'READ')
        try:
            operation = self.store.get_operation(request.params['handle'])
        except NotFound as error:
            raise HttpError(404, 'unknown handle') from error
        return Response.json(operation['result'] or {}, 200 if operation['status'] == 'Completed' else 400)

    # -- registry --------------------------------------------------------------------------------
    def get_descriptors(self, request, kind):
        self.allow(request, 'READ')
        limit, cursor = _paging(request)
        items, nxt = self.store.list_descriptors(kind, limit, cursor)
        items = [d for d in (self._descriptor_view(request, kind, d) for d in items) if d is not None]
        if kind == 'aas':
            asset_kind, asset_type = request.arg('assetKind'), request.arg('assetType')
            if asset_kind:
                items = [d for d in items if d.get('assetKind') == asset_kind]
            if asset_type:
                items = [d for d in items if d.get('assetType') == _id(asset_type)]
        return Response.json(_paged(items, nxt))

    def _descriptor_view(self, request, kind, descriptor):
        """A descriptor as this caller may see it: None if it describes a local object the caller
        cannot read; a shell descriptor loses the submodel descriptors of hidden local submodels."""
        if not self.local_visible(request, 'shell' if kind == 'aas' else 'submodel', descriptor.get('id')):
            return None
        if kind == 'aas' and descriptor.get('submodelDescriptors'):
            descriptor = dict(descriptor)
            descriptor['submodelDescriptors'] = [d for d in descriptor['submodelDescriptors']
                                                 if self.local_visible(request, 'submodel', d.get('id'))]
        return descriptor

    def get_descriptor(self, request, kind, identifier):
        self.allow(request, 'READ')
        try:
            descriptor = self._descriptor_view(request, kind, self.store.get_descriptor(kind, identifier))
        except NotFound as error:
            raise HttpError(404, 'descriptor not found') from error
        if descriptor is None:
            raise HttpError(404, 'descriptor not found')
        return Response.json(descriptor)

    def post_descriptor(self, request, kind):
        self.allow(request, 'CREATE')
        body = request.json()
        try:
            self.store.put_descriptor(kind, body, must_exist=False)
        except Conflict as error:
            raise HttpError(409, str(error)) from error
        except ValueError as error:
            raise HttpError(400, str(error)) from error
        return Response.json(body, 201)

    def put_descriptor(self, request, kind, identifier):
        self.allow(request, 'UPDATE')
        body = request.json()
        if body.get('id') != identifier:
            raise HttpError(400, 'id in body does not match the path')
        created = self.store.put_descriptor(kind, body)
        return Response.json(body, 201) if created else Response.empty()

    def delete_descriptor(self, request, kind, identifier):
        self.allow(request, 'DELETE')
        try:
            self.store.delete_descriptor(kind, identifier)
        except NotFound as error:
            raise HttpError(404, 'descriptor not found') from error
        return Response.empty()

    def _shell_descriptor(self, request):
        try:
            return self.store.get_descriptor('aas', _id(request.params['aas']))
        except NotFound as error:
            raise HttpError(404, 'shell descriptor not found') from error

    def get_nested_descriptors(self, request):
        self.allow(request, 'READ')
        limit, cursor = _paging(request)
        descriptor = self._descriptor_view(request, 'aas', self._shell_descriptor(request))
        if descriptor is None:
            raise HttpError(404, 'descriptor not found')
        page, nxt = _list_page(descriptor.get('submodelDescriptors') or [], limit, cursor)
        return Response.json(_paged(page, nxt))

    def _change_nested(self, request, mutate):
        descriptor = self._shell_descriptor(request)
        result = mutate(descriptor.setdefault('submodelDescriptors', []))
        self.store.put_descriptor('aas', descriptor)
        return result

    def post_nested_descriptor(self, request):
        self.allow(request, 'CREATE')
        body = request.json()

        def mutate(items):
            if any(d.get('id') == body.get('id') for d in items):
                raise HttpError(409, 'submodel descriptor already exists')
            items.append(body)
        self._change_nested(request, mutate)
        return Response.json(body, 201)

    def get_nested_descriptor(self, request):
        self.allow(request, 'READ')
        target = _id(request.params['sm'])
        shell_descriptor = self._descriptor_view(request, 'aas', self._shell_descriptor(request)) or {}
        for descriptor in shell_descriptor.get('submodelDescriptors') or []:
            if descriptor.get('id') == target:
                return Response.json(descriptor)
        raise HttpError(404, 'submodel descriptor not found')

    def put_nested_descriptor(self, request):
        self.allow(request, 'UPDATE')
        target = _id(request.params['sm'])
        body = request.json()

        def mutate(items):
            for index, descriptor in enumerate(items):
                if descriptor.get('id') == target:
                    items[index] = body
                    return False
            items.append(body)
            return True
        created = self._change_nested(request, mutate)
        return Response.json(body, 201) if created else Response.empty()

    def delete_nested_descriptor(self, request):
        self.allow(request, 'DELETE')
        target = _id(request.params['sm'])

        def mutate(items):
            remaining = [d for d in items if d.get('id') != target]
            if len(remaining) == len(items):
                raise HttpError(404, 'submodel descriptor not found')
            items[:] = remaining
        self._change_nested(request, mutate)
        return Response.empty()

    def bulk_descriptors(self, request, kind):
        self.allow(request)
        payload = request.json()
        handle = self.store.new_operation('bulk')
        try:
            with self.store.transaction():   # all-or-nothing
                for item in payload:
                    if request.method == 'DELETE':
                        self.store.delete_descriptor(kind, item if isinstance(item, str) else item['id'])
                    else:
                        self.store.put_descriptor(kind, item, must_exist=True if request.method == 'PUT' else False)
            self.store.finish_operation(handle, 'Completed', {'count': len(payload)})
        except (NotFound, Conflict, ValueError) as error:
            self.store.finish_operation(handle, 'Failed', {'messages': [{'text': str(error)}]})
        return Response(202, b'', '', {'Location': f'{request.base}/bulk/status/{handle}'})

    # -- discovery ---------------------------------------------------------------------------------
    def _links_match(self, links, wanted):
        have = {(link.get('name'), link.get('value')) for link in links}
        return all((w.get('name'), w.get('value')) in have for w in wanted)

    def lookup_shells(self, request):
        self.allow(request, 'READ')
        limit, cursor = _paging(request)
        wanted = self._asset_ids_arg(request)
        ids = sorted(i for i, links in self.store.all_asset_links().items()
                     if (not wanted or self._links_match(links, wanted)) and self.local_visible(request, 'shell', i))
        page, nxt = _list_page(ids, limit, cursor)
        return Response.json(_paged(page, nxt))

    def lookup_by_links(self, request):
        self.allow(request, 'READ')
        limit, cursor = _paging(request)
        wanted = request.json()
        ids = sorted(i for i, links in self.store.all_asset_links().items()
                     if self._links_match(links, wanted) and self.local_visible(request, 'shell', i))
        page, nxt = _list_page(ids, limit, cursor)
        return Response.json(_paged(page, nxt))

    def get_asset_links(self, request):
        self.allow(request, 'READ')
        if not self.local_visible(request, 'shell', _id(request.params['aas'])):
            raise HttpError(404, 'no asset links for this shell')
        try:
            return Response.json(self.store.get_asset_links(_id(request.params['aas'])))
        except NotFound as error:
            raise HttpError(404, 'no asset links for this shell') from error

    def post_asset_links(self, request):
        self.allow(request, 'CREATE')
        links = request.json()
        if not isinstance(links, list):
            raise HttpError(400, 'body must be a list of SpecificAssetId')
        self.store.set_asset_links(_id(request.params['aas']), links)
        return Response.json(links, 201)

    def delete_asset_links(self, request):
        self.allow(request, 'DELETE')
        try:
            self.store.delete_asset_links(_id(request.params['aas']))
        except NotFound as error:
            raise HttpError(404, 'no asset links for this shell') from error
        return Response.empty()

    # -- events ------------------------------------------------------------------------------------
    def events(self, request):
        self.allow(request, 'READ')
        try:
            since = int(request.arg('since', '0'))
        except ValueError as error:
            raise HttpError(400, 'since must be an integer') from error
        if 'text/event-stream' not in (request.headers.get('Accept') or ''):
            return Response.json({'events': [e for e in map(lambda e: self._event_view(request, e),
                                                            self.store.events_since(since)) if e]})
        import queue
        inbox = queue.Queue(maxsize=10000)

        def deliver(event):
            try:
                inbox.put_nowait(event)
            except queue.Full:
                pass            # a stalled client loses events rather than memory; it can resume with since=

        self.store.subscribe(deliver)

        def frame(event):
            return f'id: {event["seq"]}\nevent: {event["op"]}\ndata: {json.dumps(event)}\n\n'.encode()

        def stream():
            try:
                for event in self.store.events_since(since):
                    event = self._event_view(request, event)
                    if event:
                        yield frame(event)
                while True:
                    try:
                        event = self._event_view(request, inbox.get(timeout=15))
                        if event:
                            yield frame(event)
                    except queue.Empty:
                        yield b': keep-alive\n\n'
            finally:
                self.store.unsubscribe(deliver)
        return Response(200, b'', 'text/event-stream', {}, stream())

    def _event_view(self, request, event):
        """An event as this caller may see it, or None. Ids of objects the caller cannot read are
        withheld; actors are never streamed; element paths only when nothing in the submodel is hidden."""
        event = {k: v for k, v in event.items() if k != 'actor'}
        kind = {'aas-descriptor': 'shell', 'sm-descriptor': 'submodel'}.get(event['kind'], event['kind'])
        if kind in ('shell', 'submodel', 'concept-description'):
            if not self.local_visible(request, kind, event['id']):
                return None
            if event.get('path') and kind == 'submodel' and self.store.exists('submodel', event['id']) and \
                    not self.unrestricted(request, 'submodel', self.store.get('submodel', event['id'])):
                event['path'] = None
        return event
