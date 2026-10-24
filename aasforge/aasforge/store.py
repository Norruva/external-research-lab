"""SQLite persistence: versioned identifiables, history, change events, attachments, registry,
discovery links, AASX packages and async operation handles.

Every write of a Shell, Submodel or ConceptDescription creates a new revision. Revisions are kept
append-only in ``history`` with validity intervals, which backs the API 3.2 ``$history`` (state at
a point in time) and ``$recent-changes`` operations and gives passports an audit trail. The
server-maintained ``created_at``/``updated_at`` are what 3.2 ``createdAt``/``updatedAt`` export from.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from . import canonical

KINDS = ('shell', 'submodel', 'concept-description')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS identifiables (
  kind TEXT NOT NULL, id TEXT NOT NULL, id_short TEXT, semantic_id TEXT, global_asset_id TEXT,
  body TEXT NOT NULL, sidecar TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  revision INTEGER NOT NULL, digest TEXT NOT NULL, PRIMARY KEY (kind, id));
CREATE INDEX IF NOT EXISTS identifiables_semantic ON identifiables(kind, semantic_id);
CREATE INDEX IF NOT EXISTS identifiables_updated ON identifiables(kind, updated_at);
CREATE TABLE IF NOT EXISTS history (
  kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
  body TEXT, deleted INTEGER NOT NULL DEFAULT 0, actor TEXT, digest TEXT, PRIMARY KEY (kind, id, revision));
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
  op TEXT NOT NULL, path TEXT, revision INTEGER, actor TEXT);
CREATE TABLE IF NOT EXISTS attachments (
  owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, path TEXT NOT NULL, content_type TEXT, filename TEXT,
  data BLOB NOT NULL, PRIMARY KEY (owner_kind, owner_id, path));
CREATE TABLE IF NOT EXISTS descriptors (
  kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (kind, id));
CREATE TABLE IF NOT EXISTS asset_links (
  aas_id TEXT PRIMARY KEY, links TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS packages (
  package_id TEXT PRIMARY KEY, filename TEXT, aas_ids TEXT NOT NULL, data BLOB NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations (
  handle_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL);
'''


class NotFound(KeyError):
    pass


class Conflict(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _semantic_id(body: dict):
    keys = (body.get('semanticId') or {}).get('keys') or []
    return keys[0].get('value') if keys else None


class Store:
    def __init__(self, path: str = ':memory:'):
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA journal_mode=WAL' if path != ':memory:' else 'PRAGMA journal_mode=MEMORY')
        self._db.execute('PRAGMA foreign_keys=ON')
        self._db.executescript(SCHEMA)
        self._lock = threading.RLock()
        self._listeners: list = []

    # -- transactions and events -------------------------------------------------------------
    def transaction(self):
        store = self

        class _Tx:
            """Re-entrant: only the outermost block begins and commits (or rolls back)."""

            def __enter__(self):
                store._lock.acquire()
                store._depth = getattr(store, '_depth', 0) + 1
                if store._depth == 1:
                    store._db.execute('BEGIN IMMEDIATE')
                return store

            def __exit__(self, exc_type, *_):
                try:
                    if store._depth == 1:
                        store._db.execute('ROLLBACK' if exc_type else 'COMMIT')
                finally:
                    store._depth -= 1
                    store._lock.release()
                return False

        return _Tx()

    def subscribe(self, callback):
        self._listeners.append(callback)

    def _event(self, kind, identifier, op, revision=None, path=None, actor=None):
        ts = now()
        cursor = self._db.execute('INSERT INTO events (ts, kind, id, op, path, revision, actor) VALUES (?,?,?,?,?,?,?)',
                                  (ts, kind, identifier, op, path, revision, actor))
        event = {'seq': cursor.lastrowid, 'ts': ts, 'kind': kind, 'id': identifier, 'op': op, 'path': path,
                 'revision': revision}
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - a broken subscriber must not break writes
                pass
        return event

    def events_since(self, seq: int = 0, limit: int = 500):
        rows = self._db.execute('SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?', (seq, limit)).fetchall()
        return [dict(r) for r in rows]

    # -- identifiables ------------------------------------------------------------------------
    def put(self, kind: str, body: dict, sidecar: list | None = None, actor: str | None = None,
            must_exist: bool | None = None, path: str | None = None) -> tuple[bool, dict]:
        """Create or replace an identifiable; returns (created, metadata)."""
        if kind not in KINDS:
            raise ValueError(kind)
        identifier = body.get('id')
        if not isinstance(identifier, str) or not identifier:
            raise ValueError('identifiable has no id')
        with self.transaction():
            row = self._db.execute('SELECT revision, created_at, digest FROM identifiables WHERE kind=? AND id=?',
                                   (kind, identifier)).fetchone()
            if must_exist is True and row is None:
                raise NotFound(identifier)
            if must_exist is False and row is not None:
                raise Conflict(f'{kind} {identifier!r} already exists')
            ts = now()
            digest = canonical.digest(body)
            if row is not None and row['digest'] == digest:
                return False, self.meta(kind, identifier)
            revision = (row['revision'] + 1) if row else 1
            created_at = row['created_at'] if row else ts
            text = json.dumps(body, ensure_ascii=False)
            self._db.execute(
                'INSERT OR REPLACE INTO identifiables (kind, id, id_short, semantic_id, global_asset_id, body, sidecar, '
                'created_at, updated_at, revision, digest) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (kind, identifier, body.get('idShort'), _semantic_id(body),
                 (body.get('assetInformation') or {}).get('globalAssetId'), text, json.dumps(sidecar or []),
                 created_at, ts, revision, digest))
            self._db.execute('UPDATE history SET valid_to=? WHERE kind=? AND id=? AND valid_to IS NULL',
                             (ts, kind, identifier))
            self._db.execute('INSERT INTO history (kind, id, revision, valid_from, body, actor, digest) VALUES (?,?,?,?,?,?,?)',
                             (kind, identifier, revision, ts, text, actor, digest))
            self._event(kind, identifier, 'created' if row is None else 'updated', revision, path, actor)
            return row is None, self.meta(kind, identifier)

    def get(self, kind: str, identifier: str, at: str | None = None) -> dict:
        if at is None:
            row = self._db.execute('SELECT body FROM identifiables WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        else:
            row = self._db.execute(
                'SELECT body FROM history WHERE kind=? AND id=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) '
                'AND deleted=0 ORDER BY revision DESC LIMIT 1', (kind, identifier, at, at)).fetchone()
        if row is None or row['body'] is None:
            raise NotFound(identifier)
        return json.loads(row['body'])

    def meta(self, kind: str, identifier: str) -> dict:
        row = self._db.execute('SELECT id, id_short, semantic_id, created_at, updated_at, revision, digest, sidecar '
                               'FROM identifiables WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        if row is None:
            raise NotFound(identifier)
        meta = dict(row)
        meta['sidecar'] = json.loads(meta['sidecar'])
        return meta

    def exists(self, kind: str, identifier: str) -> bool:
        return self._db.execute('SELECT 1 FROM identifiables WHERE kind=? AND id=?', (kind, identifier)).fetchone() is not None

    def delete(self, kind: str, identifier: str, actor: str | None = None) -> None:
        with self.transaction():
            row = self._db.execute('SELECT revision FROM identifiables WHERE kind=? AND id=?', (kind, identifier)).fetchone()
            if row is None:
                raise NotFound(identifier)
            ts = now()
            self._db.execute('DELETE FROM identifiables WHERE kind=? AND id=?', (kind, identifier))
            self._db.execute('UPDATE history SET valid_to=? WHERE kind=? AND id=? AND valid_to IS NULL', (ts, kind, identifier))
            self._db.execute('INSERT INTO history (kind, id, revision, valid_from, valid_to, body, deleted, actor) '
                             'VALUES (?,?,?,?,?,NULL,1,?)', (kind, identifier, row['revision'] + 1, ts, ts, actor))
            self._db.execute('DELETE FROM attachments WHERE owner_kind=? AND owner_id=?', (kind, identifier))
            self._event(kind, identifier, 'deleted', row['revision'] + 1, None, actor)

    def list(self, kind: str, *, id_short=None, semantic_id=None, ids=None, limit=None, cursor=None,
             created_from=None, updated_from=None, order='id'):
        clauses, params = ['kind=?'], [kind]
        if id_short:
            clauses.append('id_short=?')
            params.append(id_short)
        if semantic_id:
            clauses.append('semantic_id=?')
            params.append(semantic_id)
        if ids is not None:
            clauses.append(f'id IN ({",".join("?" * len(ids))})' if ids else '0')
            params += list(ids)
        if created_from:
            clauses.append('created_at>=?')
            params.append(created_from)
        if updated_from:
            clauses.append('updated_at>=?')
            params.append(updated_from)
        sort = 'updated_at, id' if order == 'updated' else 'id'
        if cursor:
            if order == 'updated':
                last_ts, last_id = json.loads(canonical.b64url_decode(cursor))
                clauses.append('(updated_at>? OR (updated_at=? AND id>?))')
                params += [last_ts, last_ts, last_id]
            else:
                clauses.append('id>?')
                params.append(canonical.b64url_decode(cursor))
        sql = f'SELECT id, body, updated_at FROM identifiables WHERE {" AND ".join(clauses)} ORDER BY {sort}'
        rows = self._db.execute(sql + (' LIMIT ?' if limit else ''), params + ([limit + 1] if limit else [])).fetchall()
        next_cursor = None
        if limit and len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            next_cursor = canonical.b64url_encode(json.dumps([last['updated_at'], last['id']]) if order == 'updated'
                                                  else last['id'])
        return [json.loads(r['body']) for r in rows], next_cursor

    def iter_all(self, kind: str):
        for row in self._db.execute('SELECT body FROM identifiables WHERE kind=? ORDER BY id', (kind,)):
            yield json.loads(row['body'])

    def history(self, kind: str, identifier: str):
        rows = self._db.execute('SELECT revision, valid_from, valid_to, deleted, actor, digest FROM history '
                                'WHERE kind=? AND id=? ORDER BY revision', (kind, identifier)).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict:
        return {k: self._db.execute('SELECT COUNT(*) FROM identifiables WHERE kind=?', (k,)).fetchone()[0] for k in KINDS}

    # -- attachments -------------------------------------------------------------------------
    def put_attachment(self, owner_kind, owner_id, path, data: bytes, content_type=None, filename=None):
        with self.transaction():
            self._db.execute('INSERT OR REPLACE INTO attachments VALUES (?,?,?,?,?,?)',
                             (owner_kind, owner_id, path, content_type, filename, data))

    def get_attachment(self, owner_kind, owner_id, path):
        row = self._db.execute('SELECT content_type, filename, data FROM attachments WHERE owner_kind=? AND owner_id=? '
                               'AND path=?', (owner_kind, owner_id, path)).fetchone()
        if row is None:
            raise NotFound(path)
        return dict(row)

    def delete_attachment(self, owner_kind, owner_id, path):
        with self.transaction():
            if not self._db.execute('DELETE FROM attachments WHERE owner_kind=? AND owner_id=? AND path=?',
                                    (owner_kind, owner_id, path)).rowcount:
                raise NotFound(path)

    def attachments_of(self, owner_kind, owner_id):
        rows = self._db.execute('SELECT path, content_type, filename, data FROM attachments WHERE owner_kind=? AND '
                                'owner_id=?', (owner_kind, owner_id)).fetchall()
        return [dict(r) for r in rows]

    # -- registry, discovery, packages, operations ----------------------------------------------
    def put_descriptor(self, kind, body, must_exist=None):
        identifier = body.get('id')
        if not identifier:
            raise ValueError('descriptor has no id')
        with self.transaction():
            exists = self._db.execute('SELECT 1 FROM descriptors WHERE kind=? AND id=?', (kind, identifier)).fetchone()
            if must_exist is True and not exists:
                raise NotFound(identifier)
            if must_exist is False and exists:
                raise Conflict(f'descriptor {identifier!r} already exists')
            self._db.execute('INSERT OR REPLACE INTO descriptors VALUES (?,?,?,?)',
                             (kind, identifier, json.dumps(body, ensure_ascii=False), now()))
            self._event(f'{kind}-descriptor', identifier, 'updated' if exists else 'created')
            return not exists

    def get_descriptor(self, kind, identifier):
        row = self._db.execute('SELECT body FROM descriptors WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        if row is None:
            raise NotFound(identifier)
        return json.loads(row['body'])

    def delete_descriptor(self, kind, identifier):
        with self.transaction():
            if not self._db.execute('DELETE FROM descriptors WHERE kind=? AND id=?', (kind, identifier)).rowcount:
                raise NotFound(identifier)
            self._event(f'{kind}-descriptor', identifier, 'deleted')

    def list_descriptors(self, kind, limit=None, cursor=None):
        params = [kind]
        sql = 'SELECT id, body FROM descriptors WHERE kind=?'
        if cursor:
            sql += ' AND id>?'
            params.append(canonical.b64url_decode(cursor))
        sql += ' ORDER BY id'
        rows = self._db.execute(sql + (' LIMIT ?' if limit else ''), params + ([limit + 1] if limit else [])).fetchall()
        nxt = None
        if limit and len(rows) > limit:
            rows = rows[:limit]
            nxt = canonical.b64url_encode(rows[-1]['id'])
        return [json.loads(r['body']) for r in rows], nxt

    def set_asset_links(self, aas_id, links):
        with self.transaction():
            self._db.execute('INSERT OR REPLACE INTO asset_links VALUES (?,?)', (aas_id, json.dumps(links)))

    def get_asset_links(self, aas_id):
        row = self._db.execute('SELECT links FROM asset_links WHERE aas_id=?', (aas_id,)).fetchone()
        if row is None:
            raise NotFound(aas_id)
        return json.loads(row['links'])

    def delete_asset_links(self, aas_id):
        with self.transaction():
            if not self._db.execute('DELETE FROM asset_links WHERE aas_id=?', (aas_id,)).rowcount:
                raise NotFound(aas_id)

    def all_asset_links(self):
        return {r['aas_id']: json.loads(r['links']) for r in self._db.execute('SELECT * FROM asset_links')}

    def put_package(self, package_id, filename, aas_ids, data):
        with self.transaction():
            self._db.execute('INSERT OR REPLACE INTO packages VALUES (?,?,?,?,?)',
                             (package_id, filename, json.dumps(aas_ids), data, now()))

    def get_package(self, package_id):
        row = self._db.execute('SELECT * FROM packages WHERE package_id=?', (package_id,)).fetchone()
        if row is None:
            raise NotFound(package_id)
        result = dict(row)
        result['aas_ids'] = json.loads(result['aas_ids'])
        return result

    def delete_package(self, package_id):
        with self.transaction():
            if not self._db.execute('DELETE FROM packages WHERE package_id=?', (package_id,)).rowcount:
                raise NotFound(package_id)

    def list_packages(self, aas_id=None):
        rows = self._db.execute('SELECT package_id, filename, aas_ids FROM packages ORDER BY package_id').fetchall()
        result = [{'packageId': r['package_id'], 'aasIds': json.loads(r['aas_ids']), 'filename': r['filename']}
                  for r in rows]
        return [p for p in result if aas_id is None or aas_id in p['aasIds']]

    def new_operation(self, kind):
        handle = str(uuid.uuid4())
        with self.transaction():
            self._db.execute('INSERT INTO operations VALUES (?,?,?,?,?)', (handle, kind, 'Running', None, now()))
        return handle

    def finish_operation(self, handle, status, result):
        with self.transaction():
            self._db.execute('UPDATE operations SET status=?, result=? WHERE handle_id=?',
                             (status, json.dumps(result), handle))

    def get_operation(self, handle):
        row = self._db.execute('SELECT * FROM operations WHERE handle_id=?', (handle,)).fetchone()
        if row is None:
            raise NotFound(handle)
        result = dict(row)
        result['result'] = json.loads(result['result']) if result['result'] else None
        return result
