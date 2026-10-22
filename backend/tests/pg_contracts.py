"""Opt-in plugin reusing the existing API contracts on a disposable PG schema.

TEST_DATABASE_URL=... PYTHONPATH=tests pytest -p pg_contracts tests/test_native_api.py ...
Only this plugin sets DATABASE_URL, after creating a new random schema. It never
truncates public data or uses a database selected by production DATABASE_URL.
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import quote

import psycopg
import pytest
from psycopg import sql

from adaptive_learning.database import close_pools
from adaptive_learning.migrate import upgrade

_base: str | None = None
_schema: str | None = None
_previous: str | None = None
_url: str | None = None


def pytest_sessionstart(session):
    global _base, _schema, _previous, _url
    _base = os.getenv('TEST_DATABASE_URL')
    if not _base:
        raise pytest.UsageError('pg_contracts requires TEST_DATABASE_URL for an isolated test schema')
    _schema = 'contracts_' + uuid.uuid4().hex
    with psycopg.connect(_base, autocommit=True) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(_schema)))
    _url = _base + ('&' if '?' in _base else '?') + 'options=' + quote('-csearch_path=' + _schema)
    _previous = os.environ.get('DATABASE_URL')
    os.environ['DATABASE_URL'] = _url
    with psycopg.connect(_url) as db:
        upgrade(db)


@pytest.fixture(autouse=True)
def isolated_contract_database():
    with psycopg.connect(_url) as db:
        tables = [r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s AND tablename<>'schema_migrations'", (_schema,))]
        if tables:
            db.execute(sql.SQL('TRUNCATE {} RESTART IDENTITY CASCADE').format(sql.SQL(',').join(map(sql.Identifier, tables))))
    yield


def pytest_sessionfinish(session, exitstatus):
    close_pools()
    if _base and _schema:
        with psycopg.connect(_base, autocommit=True) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(_schema)))
    if _previous is None:
        os.environ.pop('DATABASE_URL', None)
    else:
        os.environ['DATABASE_URL'] = _previous
