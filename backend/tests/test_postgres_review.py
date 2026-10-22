"""Independent migration review: failure after a partially imported source graph."""

import sqlite3

import psycopg
import pytest
from test_postgres import pg as pg

from adaptive_learning.community import CommunityRepository
from adaptive_learning.database import DATABASE_ERRORS, connect
from adaptive_learning.migrate import import_sqlite
from adaptive_learning.native import NativeRepository


def test_late_foreign_key_failure_rolls_back_previously_imported_tables(pg, tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    source = tmp_path / "source"
    community = CommunityRepository(source / "state/community.sqlite3")
    with community.connect() as db:
        db.execute("INSERT INTO visitors VALUES('review-owner','review-token',1)")
    native = NativeRepository(source / "state/native.sqlite3")
    # Simulate an inconsistent legacy database without modifying application files.
    with sqlite3.connect(native.path) as db:
        db.execute(
            "INSERT INTO native_resources(id,workspace_id,book_id,created) VALUES(?,?,?,?)",
            ("orphan", "missing-workspace", "book", 1),
        )
    monkeypatch.setenv("DATABASE_URL", pg)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        import_sqlite(
            data_dir=source, backup_dir=tmp_path / "backup", url=pg, writes_stopped=True
        )
    with psycopg.connect(pg) as db:
        assert db.execute("SELECT COUNT(*) FROM visitors").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM native_resources").fetchone()[0] == 0
    with sqlite3.connect(community.path) as db:
        assert db.execute("SELECT id FROM visitors").fetchone()[0] == "review-owner"
    assert not (tmp_path / "backup/report.json").exists()


def test_migration_requires_explicit_stopped_writers_before_creating_backup(pg, tmp_path):
    with pytest.raises(ValueError, match="Stop API writes"):
        import_sqlite(
            data_dir=tmp_path / "source", backup_dir=tmp_path / "backup",
            url=pg, writes_stopped=False,
        )
    assert not (tmp_path / "backup").exists()


def test_corrupt_json_rejects_migration_without_partial_import(pg, tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    source = tmp_path / "source"
    community = CommunityRepository(source / "state/community.sqlite3")
    with community.connect() as db:
        db.execute("INSERT INTO visitors VALUES('review-owner','review-token',1)")
    native = NativeRepository(source / "state/native.sqlite3")
    native.save_workspace("review-owner", "workspace", "课程", {})
    with native.connect() as db:
        db.execute("UPDATE native_workspaces SET preferences='broken-json'")
    monkeypatch.setenv("DATABASE_URL", pg)
    with pytest.raises(ValueError, match="Invalid JSON"):
        import_sqlite(
            data_dir=source, backup_dir=tmp_path / "backup", url=pg, writes_stopped=True
        )
    with psycopg.connect(pg) as db:
        assert db.execute("SELECT COUNT(*) FROM visitors").fetchone()[0] == 0


def test_cursor_cannot_be_read_after_connection_returns_to_pool(pg, tmp_path):
    with connect(tmp_path / "unused") as db:
        cursor = db.execute("SELECT 1")
    with pytest.raises(DATABASE_ERRORS, match="closed"):
        cursor.fetchone()
