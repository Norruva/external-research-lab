"""Persistence on SQLite or PostgreSQL (see ``db.py``): versioned identifiables, history, change
events, attachments, registry, discovery links, AASX packages and async operation handles.

Every write of a Shell, Submodel or ConceptDescription creates a new revision. Revisions are kept
append-only in ``history`` with validity intervals, which backs the API 3.2 ``$history`` (state at
a point in time) and ``$recent-changes`` operations and gives passports an audit trail. The
server-maintained ``created_at``/``updated_at`` are what 3.2 ``createdAt``/``updatedAt`` export from.

Three index tables are maintained in the same transaction as each write. They let
``sqlfilter`` turn Query Language and Access Rule formulas into SQL prefilters:

* ``element_index``: one row per submodel element value (path, ``[]`` pattern, idShort,
  semanticId, valueType, text and numeric value);
* ``shell_submodels``: the submodel references of each shell;
* ``shell_asset_ids``: the specific asset IDs of each shell.
"""
from __future__ import annotations

import json
import math
import re
import threading
import uuid
from datetime import datetime, timezone

from . import canonical, elements
from .db import Database

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
CREATE TABLE IF NOT EXISTS element_index (
  owner_id TEXT NOT NULL, path TEXT NOT NULL, pattern TEXT NOT NULL, id_short TEXT, semantic_id TEXT,
  value_type TEXT, model_type TEXT, value_text TEXT, value_num REAL);
CREATE INDEX IF NOT EXISTS element_index_owner ON element_index(owner_id);
CREATE INDEX IF NOT EXISTS element_index_pattern ON element_index(pattern);
CREATE INDEX IF NOT EXISTS element_index_semantic ON element_index(semantic_id);
CREATE INDEX IF NOT EXISTS element_index_num ON element_index(pattern, value_num);
CREATE TABLE IF NOT EXISTS shell_submodels (
  aas_id TEXT NOT NULL, sm_id TEXT NOT NULL, PRIMARY KEY (aas_id, sm_id));
CREATE INDEX IF NOT EXISTS shell_submodels_sm ON shell_submodels(sm_id);
CREATE TABLE IF NOT EXISTS dtr_access_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shell_asset_ids (
  aas_id TEXT NOT NULL, name TEXT, value TEXT);
