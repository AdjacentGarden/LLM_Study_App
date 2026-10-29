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
    assert SCHEMA_VERSION == 10
    assert TABLES['course_jobs.sqlite3'] == (
        'course_jobs', 'course_job_requests', 'teaching_course_repairs', 'teaching_course_units',
        'teaching_course_proofs', 'teaching_course_submissions', 'teaching_course_epochs')
    assert TABLES['preparation.sqlite3'] == ('preparation',)
    assert 'studio_note_request_aliases' in TABLES['community.sqlite3']
    assert JSON_COLUMNS['course_jobs'] == {'result'}
    assert JSON_COLUMNS['studio_note_request_aliases'] == {'body_json'}
    schema = (MIGRATIONS / '006_course_preparation_note_aliases.sql').read_text()
    assert 'next_step INTEGER NOT NULL DEFAULT 0' in schema


def test_private_chapter_approvals_and_submission_ledger_have_explicit_import_contract():
    assert {'diagnostic_chapter_cache', 'diagnostic_chapter_submissions', 'diagnostic_recovery_epochs',
            'diagnostic_recovery_grants'} <= set(TABLES['assessments.sqlite3'])
    assert JSON_COLUMNS['diagnostic_chapter_cache'] == {'identity_json', 'items_json'}
    schema = (MIGRATIONS / '007_diagnostic_chapter_cache.sql').read_text()
    assert "'running','approved','rejected','not_admitted','unknown'" in schema


def test_isolated_teaching_repair_state_and_submission_proofs_have_explicit_import_contract():
    assert JSON_COLUMNS['teaching_course_repairs'] == {'identity_json', 'state_json'}
    assert JSON_COLUMNS['teaching_course_units'] == {'candidate_json', 'feedback_json', 'work_json'}
    assert JSON_COLUMNS['teaching_course_proofs'] == {'proof_json'}
    assert JSON_COLUMNS['teaching_course_submissions'] == {'response_json'}
    repair_schema = (MIGRATIONS / '009_teaching_course_repairs.sql').read_text()
    unit_schema = (MIGRATIONS / '010_isolated_teaching_units.sql').read_text()
    assert 'CREATE TABLE IF NOT EXISTS teaching_course_repairs' in repair_schema
    assert 'ALTER TABLE course_jobs ADD COLUMN IF NOT EXISTS retry_at' in repair_schema
    assert set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', repair_schema)) == {
        'teaching_course_repairs', 'teaching_course_units', 'teaching_course_proofs', 'teaching_course_submissions'}
    assert set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', unit_schema)) == {'teaching_course_epochs'}
    assert 'PRIMARY KEY(run_key,unit_id)' in repair_schema
    assert 'PRIMARY KEY(run_key,proof_key)' in repair_schema
    assert 'PRIMARY KEY(run_key,request_key)' in repair_schema
    assert 'PRIMARY KEY(scope,chapter_id)' in unit_schema
    assert 'ALTER TABLE teaching_course_units ADD COLUMN IF NOT EXISTS work_json' in unit_schema
    assert 'ALTER TABLE teaching_course_submissions ADD COLUMN IF NOT EXISTS unit_id' in unit_schema
