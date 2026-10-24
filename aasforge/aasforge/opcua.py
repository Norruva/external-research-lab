"""OPC UA for the Asset Administration Shell: the AAS data as an OPC UA address space built on the
I4AAS companion specification (OPC 30270, ``data/opcua/Opc.Ua.I4AAS.NodeSet2.xml``).

Two uses:

* :func:`export_nodeset`: a NodeSet2 XML file of the instances that any OPC UA server can load
  together with the I4AAS NodeSet;
* :class:`OpcUaServer`: a live server (asyncua) that follows the store. Every change made through
  the AAS API reaches subscribed OPC UA clients. Property, Range and MultiLanguageProperty values
  can optionally be written back, authorised by the same access rules as the HTTP API.

What the address space shows is what an anonymous caller of the HTTP API may read: shells and
submodels hidden by access rules are absent, and FILTERs and Battery Regulation tiers apply.
OPC UA users log in with an API key or a bearer token as the password. That identity is used only
to authorise writes; there is a single address space for all sessions.

Mapping of metamodel 3.1 onto I4AAS 1.00, which was written for metamodel 2.0:

* shells, submodels and elements use the I4AAS ObjectTypes. Browse names are idShorts; list items
  without an idShort are named ``[i]``;
* ``id`` becomes ``Identification`` (``Id`` plus ``IdType``, guessed IRI/IRDI/Custom); ``kind``
  becomes ``ModelingKind``; ``assetInformation`` becomes the ``Asset`` object; ``semanticId``
  becomes a ``HasDictionaryEntry`` reference to a ConceptDescription node in
  ``AASROOT/ConceptDictionary``;
* ``SubmodelElementList`` becomes ``AASOrderedSubmodelElementCollectionType`` (or the unordered
  collection when ``orderRelevant`` is false);
* a ``MultiLanguageProperty`` value is an array of LocalizedText (I4AAS has a scalar);
* ``specificAssetIds`` become string properties of the Asset object in the aasforge namespace,
  since I4AAS 1.00 has no counterpart;
* Blob values, and File attachments stored on the server, are readable through the standard
  ``FileType`` methods (Open, Read, GetPosition, SetPosition, Close; read only);
* an Operation is an ``AASOperationType`` with an ``Operation`` method. Calls run the operation
  with the anonymous identity's EXECUTE permission.

``asyncua`` is an optional dependency, needed only for this module.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources

from . import canonical, elements, security
from .http import Request
from .store import NotFound

I4AAS_URI = 'http://opcfoundation.org/UA/I4AAS/'
INSTANCE_URI = 'urn:aasforge:opcua'
TYPES = {
    'AssetAdministrationShell': 1002, 'Asset': 1005, 'ConceptDictionary': 1007, 'Reference': 1004, 'Identifier': 1029,
    'AdministrativeInformation': 1030, 'Qualifier': 1032, 'Submodel': 1006, 'Property': 1013,
    'MultiLanguageProperty': 1012, 'Range': 1023, 'File': 1017, 'Blob': 1016, 'SubmodelElementCollection': 1010,
    'OrderedSubmodelElementCollection': 1011, 'ReferenceElement': 1020, 'RelationshipElement': 1018,
    'AnnotatedRelationshipElement': 1019, 'Entity': 1022, 'Operation': 1015, 'Capability': 1014,
    'BasicEventElement': 1021, 'IriConceptDescription': 1025, 'IrdiConceptDescription': 1024,
    'IAASReferable': 1033, 'IAASIdentifiable': 1034,
}
DATA_TYPES = {'AssetKind': 3003, 'EntityType': 3006, 'IdentifierType': 3010, 'KeyElements': 3012, 'KeyType': 3002,
              'ModelingKind': 3015, 'ValueType': 3004, 'Mime': 3016, 'Key': 3011}
HAS_DICTIONARY_ENTRY, HAS_INTERFACE = 17597, 17603
# xs: valueType -> (AASValueTypeDataType member, VariantType name, converter from the lexical form)
VALUE_TYPES = {
    'xs:boolean': ('Boolean', 'Boolean', lambda v: v.strip().lower() in ('true', '1')),
    'xs:byte': ('SByte', 'SByte', int), 'xs:unsignedByte': ('Byte', 'Byte', int),
    'xs:short': ('Int16', 'Int16', int), 'xs:unsignedShort': ('UInt16', 'UInt16', int),
    'xs:int': ('Int32', 'Int32', int), 'xs:unsignedInt': ('UInt32', 'UInt32', int),
    'xs:long': ('Int64', 'Int64', int), 'xs:unsignedLong': ('UInt64', 'UInt64', int),
    'xs:integer': ('Int64', 'Int64', int), 'xs:nonNegativeInteger': ('UInt64', 'UInt64', int),
    'xs:positiveInteger': ('UInt64', 'UInt64', int), 'xs:negativeInteger': ('Int64', 'Int64', int),
    'xs:nonPositiveInteger': ('Int64', 'Int64', int),
    'xs:float': ('Float', 'Float', float), 'xs:double': ('Double', 'Double', float),
    'xs:decimal': ('Double', 'Double', float),
    'xs:dateTime': ('DateTime', 'DateTime', lambda v: _datetime(v)),
    'xs:base64Binary': ('ByteString', 'ByteString', lambda v: base64.b64decode(v, validate=True)),
    'xs:hexBinary': ('ByteString', 'ByteString', bytes.fromhex),
}
IRDI = re.compile(r'^\d{4}-\d#\d{2}-')
_log = logging.getLogger(__name__)


def _datetime(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _ua():
    try:
        from asyncua import ua
    except ImportError as error:  # pragma: no cover - optional dependency
        raise RuntimeError('OPC UA support needs asyncua: pip install asyncua') from error
    return ua


def nodeset_path() -> str:
    return str(resources.files('aasforge.data').joinpath('opcua/Opc.Ua.I4AAS.NodeSet2.xml'))


def _first_text(lang_strings, prefer='en'):
    if not lang_strings:
        return None
    for item in lang_strings:
        if item.get('language', '').lower().startswith(prefer):
            return item.get('text')
    return lang_strings[0].get('text')


def api_request(method: str, route: str, claims: dict | None = None) -> Request:
    """A request as the HTTP API would see it. OPC UA operations use the AAS API route of the
    object they touch, so ROUTE-scoped access rules mean the same thing on both protocols."""
    request = Request(method, route, {}, {})
    request.subject = security.Subject(dict(claims or {}), anonymous=not claims)
    return request


def element_route(submodel_id: str, path: str, suffix: str = '') -> str:
    return f'/submodels/{canonical.b64url_encode(submodel_id)}/submodel-elements/{path}{suffix}'


def visible_environment(forge, claims: dict | None = None) -> dict:
    """Shells, submodels and concept descriptions as a caller with ``claims`` (default: anonymous)
    may read them through the AAS API."""
    api = forge.api
    shells = []
    for shell in forge.store.iter_all('shell'):
        request = api_request('GET', f'/shells/{canonical.b64url_encode(shell["id"])}', claims)
        if api.visible(request, 'shell', shell):
            shells.append(api.readable_shell(request, shell))
    submodels = []
    for submodel in forge.store.iter_all('submodel'):
        request = api_request('GET', f'/submodels/{canonical.b64url_encode(submodel["id"])}', claims)
        if api.visible(request, 'submodel', submodel):
            try:
                submodels.append(api.readable_submodel(request, submodel))
            except PermissionError:
                continue
    cds = [c for c in forge.store.iter_all('concept-description') if api.visible(
        api_request('GET', f'/concept-descriptions/{canonical.b64url_encode(c["id"])}', claims), 'concept-description', c)]
    return {'assetAdministrationShells': shells, 'submodels': submodels, 'conceptDescriptions': cds}


MAX_OPEN_FILES = 256            # FileType handles across all sessions; the oldest is closed first
OPEN_FILE_SECONDS = 600         # handles not used for this long are closed


@dataclass
class _OpenFile:
    data: bytes
    position: int = 0
    used: float = field(default_factory=lambda: __import__('time').monotonic())


@dataclass
class Binding:
    """A writable OPC UA variable and the AAS value it shows."""
    submodel_id: str
    path: str
    part: str = 'value'           # value | min | max
    value_type: str | None = None
    model_type: str = 'Property'


@dataclass
class ForgeUser:
    """asyncua user carrying the claims of the API key or token it logged in with."""
    role: object
    name: str | None = None
    claims: dict = field(default_factory=dict)


class AddressSpace:
    """Builds the I4AAS address space for an environment into an asyncua server."""

    def __init__(self, server, i4: int, ns: int, files=None, operations=None):
        self.server, self.i4, self.ns = server, i4, ns
        self.ua = _ua()
        self.session = server.iserver.isession
        self.bindings: dict = {}            # NodeId -> Binding
        self.files = files or (lambda submodel_id, path: None)     # attachment bytes, if any
        self.operations = operations        # callable(submodel_id, path, inputs) -> outputs
        self.cd_nodes: dict = {}
        self.cds: dict = {}
        self.submodel_nodes: dict = {}
        self.open_files: dict = {}
        self._next_handle = 1
        self._added = 0
        self.parents: dict = {}             # submodel id -> [(parent NodeId, reference type)]

    # -- node ids and primitives ------------------------------------------------------------------
    def nid(self, text: str):
        return self.ua.NodeId(text, self.ns)

    def t(self, name: str):
        return self.ua.NodeId(TYPES[name], self.i4)

    def dt(self, name: str):
        return self.ua.NodeId(DATA_TYPES[name], self.i4)

    async def _add(self, items):
        self._added += len(items)
        if self._added % 200 < len(items):
            await asyncio.sleep(0)      # building is CPU-only: let the server serve its sessions
        results = await self.session.add_nodes(items)
        for result in results:
            result.StatusCode.check()
        return [r.AddedNodeId for r in results]

    async def add_object(self, parent, nodeid, name, type_name, *, ref=None, description=None, display=None,
                         name_ns=None, type_id=None):
        ua = self.ua
        item = ua.AddNodesItem()
        item.RequestedNewNodeId = nodeid
        item.BrowseName = ua.QualifiedName(name, self.ns if name_ns is None else name_ns)
        item.ParentNodeId = parent
        item.ReferenceTypeId = ua.NodeId(ref or ua.ObjectIds.HasComponent)
        item.NodeClass = ua.NodeClass.Object
        item.TypeDefinition = type_id or self.t(type_name)
        attrs = ua.ObjectAttributes()
        attrs.EventNotifier = 0
        attrs.DisplayName = ua.LocalizedText(display or name)
        attrs.Description = ua.LocalizedText(description or '')
        attrs.WriteMask = attrs.UserWriteMask = 0
        item.NodeAttributes = attrs
        return (await self._add([item]))[0]

    async def add_property(self, parent, name, variant, datatype, *, nodeid=None, writable=False, name_ns=None,
                           array=False):
        ua = self.ua
        item = ua.AddNodesItem()
        item.RequestedNewNodeId = nodeid or self.nid(f'{parent.Identifier}/{name}')
        item.BrowseName = ua.QualifiedName(name, self.i4 if name_ns is None else name_ns)
        item.ParentNodeId = parent
        item.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HasProperty)
        item.NodeClass = ua.NodeClass.Variable
        item.TypeDefinition = ua.NodeId(ua.ObjectIds.PropertyType)
        attrs = ua.VariableAttributes()
        attrs.DisplayName = ua.LocalizedText(name)
        attrs.Description = ua.LocalizedText('')
        attrs.Value = variant
        attrs.DataType = datatype
        attrs.ValueRank = 1 if array else -1
        if array:
            attrs.ArrayDimensions = [0]
        level = ua.AccessLevel.CurrentRead.mask | (ua.AccessLevel.CurrentWrite.mask if writable else 0)
        attrs.AccessLevel = attrs.UserAccessLevel = level
        attrs.WriteMask = attrs.UserWriteMask = 0
        item.NodeAttributes = attrs
        return (await self._add([item]))[0]

    async def add_reference(self, source, target, ref, forward=True, target_class=None):
        ua = self.ua
        item = ua.AddReferencesItem()
        item.SourceNodeId, item.TargetNodeId = source, target
        item.ReferenceTypeId = ua.NodeId(ref)
        item.IsForward = forward
        item.TargetNodeClass = target_class or ua.NodeClass.Object
        results = await self.session.add_references([item])
        for result in results:
            result.check()

    def datatype_of(self, variant):
        """The built-in DataType matching a Variant (Null values get BaseDataType)."""
        ua = self.ua
        if variant.VariantType == ua.VariantType.Null:
            return ua.NodeId(ua.ObjectIds.BaseDataType)
        return ua.NodeId(variant.VariantType.value)

    def string(self, text):
        return self.ua.Variant(text or '', self.ua.VariantType.String)

    def enum(self, value: int):
        return self.ua.Variant(value, self.ua.VariantType.Int32)

    # -- AAS building blocks ------------------------------------------------------------------------
    def _enum_value(self, type_name: str, member: str, default: str) -> int:
        enum = getattr(self.ua, f'AAS{type_name}DataType')
        return int(getattr(enum, member, getattr(enum, default)))

    def _key(self, key: dict, model_reference: bool):
        ua = self.ua
        k = ua.AASKeyDataType()
        key_type = key.get('type') or 'GlobalReference'
        key_type = {'SubmodelElementList': 'SubmodelElementCollection', 'Identifiable': 'SubmodelElement',
                    'Referable': 'SubmodelElement', 'BasicEventElement': 'Event'}.get(key_type, key_type)
        k.Type = getattr(ua.AASKeyElementsDataType, key_type, ua.AASKeyElementsDataType.GlobalReference)
        k.Local = bool(model_reference)
        k.Value = key.get('value') or ''
        k.IdType = getattr(ua.AASKeyTypeDataType, _id_type(k.Value, key_type))
        return k

    async def reference(self, parent, name, reference, nodeid=None, ref=None):
        if not reference:
            return None
        node = await self.add_object(parent, nodeid or self.nid(f'{parent.Identifier}/{name}'), name, 'Reference',
                                     ref=ref, name_ns=self.i4)
        model = reference.get('type') == 'ModelReference'
        keys = [self._key(k, model) for k in reference.get('keys') or []]
        await self.add_property(node, 'Keys', self.ua.Variant(keys, self.ua.VariantType.ExtensionObject), self.dt('Key'),
                                array=True)
        return node

    async def referable(self, node, obj, type_name=None):
        await self.add_property(node, 'Category', self.string(obj.get('category')), self.ua.NodeId(self.ua.ObjectIds.String))
        await self.add_reference(node, self.t('IAASReferable'), HAS_INTERFACE, target_class=self.ua.NodeClass.ObjectType)
        if obj.get('semanticId'):
            await self.semantic(node, obj['semanticId'])
        for qualifier in obj.get('qualifiers') or []:
            await self.qualifier(node, qualifier)

    async def identifiable(self, node, obj):
        ua = self.ua
        await self.add_reference(node, self.t('IAASIdentifiable'), HAS_INTERFACE, target_class=ua.NodeClass.ObjectType)
        ident = await self.add_object(node, self.nid(f'{node.Identifier}/Identification'), 'Identification', 'Identifier',
                                      name_ns=self.i4)
        await self.add_property(ident, 'Id', self.string(obj['id']), ua.NodeId(ua.ObjectIds.String))
        await self.add_property(ident, 'IdType', self.enum(self._enum_value('IdentifierType', _id_type(obj['id']), 'Custom')),
                                self.dt('IdentifierType'))
        admin = obj.get('administration') or {}
        admin_node = await self.add_object(node, self.nid(f'{node.Identifier}/Administration'), 'Administration',
                                           'AdministrativeInformation', name_ns=self.i4)
        for key, name in (('version', 'Version'), ('revision', 'Revision')):
            if admin.get(key):
                await self.add_property(admin_node, name, self.string(admin[key]), ua.NodeId(ua.ObjectIds.String))

    async def semantic(self, node, reference):
        keys = reference.get('keys') or []
        if not keys:
            return
        target = await self.concept(keys[0].get('value'))
        await self.add_reference(node, target, HAS_DICTIONARY_ENTRY)

    async def replace_submodel(self, submodel):
        """Rebuild one submodel's subtree in place (same NodeIds), keeping every parent reference."""
        ua = self.ua
        node = self.submodel_nodes[submodel['id']]
        parents = self.parents.pop(submodel['id'], [])
        prefix = f'sm|{submodel["id"]}'
        self.bindings = {k: v for k, v in self.bindings.items() if v.submodel_id != submodel['id']}
        await self.server.delete_nodes([self.server.get_node(node)], recursive=True)
        if not parents:
            return
        (first_parent, first_ref), others = parents[0], parents[1:]
        await self.submodel(first_parent, submodel, ref=first_ref)
        for parent, ref in others:
            await self.add_reference(parent, self.nid(prefix), ref)
            self.parents[submodel['id']].append((parent, ref))

    async def concept(self, identifier: str):
        if identifier in self.cd_nodes:
            return self.cd_nodes[identifier]
        cd = self.cds.get(identifier, {})
        type_name = 'IrdiConceptDescription' if IRDI.match(identifier) else 'IriConceptDescription'
        name = cd.get('idShort') or identifier
        node = await self.add_object(self.dictionary, self.nid(f'cd|{identifier}'), name, type_name,
                                     description=_first_text(cd.get('description')))
        await self.identifiable(node, {'id': identifier, 'administration': cd.get('administration')})
        self.cd_nodes[identifier] = node
        return node

    async def qualifier(self, parent, qualifier):
        ua = self.ua
        name = qualifier.get('type') or 'Qualifier'
        node = await self.add_object(parent, self.nid(f'{parent.Identifier}/qualifier:{name}'), name, 'Qualifier')
        await self.add_property(node, 'Type', self.string(qualifier.get('type')), ua.NodeId(ua.ObjectIds.String))
        value_type = qualifier.get('valueType') or 'xs:string'
        await self.add_property(node, 'ValueType', self.enum(self._value_type_enum(value_type)), self.dt('ValueType'))
        if qualifier.get('value') is not None:
            variant = self.typed(qualifier['value'], value_type)
            await self.add_property(node, 'Value', variant, self.datatype_of(variant))
        if qualifier.get('valueId'):
            await self.reference(node, 'ValueId', qualifier['valueId'])

    def _declared(self, value_type, variant):
        """DataType of a value variable: the one its valueType maps to, so an empty or unparsable
        value does not change the declared type (unknown types are String)."""
        spec = VALUE_TYPES.get(value_type)
        if spec:
            return self.ua.NodeId(getattr(self.ua.VariantType, spec[1]).value)
        return self.ua.NodeId(self.ua.ObjectIds.String) if variant.VariantType != self.ua.VariantType.Null \
            else self.ua.NodeId(self.ua.ObjectIds.BaseDataType)

    def _value_type_enum(self, value_type: str) -> int:
        member = VALUE_TYPES.get(value_type, ('String',))[0]
        return int(getattr(self.ua.AASValueTypeDataType, member))

    def typed(self, text, value_type):
        """The AAS lexical value as a typed Variant (String when it does not parse)."""
        ua = self.ua
        if text is None:
            return ua.Variant(None, ua.VariantType.Null)
        spec = VALUE_TYPES.get(value_type)
        if spec:
            try:
                return ua.Variant(spec[2](str(text)), getattr(ua.VariantType, spec[1]))
            except (ValueError, TypeError, OverflowError):
                pass
        return ua.Variant(str(text), ua.VariantType.String)

    def lang_texts(self, items):
        ua = self.ua
        return ua.Variant([ua.LocalizedText(i.get('text'), i.get('language')) for i in items or []],
                          ua.VariantType.LocalizedText)

    # -- identifiables --------------------------------------------------------------------------------
    async def build(self, environment: dict):
        ua = self.ua
        self.cds = {c['id']: c for c in environment.get('conceptDescriptions') or []}
        root = await self.add_object(self.server.nodes.objects.nodeid, self.nid('AASROOT'), 'AASROOT', None,
                                     ref=ua.ObjectIds.Organizes, type_id=ua.NodeId(ua.ObjectIds.FolderType))
        self.root = root
        self.dictionary = await self.add_object(root, self.nid('AASROOT/ConceptDictionary'), 'ConceptDictionary',
                                                'ConceptDictionary', ref=ua.ObjectIds.Organizes)
        referenced = set()
        submodels = {s['id']: s for s in environment.get('submodels') or []}
        for shell in environment.get('assetAdministrationShells') or []:
            await self.shell(root, shell, submodels, referenced)
        for submodel in submodels.values():
            if submodel['id'] not in referenced:
                await self.submodel(root, submodel, ref=ua.ObjectIds.Organizes)
        return root

    async def shell(self, root, shell, submodels, referenced):
        ua = self.ua
        node = await self.add_object(root, self.nid(f'aas|{shell["id"]}'), shell.get('idShort') or shell['id'],
                                     'AssetAdministrationShell', ref=ua.ObjectIds.Organizes,
                                     description=_first_text(shell.get('description')),
                                     display=_first_text(shell.get('displayName')))
        await self.referable(node, shell)
        await self.identifiable(node, shell)
        await self.add_reference(node, self.dictionary, ua.ObjectIds.HasComponent)
        info = shell.get('assetInformation') or {}
        asset = await self.add_object(node, self.nid(f'aas|{shell["id"]}/Asset'), 'Asset', 'Asset', name_ns=self.i4)
        kind = {'Type': 'Type', 'Role': 'Type'}.get(info.get('assetKind'), 'Instance')
        await self.add_property(asset, 'AssetKind', self.enum(self._enum_value('AssetKind', kind, 'Instance')),
                                self.dt('AssetKind'))
        if info.get('globalAssetId'):
            await self.identifiable(asset, {'id': info['globalAssetId']})
        for specific in info.get('specificAssetIds') or []:
            await self.add_property(asset, specific.get('name') or 'specificAssetId', self.string(specific.get('value')),
                                    ua.NodeId(ua.ObjectIds.String), name_ns=self.ns,
                                    nodeid=self.nid(f'aas|{shell["id"]}/Asset/specificAssetId:{specific.get("name")}'))
        if shell.get('derivedFrom'):
            await self.reference(node, 'DerivedFrom', shell['derivedFrom'])
        for index, ref in enumerate(shell.get('submodels') or []):
            target = ((ref.get('keys') or [{}])[-1]).get('value')
            sm = submodels.get(target)
            name = (sm or {}).get('idShort') or f'SubmodelReference{index}'
            await self.reference(node, name, ref, nodeid=self.nid(f'aas|{shell["id"]}/ref:{index}'))
            if sm is None:
                continue
            if target in self.submodel_nodes:
                await self.add_reference(node, self.submodel_nodes[target], ua.ObjectIds.HasComponent)
                self.parents.setdefault(target, []).append((node, ua.ObjectIds.HasComponent))
            else:
                await self.submodel(node, sm)
            referenced.add(target)

    async def submodel(self, parent, submodel, ref=None):
        ua = self.ua
        self.parents.setdefault(submodel['id'], []).insert(0, (parent, ref or ua.ObjectIds.HasComponent))
        base = f'sm|{submodel["id"]}'
        node = await self.add_object(parent, self.nid(base), submodel.get('idShort') or submodel['id'], 'Submodel',
                                     ref=ref, description=_first_text(submodel.get('description')),
                                     display=_first_text(submodel.get('displayName')))
        self.submodel_nodes[submodel['id']] = node
        await self.add_property(node, 'ModelingKind', self.enum(self._enum_value(
            'ModelingKind', submodel.get('kind') or 'Instance', 'Instance')), self.dt('ModelingKind'))
        await self.referable(node, submodel)
        await self.identifiable(node, submodel)
        for child in submodel.get('submodelElements') or []:
            await self.element(node, child, submodel['id'], child.get('idShort'))
        return node

    # -- submodel elements -----------------------------------------------------------------------------
    async def element(self, parent, element, submodel_id, path, name=None):
        ua = self.ua
        model_type = element.get('modelType')
        type_name = model_type
        if model_type == 'SubmodelElementList':
            type_name = 'SubmodelElementCollection' if element.get('orderRelevant') is False \
                else 'OrderedSubmodelElementCollection'
        if type_name not in TYPES:
            type_name = 'Capability'
        node = await self.add_object(parent, self.nid(f'sm|{submodel_id}|{path}'), name or element.get('idShort') or path,
                                     type_name, description=_first_text(element.get('description')),
                                     display=_first_text(element.get('displayName')))
        await self.add_property(node, 'ModelingKind', self.enum(self._enum_value('ModelingKind', 'Instance', 'Instance')),
                                self.dt('ModelingKind'))
        await self.referable(node, element)
        string = ua.NodeId(ua.ObjectIds.String)
        if model_type == 'Property':
            value_type = element.get('valueType') or 'xs:string'
            await self.add_property(node, 'ValueType', self.enum(self._value_type_enum(value_type)), self.dt('ValueType'))
            variant = self.typed(element.get('value'), value_type)
            value_node = await self.add_property(node, 'Value', variant, self._declared(value_type, variant),
                                                 writable=self.writable)
            self.bindings[value_node] = Binding(submodel_id, path, 'value', value_type, model_type)
            if element.get('valueId'):
                await self.reference(node, 'ValueId', element['valueId'])
        elif model_type == 'MultiLanguageProperty':
            value_node = await self.add_property(node, 'Value', self.lang_texts(element.get('value')),
                                                 ua.NodeId(ua.ObjectIds.LocalizedText), array=True, writable=self.writable)
            self.bindings[value_node] = Binding(submodel_id, path, 'value', None, model_type)
        elif model_type == 'Range':
            value_type = element.get('valueType') or 'xs:double'
            await self.add_property(node, 'ValueType', self.enum(self._value_type_enum(value_type)), self.dt('ValueType'))
            for key, name in (('min', 'Min'), ('max', 'Max')):
                variant = self.typed(element.get(key), value_type)
                value_node = await self.add_property(node, name, variant, self._declared(value_type, variant),
                                                     writable=self.writable)
                self.bindings[value_node] = Binding(submodel_id, path, key, value_type, model_type)
        elif model_type in ('File', 'Blob'):
            if model_type == 'File':
                await self.add_property(node, 'MimeType', self.string(element.get('contentType')), self.dt('Mime'))
                await self.add_property(node, 'Value', self.string(element.get('value')), string)
                data = self.files(submodel_id, path)
            else:
                data = base64.b64decode(element['value']) if element.get('value') else b''
            if data is not None:
                await self.file_object(node, data)
        elif model_type in ('SubmodelElementCollection', 'SubmodelElementList'):
            if model_type == 'SubmodelElementCollection':
                await self.add_property(node, 'AllowDuplicates', ua.Variant(False, ua.VariantType.Boolean),
                                        ua.NodeId(ua.ObjectIds.Boolean))
            for index, child in enumerate(element.get('value') or []):
                if model_type == 'SubmodelElementList':
                    child_path, child_name = f'{path}[{index}]', child.get('idShort') or f'[{index}]'
                else:
                    child_path, child_name = f'{path}.{child.get("idShort")}', None
                await self.element(node, child, submodel_id, child_path, child_name)
        elif model_type == 'ReferenceElement':
            await self.reference(node, 'Value', element.get('value'))
        elif model_type in ('RelationshipElement', 'AnnotatedRelationshipElement'):
            await self.reference(node, 'First', element.get('first'))
            await self.reference(node, 'Second', element.get('second'))
            for child in element.get('annotations') or []:
                await self.element(node, child, submodel_id, f'{path}.{child.get("idShort")}')
        elif model_type == 'Entity':
            entity_type = element.get('entityType') or 'SelfManagedEntity'
            await self.add_property(node, 'EntityType', self.enum(self._enum_value('EntityType', entity_type,
                                                                                   'SelfManagedEntity')),
                                    self.dt('EntityType'))
            if element.get('globalAssetId'):
                await self.reference(node, 'Asset', {'type': 'ExternalReference', 'keys': [
                    {'type': 'GlobalReference', 'value': element['globalAssetId']}]})
            for child in element.get('statements') or []:
                await self.element(node, child, submodel_id, f'{path}.{child.get("idShort")}')
        elif model_type == 'Operation':
            await self.operation(node, element, submodel_id, path)
        return node

    async def operation(self, node, element, submodel_id, path):
        ua = self.ua

        def arguments(variables):
            result = []
            for variable in variables or []:
                value = variable.get('value') or {}
                argument = ua.Argument()
                argument.Name = value.get('idShort') or 'argument'
                spec = VALUE_TYPES.get(value.get('valueType'))
                argument.DataType = ua.NodeId(getattr(ua.ObjectIds, spec[1]) if spec else ua.ObjectIds.String)
                argument.ValueRank = -1
                result.append(argument)
            return result

        inputs = arguments(element.get('inputVariables'))
        outputs = arguments(element.get('outputVariables'))
        input_names = [a.Name for a in inputs]
        output_names = [a.Name for a in outputs]
        output_types = [v.get('value', {}).get('valueType') for v in element.get('outputVariables') or []]
        space = self

        async def call(parent, *values):
            if space.operations is None:
                return [ua.Variant(None)] * len(output_names)
            args = {name: _python(v.Value if isinstance(v, ua.Variant) else v) for name, v in zip(input_names, values)}
            try:
                result = await asyncio.get_running_loop().run_in_executor(None, space.operations, submodel_id, path, args)
            except PermissionError as error:
                raise ua.UaStatusCodeError(ua.StatusCodes.BadUserAccessDenied) from error
            outputs_value = (result or {}).get('outputArguments') or {}
            return [space.typed(None if outputs_value.get(n) is None else str(outputs_value.get(n)), t)
                    for n, t in zip(output_names, output_types)]

        await self.server.get_node(node).add_method(self.nid(f'{node.Identifier}/Operation'),
                                                     ua.QualifiedName('Operation', self.i4), call, inputs, outputs)

    async def file_object(self, parent, data: bytes):
        """OPC UA FileType (Part 5 C.2) over ``data``; read only."""
        ua = self.ua
        node = await self.add_object(parent, self.nid(f'{parent.Identifier}/File'), 'File', None, name_ns=self.i4,
                                     type_id=ua.NodeId(ua.ObjectIds.FileType))
        uint16, uint32, uint64, int32, boolean = (ua.NodeId(getattr(ua.ObjectIds, n)) for n in
                                                  ('UInt16', 'UInt32', 'UInt64', 'Int32', 'Boolean'))
        for name, variant, dtype in (('Size', ua.Variant(len(data), ua.VariantType.UInt64), uint64),
                                     ('Writable', ua.Variant(False, ua.VariantType.Boolean), boolean),
                                     ('UserWritable', ua.Variant(False, ua.VariantType.Boolean), boolean),
                                     ('OpenCount', ua.Variant(0, ua.VariantType.UInt16), uint16)):
            await self.add_property(node, name, variant, dtype, name_ns=0)
        handles = self.open_files
        space = self

        def argument(name, dtype):
            a = ua.Argument()
            a.Name, a.DataType, a.ValueRank = name, dtype, -1
            return a

        async def open_(parent, mode):
            mode = mode.Value if isinstance(mode, ua.Variant) else mode
            if mode & 0x6:            # Write / EraseExisting
                raise ua.UaStatusCodeError(ua.StatusCodes.BadNotWritable)
            import time as clock
            now = clock.monotonic()
            for old in [h for h, f in handles.items() if now - f.used > OPEN_FILE_SECONDS]:
                del handles[old]
            while len(handles) >= MAX_OPEN_FILES:
                del handles[min(handles, key=lambda h: handles[h].used)]
            handle = space._next_handle
            space._next_handle += 1
            handles[handle] = _OpenFile(data)
            return [ua.Variant(handle, ua.VariantType.UInt32)]

        def opened(handle):
            handle = handle.Value if isinstance(handle, ua.Variant) else handle
            if handle not in handles:
                raise ua.UaStatusCodeError(ua.StatusCodes.BadInvalidArgument)
            handles[handle].used = __import__('time').monotonic()
            return handle, handles[handle]

        async def close(parent, handle):
            handle, _ = opened(handle)
            del handles[handle]
            return []

        async def read(parent, handle, length):
            _, state = opened(handle)
            length = length.Value if isinstance(length, ua.Variant) else length
            chunk = state.data[state.position:state.position + max(0, length)]
            state.position += len(chunk)
            return [ua.Variant(chunk, ua.VariantType.ByteString)]

        async def get_position(parent, handle):
            _, state = opened(handle)
            return [ua.Variant(state.position, ua.VariantType.UInt64)]

        async def set_position(parent, handle, position):
            _, state = opened(handle)
            state.position = min(len(state.data), position.Value if isinstance(position, ua.Variant) else position)
            return []

        file_node = self.server.get_node(node)
        byte = ua.NodeId(ua.ObjectIds.Byte)
        for name, func, inputs, outputs in (
                ('Open', open_, [argument('Mode', byte)], [argument('FileHandle', uint32)]),
                ('Close', close, [argument('FileHandle', uint32)], []),
                ('Read', read, [argument('FileHandle', uint32), argument('Length', int32)],
                 [argument('Data', ua.NodeId(ua.ObjectIds.ByteString))]),
                ('GetPosition', get_position, [argument('FileHandle', uint32)], [argument('Position', uint64)]),
                ('SetPosition', set_position, [argument('FileHandle', uint32), argument('Position', uint64)], [])):
            await file_node.add_method(self.nid(f'{node.Identifier}/{name}'), ua.QualifiedName(name, 0), func,
                                       inputs, outputs)

    writable = False