CREATE INDEX IF NOT EXISTS shell_asset_ids_owner ON shell_asset_ids(aas_id);
CREATE INDEX IF NOT EXISTS shell_asset_ids_value ON shell_asset_ids(name, value);
'''
INDEX_PATTERN = re.compile(r'\[\d+\]')


class NotFound(KeyError):
    pass


class Conflict(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _semantic_id(body: dict):
    keys = (body.get('semanticId') or {}).get('keys') or []
    return keys[0].get('value') if keys else None


def _index_rows(submodel: dict):
    """element_index rows; values are typed exactly as the formula evaluator coerces them."""
    from . import formula   # formula imports elements only; deferred to keep import order simple
    for path, element in elements.walk_elements(submodel):
        values = [v.value for v in formula._sme_attribute(element, 'value', ())] or [None]
        base = (submodel['id'], path, INDEX_PATTERN.sub('[]', path), element.get('idShort'), _semantic_id(element),
                element.get('valueType'), element.get('modelType'))
        for value in values:
            text = num = None
            if value is not None:
                text = formula._coerce(formula.Value(value), 'str')
                try:
                    num = formula._coerce(formula.Value(value), 'num')
                except (ValueError, TypeError):
                    num = None
                if num is not None and math.isnan(num):
                    num = None      # NaN compares false with everything, as an absent value does
            yield base + (text, num)


def _package_record(text: str) -> dict:
    record = json.loads(text)
    if isinstance(record, list):      # stores written before package contents were recorded
        return {'aasIds': record, 'contents': {'shell': record}}
    return record


class Store:
    def __init__(self, path: str = ':memory:'):
        """``path``: a SQLite file, ``:memory:``, or a ``postgresql://`` DSN."""
        self._db = Database(path)
        self._db.script(SCHEMA)
        self._listeners: list = []
        self._own_events: set = set()
        if not self._db.execute('SELECT 1 FROM element_index LIMIT 1').fetchone():
            self.reindex()

    @property
    def db(self) -> Database:
        return self._db

    def close(self):
        self._db.close()

    def reindex(self):
        """Rebuild the index tables from the stored objects (after upgrading an older store)."""
        with self.transaction():
            self._db.lock_key('reindex')         # workers starting together rebuild once
            if self._db.execute('SELECT 1 FROM element_index LIMIT 1').fetchone():
                return
            for table in ('element_index', 'shell_submodels', 'shell_asset_ids'):
                self._db.execute(f'DELETE FROM {table}')
            for kind in ('shell', 'submodel'):
                for body in self.iter_all(kind):
                    self._index(kind, body)

    def _index(self, kind: str, body: dict):
        identifier = body['id']
        if kind == 'submodel':
            self._db.execute('DELETE FROM element_index WHERE owner_id=?', (identifier,))
            self._db.executemany('INSERT INTO element_index VALUES (?,?,?,?,?,?,?,?,?)', _index_rows(body))
        elif kind == 'shell':
            self._db.execute('DELETE FROM shell_submodels WHERE aas_id=?', (identifier,))
            self._db.execute('DELETE FROM shell_asset_ids WHERE aas_id=?', (identifier,))
            refs = {(r.get('keys') or [{}])[-1].get('value') for r in body.get('submodels') or []}
            for sm_id in sorted(r for r in refs if r):
                self._db.execute('INSERT INTO shell_submodels VALUES (?,?)', (identifier, sm_id))
            for asset_id in (body.get('assetInformation') or {}).get('specificAssetIds') or []:
                self._db.execute('INSERT INTO shell_asset_ids VALUES (?,?,?)',
                                 (identifier, asset_id.get('name'), asset_id.get('value')))

    def _unindex(self, kind: str, identifier: str):
        if kind == 'submodel':
            self._db.execute('DELETE FROM element_index WHERE owner_id=?', (identifier,))
        elif kind == 'shell':
            self._db.execute('DELETE FROM shell_submodels WHERE aas_id=?', (identifier,))
            self._db.execute('DELETE FROM shell_asset_ids WHERE aas_id=?', (identifier,))

    # -- transactions and events -------------------------------------------------------------
    def transaction(self):
        """Re-entrant transaction (see ``Database.transaction``); events are delivered after commit."""
        return self._db.transaction()

    def subscribe(self, callback):
        self._listeners.append(callback)

    def unsubscribe(self, callback):
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _event(self, kind, identifier, op, revision=None, path=None, actor=None):
        ts = now()
        seq = self._db.execute('INSERT INTO events (ts, kind, id, op, path, revision, actor) VALUES (?,?,?,?,?,?,?) '
                               'RETURNING seq', (ts, kind, identifier, op, path, revision, actor)).fetchone()['seq']
        event = {'seq': seq, 'ts': ts, 'kind': kind, 'id': identifier, 'op': op, 'path': path,
                 'revision': revision}
        self._own_events.add(seq)
        self._db.notify(str(seq))
        self._db.after_commit(lambda: self._dispatch(event))
        return event

    def _dispatch(self, event):
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - a broken subscriber must not break writes
                pass

    # -- events from other processes ------------------------------------------------------------
    def watch(self, interval: float = 0.5):
        """Deliver events written by other processes (sharing this database) to the local
        subscribers, so caches, SSE streams and bridges stay current in every worker. Sequence
        numbers can commit out of order, so a window of recent numbers is re-read each time."""
        if getattr(self, '_watcher', None):
            return
        start = self._db.execute('SELECT MAX(seq) AS seq FROM events').fetchone()['seq'] or 0
        self._watch_stop = threading.Event()

        listener = None
        if self._db.dialect == 'postgres':
            try:
                listener = self._db.listener()
            except Exception:  # noqa: BLE001 - fall back to polling
                listener = None

        def wait():
            """Until a notification arrives (PostgreSQL) or ``interval`` passes; False when stopping."""
            if listener is not None:
                try:
                    for _ in listener.notifies(timeout=interval, stop_after=1):
                        pass
                except Exception:  # noqa: BLE001
                    self._watch_stop.wait(interval)
                return not self._watch_stop.is_set()
            return not self._watch_stop.wait(interval)

        def loop():
            last, seen = start, set()
            while wait():
                try:
                    rows = self._db.execute('SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT 1000',
                                            (max(0, last - 256),)).fetchall()
                except Exception:  # noqa: BLE001 - keep watching through transient errors
                    continue
                for row in rows:
                    seq = row['seq']
                    if seq in seen or seq <= start:
                        continue
                    seen.add(seq)
                    last = max(last, seq)
                    if seq in self._own_events:
                        continue
                    self._dispatch({k: row[k] for k in ('seq', 'ts', 'kind', 'id', 'op', 'path', 'revision')})
                seen = {s for s in seen if s > last - 256}
                self._own_events = {s for s in self._own_events if s > last - 4096}
        self._watcher = threading.Thread(target=loop, name='aasforge-event-watch', daemon=True)
        self._watcher.start()

    def events_since(self, seq: int = 0, limit: int = 500):
        rows = self._db.execute('SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?', (seq, limit)).fetchall()
        return [dict(r) for r in rows]

    # -- identifiables ------------------------------------------------------------------------
    def put(self, kind: str, body: dict, sidecar: list | None = None, actor: str | None = None,
            must_exist: bool | None = None, path: str | None = None) -> tuple[bool, dict]:
        """Create or replace an identifiable; returns (created, metadata).

        ``sidecar`` holds fields of other metamodel versions (see ``versioning.reduce_32``). ``None``
        keeps the stored sidecar, so internal updates and 3.1 clients do not drop 3.2 data; pass
        ``[]`` to clear it."""
        if kind not in KINDS:
            raise ValueError(kind)
        identifier = body.get('id')
        if not isinstance(identifier, str) or not identifier:
            raise ValueError('identifiable has no id')
        with self.transaction():
            self._db.lock_key('identifiable', kind, identifier)
            row = self._db.execute('SELECT revision, created_at, digest, sidecar FROM identifiables WHERE kind=? AND id=?',
                                   (kind, identifier)).fetchone()
            if must_exist is True and row is None:
                raise NotFound(identifier)
            if must_exist is False and row is not None:
                raise Conflict(f'{kind} {identifier!r} already exists')
            ts = now()
            digest = canonical.digest(body)
            if sidecar is None:
                sidecar = json.loads(row['sidecar']) if row else []
            if row is not None and row['digest'] == digest and json.loads(row['sidecar']) == sidecar:
                return False, self.meta(kind, identifier)
            revision = (row['revision'] + 1) if row else 1
            created_at = row['created_at'] if row else ts
            text = json.dumps(body, ensure_ascii=False)
            self._db.execute(
                'INSERT INTO identifiables (kind, id, id_short, semantic_id, global_asset_id, body, sidecar, '
                'created_at, updated_at, revision, digest) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (kind, id) DO '
                'UPDATE SET id_short=excluded.id_short, semantic_id=excluded.semantic_id, '
                'global_asset_id=excluded.global_asset_id, body=excluded.body, sidecar=excluded.sidecar, '
                'updated_at=excluded.updated_at, revision=excluded.revision, digest=excluded.digest',
                (kind, identifier, body.get('idShort'), _semantic_id(body),
                 (body.get('assetInformation') or {}).get('globalAssetId'), text, json.dumps(sidecar),
                 created_at, ts, revision, digest))
            self._db.execute('UPDATE history SET valid_to=? WHERE kind=? AND id=? AND valid_to IS NULL',
                             (ts, kind, identifier))
            self._db.execute('INSERT INTO history (kind, id, revision, valid_from, body, actor, digest) VALUES (?,?,?,?,?,?,?)',
                             (kind, identifier, revision, ts, text, actor, digest))
            self._index(kind, body)
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
            self._db.lock_key('identifiable', kind, identifier)
            row = self._db.execute('SELECT revision FROM identifiables WHERE kind=? AND id=?', (kind, identifier)).fetchone()
            if row is None:
                raise NotFound(identifier)
            ts = now()
            self._db.execute('DELETE FROM identifiables WHERE kind=? AND id=?', (kind, identifier))
            self._db.execute('UPDATE history SET valid_to=? WHERE kind=? AND id=? AND valid_to IS NULL', (ts, kind, identifier))
            self._db.execute('INSERT INTO history (kind, id, revision, valid_from, valid_to, body, deleted, actor) '
                             'VALUES (?,?,?,?,?,NULL,1,?)', (kind, identifier, row['revision'] + 1, ts, ts, actor))
            self._db.execute('DELETE FROM attachments WHERE owner_kind=? AND owner_id=?', (kind, identifier))
            self._unindex(kind, identifier)
            self._event(kind, identifier, 'deleted', row['revision'] + 1, None, actor)

    def list(self, kind: str, *, id_short=None, semantic_id=None, ids=None, limit=None, cursor=None,
             created_from=None, updated_from=None, order='id', where=None):
        """``where``: an extra ``(sql, params)`` condition on ``identifiables`` (see ``sqlfilter``)."""
        clauses, params = ['kind=?'], [kind]
        if where:
            clauses.append(f'({where[0]})')
            params += list(where[1])
        if id_short:
            clauses.append('id_short=?')
            params.append(id_short)
        if semantic_id:
            clauses.append('semantic_id=?')
            params.append(semantic_id)
        if ids is not None:
            clauses.append(f'id IN ({",".join("?" * len(ids))})' if ids else '1=0')
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

    def iter_all(self, kind: str, where=None):
        sql, params = 'SELECT body FROM identifiables WHERE kind=?', [kind]
        if where:
            sql += f' AND ({where[0]})'
            params += list(where[1])
        rows = self._db.execute(sql + ' ORDER BY id', params).fetchall()
        for row in rows:
            yield json.loads(row['body'])

    def history(self, kind: str, identifier: str):
        rows = self._db.execute('SELECT revision, valid_from, valid_to, deleted, actor, digest FROM history '
                                'WHERE kind=? AND id=? ORDER BY revision', (kind, identifier)).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict:
        return {k: self._db.execute('SELECT COUNT(*) AS n FROM identifiables WHERE kind=?', (k,)).fetchone()['n'] for k in KINDS}

    # -- attachments -------------------------------------------------------------------------
    def put_attachment(self, owner_kind, owner_id, path, data: bytes, content_type=None, filename=None):
        with self.transaction():
            self._db.execute('INSERT INTO attachments VALUES (?,?,?,?,?,?) ON CONFLICT (owner_kind, owner_id, path) '
                             'DO UPDATE SET content_type=excluded.content_type, filename=excluded.filename, '
                             'data=excluded.data', (owner_kind, owner_id, path, content_type, filename, data))

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
            self._db.execute('INSERT INTO descriptors VALUES (?,?,?,?) ON CONFLICT (kind, id) DO UPDATE SET '
                             'body=excluded.body, updated_at=excluded.updated_at',
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
            self._db.execute('INSERT INTO asset_links VALUES (?,?) ON CONFLICT (aas_id) DO UPDATE SET '
                             'links=excluded.links', (aas_id, json.dumps(links)))

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
        return {r['aas_id']: json.loads(r['links']) for r in self._db.execute('SELECT * FROM asset_links').fetchall()}

    def put_package(self, package_id, filename, aas_ids, data, contents=None):
        """``contents``: ids of every object in the package by kind (``aas_ids`` are its shells)."""
        record = {'aasIds': list(aas_ids), 'contents': contents or {'shell': list(aas_ids)}}
        with self.transaction():
            self._db.execute('INSERT INTO packages VALUES (?,?,?,?,?) ON CONFLICT (package_id) DO UPDATE SET '
                             'filename=excluded.filename, aas_ids=excluded.aas_ids, data=excluded.data',
                             (package_id, filename, json.dumps(record), data, now()))

    def get_package(self, package_id):
        row = self._db.execute('SELECT * FROM packages WHERE package_id=?', (package_id,)).fetchone()
        if row is None:
            raise NotFound(package_id)
        result = dict(row)
        record = _package_record(result['aas_ids'])
        result['aas_ids'], result['contents'] = record['aasIds'], record['contents']
        return result

    def delete_package(self, package_id):
        with self.transaction():
            if not self._db.execute('DELETE FROM packages WHERE package_id=?', (package_id,)).rowcount:
                raise NotFound(package_id)

    def list_packages(self, aas_id=None):
        rows = self._db.execute('SELECT package_id, filename, aas_ids FROM packages ORDER BY package_id').fetchall()
        result = [{'packageId': r['package_id'], 'aasIds': _package_record(r['aas_ids'])['aasIds'],
                   'filename': r['filename']} for r in rows]
        return [p for p in result if aas_id is None or aas_id in p['aasIds']]

    # -- Catena-X DTR granular access rules -----------------------------------------------------
    def list_dtr_rules(self):
        return [{**json.loads(r['body']), 'id': r['id']} for r in
                self._db.execute('SELECT id, body FROM dtr_access_rules ORDER BY id').fetchall()]

    def get_dtr_rule(self, rule_id: int):
        row = self._db.execute('SELECT id, body FROM dtr_access_rules WHERE id=?', (rule_id,)).fetchone()
        if row is None:
            raise NotFound(str(rule_id))
        return {**json.loads(row['body']), 'id': row['id']}

    def put_dtr_rule(self, body: dict, rule_id: int | None = None) -> int:
        data = json.dumps({k: v for k, v in body.items() if k not in ('id', 'tid')})
        with self.transaction():
            if rule_id is None:
                rule_id = self._db.execute('INSERT INTO dtr_access_rules (body, updated_at) VALUES (?,?) RETURNING id',
                                           (data, now())).fetchone()['id']
            elif not self._db.execute('UPDATE dtr_access_rules SET body=?, updated_at=? WHERE id=?',
                                      (data, now(), rule_id)).rowcount:
                raise NotFound(str(rule_id))
            self._event('dtr-access-rule', str(rule_id), 'updated')
        return rule_id

    def delete_dtr_rule(self, rule_id: int):
        with self.transaction():
            if not self._db.execute('DELETE FROM dtr_access_rules WHERE id=?', (rule_id,)).rowcount:
                raise NotFound(str(rule_id))
            self._event('dtr-access-rule', str(rule_id), 'deleted')

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
