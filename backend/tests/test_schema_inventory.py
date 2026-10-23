"""Prevent authority tables from escaping explicit migrations/import inventory."""
import ast
import re
from pathlib import Path

from adaptive_learning.migrate import JSON_COLUMNS, MIGRATIONS, SCHEMA_VERSION, TABLES


def test_authoritative_schema_and_import_inventory_are_complete():
    root = Path(__file__).parents[1] / 'src/adaptive_learning'
    source_tables = set()
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                source_tables.update(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', node.value))
    # This is a non-authoritative, recomputable model review cache.
    source_tables -= {'schema_migrations', 'reviewed_flashcards', 'worker_presence'}
    mapped = [table for tables in TABLES.values() for table in tables]
    assert len(mapped) == len(set(mapped)), 'Multiple SQLite sources map to the same target table'
    assert source_tables == set(mapped)
    schema_tables = set()
    for path in MIGRATIONS.glob('*.sql'):
        schema_tables.update(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', path.read_text()))
    assert schema_tables - {"worker_presence"} == source_tables


def test_merged_queues_and_durable_note_aliases_have_explicit_import_contract():
    assert SCHEMA_VERSION == 6
    assert TABLES['course_jobs.sqlite3'] == ('course_jobs', 'course_job_requests')
    assert TABLES['preparation.sqlite3'] == ('preparation',)
    assert 'studio_note_request_aliases' in TABLES['community.sqlite3']
    assert JSON_COLUMNS['course_jobs'] == {'result'}
    assert JSON_COLUMNS['studio_note_request_aliases'] == {'body_json'}
    schema = (MIGRATIONS / '006_course_preparation_note_aliases.sql').read_text()
    assert 'next_step INTEGER NOT NULL DEFAULT 0' in schema