def _id_type(identifier: str, key_type: str = '') -> str:
    if key_type in ('Property', 'SubmodelElementCollection', 'MultiLanguageProperty', 'Range', 'File', 'Blob',
                    'ReferenceElement', 'RelationshipElement', 'AnnotatedRelationshipElement', 'Entity', 'Operation',
                    'Capability', 'Event', 'SubmodelElement', 'DataElement'):
        return 'IdShort'
    if IRDI.match(identifier or ''):
        return 'IRDI'
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', identifier or ''):
        return 'IRI'
    return 'Custom'


def _python(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return value


async def _new_server(endpoint: str | None = None, user_manager=None):
    from asyncua import Server
    server = Server(user_manager=user_manager)
    await server.init()
    if endpoint:
        server.set_endpoint(endpoint)
    server.set_server_name('aasforge OPC UA (I4AAS)')
    await server.import_xml(nodeset_path())
    await server.load_data_type_definitions()
    i4 = await server.get_namespace_index(I4AAS_URI)
    ns = await server.register_namespace(INSTANCE_URI)
    return server, i4, ns


_EXPORTS: dict = {}
_EXPORT_LOCK = threading.Semaphore(1)


def export_nodeset(forge, claims: dict | None = None) -> bytes:
    """NodeSet2 XML of the environment a caller with ``claims`` (default: anonymous) may read
    (instances only; load it after the I4AAS NodeSet). Exports are built one at a time and cached
    until the store changes, since each one builds a complete OPC UA address space."""
    import json as _json
    latest = forge.store.db.execute('SELECT MAX(seq) AS seq FROM events').fetchone()['seq']
    key = (id(forge), _json.dumps(claims or {}, sort_keys=True), latest)
    with _EXPORT_LOCK:
        if key not in _EXPORTS:
            if len(_EXPORTS) > 16:
                _EXPORTS.clear()
            _EXPORTS[key] = asyncio.run(_export(forge, claims))
        return _EXPORTS[key]


async def _export(forge, claims):
    from asyncua.common.xmlexporter import XmlExporter
    logging.getLogger('asyncua').setLevel(logging.ERROR)
    server, i4, ns = await _new_server()
    space = AddressSpace(server, i4, ns, files=_attachment_reader(forge))
    await space.build(visible_environment(forge, claims))
    nodes = [server.get_node(n) for n in list(server.iserver.aspace.keys()) if n.NamespaceIndex == ns]
    exporter = XmlExporter(server, export_values=True)
    await exporter.build_etree(nodes)
    import io
    from xml.etree import ElementTree
    buffer = io.BytesIO()
    ElementTree.ElementTree(exporter.etree.getroot()).write(buffer, encoding='utf-8', xml_declaration=True)
    return buffer.getvalue()


def _attachment_reader(forge):
    def read(submodel_id, path):
        try:
            return forge.store.get_attachment('submodel', submodel_id, path)['data']
        except NotFound:
            return None
    return read


class _UserManager:
    """Password = API key or bearer token; the username is informational. Never grants Admin."""

    def __init__(self, authenticator):
        self.authenticator = authenticator

    def get_user(self, iserver, username=None, password=None, certificate=None):
        from asyncua.crypto.permission_rules import UserRole
        if username is None and password is None:
            return ForgeUser(UserRole.Anonymous, 'anonymous', {})
        if not password:
            return None
        headers = {'Authorization': f'Bearer {password}'} if password.count('.') == 2 else {'X-API-Key': password}
        try:
            subject = self.authenticator.authenticate(headers)
        except PermissionError:
            return None
        return ForgeUser(UserRole.User, subject.claims.get('sub') or username, dict(subject.claims))


class _Permissions:
    """No session may change the address space; reads, browsing, subscriptions, writes and calls are
    allowed and then checked against the AAS access rules (writes, calls) where they apply."""

    def __init__(self):
        from asyncua import ua
        from asyncua.crypto.permission_rules import USER_TYPES
        self.allowed = {ua.NodeId(t) for t in USER_TYPES}

    def check_validity(self, user, action_type_id, body):
        return action_type_id in self.allowed


class OpcUaServer:
    """Live I4AAS server following the store."""

    def __init__(self, forge, endpoint: str = 'opc.tcp://127.0.0.1:4840/aasforge/', writable: bool = False,
                 cert_dir: str | None = None):
        self.forge, self.endpoint, self.writable = forge, endpoint, writable
        self.cert_dir = cert_dir
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.error = None
        self._dirty = asyncio.Event()
        self._thread = threading.Thread(target=self._run, name='aasforge-opcua', daemon=True)

    # -- lifecycle ---------------------------------------------------------------------------------
    def start(self, timeout: float = 60):
        self._thread.start()
        if not self.ready.wait(timeout) or self.error:
            raise RuntimeError(f'OPC UA server did not start: {self.error}')
        self.forge.store.subscribe(self._on_event)
        return self

    def stop(self):
        self.forge.store.unsubscribe(self._on_event)
        if self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._stop(), self.loop).result(30)
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(30)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._start())
        except Exception as error:  # noqa: BLE001 - reported to start()
            self.error = error
            self.ready.set()
            return
        self.ready.set()
        self.loop.run_forever()

    async def _start(self):
        from asyncua import ua
        from asyncua.common.callback import CallbackType
        logging.getLogger('asyncua').setLevel(logging.ERROR)
        self.server, self.i4, self.ns = await _new_server(self.endpoint, _UserManager(self.forge.authenticator))
        await self._certificate()
        # Anonymous reads may use NoSecurity; passwords (API keys, tokens) are always encrypted with
        # the server certificate, and clients can choose a signed and encrypted channel.
        self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity,
                                         ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt],
                                        permission_ruleset=_Permissions())
        self.server.set_identity_tokens([ua.AnonymousIdentityToken, ua.UserNameIdentityToken])
        await self._build()
        self.server.iserver.subscribe_server_callback(CallbackType.PreWrite, self._pre_write)
        await self.server.start()
        self._refresher = asyncio.ensure_future(self._refresh_loop())

    async def _certificate(self):
        from pathlib import Path
        from urllib.parse import urlsplit
        from cryptography.x509.oid import ExtendedKeyUsageOID
        from asyncua.crypto.cert_gen import setup_self_signed_certificate
        folder = Path(self.cert_dir) if self.cert_dir else None
        if folder is None:
            import tempfile
            self._tmp = tempfile.TemporaryDirectory()
            folder = Path(self._tmp.name)
        folder.mkdir(parents=True, exist_ok=True)
        key, cert = folder / 'opcua-server-key.pem', folder / 'opcua-server-cert.der'
        uri = 'urn:aasforge:opcua:server'
        await self.server.set_application_uri(uri)
        await setup_self_signed_certificate(key, cert, uri, urlsplit(self.endpoint).hostname or 'localhost',
                                            [ExtendedKeyUsageOID.SERVER_AUTH], {'commonName': 'aasforge OPC UA'})
        await self.server.load_certificate(str(cert))
        await self.server.load_private_key(str(key))

    async def _stop(self):
        self._refresher.cancel()
        await self.server.stop()

    async def _build(self):
        space = AddressSpace(self.server, self.i4, self.ns, files=_attachment_reader(self.forge),
                             operations=self._invoke)
        space.writable = self.writable
        environment = visible_environment(self.forge)
        await space.build(environment)
        self.space = space
        self.snapshot = _structure(environment)

    # -- following the store -----------------------------------------------------------------------
    def _on_event(self, event):
        if event['kind'] in ('shell', 'submodel', 'concept-description'):
            self.loop.call_soon_threadsafe(self._dirty.set)

    async def _refresh_loop(self):
        while True:
            await self._dirty.wait()
            await asyncio.sleep(0.05)          # coalesce bursts of writes
            self._dirty.clear()
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001 - keep serving the last good address space
                _log.exception('OPC UA refresh failed')

    async def refresh(self):
        """Write changed values in place, rebuild the subtrees of submodels whose structure changed,
        and rebuild everything when shells, concept descriptions or the set of objects changed."""
        environment = await self.loop.run_in_executor(None, visible_environment, self.forge)
        structure = _structure(environment)
        if structure != self.snapshot:
            changed = _changed_submodels(self.snapshot, structure)
            if changed is None:
                await self._rebuild()
                return
            for submodel in environment['submodels']:
                if submodel['id'] in changed:
                    await self.space.replace_submodel(submodel)
            self.snapshot = structure
        submodels = {s['id']: s for s in environment['submodels']}
        rebuild = False
        for nodeid, binding in list(self.space.bindings.items()):
            submodel = submodels.get(binding.submodel_id)
            try:
                element = elements.get(submodel, binding.path)
            except (elements.PathError, TypeError):
                continue
            if binding.model_type == 'MultiLanguageProperty':
                variant = self.space.lang_texts(element.get('value'))
            else:
                variant = self.space.typed(element.get(binding.part), binding.value_type)
            node = self.server.get_node(nodeid)
            try:
                if (await node.read_data_value()).Value != variant:
                    await node.write_value(variant)
            except Exception:  # noqa: BLE001 - e.g. a value that no longer parses as its valueType
                rebuild = True
        if rebuild:
            await self._rebuild()

    async def _rebuild(self):
        await self.server.delete_nodes([self.server.get_node(self.space.root)], recursive=True)
        self.space.open_files.clear()
        await self._build()

    # -- writes and calls from OPC UA clients ------------------------------------------------------------

    async def _pre_write(self, event, dispatcher):
        from asyncua import ua
        if not event.is_external:
            return              # the server's own updates from the store
        for write in event.request_params.NodesToWrite:
            binding = self.space.bindings.get(write.NodeId)
            if binding is None or write.AttributeId != ua.AttributeIds.Value:
                continue
            if not self.writable:
                write.NodeId = ua.NodeId('refused', self.ns)       # writes are switched off
                continue
            try:
                await self.loop.run_in_executor(None, self._apply, event.user, binding, write.Value.Value.Value)
            except Exception as error:  # noqa: BLE001 - any refusal fails this write only
                _log.info('OPC UA write to %s refused: %s', binding.path, error)
                write.NodeId = ua.NodeId('refused', self.ns)       # the write then fails with BadNodeIdUnknown

    def _apply(self, user, binding, value):
        request = api_request('PATCH', element_route(binding.submodel_id, binding.path, '/$value'),
                              getattr(user, 'claims', None))
        api = self.forge.api
        submodel = self.forge.store.get('submodel', binding.submodel_id)
        api.allow(request, 'UPDATE', 'submodel', submodel, binding.path)

        def mutate(target):
            element = elements.get(target, binding.path)
            if binding.model_type == 'MultiLanguageProperty':
                elements.patch_value(element, [{t.Locale or 'en': t.Text} for t in value or []])
            elif binding.part in ('min', 'max'):
                elements.patch_value(element, {binding.part: _python(value)})
            else:
                elements.patch_value(element, _python(value))
        self.forge.repository.update_submodel(binding.submodel_id, mutate, actor=f'opcua:{request.subject.name}',
                                              path=binding.path)

    def _invoke(self, submodel_id, path, inputs):
        request = api_request('POST', element_route(submodel_id, path, '/invoke/$value'))
        api = self.forge.api
        submodel = self.forge.store.get('submodel', submodel_id)
        api.allow(request, 'EXECUTE', 'submodel', submodel, path)
        operation = elements.get(submodel, path)
        return api._run_operation(operation, {'inputArguments': inputs}, True)


def _changed_submodels(before: dict, after: dict):
    """Ids of submodels whose structure changed, or None if more than submodels changed."""
    for key in ('assetAdministrationShells', 'conceptDescriptions'):
        if before.get(key) != after.get(key):
            return None
    old = {s['id']: s for s in before.get('submodels') or []}
    new = {s['id']: s for s in after.get('submodels') or []}
    if set(old) != set(new):
        return None
    return {i for i in new if new[i] != old[i]}


def _structure(environment: dict):
    """What decides the shape of the address space (everything except Property/Range/MLP values)."""
    def strip(node):
        if isinstance(node, dict):
            skip = ('value', 'min', 'max') if node.get('modelType') in ('Property', 'Range', 'MultiLanguageProperty') else ()
            return {k: strip(v) for k, v in node.items() if k not in skip}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node
    return strip(environment)
