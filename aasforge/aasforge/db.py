"""Database adapter: SQLite or PostgreSQL behind the small surface the store uses.

The store writes portable SQL (``?`` placeholders, ``INSERT ... ON CONFLICT DO UPDATE``,
``RETURNING``). This module translates it per dialect:

* placeholders: ``?`` becomes ``%s`` for psycopg;
* schema: ``AUTOINCREMENT`` becomes identity columns, ``BLOB`` becomes ``BYTEA``, and ``REAL``
  becomes ``DOUBLE PRECISION``. Text columns use ``COLLATE "C"`` so ordering and range comparisons
  are by code point, as in SQLite and the Python evaluator;
* functions: :meth:`Database.contains` gives the dialect's substring test.

Concurrency:

* **SQLite**: one connection behind one re-entrant lock, held for whole transactions. Rows are
  fetched while it is held.
* **PostgreSQL**: a bounded pool of connections. A transaction keeps one connection for its whole
  duration (per thread), and statements outside a transaction borrow one briefly. Concurrent
  writers of the same object are serialised by :meth:`Database.lock_key` (a transaction-scoped
  advisory lock), so several threads and several server processes can share one database.

A PostgreSQL DSN may carry ``aasforge_schema=<name>``: the schema is created if needed and used as
the search path. This lets several stores (for example, parallel test cases) share one database.
"""
from __future__ import annotations

import contextlib
import hashlib
import queue
import re
import sqlite3
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SCHEMA_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')


def is_postgres(target: str) -> bool:
    return target.startswith(('postgresql://', 'postgres://'))


