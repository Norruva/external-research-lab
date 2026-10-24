"""Application assembly and configuration."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import api, http, security, signing
from .repository import Repository
from .db import is_postgres
from .store import Store


@dataclass
class Config:
    data_dir: str = 'var'
    database: str | None = None              # SQLite path (default <data_dir>/aasforge.sqlite3), ':memory:' or postgresql:// DSN
    host: str = '127.0.0.1'
    port: int = 8081
    public_url: str | None = None            # external base URL used in descriptors and passport links
    verification: str = 'warn'               # reject | warn | off
    access_rules: str | None = None          # IDTA-01004 JSON rule file, 'dpp' (generated tier rules) or None (open)
    hs256_secret: str | None = None
    rs256_public_keys: list = field(default_factory=list)
    api_keys: dict = field(default_factory=dict)
    issuer: str | None = None
    audience: str | None = None
    cors_origins: list = field(default_factory=list)
    signing: bool = True
    trusted_certificates: list = field(default_factory=list)
    dpp: bool = True
    operation_delegation: list = field(default_factory=list)   # URL prefixes invocationDelegation may target
    max_body: int = 64 * 1024 * 1024                           # request body limit in bytes
    max_package: int = 512 * 1024 * 1024                       # total uncompressed AASX size limit
    opcua_endpoint: str | None = None          # e.g. opc.tcp://0.0.0.0:4840/aasforge/ to serve OPC UA (I4AAS)
    opcua_writable: bool = False               # allow authorised OPC UA clients to write values back
    mqtt_url: str | None = None                # e.g. mqtt://user:pass@broker:1883 to publish change events
    mqtt_repository: str = 'aasforge'          # repository id in the BaSyx-style topics
    mqtt_ingest_claims: dict | None = None     # claims telemetry writes are authorised with; None: no ingest
    catenax: dict | None = None                # {"bpn": ..., "dataplane_url": ..., "dsp_url": ...}: Catena-X DTR mode

    @classmethod
    def from_env(cls, **overrides):
        config = cls()
        mapping = {'AASFORGE_DATA_DIR': 'data_dir', 'AASFORGE_DATABASE': 'database', 'AASFORGE_HOST': 'host',
                   'AASFORGE_PORT': 'port', 'AASFORGE_PUBLIC_URL': 'public_url',
                   'AASFORGE_VERIFICATION': 'verification', 'AASFORGE_ACCESS_RULES': 'access_rules',
                   'AASFORGE_HS256_SECRET': 'hs256_secret', 'AASFORGE_ISSUER': 'issuer',
                   'AASFORGE_AUDIENCE': 'audience'}
        for env, attribute in mapping.items():
            if os.environ.get(env):
                setattr(config, attribute, int(os.environ[env]) if attribute == 'port' else os.environ[env])
        if os.environ.get('AASFORGE_API_KEYS'):
            config.api_keys = json.loads(os.environ['AASFORGE_API_KEYS'])
        if os.environ.get('AASFORGE_OPERATION_DELEGATION'):
            config.operation_delegation = os.environ['AASFORGE_OPERATION_DELEGATION'].split(',')
        if os.environ.get('AASFORGE_OPCUA_ENDPOINT'):
            config.opcua_endpoint = os.environ['AASFORGE_OPCUA_ENDPOINT']
        if os.environ.get('AASFORGE_MQTT_URL'):
            config.mqtt_url = os.environ['AASFORGE_MQTT_URL']
        if os.environ.get('AASFORGE_MQTT_REPOSITORY'):
            config.mqtt_repository = os.environ['AASFORGE_MQTT_REPOSITORY']
        if os.environ.get('AASFORGE_MQTT_INGEST_CLAIMS'):
            config.mqtt_ingest_claims = json.loads(os.environ['AASFORGE_MQTT_INGEST_CLAIMS'])
        if os.environ.get('AASFORGE_CATENAX'):
            config.catenax = json.loads(os.environ['AASFORGE_CATENAX'])
        if os.environ.get('AASFORGE_OPCUA_WRITABLE'):
            config.opcua_writable = os.environ['AASFORGE_OPCUA_WRITABLE'].lower() in ('1', 'true', 'yes')
        for env, attribute in (('AASFORGE_MAX_BODY', 'max_body'), ('AASFORGE_MAX_PACKAGE', 'max_package')):
            if os.environ.get(env):
                setattr(config, attribute, int(os.environ[env]))
        if os.environ.get('AASFORGE_CORS_ORIGINS'):
            config.cors_origins = os.environ['AASFORGE_CORS_ORIGINS'].split(',')
        for key, value in overrides.items():
            setattr(config, key, value)
        return config

    @property
    def base_url(self):
        return (self.public_url or f'http://{self.host}:{self.port}').rstrip('/')


class Forge:
    """The assembled application: store, repository, API, DPP layer and HTTP app."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        database = self.config.database or str(Path(self.config.data_dir) / 'aasforge.sqlite3')
        if database != ':memory:' and not is_postgres(database):
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.store = Store(database)
        self.repository = Repository(self.store, self.config.verification, self.config.base_url + '/api/v3.1')
        rules_doc = None
        if self.config.access_rules and self.config.access_rules != 'dpp':
            rules_doc = json.loads(Path(self.config.access_rules).read_text())
        self.rules = security.AccessRules(rules_doc)
        self.authenticator = security.Authenticator(self.config.hs256_secret, self.config.rs256_public_keys,
                                                    self.config.api_keys, self.config.issuer, self.config.audience)
        self.signer = None
        if self.config.signing:
            self.signer = (signing.Signer.self_signed() if database == ':memory:'
                           else signing.Signer.load_or_create(Path(self.config.data_dir) / 'keys'))
        self.api = api.Api(self.repository, self.authenticator, self.rules, self.signer,
                           self.config.trusted_certificates)
        self.api.delegation_prefixes = list(self.config.operation_delegation)
        self.api.forge = self
        self.opcua = None
        self.mqtt = None
        self.repository.max_package = self.config.max_package
        self.app = http.App(self.api.router, cors_origins=self.config.cors_origins)
        self.app.before.append(self.api.authenticate)
        self.catenax = None
        if self.config.catenax:
            from . import catenax
            self.catenax = catenax.CatenaX(self, **self.config.catenax).install()
        self.dpp = None
        if self.config.dpp:
            from .dpp import service
            self.dpp = service.DppService(self)
            self.dpp.register(self.api.router)

    def serve(self):
        server = http.serve(self.app, self.config.host, self.config.port, self.config.max_body)
        if self.config.opcua_endpoint:
            from . import opcua
            self.opcua = opcua.OpcUaServer(self, self.config.opcua_endpoint, self.config.opcua_writable).start()
        if self.config.mqtt_url:
            from . import mqtt
            self.mqtt = mqtt.MqttBridge(self, self.config.mqtt_url, self.config.mqtt_repository,
                                        ingest_claims=self.config.mqtt_ingest_claims).start()
        return server
