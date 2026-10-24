"""MQTT bridge: change events out, telemetry in.

Outbound, every change in the store is published on BaSyx-compatible topics (``repo`` is
``Config.mqtt_repository``):

    aas-repository/<repo>/shells/created                       payload: the shell
    aas-repository/<repo>/shells/<b64 id>/updated|deleted
    sm-repository/<repo>/submodels/created
    sm-repository/<repo>/submodels/<b64 id>/updated|deleted
    sm-repository/<repo>/submodels/<b64 id>/submodelElements/<idShortPath>/updated|created|deleted
    sm-repository/<repo>/submodels/<b64 id>/$value              retained ValueOnly of the submodel
    cd-repository/<repo>/conceptDescriptions/...                 as for shells
    aas-registry/<repo>/shell-descriptors/created|<b64>/updated|<b64>/deleted
    sm-registry/<repo>/submodel-descriptors/...

A broker usually has many readers, so payloads are what an anonymous caller of the HTTP API may
read. Objects it may not read produce no messages. A change inside a FILTERed part produces no
message either, because publishing is skipped when the visible state did not change.

Inbound (optional, ``Config.mqtt_ingest_claims``): devices publish ValueOnly JSON to

    aasforge/<repo>/submodels/<b64 id>/submodelElements/<idShortPath>/set

Each write is authorised by the access rules as the configured claims would be through
``PATCH .../$value``, so a dataspace's rules decide which elements telemetry may change.

``paho-mqtt`` is an optional dependency.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from urllib.parse import unquote, urlsplit

from . import canonical, elements, security
from .http import Request
from .store import NotFound

_log = logging.getLogger(__name__)
KINDS = {
    'shell': ('aas-repository', 'shells', '/shells'),
    'submodel': ('sm-repository', 'submodels', '/submodels'),
    'concept-description': ('cd-repository', 'conceptDescriptions', '/concept-descriptions'),
    'aas-descriptor': ('aas-registry', 'shell-descriptors', '/shell-descriptors'),
    'sm-descriptor': ('sm-registry', 'submodel-descriptors', '/submodel-descriptors'),
}
STORE_KIND = {'aas-descriptor': 'aas', 'sm-descriptor': 'sm'}


def _request(method, route, claims=None) -> Request:
    request = Request(method, route, {}, {})
    request.subject = security.Subject(dict(claims or {}), anonymous=not claims)
    return request


class MqttBridge:
    def __init__(self, forge, url: str, repository: str = 'aasforge', qos: int = 1, ingest_claims: dict | None = None,
                 client_id: str | None = None):
        try:
            import paho.mqtt.client as paho
        except ImportError as error:  # pragma: no cover - optional dependency
            raise RuntimeError('the MQTT bridge needs paho-mqtt: pip install paho-mqtt') from error
        self.forge, self.repository, self.qos, self.ingest_claims = forge, repository, qos, ingest_claims
        parts = urlsplit(url)
        if parts.scheme not in ('mqtt', 'mqtts'):
            raise ValueError('MQTT URL must start with mqtt:// or mqtts://')
        self.client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=client_id or f'aasforge-{repository}')
        if parts.username:
            self.client.username_pw_set(unquote(parts.username), unquote(parts.password or ''))
        if parts.scheme == 'mqtts':
            self.client.tls_set()
        self.host, self.port = parts.hostname or '127.0.0.1', parts.port or (8883 if parts.scheme == 'mqtts' else 1883)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe
        self.connected = threading.Event()
        self.inbox: queue.Queue = queue.Queue(maxsize=100000)
        self.published: dict = {}       # (kind, id) -> digest of the last visible state sent
        self._worker = threading.Thread(target=self._work, name='aasforge-mqtt', daemon=True)
        self._stopping = False

    @property
    def ingest_topic(self) -> str:
        return f'aasforge/{self.repository}/submodels/+/submodelElements/#'

    # -- lifecycle ---------------------------------------------------------------------------------
    def start(self, timeout: float = 10):
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()
        if not self.connected.wait(timeout):
            self.client.loop_stop()
            raise RuntimeError(f'could not connect to the MQTT broker at {self.host}:{self.port}')
        self._seed()
        self._worker.start()
        self.forge.store.subscribe(self._on_event)
        return self

    def _seed(self):
        """Remember what is visible now, so later updates and deletions of existing objects are
        reported (nothing is published for the existing state)."""
        for kind in ('shell', 'submodel', 'concept-description'):
            for obj in self.forge.store.iter_all(kind):
                visible = self._visible(kind, obj['id'])
                if visible is not None:
                    self.published[(kind, obj['id'])] = canonical.digest(visible)

    def stop(self):
        self.forge.store.unsubscribe(self._on_event)
        self._stopping = True
        self.inbox.put(None)
        self._worker.join(10)
        self.client.disconnect()
        self.client.loop_stop()

    def flush(self, timeout: float = 10):
        """Wait until every queued event has been published (tests, shutdown)."""
        done = threading.Event()
        self.inbox.put(done)
        done.wait(timeout)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            _log.warning('MQTT connection refused: %s', reason_code)
            return
        if self.ingest_claims is not None:
            client.subscribe(self.ingest_topic, qos=self.qos)   # ready once the broker confirms (SUBACK)
        else:
            self.connected.set()

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if any(code.is_failure for code in reason_codes):
            _log.warning('MQTT broker refused the ingest subscription: %s', reason_codes)
        self.connected.set()

    # -- outbound ------------------------------------------------------------------------------------
    def _on_event(self, event):
        try:
            self.inbox.put_nowait(event)
        except queue.Full:
            _log.warning('MQTT queue full; event %s dropped', event.get('seq'))

    def _work(self):
        while True:
            item = self.inbox.get()
            if item is None:
                return
            if isinstance(item, threading.Event):
                item.set()
                continue
            try:
                for topic, payload, retain in self.messages(item):
                    self.client.publish(topic, payload, qos=self.qos, retain=retain)
            except Exception:  # noqa: BLE001 - a broken event must not stop the bridge
                _log.exception('MQTT publish failed for event %s', item.get('seq'))

    def _visible(self, kind, identifier):
        """The object as an anonymous reader sees it, or None."""
        api = self.forge.api
        b64 = canonical.b64url_encode(identifier)
        route = f'{KINDS[kind][2]}/{b64}'
        if kind in STORE_KIND:
            try:
                descriptor = self.forge.store.get_descriptor(STORE_KIND[kind], identifier)
            except NotFound:
                return None
            return api._descriptor_view(_request('GET', route), STORE_KIND[kind], descriptor)
        try:
            obj = self.forge.store.get(kind, identifier)
        except NotFound:
            return None
        request = _request('GET', route)
        if not api.visible(request, kind, obj):
            return None
        if kind == 'submodel':
            try:
                return api.readable_submodel(request, obj)
            except PermissionError:
                return None
        return obj

    def messages(self, event):
        """(topic, payload, retain) to publish for a store event."""
        kind = event['kind']
        if kind not in KINDS:
            return []
        prefix, collection, _ = KINDS[kind]
        base = f'{prefix}/{self.repository}/{collection}'
        identifier, op = event['id'], event['op']
        b64 = canonical.b64url_encode(identifier)
        key = (kind, identifier)
        if op == 'deleted':
            if key not in self.published:
                return []           # never visible: its deletion is not news either
            del self.published[key]
            out = [(f'{base}/{b64}/deleted', json.dumps({'id': identifier}), False)]
            if kind == 'submodel':
                out.append((f'{base}/{b64}/$value', b'', True))         # clears the retained value
            return out
        obj = self._visible(kind, identifier)
        if obj is None:
            if key in self.published:        # no longer visible to anonymous readers
                del self.published[key]
                out = [(f'{base}/{b64}/deleted', json.dumps({'id': identifier}), False)]
                return out + ([(f'{base}/{b64}/$value', b'', True)] if kind == 'submodel' else [])
            return []
        digest = canonical.digest(obj)
        if self.published.get(key) == digest:
            return []                         # a hidden part changed; nothing visible to report
        first = key not in self.published
        self.published[key] = digest
        payload = json.dumps(obj, ensure_ascii=False)
        out = [(f'{base}/created', payload, False) if first and op == 'created'
               else (f'{base}/{b64}/updated', payload, False)]
        if kind == 'submodel':
            path = event.get('path')
            if path and not first:
                try:
                    element = elements.get(obj, elements.unquote(path))
                    out.append((f'{base}/{b64}/submodelElements/{elements.unquote(path)}/updated',
                                json.dumps(element, ensure_ascii=False), False))
                except elements.PathError:
                    pass
            out.append((f'{base}/{b64}/$value', json.dumps(elements.submodel_value_only(obj), ensure_ascii=False),
                        True))
        return out

    # -- inbound ------------------------------------------------------------------------------------
    def _on_message(self, client, userdata, message):
        threading.Thread(target=self.ingest, args=(message.topic, message.payload), daemon=True).start()

    def ingest(self, topic: str, payload: bytes):
        """Apply ``.../submodels/<b64>/submodelElements/<path>/set``; returns True when stored."""
        if self.ingest_claims is None:
            return False
        parts = topic.split('/')
        if len(parts) < 7 or parts[:3] != ['aasforge', self.repository, 'submodels'] or parts[4] != 'submodelElements' \
                or parts[-1] != 'set':
            return False
        try:
            submodel_id = canonical.b64url_decode(parts[3])
            path = '/'.join(parts[5:-1])
            value = json.loads(payload)
            submodel = self.forge.store.get('submodel', submodel_id)
            request = _request('PATCH', f'/submodels/{parts[3]}/submodel-elements/{path}/$value', self.ingest_claims)
            self.forge.api.allow(request, 'UPDATE', 'submodel', submodel, path)
            self.forge.repository.update_submodel(
                submodel_id, lambda s: elements.patch_value(elements.get(s, path), value),
                actor=f'mqtt:{request.subject.name}', path=path)
            return True
        except Exception as error:  # noqa: BLE001 - refused telemetry is logged, never fatal
            _log.info('MQTT ingest on %s refused: %s', topic, error)
            return False
