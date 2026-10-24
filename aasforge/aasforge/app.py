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
        self.app = http.App(self.api.router, cors_origins=self.config.cors_origins)
        self.app.before.append(self.api.authenticate)
        self.dpp = None
        if self.config.dpp:
            from .dpp import service
            self.dpp = service.DppService(self)
            self.dpp.register(self.api.router)

    def serve(self):
        return http.serve(self.app, self.config.host, self.config.port)
