"""Registry federation: descriptors this registry does not hold are looked up in other registries.

Two sources, in this order:

* ``upstreams``: registry base URLs (e.g. ``https://registry.partner.example/api/v3.0``), asked in
  turn for ``/shell-descriptors/{id}``, ``/submodel-descriptors/{id}`` and, when
  ``federate_discovery`` is set, ``/lookup/shells``;
* the BaSyx hierarchy: for an id that is a URL on a host under one of ``hierarchy_domains``, the
  registry at ``hierarchy_template`` (default ``https://registry.{host}/api/v3.0``).

Federated answers are never stored (the other registry stays the owner). They are cached for
``ttl`` seconds and marked with the ``X-AAS-Federated-From`` header. They pass through the same
descriptor views as local ones.

Fetching URLs that ids point to is a server-side request forgery risk, so:

* only configured upstreams and hosts under the allowed domains are contacted, over https (http
  only if ``allow_http``);
* hosts that resolve to loopback, private, link-local or reserved addresses are refused unless
  ``allow_private`` is set (meant for tests and closed networks);
* redirects are not followed, responses are limited to ``max_bytes``, and calls time out.

Residual risk: the address check and the connection resolve the name separately, so a hostile DNS
server under an allowed domain could rebind between the two. Where that matters, configure
``upstreams`` only and leave ``hierarchy_domains`` empty.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import canonical


class FederationError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Federation:
    def __init__(self, upstreams=(), hierarchy_domains=(), hierarchy_template='https://registry.{host}/api/v3.0',
                 ttl: float = 300, timeout: float = 5, max_bytes: int = 2 * 1024 * 1024, allow_http: bool = False,
                 allow_private: bool = False, federate_discovery: bool = False, headers: dict | None = None):
        self.upstreams = [u.rstrip('/') for u in upstreams]
        self.domains = [d.lower().lstrip('.') for d in hierarchy_domains]
        self.template = hierarchy_template
        self.ttl, self.timeout, self.max_bytes = ttl, timeout, max_bytes
        self.allow_http, self.allow_private = allow_http, allow_private
        self.federate_discovery = federate_discovery
        self.headers = dict(headers or {})
        self._cache: dict = {}
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(_NoRedirect)

    # -- where to ask ---------------------------------------------------------------------------------
    def registries_for(self, identifier: str) -> list[str]:
        result = list(self.upstreams)
        hierarchy = self.hierarchy_registry(identifier)
        if hierarchy and hierarchy not in result:
            result.append(hierarchy)
        return result

    def hierarchy_registry(self, identifier: str) -> str | None:
        parts = urllib.parse.urlsplit(identifier or '')
        host = (parts.hostname or '').lower()
        if parts.scheme not in ('http', 'https') or not host:
            return None
        if not any(host == d or host.endswith('.' + d) for d in self.domains):
            return None
        return self.template.format(host=host).rstrip('/')

    # -- safe fetching ------------------------------------------------------------------------------------
    def _check_url(self, url: str):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'https' and not (self.allow_http and parts.scheme == 'http'):
            raise FederationError(f'refusing non-https registry URL {url}')
        if not parts.hostname:
            raise FederationError(f'registry URL has no host: {url}')
        if self.allow_private:
            return
        try:
            infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80))
        except socket.gaierror as error:
            raise FederationError(f'cannot resolve {parts.hostname}') from error
        for info in infos:
            address = ipaddress.ip_address(info[4][0])
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved \
                    or address.is_multicast or address.is_unspecified:
                raise FederationError(f'refusing {parts.hostname}: resolves to a non-public address')

    def _get(self, url: str, method: str = 'GET', body=None):
        self._check_url(url)
        data = None if body is None else json.dumps(body).encode()
        headers = {'Accept': 'application/json', **self.headers}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data, headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = response.read(self.max_bytes + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise FederationError(f'{url}: HTTP {error.code}') from error
        except (urllib.error.URLError, OSError) as error:
            raise FederationError(f'{url}: unreachable') from error
        if len(payload) > self.max_bytes:
            raise FederationError(f'{url}: response too large')
        try:
            return json.loads(payload)
        except json.JSONDecodeError as error:
            raise FederationError(f'{url}: not JSON') from error

    def _cached(self, key, compute):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        value = compute()
        with self._lock:
            self._cache[key] = (now + self.ttl, value)
        return value

    # -- lookups ----------------------------------------------------------------------------------------
    def descriptor(self, kind: str, identifier: str):
        """(descriptor, registry) from the first registry that has it, or (None, None)."""
        collection = 'shell-descriptors' if kind == 'aas' else 'submodel-descriptors'

        def compute():
            errors = []
            for registry in self.registries_for(identifier):
                try:
                    found = self._get(f'{registry}/{collection}/{canonical.b64url_encode(identifier)}')
                except FederationError as error:
                    errors.append(str(error))
                    continue
                if isinstance(found, dict) and found.get('id') == identifier:
                    return found, registry
            return None, None
        return self._cached(('descriptor', kind, identifier), compute)

    def lookup(self, asset_ids: list[dict]) -> list[str]:
        """AAS ids from the upstreams' discovery for these specificAssetIds (if enabled)."""
        if not self.federate_discovery:
            return []

        def compute():
            found = []
            query = '&'.join(f'assetIds={canonical.b64url_encode(json.dumps(a))}' for a in asset_ids)
            for registry in self.upstreams:
                try:
                    result = self._get(f'{registry}/lookup/shells?{query}')
                except FederationError:
                    continue
                for identifier in (result or {}).get('result', []):
                    if isinstance(identifier, str) and identifier not in found:
                        found.append(identifier)
            return found
        return self._cached(('lookup', json.dumps(asset_ids, sort_keys=True)), compute)
