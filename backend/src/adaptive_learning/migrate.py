"""Explicit schema upgrades and offline, empty-target SQLite cutover rehearsal.

Run with --help. Never run import against live writers. The backup API captures a
consistent image of each SQLite file; --writes-stopped acknowledges cross-file
consistency has been established by stopping API and workers first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from .database import WRITE_LOCK, database_url

MIGRATIONS = Path(__file__).with_name("migrations")
SCHEMA_VERSION = 11
TABLES = {
    "community.sqlite3": (
        "visitors", "user_profiles", "assets", "library_books", "removed_books", "resources",
        "posts", "acquisitions", "session_owners", "upload_owners", "accounts", "account_sessions",
        "email_codes", "email_rate", "social_users", "friendships", "social_blocks", "chat_messages",
        "chat_reads", "social_rate", "studio_notes", "studio_jobs", "studio_note_editions", "reading_doubts",
        "studio_note_request_aliases",
        "studio_note_logical_inputs",
    ),
    "native.sqlite3": (
        "native_workspaces", "native_resources", "native_session_context", "native_progress",
        "native_mutations", "native_selection", "native_learner_preferences", "native_catalog_practice",
        "native_design_overrides", "native_daily_plans", "native_task_completions", "native_design_adjustments",
    ),
    "assessments.sqlite3": ("diagnostic_banks", "interview_sessions", "course_bundles", "learning_events",
                            "diagnostic_chapter_cache", "diagnostic_chapter_submissions", "diagnostic_recovery_epochs",
                            "diagnostic_recovery_grants", "diagnostic_item_repairs"),
    "ocr_jobs.sqlite3": ("books", "ocr_jobs", "book_structures"),
    "course_jobs.sqlite3": ("course_jobs", "course_job_requests", "teaching_course_repairs",
                            "teaching_course_units", "teaching_course_proofs", "teaching_course_submissions",
                            "teaching_course_epochs"),
    "preparation.sqlite3": ("preparation",),
}

JSON_COLUMNS = {
    "assets": {"catalog"}, "resources": {"content"}, "posts": {"content"},
    "accounts": {"interests"}, "studio_notes": {"data"}, "studio_jobs": {"data", "result"},
    "reading_doubts": {"pages"}, "native_workspaces": {"preferences"},
    "native_resources": {"structure_json"}, "native_session_context": {"structure_json", "items_json"},
    "diagnostic_banks": {"items_json"}, "interview_sessions": {"session_json"},
    "course_bundles": {"bundle_json"}, "learning_events": {"payload_json"},
    "ocr_jobs": {"result_json"}, "book_structures": {"structure_json"},
    "course_jobs": {"result"}, "studio_note_request_aliases": {"body_json"},
    "teaching_course_repairs": {"identity_json", "state_json"},
    "teaching_course_units": {"candidate_json", "feedback_json", "work_json"},
    "teaching_course_proofs": {"proof_json"}, "teaching_course_submissions": {"response_json"},
    "diagnostic_chapter_cache": {"identity_json", "items_json"},
    "diagnostic_item_repairs": {"state_json"}, "preparation": {"details"},
}


def upgrade(connection: Any) -> None:
    connection.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK,))
    connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    versions = [r[0] for r in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
    if any(v > SCHEMA_VERSION for v in versions):
        raise RuntimeError("Target schema is newer than this tool")
    for version, filename in ((1, "001_initial.sql"), (2, "002_content_digest.sql"), (3, "003_studio_note_editions.sql"), (4, "004_chat_cursor_bigint.sql"), (5, "005_worker_presence.sql"), (6, "006_course_preparation_note_aliases.sql"), (7, "007_diagnostic_chapter_cache.sql"), (8, "008_diagnostic_item_repairs.sql"), (9, "009_teaching_course_repairs.sql"), (10, "010_isolated_teaching_units.sql"), (11, "011_studio_logical_notes.sql")):
        if version not in versions:
            connection.execute((MIGRATIONS / filename).read_text())
            connection.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))


def _digest(rows: list[tuple[Any, ...]]) -> str:
    def normalize(value: Any) -> Any:
        return {"bytes": bytes(value).hex()} if isinstance(value, (bytes, memoryview)) else value
    encoded = sorted(json.dumps([normalize(v) for v in row], ensure_ascii=False,
                                separators=(",", ":"), allow_nan=False) for row in rows)
    return hashlib.sha256("\n".join(encoded).encode()).hexdigest()


def import_sqlite(*, data_dir: Path, backup_dir: Path, url: str,
                  writes_stopped: bool) -> dict[str, Any]:
    if not writes_stopped:
        raise ValueError("Stop API writes and all workers, then pass --writes-stopped")
    backup_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    sources: dict[str, Path] = {}
    for filename in TABLES:
        source = data_dir / "state" / filename
        if not source.exists():
            continue
        backup_path = backup_dir / filename
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin:
            with sqlite3.connect(backup_path) as destination:
                origin.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError(f"Backup integrity failure: {filename}")
        backup_path.chmod(0o600)
        sources[filename] = backup_path
    if not sources:
        raise ValueError("No source databases found")
    report: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "tables": {}, "backups": list(sources),
                              "verified": False}
    with psycopg.connect(url, connect_timeout=10) as target:
        # DDL, import, checks and identity restarts commit together or roll back.
        upgrade(target)
        if target.execute("SELECT 1 FROM worker_presence WHERE expires>%s LIMIT 1", (time.time(),)).fetchone():
            raise ValueError("Live workers remain; stop every worker before importing")
        for tables in TABLES.values():
            for table in tables:
                if target.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table))).fetchone():
                    raise ValueError("Target is not empty; refusing to overwrite business data")
        for filename, tables in TABLES.items():
            if filename not in sources:
                continue
            with sqlite3.connect(sources[filename]) as origin:
                existing = {r[0] for r in origin.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                unexpected = existing - set(tables) - {"sqlite_sequence", "worker_presence"}
                if unexpected:
                    raise ValueError(f"Unmapped source tables in {filename}: {sorted(unexpected)}")
                for table in tables:
                    if table not in existing:
                        continue
                    columns = [r[1] for r in origin.execute(f'PRAGMA table_info("{table}")')]
                    if table == "native_catalog_practice":
                        columns.append("rowid")
                    names = ",".join(f'"{c}"' for c in columns)
                    rows = origin.execute(f'SELECT {names} FROM "{table}"').fetchall()
                    for column in set(columns) & (JSON_COLUMNS.get(table, set()) | {"payload"}):
                        position = columns.index(column)
                        for row in rows:
                            if row[position] is not None:
                                try:
                                    json.loads(row[position])
                                except (ValueError, TypeError) as error:
                                    raise ValueError(f"Invalid JSON in {table}.{column}") from error
                    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)),
                        sql.SQL(",").join(sql.Placeholder() for _ in columns))
                    with target.cursor() as cursor:
                        cursor.executemany(statement, rows)
                    imported = target.execute(sql.SQL("SELECT {} FROM {}").format(
                        sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(table))).fetchall()
                    if len(rows) != len(imported) or _digest(rows) != _digest(imported):
                        raise RuntimeError(f"Content verification failed for {table}")
                    report["tables"][table] = {"rows": len(rows), "sha256": _digest(rows)}
        target.execute("SET CONSTRAINTS ALL IMMEDIATE")
        # ALTER SEQUENCE RESTART is transactional, unlike setval(). IDs and the
        # native event insertion order survive import, including sparse IDs.
        for table, column in (("ocr_jobs", "job_id"), ("chat_messages", "id"), ("native_catalog_practice", "rowid")):
            sequence_row = target.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=pg_get_serial_sequence(%s,%s)::regclass", (table, column)).fetchone()
            maximum_row = target.execute(sql.SQL("SELECT COALESCE(MAX({}),0) FROM {}").format(sql.Identifier(column), sql.Identifier(table))).fetchone()
            assert sequence_row is not None and maximum_row is not None
            sequence_schema, sequence_name, maximum = sequence_row[0], sequence_row[1], maximum_row[0]
            target.execute(sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(sql.Identifier(sequence_schema, sequence_name), sql.Literal(maximum + 1)))
        report["verified"] = True
    (backup_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schema", help="Apply explicit, transactional schema migrations")
    migrate = sub.add_parser("import-sqlite", help="Offline import to an empty PostgreSQL target")
    migrate.add_argument("--data-dir", type=Path, required=True)
    migrate.add_argument("--backup-dir", type=Path, required=True)
    migrate.add_argument("--writes-stopped", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    url = database_url()
    if url is None:
        parser.error("DATABASE_URL must be configured")
    if args.command == "schema":
        with psycopg.connect(url, connect_timeout=10) as connection:
            upgrade(connection)
        print(f"Schema version {SCHEMA_VERSION} ready")
    else:
        report = import_sqlite(data_dir=args.data_dir, backup_dir=args.backup_dir,
                               url=url, writes_stopped=args.writes_stopped)
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
