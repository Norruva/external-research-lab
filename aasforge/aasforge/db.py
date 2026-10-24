"""Database adapter: one connection, SQLite or PostgreSQL, behind the small surface the store uses.

The store writes portable SQL (``?`` placeholders, ``INSERT ... ON CONFLICT DO UPDATE``,
``RETURNING``). This module translates it per dialect:

* placeholders: ``?`` becomes ``%s`` for psycopg;
* schema: ``AUTOINCREMENT`` becomes identity columns, ``BLOB`` becomes ``BYTEA``, and ``REAL``
  becomes ``DOUBLE PRECISION``. Text columns use ``COLLATE "C"`` so ordering and range comparisons
  are by code point, as in SQLite and the Python evaluator;
* functions: :meth:`Database.contains` gives the dialect's substring test.

All access goes through one re-entrant lock, held for whole transactions, so a single connection
can be shared by the threaded HTTP server.

A PostgreSQL DSN may carry ``aasforge_schema=<name>``: the schema is created if needed and used as
the search path. This lets several stores (for example, parallel test cases) share one database.
"""
from __future__ import annotations

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
    def __init__(self, target: str = ':memory:'):
        self.lock = threading.RLock()
        self.depth = 0
        if is_postgres(target):
            self.dialect = 'postgres'
            self._open_postgres(target)
        else:
            self.dialect = 'sqlite'
            self._conn = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute('PRAGMA journal_mode=WAL' if target != ':memory:' else 'PRAGMA journal_mode=MEMORY')
            self._conn.execute('PRAGMA foreign_keys=ON')

    def _open_postgres(self, dsn: str):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:  # pragma: no cover - optional dependency
            raise RuntimeError('PostgreSQL storage needs psycopg: pip install "psycopg[binary]"') from error
        parts = urlsplit(dsn)
        query = dict(parse_qsl(parts.query))
        schema = query.pop('aasforge_schema', None)
        if schema is not None and not _SCHEMA_NAME.match(schema):
            raise ValueError(f'invalid schema name {schema!r}')
        dsn = urlunsplit(parts._replace(query=urlencode(query)))
        self._conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        if schema:
            self._conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            self._conn.execute(f'SET search_path TO "{schema}"')
        self.schema = schema

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

    # -- execution ------------------------------------------------------------------------------
    def execute(self, sql: str, params=()) -> 'Result':
        """Run one statement and fetch its rows while holding the lock: the connection is shared,
        so a cursor must not be read after another thread has used the connection."""
        with self.lock:
            cursor = self._conn.execute(self._sql(sql), tuple(params))
            rows = cursor.fetchall() if cursor.description else []
            return Result(rows, cursor.rowcount)

    def executemany(self, sql: str, rows):
        rows = [tuple(r) for r in rows]
        if not rows:
            return
        with self.lock:
            if self.dialect == 'sqlite':
                self._conn.executemany(sql, rows)
            else:
                with self._conn.cursor() as cursor:
                    cursor.executemany(self._sql(sql), rows)

    def script(self, script: str):
        with self.lock:
            for statement in self.ddl(script).split(';'):
                if statement.strip():
                    self._conn.execute(statement)

    def begin(self):
        self._conn.execute('BEGIN IMMEDIATE' if self.dialect == 'sqlite' else 'BEGIN')

    def commit(self):
        self._conn.execute('COMMIT')

    def rollback(self):
        self._conn.execute('ROLLBACK')

    def close(self):
        with self.lock:
            self._conn.close()

    def drop_schema(self):
        """Remove a PostgreSQL ``aasforge_schema`` (tests)."""
        if self.dialect == 'postgres' and self.schema:
            with self.lock:
                self._conn.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
