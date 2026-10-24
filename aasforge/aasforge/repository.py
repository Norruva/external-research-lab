"""Repository service: validated writes, strict imports with loss detection, environment export,
and integrated registry/discovery maintenance.

Write policy (``verification``):
* ``reject``  structural (deserialisation) errors and constraint violations are rejected;
* ``warn``    structural errors are rejected, constraint violations are stored and reported;
* ``off``     structural errors are rejected only.
Structural validity is never optional: nothing that aas-core3.1 cannot read is stored.
"""
from __future__ import annotations

import copy
import json
import mimetypes
import re
import uuid
from dataclasses import dataclass, field

import aas_core3_1.jsonization as j31
import aas_core3_1.verification as v31

from . import aasx, canonical, elements, versioning
from .store import NotFound, Store

COLLECTIONS = {'assetAdministrationShells': 'shell', 'submodels': 'submodel', 'conceptDescriptions': 'concept-description'}
FROM_JSONABLE = {'shell': j31.asset_administration_shell_from_jsonable, 'submodel': j31.submodel_from_jsonable,
                 'concept-description': j31.concept_description_from_jsonable}


class ValidationFailed(ValueError):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or []


@dataclass
class ImportReport:
    source_format: str
    source_version: str
    expected: int = 0
    stored: dict = field(default_factory=lambda: {'created': 0, 'updated': 0, 'unchanged': 0})
    lost: list = field(default_factory=list)
    verification_errors: list = field(default_factory=list)
    repairs: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    sidecar_fields: int = 0
    attachments: int = 0
    ids: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.lost

    def as_dict(self) -> dict:
        data = dict(self.__dict__)
        data['ok'] = self.ok
        return data


def _raw_count(data: bytes, fmt: str) -> int:
    """Identifiables present in the source bytes, counted independently of any SDK."""
    if fmt == 'json':
        env = json.loads(data)
        return sum(len(env.get(k) or []) for k in COLLECTIONS)
    text = data.decode('utf-8', errors='replace')
    count = 0
    for collection, item in (('assetAdministrationShells', 'assetAdministrationShell'), ('submodels', 'submodel'),
                             ('conceptDescriptions', 'conceptDescription')):
        for block in re.findall(rf'<(?:\w+:)?{collection}>(.*?)</(?:\w+:)?{collection}>', text, re.S):
            count += len(re.findall(rf'<(?:\w+:)?{item}>', block))
    return count