class Result:
    """Rows of one statement, fetched eagerly (see :meth:`Database.execute`)."""

    __slots__ = ('rows', 'rowcount')

    def __init__(self, rows, rowcount):
        self.rows, self.rowcount = rows, rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Database:
    def __init__(self, target: str = ':memory:', pool_size: int = 8):
        self._local = threading.local()
        self.schema = None
        if is_postgres(target):
            self.dialect = 'postgres'
            self._setup_postgres(target, pool_size)
        else:
            self.dialect = 'sqlite'
            self.lock = threading.RLock()
            self._conn = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute('PRAGMA journal_mode=WAL' if target != ':memory:' else 'PRAGMA journal_mode=MEMORY')
            self._conn.execute('PRAGMA foreign_keys=ON')
            self._conn.execute('PRAGMA busy_timeout=10000')

    # -- PostgreSQL pool ------------------------------------------------------------------------
    def _setup_postgres(self, dsn: str, pool_size: int):
        try:
            import psycopg
        except ImportError as error:  # pragma: no cover - optional dependency
            raise RuntimeError('PostgreSQL storage needs psycopg: pip install "psycopg[binary]"') from error
        parts = urlsplit(dsn)
        query = dict(parse_qsl(parts.query))
        schema = query.pop('aasforge_schema', None)
        if schema is not None and not _SCHEMA_NAME.match(schema):
            raise ValueError(f'invalid schema name {schema!r}')
        self._dsn = urlunsplit(parts._replace(query=urlencode(query)))
        self.schema = schema
        self._pool: queue.LifoQueue = queue.LifoQueue()
        self._pool_size, self._created = pool_size, 0
        self._pool_lock = threading.Lock()
        self._all: list = []
        if schema:
            with self._borrow() as conn:
                try:
                    conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                except psycopg.errors.UniqueViolation:
                    pass                # created concurrently by another process
        self.lock = contextlib.nullcontext()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(self._dsn, autocommit=True, row_factory=dict_row)
        if self.schema:
            conn.execute(f'SET search_path TO "{self.schema}"')
        return conn

    @contextlib.contextmanager
    def _borrow(self):
        try:
            conn = self._pool.get_nowait()
        except queue.Empty:
            with self._pool_lock:
                create = self._created < self._pool_size
                if create:
                    self._created += 1
            if create:
                conn = self._connect()
                self._all.append(conn)
            else:
                conn = self._pool.get(timeout=60)
        try:
            yield conn
        finally:
            if conn.closed:
                with self._pool_lock:
                    self._created -= 1
            else:
                self._pool.put(conn)

    # -- SQL translation ------------------------------------------------------------------------
    def _sql(self, sql: str) -> str:
        if self.dialect == 'sqlite':
            return sql
        return sql.replace('%', '%%').replace('?', '%s')

    def ddl(self, script: str) -> str:
        if self.dialect == 'sqlite':
            return script
        script = script.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY')
        script = script.replace('BLOB', 'BYTEA').replace(' REAL', ' DOUBLE PRECISION')
        return re.sub(r'\bTEXT\b', 'TEXT COLLATE "C"', script)

    def contains(self, haystack: str) -> str:
        """SQL for 'haystack contains the next parameter'."""
        return f'instr({haystack}, ?) > 0' if self.dialect == 'sqlite' else f'strpos({haystack}, ?) > 0'

    # -- connections and transactions -------------------------------------------------------------
    @contextlib.contextmanager
    def connection(self):
        """The connection to use now: the transaction's own, or a briefly borrowed one."""
        if self.dialect == 'sqlite':
            with self.lock:
                yield self._conn
            return
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            yield conn
            return
        with self._borrow() as conn:
            yield conn

    @contextlib.contextmanager
    def transaction(self):
        """Re-entrant transaction: only the outermost block begins and commits (or rolls back)."""
        depth = getattr(self._local, 'depth', 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return
        self._local.after = []
        if self.dialect == 'sqlite':
            committed = False
            with self.lock:
                self._local.depth = 1
                self._conn.execute('BEGIN IMMEDIATE')
                try:
                    yield
                except BaseException:
                    self._conn.execute('ROLLBACK')
                    self._local.after = []
                    raise
                else:
                    self._conn.execute('COMMIT')
                    committed = True
                finally:
                    self._local.depth = 0
            if committed:
                self._run_after()
            return
        committed = False
        with self._borrow() as conn:
            self._local.conn, self._local.depth = conn, 1
            conn.execute('BEGIN')
            try:
                yield
            except BaseException:
                conn.execute('ROLLBACK')
                self._local.after = []
                raise
            else:
                conn.execute('COMMIT')
                committed = True
            finally:
                self._local.conn, self._local.depth = None, 0
        if committed:
            self._run_after()

    def after_commit(self, callback):
        """Run ``callback`` once the current transaction has committed (at once if there is none)."""
        if not self.in_transaction():
            callback()
            return
        self._local.after.append(callback)

    def _run_after(self):
        callbacks, self._local.after = getattr(self._local, 'after', []), []
        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - a failing subscriber must not affect the writer
                pass

    def in_transaction(self) -> bool:
        return bool(getattr(self._local, 'depth', 0))

    def lock_key(self, *parts: str):
        """Serialise concurrent writers of one object until the end of the current transaction
        (PostgreSQL advisory lock; SQLite transactions are already serialised)."""
        if self.dialect != 'postgres':
            return
        key = int.from_bytes(hashlib.sha256('\x1f'.join(parts).encode()).digest()[:8], 'big', signed=True)
        self.execute('SELECT pg_advisory_xact_lock(?)', (key,))

    # -- execution ------------------------------------------------------------------------------
    def execute(self, sql: str, params=()) -> Result:
        """Run one statement and fetch its rows before the connection can be used by anyone else."""
        with self.connection() as conn:
            cursor = conn.execute(self._sql(sql), tuple(params))
            rows = cursor.fetchall() if cursor.description else []
            return Result(rows, cursor.rowcount)

    def executemany(self, sql: str, rows):
        rows = [tuple(r) for r in rows]
        if not rows:
            return
        with self.connection() as conn:
            if self.dialect == 'sqlite':
                conn.executemany(sql, rows)
            else:
                with conn.cursor() as cursor:
                    cursor.executemany(self._sql(sql), rows)

    def script(self, script: str):
        """Run DDL; on PostgreSQL under an advisory lock, so several processes may start at once."""
        with self.transaction():
            if self.dialect == 'postgres':
                self.execute('SELECT pg_advisory_xact_lock(7230420001)')
            for statement in self.ddl(script).split(';'):
                if statement.strip():
                    self.execute(statement)

    @property
    def channel(self) -> str:
        """NOTIFY channel for this database (and schema)."""
        return 'aasforge_' + hashlib.sha256((self.schema or 'public').encode()).hexdigest()[:16]

    def notify(self, payload: str):
        """Queue a notification; PostgreSQL delivers it when the transaction commits."""
        if self.dialect == 'postgres':
            self.execute('SELECT pg_notify(?, ?)', (self.channel, payload))

    def listener(self):
        """A dedicated connection listening on :attr:`channel` (PostgreSQL only)."""
        conn = self._connect()
        conn.execute(f'LISTEN {self.channel}')
        return conn

    def close(self):
        if self.dialect == 'sqlite':
            with self.lock:
                self._conn.close()
            return
        for conn in self._all:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - closing best effort
                pass

    def drop_schema(self):
        """Remove a PostgreSQL ``aasforge_schema`` (tests)."""
        if self.dialect == 'postgres' and self.schema:
            self.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
