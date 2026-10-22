"""Small DB-API boundary: portable SQL, explicit write locks, bounded PG pools.

SQL dialect is never inferred or rewritten. The only parameter adaptation is qmark
binding (SQL literals are preserved). Schema changes live in migrations/, not here.
Each context owns one connection and commits or rolls back before returning it.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psycopg
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout

DATABASE_ERRORS = (sqlite3.Error, psycopg.Error, PoolTimeout, PoolClosed)
INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)
_pools: dict[str, ConnectionPool[Any]] = {}
_pool_lock = threading.Lock()
# Compatibility write transactions serialize the former SQLite BEGIN IMMEDIATE
# critical sections, including global Studio budgets. Queue claims use row locks.
WRITE_LOCK = 2026102201


def database_url() -> str | None:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        if host := os.getenv("DATABASE_HOST", "").strip():
            from psycopg.conninfo import make_conninfo
            return make_conninfo(host=host, port=os.getenv("DATABASE_PORT", "5432"),
                                 dbname=os.getenv("DATABASE_NAME", "adaptive_learning"),
                                 user=os.getenv("DATABASE_USER", "adaptive_learning"),
                                 password=os.getenv("DATABASE_PASSWORD", ""))
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("DATABASE_URL must be a PostgreSQL URL with host and database")
    return value


def _pool(url: str) -> ConnectionPool[Any]:
    with _pool_lock:
        if url not in _pools:
            size = int(os.getenv("DATABASE_POOL_SIZE", "8"))
            if not 1 <= size <= 64:
                raise ValueError("DATABASE_POOL_SIZE must be between 1 and 64")
            pool = ConnectionPool(url, min_size=0, max_size=size, timeout=15,
                                  kwargs={"connect_timeout": 10}, open=True)
            _pools[url] = pool
        return _pools[url]


def close_pools() -> None:
    with _pool_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


class Row(Mapping[str, Any]):
    def __init__(self, names: Sequence[str], values: Sequence[Any]) -> None:
        self.names = tuple(names)
        self._values = tuple(values)
        self.data = dict(zip(self.names, self._values, strict=True))

    def __getitem__(self, key: str | int | slice) -> Any:
        return self._values[key] if isinstance(key, (int, slice)) else self.data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.names)

    def __len__(self) -> int:
        return len(self.names)


def _parameters(sql: str) -> str:
    # Repository SQL has single/double quoted literals, no dollar-quoted strings.
    result: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote:
            result.append("%%" if char == "%" else char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    result.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
            result.append(char)
        elif char == "?":
            result.append("%s")
        else:
            result.append("%%" if char == "%" else char)
        index += 1
    return "".join(result)


class Cursor:
    def __init__(self, cursor: Any, postgres: bool, owner: Connection) -> None:
        self.raw = cursor
        self.postgres = postgres
        self.owner = owner

    @property
    def rowcount(self) -> int:
        return int(self.raw.rowcount)

    def _row(self, row: Any) -> Any:
        if row is None or not self.postgres:
            return row
        return Row([column.name for column in self.raw.description], row)

    def fetchone(self) -> Any:
        self.owner.check_open()
        return self._row(self.raw.fetchone())

    def fetchall(self) -> list[Any]:
        self.owner.check_open()
        return [self._row(row) for row in self.raw.fetchall()]

    def __iter__(self) -> Iterator[Any]:
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class Connection:
    def __init__(self, raw: Any, postgres: bool) -> None:
        self.raw = raw
        self.postgres = postgres
        self.closed = False

    def check_open(self) -> None:
        if self.closed:
            raise sqlite3.ProgrammingError("Connection context is closed")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        self.check_open()
        return Cursor(self.raw.execute(_parameters(sql) if self.postgres and params else sql,
                                       tuple(params) if params else None) if self.postgres
                      else self.raw.execute(sql, tuple(params)), self.postgres, self)

    def executescript(self, sql: str) -> None:
        self.check_open()
        if self.postgres:
            # Only SQLite development initializers call this. PG requires the CLI
            # migration to have run and never performs DDL during app startup.
            return
        self.raw.executescript(sql)

    def begin_write(self, *, serialize: bool = True) -> None:
        self.check_open()
        if self.postgres:
            if serialize:
                self.raw.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK,))
        else:
            self.raw.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self.check_open()
        self.raw.commit()

    def rollback(self) -> None:
        self.check_open()
        self.raw.rollback()


@contextmanager
def connect(path: Path) -> Iterator[Connection]:
    url = database_url()
    if url:
        with _pool(url).connection() as raw:
            wrapper = Connection(raw, True)
            try:
                yield wrapper
            finally:
                wrapper.closed = True
    else:
        raw = sqlite3.connect(path, timeout=30)
        raw.row_factory = sqlite3.Row
        raw.create_function("strpos", 2, lambda value, part: value.find(part) + 1 if value is not None and part is not None else None, deterministic=True)
        raw.execute("PRAGMA foreign_keys=ON")
        raw.execute("PRAGMA busy_timeout=30000")
        wrapper = Connection(raw, False)
        try:
            with raw:
                yield wrapper
        finally:
            wrapper.closed = True
            raw.close()


def healthcheck() -> None:
    if url := database_url():
        from .migrate import SCHEMA_VERSION
        with _pool(url).connection() as raw:
            row = raw.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
            if row is None or row[0] != SCHEMA_VERSION:
                raise RuntimeError("Database schema is not current; run python -m adaptive_learning.migrate schema")