class Repository:
    def __init__(self, store: Store, verification: str = 'warn', base_url: str = 'http://localhost:8081/api/v3.1',
                 auto_register: bool = True):
        self.store = store
        self.verification = verification
        self.base_url = base_url.rstrip('/')
        self.auto_register = auto_register
        self.validators: list = []       # (kind, body) -> None, raise ValueError to refuse
        self.endpoint_hooks: list = []   # (kind, identifiable) -> extra descriptor endpoints

    # -- validation -----------------------------------------------------------------------
    def check(self, kind: str, body: dict) -> list[dict]:
        if not isinstance(body, dict):
            raise ValidationFailed('body must be a JSON object')
        try:
            instance = FROM_JSONABLE[kind](body)
        except j31.DeserializationException as error:
            raise ValidationFailed(f'invalid {kind}: {error.path}: {error.cause}') from error
        if self.verification == 'off':
            return []
        errors = [{'path': str(e.path), 'cause': e.cause} for e in v31.verify(instance)]
        if errors and self.verification == 'reject':
            raise ValidationFailed(f'{len(errors)} constraint violation(s)', errors[:50])
        return errors

    def check_element(self, element: dict) -> None:
        try:
            j31.submodel_element_from_jsonable(element)
        except j31.DeserializationException as error:
            raise ValidationFailed(f'invalid submodel element: {error.path}: {error.cause}') from error

    # -- writes ---------------------------------------------------------------------------
    def validate(self, kind: str, body: dict):
        """Run domain validators (e.g. Catena-X DTR rules); they raise ValueError."""
        for validator in self.validators:
            try:
                validator(kind, body)
            except ValidationFailed:
                raise
            except ValueError as error:
                raise ValidationFailed(str(error)) from error

    def save(self, kind: str, body: dict, *, actor=None, must_exist=None, sidecar=None, path=None):
        self.validate(kind, body)
        warnings = self.check(kind, body)
        with self.store.transaction():
            created, meta = self.store.put(kind, body, sidecar=sidecar, actor=actor, must_exist=must_exist, path=path)
            if kind == 'shell' and self.auto_register:
                self._register_shell(body)
            if kind == 'submodel' and self.auto_register:
                self._refresh_descriptors_for_submodel(body)
        return created, meta, warnings

    def remove(self, kind: str, identifier: str, actor=None):
        with self.store.transaction():
            body = self.store.get(kind, identifier)
            self.store.delete(kind, identifier, actor=actor)
            if kind == 'shell' and self.auto_register:
                for call in (lambda: self.store.delete_descriptor('aas', identifier),
                             lambda: self.store.delete_asset_links(identifier)):
                    try:
                        call()
                    except NotFound:
                        pass
            if kind == 'submodel':
                for shell in list(self.store.iter_all('shell')):
                    refs = shell.get('submodels') or []
                    kept = [r for r in refs if (r.get('keys') or [{}])[-1].get('value') != identifier]
                    if len(kept) != len(refs):
                        if kept:
                            shell['submodels'] = kept
                        else:
                            shell.pop('submodels', None)
                        self.store.put('shell', shell, actor=actor)
                        if self.auto_register:
                            self._register_shell(shell)
        return body

    def update_submodel(self, submodel_id: str, mutate, *, actor=None, path=None):
        """Load, mutate in place via ``mutate(submodel)``, validate and store a new revision."""
        with self.store.transaction():
            submodel = self.store.get('submodel', submodel_id)
            sidecar = self.store.meta('submodel', submodel_id)['sidecar']
            result = mutate(submodel)
            _, meta, warnings = self.save('submodel', submodel, actor=actor, must_exist=True, sidecar=sidecar, path=path)
        return result, meta, warnings

    def update_shell(self, shell_id: str, mutate, *, actor=None):
        with self.store.transaction():
            shell = self.store.get('shell', shell_id)
            result = mutate(shell)
            _, meta, warnings = self.save('shell', shell, actor=actor, must_exist=True)
        return result, meta, warnings

    # -- registry / discovery integration ------------------------------------------------------
    def _endpoint(self, interface, href):
        return {'interface': interface, 'protocolInformation': {'href': href, 'endpointProtocol': 'HTTP',
                                                                'endpointProtocolVersion': ['1.1']}}

    def submodel_descriptor(self, submodel: dict) -> dict:
        descriptor = {'id': submodel['id'], 'endpoints': [self._endpoint(
            'SUBMODEL-3.1', f'{self.base_url}/submodels/{canonical.b64url_encode(submodel["id"])}')]}
        for hook in self.endpoint_hooks:
            descriptor['endpoints'] += hook('submodel', submodel)
        for key in ('idShort', 'semanticId', 'supplementalSemanticIds', 'description', 'displayName', 'administration',
                    'extensions'):
            if submodel.get(key):
                descriptor[key] = copy.deepcopy(submodel[key])
        return descriptor

    def _register_shell(self, shell: dict):
        info = shell.get('assetInformation') or {}
        descriptor = {'id': shell['id'], 'endpoints': [self._endpoint(
            'AAS-3.1', f'{self.base_url}/shells/{canonical.b64url_encode(shell["id"])}')]}
        for key in ('idShort', 'description', 'displayName', 'administration', 'extensions'):
            if shell.get(key):
                descriptor[key] = copy.deepcopy(shell[key])
        for key in ('assetKind', 'assetType', 'globalAssetId', 'specificAssetIds'):
            if info.get(key):
                descriptor[key] = copy.deepcopy(info[key])
        submodel_descriptors = []
        for ref in shell.get('submodels') or []:
            sm_id = (ref.get('keys') or [{}])[-1].get('value')
            try:
                submodel_descriptors.append(self.submodel_descriptor(self.store.get('submodel', sm_id)))
            except NotFound:
                submodel_descriptors.append({'id': sm_id, 'endpoints': [self._endpoint(
                    'SUBMODEL-3.1', f'{self.base_url}/submodels/{canonical.b64url_encode(sm_id)}')]})
        if submodel_descriptors:
            descriptor['submodelDescriptors'] = submodel_descriptors
        self.store.put_descriptor('aas', descriptor)
        links = [{'name': 'globalAssetId', 'value': info['globalAssetId']}] if info.get('globalAssetId') else []
        links += [{'name': s['name'], 'value': s['value']} for s in info.get('specificAssetIds') or []]
        self.store.set_asset_links(shell['id'], links)

    def _refresh_descriptors_for_submodel(self, submodel: dict):
        self.store.put_descriptor('sm', self.submodel_descriptor(submodel))
        for shell in self.store.iter_all('shell'):
            if any((r.get('keys') or [{}])[-1].get('value') == submodel['id'] for r in shell.get('submodels') or []):
                self._register_shell(shell)

    # -- imports -------------------------------------------------------------------------------
    def import_bytes(self, data: bytes, filename: str = '', content_type: str = '', *, actor=None,
                     overwrite: bool = True) -> ImportReport:
        is_aasx = filename.lower().endswith('.aasx') or 'aasx' in content_type or data[:2] == b'PK'
        files, content_types, repairs = {}, {}, []
        if is_aasx:
            package = aasx.read(data, getattr(self, 'max_package', aasx.MAX_PACKAGE_BYTES))
            normalised, files, content_types, repairs = (package.normalised, package.files, package.content_types,
                                                         package.repairs)
            expected = sum(len(normalised.environment.get(k) or []) for k in COLLECTIONS)
            fmt = 'aasx/' + normalised.source_format
        else:
            text_start = data.lstrip()[:1]
            fmt = 'json' if text_start in (b'{', b'[') else 'xml'
            normalised = versioning.parse_json(data) if fmt == 'json' else versioning.parse_xml(data)
            expected = _raw_count(data, fmt)
        report = ImportReport(fmt, normalised.source_version, expected, repairs=list(repairs), notes=list(normalised.notes))
        report.sidecar_fields = len(normalised.sidecar)
        env = normalised.environment
        report.verification_errors = versioning.verify(env, limit=50)
        if report.verification_errors and self.verification == 'reject':
            raise ValidationFailed(f'{len(report.verification_errors)} constraint violation(s) in import',
                                   report.verification_errors)
        sidecars = {}
        for entry in normalised.sidecar:
            collection, index, *rest = entry['path']
            sidecars.setdefault((collection, index), []).append({**entry, 'path': rest})
        with self.store.transaction():
            for collection, kind in COLLECTIONS.items():
                for index, body in enumerate(env.get(collection) or []):
                    if not overwrite and self.store.exists(kind, body['id']):
                        report.stored['unchanged'] += 1
                        continue
                    self.validate(kind, body)
                    before = self.store.meta(kind, body['id'])['revision'] if self.store.exists(kind, body['id']) else None
                    self.store.put(kind, body, sidecar=sidecars.get((collection, index), []), actor=actor)
                    after = self.store.meta(kind, body['id'])['revision']
                    key = 'created' if before is None else ('unchanged' if before == after else 'updated')
                    report.stored[key] += 1
                    report.ids.setdefault(kind, []).append(body['id'])
            if self.auto_register:
                for body in env.get('submodels') or []:
                    self.store.put_descriptor('sm', self.submodel_descriptor(body))
                for body in env.get('assetAdministrationShells') or []:
                    self._register_shell(body)
            report.attachments = self._store_attachments(env, files, content_types)
        stored_total = sum(report.stored.values())
        if stored_total != report.expected:
            report.lost.append(f'source declares {report.expected} identifiables, {stored_total} were stored')
        for collection, kind in COLLECTIONS.items():
            for body in env.get(collection) or []:
                if canonical.digest(self.store.get(kind, body['id'])) != canonical.digest(body) and overwrite:
                    report.lost.append(f'{kind} {body["id"]!r} changed while being stored')
        return report

    def _store_attachments(self, env: dict, files: dict, content_types: dict) -> int:
        count = 0
        for submodel in env.get('submodels') or []:
            for path, element in elements.walk_elements(submodel):
                if element.get('modelType') == 'File' and element.get('value') in files:
                    part = element['value']
                    self.store.put_attachment('submodel', submodel['id'], path, files[part],
                                              element.get('contentType') or content_types.get(part),
                                              part.rsplit('/', 1)[-1])
                    count += 1
        for shell in env.get('assetAdministrationShells') or []:
            thumb = ((shell.get('assetInformation') or {}).get('defaultThumbnail') or {}).get('path')
            if thumb in files:
                self.store.put_attachment('shell', shell['id'], 'thumbnail', files[thumb],
                                          ((shell['assetInformation'].get('defaultThumbnail') or {}).get('contentType')
                                           or content_types.get(thumb)), thumb.rsplit('/', 1)[-1])
                count += 1
        return count

    # -- exports -------------------------------------------------------------------------------
    def environment(self, aas_ids=None, submodel_ids=None, include_cds=True, version='3.1') -> dict:
        shells = [self.store.get('shell', i) for i in aas_ids] if aas_ids else (
            [] if submodel_ids else list(self.store.iter_all('shell')))
        wanted = set(submodel_ids or [])
        for shell in shells:
            wanted |= {(r.get('keys') or [{}])[-1].get('value') for r in shell.get('submodels') or []}
        if aas_ids or submodel_ids:
            submodels = [s for s in (self._try_get('submodel', i) for i in sorted(wanted)) if s]
        else:
            submodels = list(self.store.iter_all('submodel'))
        env = {}
        if shells:
            env['assetAdministrationShells'] = shells
        if submodels:
            env['submodels'] = submodels
        if include_cds:
            cds = list(self.store.iter_all('concept-description'))
            if aas_ids or submodel_ids:
                used = set()
                for submodel in submodels:
                    for _, element in elements.walk_elements(submodel):
                        sem = ((element.get('semanticId') or {}).get('keys') or [{}])[0].get('value')
                        if sem:
                            used.add(sem)
                cds = [c for c in cds if c['id'] in used]
            if cds:
                env['conceptDescriptions'] = cds
        if version == '3.2':
            env = self._with_32_fields(env)
        return env

    def _with_32_fields(self, env: dict) -> dict:
        env = copy.deepcopy(env)
        for collection, kind in COLLECTIONS.items():
            env[collection] = [self.as_32(kind, body) for body in env.get(collection) or []]
        return {k: v for k, v in env.items() if v}

    def as_32(self, kind: str, body: dict) -> dict:
        """An identifiable as metamodel 3.2: sidecar fields restored, createdAt/updatedAt from the store."""
        meta = self.store.meta(kind, body['id'])
        sidecar = meta['sidecar']
        if canonical.digest(body) != meta['digest']:
            # a filtered or partial copy: element indices may have shifted, restore top-level fields only
            sidecar = [e for e in sidecar if 'submodelElements' not in e['path']]
        restored = versioning.apply_sidecar({'x': body}, [{**e, 'path': ['x'] + e['path']} for e in sidecar])['x']
        if kind != 'concept-description' or 'administration' in restored:
            admin = restored.setdefault('administration', {})
            admin.setdefault('createdAt', meta['created_at'])
            admin.setdefault('updatedAt', meta['updated_at'])
        return restored

    @staticmethod
    def from_32(kind: str, body: dict) -> tuple[dict, list]:
        """Split a 3.2 identifiable into its 3.1 form and the sidecar of 3.2-only fields."""
        collection = next(c for c, k in COLLECTIONS.items() if k == kind)
        reduced, sidecar = versioning.reduce_32({collection: [body]})
        return reduced[collection][0], [{**e, 'path': e['path'][2:]} for e in sidecar]

    def _try_get(self, kind, identifier):
        try:
            return self.store.get(kind, identifier)
        except NotFound:
            return None

    def package(self, aas_ids=None, submodel_ids=None, fmt='json', version='3.1', environment=None) -> bytes:
        """AASX of the selected objects, or of ``environment`` (e.g. one already filtered for a caller);
        attachments are included only for File elements still present in it."""
        env = environment if environment is not None else self.environment(aas_ids, submodel_ids, version='3.1')
        files, types = {}, {}
        env = copy.deepcopy(env)
        for submodel in env.get('submodels') or []:
            for attachment in self.store.attachments_of('submodel', submodel['id']):
                try:
                    element = elements.get(submodel, attachment['path'])
                except elements.PathError:
                    continue
                part = element.get('value') if str(element.get('value', '')).startswith('/aasx/') else \
                    f'/aasx/files/{uuid.uuid5(uuid.NAMESPACE_URL, submodel["id"] + attachment["path"]).hex}-' \
                    f'{attachment["filename"] or "file"}'
                element['value'] = part
                files[part] = attachment['data']
                types[part] = attachment['content_type'] or mimetypes.guess_type(part)[0] or 'application/octet-stream'
        for submodel in env.get('submodels') or []:
            for _, element in elements.walk_elements(submodel):
                if element.get('modelType') == 'File' and str(element.get('value', '')).startswith('/') \
                        and element['value'] not in files:
                    element.pop('value')   # dangling package-local path: drop it so the package stays valid
        thumbnail = None
        for shell in env.get('assetAdministrationShells') or []:
            try:
                attachment = self.store.get_attachment('shell', shell['id'], 'thumbnail')
            except NotFound:
                thumb = (shell.get('assetInformation') or {}).get('defaultThumbnail')
                if thumb and str(thumb.get('path', '')).startswith('/'):
                    shell['assetInformation'].pop('defaultThumbnail')
                continue
            part = f'/aasx/thumbnails/{uuid.uuid5(uuid.NAMESPACE_URL, shell["id"]).hex}-{attachment["filename"] or "thumb"}'
            shell['assetInformation']['defaultThumbnail'] = {'path': part,
                                                             'contentType': attachment['content_type'] or 'image/png'}
            files[part] = attachment['data']
            types[part] = attachment['content_type'] or 'image/png'
            thumbnail = thumbnail or part
        return aasx.write(env, files, types, fmt=fmt, version=version, thumbnail=thumbnail)
