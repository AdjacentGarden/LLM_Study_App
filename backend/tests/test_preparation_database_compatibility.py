from __future__ import annotations

import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_learning.database import Connection
from adaptive_learning.ingestion import preparation


def test_windows_worker_lock_uses_one_byte_and_releases(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(
        LK_NBLCK=1, LK_UNLCK=2,
        locking=lambda fd, mode, count: calls.append((mode, count)),
    ))
    lock = tmp_path / 'worker.lock'
    with preparation._worker_lock(lock) as acquired:
        assert acquired
        assert lock.read_bytes() == b'\0'
        assert calls == [(1, 1)]
    assert calls == [(1, 1), (2, 1)]


def test_windows_worker_lock_contention_does_not_claim_owner(tmp_path, monkeypatch):
    calls = []

    def busy(fd, mode, count):
        calls.append((mode, count))
        raise OSError('already owned')

    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=busy))
    with preparation._worker_lock(tmp_path / 'worker.lock') as acquired:
        assert not acquired
    assert calls == [(1, 1)]


@pytest.fixture
def postgres_lock(monkeypatch):
    mutex = threading.Lock()
    state = {'owner': None, 'queries': [], 'tries': 0}

    class FakePostgres(Connection):
        def __init__(self):
            self.postgres = True
            self.healthy = True
            self.raw = SimpleNamespace(close=self.close)

        def close(self):
            self.healthy = False
            with mutex:
                if state['owner'] is self:
                    state['owner'] = None

        def execute(self, sql, params=()):
            if not self.healthy:
                raise sqlite3.OperationalError('ownership session lost')
            with mutex:
                state['queries'].append(sql)
                value = None
                if 'pg_try_advisory_lock' in sql:
                    state['tries'] += 1
                    if state['owner'] is None:
                        state['owner'] = self
                    value = (state['owner'] is self,)
                elif 'pg_advisory_unlock' in sql:
                    assert state['owner'] is self
                    state['owner'] = None
                    value = (True,)
                elif sql == 'SELECT 1':
                    value = (1,)
            return SimpleNamespace(fetchone=lambda: value)

        def begin_write(self, **kwargs):
            pass

        def commit(self):
            if not self.healthy:
                raise sqlite3.OperationalError('ownership session lost')

        def rollback(self):
            if not self.healthy:
                raise sqlite3.OperationalError('ownership session lost')

    @contextmanager
    def connect(path):
        yield FakePostgres()

    monkeypatch.setattr(preparation, 'connect', connect)
    monkeypatch.setattr(preparation, 'database_url', lambda: 'postgresql://test.invalid/db')
    return state, FakePostgres


def test_postgres_initializer_does_not_issue_ddl_or_sqlite_pragmas(tmp_path, postgres_lock):
    state, _ = postgres_lock
    preparation.PreparationQueue(tmp_path / 'unused.sqlite3', [('indexing', lambda book: None)])
    assert state['queries'] == []
    assert not (tmp_path / 'unused.sqlite3').exists()


def test_postgres_waiting_worker_cannot_recover_live_owner(tmp_path, postgres_lock):
    state, _ = postgres_lock
    path = tmp_path / 'unused.sqlite3'
    first = preparation.PreparationQueue(path, [('indexing', lambda book: None)], poll_interval=.005)
    second = preparation.PreparationQueue(path, [('indexing', lambda book: None)], poll_interval=.005)
    with first._consumer_lock() as owner:
        assert owner
        second.start()
        try:
            deadline = time.monotonic() + 1
            while state['tries'] < 4 and time.monotonic() < deadline:
                time.sleep(.005)
            assert state['tries'] >= 4
            assert not any(sql.startswith('UPDATE preparation') for sql in state['queries'])
        finally:
            second.stop()
    assert state['owner'] is None


def test_postgres_lost_owner_cannot_complete_through_fresh_connection(tmp_path, postgres_lock):
    state, fake = postgres_lock
    queue = preparation.PreparationQueue(tmp_path / 'unused.sqlite3', [('indexing', lambda book: None)])
    owner = fake()
    owner.healthy = False
    queue._owner.connection = owner
    with pytest.raises(sqlite3.OperationalError, match='ownership session lost'):
        queue._update('book', 'succeeded', 'ready', 1)
    assert not state['queries']


def test_explicit_refresh_restarts_all_successful_checkpoints(tmp_path: Path):
    calls = []
    queue = preparation.PreparationQueue(tmp_path / 'queue.sqlite3', [
        ('indexing', lambda book: calls.append('indexing')),
        ('diagnostics', lambda book: calls.append('diagnostics')),
    ])
    queue.enqueue('book')
    assert queue.process_one()
    assert queue.get('book')['next_step'] == 2
    assert queue.enqueue('book', refresh=True)['next_step'] == 0
    assert queue.process_one()
    assert calls == ['indexing', 'diagnostics', 'indexing', 'diagnostics']
