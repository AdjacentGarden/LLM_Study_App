"""Real PostgreSQL persistence tests; each test creates a disposable schema."""
from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import quote

import psycopg
import pytest
from fastapi import HTTPException
from psycopg import sql

from adaptive_learning import native_catalog
from adaptive_learning.accounts import LOCAL_OWNER, Accounts
from adaptive_learning.api.course_jobs import CourseJobQueue
from adaptive_learning.assessment.models import (
    DiagnosticItem,
    InterviewSession,
    LearnerProfile,
    ResponseType,
)
from adaptive_learning.assessment.repository import SessionConflictError, SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository
from adaptive_learning.database import close_pools, connect
from adaptive_learning.ingestion.jobs import JobStateError, OCRRunResult, SQLiteOCRJobRepository
from adaptive_learning.migrate import TABLES, import_sqlite, upgrade
from adaptive_learning.native import NativeConflict, NativeRepository
from adaptive_learning.personalization.models import ChapterLearningBundle
from adaptive_learning.personalization.policy import PersonalizationPolicy
from adaptive_learning.social import SocialRepository
from adaptive_learning.studio import Studio, StudioLeaseLost
from adaptive_learning.studio_models import NoteInput


@pytest.fixture
def pg(monkeypatch):
    base = os.getenv('TEST_DATABASE_URL')
    if not base:
        pytest.skip('Set TEST_DATABASE_URL to run against real PostgreSQL')
    name = 'test_' + uuid.uuid4().hex
    with psycopg.connect(base, autocommit=True) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
    url = base + ('&' if '?' in base else '?') + 'options=' + quote('-csearch_path=' + name)
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.delenv('DATABASE_HOST', raising=False)
    with psycopg.connect(url) as db:
        upgrade(db)
    yield url
    close_pools()
    with psycopg.connect(base, autocommit=True) as db:
        db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))

def resource(root, owner='owner'):
    repo = NativeRepository(root / 'state/native.sqlite3')
    repo.save_workspace(owner, 'workspace', '课程', {})
    return repo, repo.add_resource(owner, 'workspace', native_catalog.BOOK_ID)


def test_diagnostic_chapter_claim_approval_and_explicit_recovery_are_atomic(pg, tmp_path):
    from test_diagnostic_chapter_cache import identity, items

    repo = SQLiteAssessmentRepository(tmp_path / 'state/assessments.sqlite3')
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: repo.claim_diagnostic_chapter(identity()), range(8)))
    first = next(row for row in claims if row['status'] == 'claimed')
    assert [row['status'] for row in claims].count('claimed') == 1
    assert [row['status'] for row in claims].count('running') == 7
    repo.fail_diagnostic_chapter(identity(), first['token'], 'unknown')
    assert repo.claim_diagnostic_chapter(identity(), now=10**12)['status'] == 'unknown'
    assert repo.grant_diagnostic_recovery('book', 'failed-revision') == 1
    assert repo.grant_diagnostic_recovery('book', 'failed-revision') == 1
    second = repo.claim_diagnostic_chapter(identity())
    assert second['status'] == 'claimed'
    from adaptive_learning.assessment.diagnostic_cache import DiagnosticChapterCacheError
    with pytest.raises(DiagnosticChapterCacheError):
        repo.approve_diagnostic_chapter(identity(), first['token'], items())
    repo.approve_diagnostic_chapter(identity(), second['token'], items())
    restored = SQLiteAssessmentRepository(tmp_path / 'state/assessments.sqlite3')
    assert restored.claim_diagnostic_chapter(identity())['items'] == items()
    assert restored.get_bank('diagnostic:book') is None
    with connect(tmp_path / 'unused') as db:
        assert db.execute('SELECT COUNT(*) FROM diagnostic_chapter_submissions').fetchone()[0] == 2
        assert db.execute("SELECT state FROM diagnostic_chapter_submissions WHERE token=?", (first['token'],)).fetchone()[0] == 'unknown'


def test_import_preserves_private_approvals_unknown_submissions_and_recovery_permissions(pg, tmp_path, monkeypatch):
    from test_diagnostic_chapter_cache import identity, items

    monkeypatch.delenv('DATABASE_URL')
    data = tmp_path / 'source'
    repo = SQLiteAssessmentRepository(data / 'state/assessments.sqlite3')
    first = repo.claim_diagnostic_chapter(identity())
    repo.fail_diagnostic_chapter(identity(), first['token'], 'unknown')
    repo.grant_diagnostic_recovery('book', 'explicit-failed-revision')
    resumed = repo.claim_diagnostic_chapter(identity())
    repo.approve_diagnostic_chapter(identity(), resumed['token'], items())
    pending = identity(scope='different-book')
    repo.claim_diagnostic_chapter(pending)
    monkeypatch.setenv('DATABASE_URL', pg)
    result = import_sqlite(data_dir=data, backup_dir=tmp_path / 'backup', url=pg, writes_stopped=True)
    assert result['verified'] and result['schema_version'] == 7
    assert result['tables']['diagnostic_chapter_cache']['rows'] == 2
    assert result['tables']['diagnostic_chapter_submissions']['rows'] == 3
    assert result['tables']['diagnostic_recovery_epochs']['rows'] == 1
    target = SQLiteAssessmentRepository(tmp_path / 'irrelevant')
    assert target.claim_diagnostic_chapter(identity())['items'] == items()
    assert target.claim_diagnostic_chapter(pending, now=10**12)['status'] == 'running'
    assert target.get_bank('diagnostic:book') is None

def test_binding_rollback_and_context_lifecycle(pg, tmp_path):
    with connect(tmp_path / 'unused') as db:
        row = db.execute("SELECT ? AS value, '50%?' AS literal", ('safe%?',)).fetchone()
        assert row[:] == ('safe%?', '50%?')
    with pytest.raises(Exception, match='closed'):
        db.execute('SELECT 1')
    with pytest.raises(psycopg.errors.UniqueViolation):
        with connect(tmp_path / 'unused') as db:
            db.execute("INSERT INTO visitors VALUES('a','x',1)")
            db.execute("INSERT INTO visitors VALUES('b','x',2)")
    with connect(tmp_path / 'unused') as db:
        assert db.execute('SELECT COUNT(*) FROM visitors').fetchone()[0] == 0

def test_revision_race_and_resource_isolation(pg, tmp_path):
    repo, row = resource(tmp_path)
    def save(index):
        try:
            repo.save_progress('owner', 'workspace', row['id'], 0, f'event-{index}', {'page': index+1})
            return True
        except NativeConflict:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(save, range(8))) == 1
    assert repo.progress(row['id'])['revision'] == 1
    with pytest.raises(KeyError):
        repo.resource('other', 'workspace', row['id'])

def test_fsrs_dedup_is_atomic(pg, tmp_path):
    repo, row = resource(tmp_path)
    card = native_catalog.public_source(row['id'], {})['flashcards'][0]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: repo.review_catalog_card(row['id'], 'same', card, 'good', 1.0), range(6)))
    assert sum(not r['duplicate'] for r in results) == 1
    assert len(repo.catalog_practice(row['id'])) == 1
    assert len({str(r['review_state']) for r in results}) == 1
    with pytest.raises(NativeConflict):
        repo.review_catalog_card(row['id'], 'same', card, 'again', 1.0)

def test_session_event_rolls_back_on_conflict(pg, tmp_path):
    repo = SQLiteAssessmentRepository(tmp_path / 'assessment')
    now = datetime.now(UTC)
    current = InterviewSession(session_id='s', profile=LearnerProfile(user_id='u', book_id='b'), created_at=now, updated_at=now)
    repo.create_session(current)
    stale = repo.get_session('s')
    repo.save_session(current)
    event = dict(event_id='event', course_id='c', item_id='i', event_type='practice', payload_json='{}')
    with pytest.raises(SessionConflictError):
        repo.save_session_with_event(stale, **event)
    assert repo.get_event_payload('s', 'event') is None
    assert repo.save_session_with_event(current, **event)
    assert not repo.save_session_with_event(current, **event)

def test_ocr_claims_and_stale_owner(pg, tmp_path):
    repo = SQLiteOCRJobRepository(tmp_path / 'ocr')
    for i in range(6):
        repo.register_book(book_id=str(i), original_name='b', file_path=tmp_path / str(i), source_sha256=str(i))
        repo.enqueue(book_id=str(i), output_dir=tmp_path / str(i), max_attempts=3, now=1)
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = list(pool.map(lambda i: repo.claim_next(owner=f'w{i}', lease_seconds=10, now=2), range(6)))
    assert len({j.job_id for j in jobs}) == 6
    old = jobs[0]
    repo.recover_expired(now=13)
    assert repo.claim_next(owner='new', lease_seconds=10, now=13)
    assert not repo.heartbeat(job_id=old.job_id, owner=old.lease_owner, lease_seconds=10, progress=.5, current_step='stale', now=14)
    with pytest.raises(JobStateError):
        repo.succeed(job_id=old.job_id, owner=old.lease_owner, result=OCRRunResult(.9, False, 1), now=14)

def test_studio_budget_leases_and_asset_fence(pg, tmp_path, monkeypatch):
    studio = Studio(tmp_path)
    monkeypatch.setenv('STUDIO_MEDIA_DAILY_CNY', '0.025')
    def submit(i):
        try:
            return studio.submit('owner', {'book_id':'b','request_id':f'r{i}','excerpt':str(i)}, 'image')['id']
        except HTTPException as e:
            return e.status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(submit, range(6))).count(429) == 5
    old = studio.claim_next()
    assert studio.claim_next() is None
    studio._lease.token = old['lease_owner']
    studio.update(old['id'], status='planning')
    future = time.time() + 100
    new = studio.claim_next(now=future)
    assert old['id'] == new['id'] and old['lease_owner'] != new['lease_owner']
    path = studio.assets / f'{old["id"]}.jpg'
    path.write_bytes(b'new')
    with pytest.raises(StudioLeaseLost):
        studio.publish_asset(old['id'], 'jpg', b'stale')
    with pytest.raises(StudioLeaseLost):
        studio.update(old['id'], status='succeeded')
    assert path.read_bytes() == b'new'
    studio._lease.token = new['lease_owner']
    studio.update(new['id'], status='submitting')
    studio.claim_next(now=future+100)
    assert studio.job('owner', new['id'])['status'] == 'uncertain'


def test_course_queue_pg_identity_and_cross_process_lock_contract(pg, tmp_path):
    expected = {table for tables in TABLES.values() for table in tables}
    with connect(tmp_path / 'schema-inventory') as db:
        actual = {row[0] for row in db.execute(
            'SELECT tablename FROM pg_tables WHERE schemaname=current_schema()'
        )}
    assert len(expected) == 51
    assert actual == expected | {'schema_migrations', 'worker_presence'}
    queue = CourseJobQueue(tmp_path / 'one/course_jobs.sqlite3', lambda *_: {'course_id': 'approved'})
    other = CourseJobQueue(tmp_path / 'two/course_jobs.sqlite3', lambda *_: pytest.fail('no duplicate'))
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = list(pool.map(lambda _: queue.enqueue('session', 'chapter', 'profile',
                                                    request_id='same-operation'), range(6)))
    assert len({job['job_id'] for job in jobs}) == 1
    key = jobs[0]['job_id']
    with queue._worker_lock() as acquired:
        assert acquired
        with other._worker_lock() as contender:
            assert not contender
        queue.recover()
        assert queue.process_one()
    saved = other.enqueue('session', 'chapter', 'changed-profile', request_id='same-operation')
    assert saved['job_id'] == key and saved['attempts'] == 1
    assert saved['course'] == {'course_id': 'approved'}
    with other._worker_lock() as acquired:
        assert acquired


def test_import_keeps_course_aliases_note_aliases_and_preparation_stage(pg, tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.delenv('DATABASE_URL')
    source = tmp_path / 'source'
    queue = CourseJobQueue(source / 'state/course_jobs.sqlite3', lambda *_: {'course_id': 'approved'})
    job = queue.enqueue('session', 'chapter', 'profile', request_id='durable-course-operation')
    queue.process_one()
    studio = Studio(source)
    note_job = studio.submit('owner', {
        'book_id': 'book', 'request_id': 'durable-note-operation',
        'note_id': 'note_123456', 'revision': 0,
    }, 'improve')
    # This is the existing on-disk queue format presented to the cutover tool.
    with sqlite3.connect(source / 'state/preparation.sqlite3') as db:
        db.execute("CREATE TABLE preparation(book_id TEXT PRIMARY KEY,status TEXT NOT NULL,"
                   "stage TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',updated REAL NOT NULL,"
                   "next_step INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO preparation VALUES('book','queued','building_rag','',1,2)")
    monkeypatch.setenv('DATABASE_URL', pg)
    report = import_sqlite(data_dir=source, backup_dir=tmp_path / 'backup', url=pg,
                           writes_stopped=True)
    assert report['verified'] and report['schema_version'] == 7
    assert report['tables']['course_jobs']['rows'] == 1
    assert report['tables']['course_job_requests']['rows'] == 1
    assert report['tables']['studio_note_request_aliases']['rows'] == 1
    assert report['tables']['preparation']['rows'] == 1
    restored = CourseJobQueue(tmp_path / 'irrelevant', lambda *_: pytest.fail('do not regenerate'))
    assert restored.enqueue('session', 'chapter', 'new-profile',
                            request_id='durable-course-operation')['job_id'] == job['job_id']
    with connect(tmp_path / 'irrelevant') as db:
        assert db.execute('SELECT job_id FROM studio_note_request_aliases').fetchone()[0] == note_job['id']
        assert db.execute('SELECT next_step FROM preparation').fetchone()[0] == 2

def test_import_preserves_ids_content_and_future_writes(pg, tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL')
    data = tmp_path / 'source'
    community = CommunityRepository(data / 'state/community.sqlite3')
    monkeypatch.setenv('NATIVE_LOCAL_LOGIN_ENABLED', '1')
    token = Accounts(community).local_session(None)
    social = SocialRepository(community)
    with community.connect() as db:
        social.ensure(db, LOCAL_OWNER)
        social.ensure(db, 'peer')
        db.execute("INSERT INTO chat_messages(id,lo,hi,sender,client_id,text,created) VALUES(40,?,?,?,?,?,1)", (*social.pair(LOCAL_OWNER, 'peer'), LOCAL_OWNER, 'm', '你好'))
    canonical = community.register_asset({'book_id': native_catalog.BOOK_ID, 'title': '内置教材', 'status': 'ready'}, 'hash', None)
    community.add_book(LOCAL_OWNER, canonical)
    studio = Studio(data)
    studio.save_note(LOCAL_OWNER, NoteInput(id='note_migration', book_id='book', revision=0, title='迁移笔记'))
    with community.connect() as db:
        db.execute("CREATE TABLE studio_note_editions(job_id TEXT PRIMARY KEY,owner TEXT NOT NULL,note_id TEXT NOT NULL,accepted INTEGER NOT NULL,updated REAL NOT NULL)")
        db.execute("INSERT INTO studio_note_editions VALUES('edition',?,'note_migration',1,1)", (LOCAL_OWNER,))
    studio.submit(LOCAL_OWNER, {'book_id':'book','request_id':'request'}, 'diagram')
    native, row = resource(data, LOCAL_OWNER)
    native.save_progress(LOCAL_OWNER, 'workspace', row['id'], 0, 'mutation', {'page':3})
    card = native_catalog.public_source(row['id'], {})['flashcards'][0]
    native.review_catalog_card(row['id'], 'review', card, 'good', 2)
    assessments = SQLiteAssessmentRepository(data / 'state/assessments.sqlite3')
    now = datetime.now(UTC)
    session = InterviewSession(session_id='s',profile=LearnerProfile(user_id=LOCAL_OWNER,book_id='book'),created_at=now,updated_at=now)
    assessments.create_session(session)
    assessments.save_bank(book_id='book', structure_fingerprint='fixture', items=[DiagnosticItem(item_id='question',chapter_id='chapter',knowledge_point_ids=['point'],prompt='问题',response_type=ResponseType.SINGLE_CHOICE,options=['a','b'],correct_option_ids=['0'])])
    bundle = ChapterLearningBundle(course_id='course',version=1,chapter_id='chapter',chapter_title='章节',decision=PersonalizationPolicy().decide(session.profile,'chapter'),opening='学习导引',summary='摘要',original_reading=[],knowledge_points=[],flashcards=[],worked_examples=[],checkpoint_questions=[],practice_items=[],generated_from_block_ids=[],profile_fingerprint='profile',source_fingerprint='source',estimated_minutes=5,created_at=now)
    assert assessments.save_course('s', bundle)
    assert assessments.save_session_with_event(session,event_id='event',course_id='course',item_id='question',event_type='practice',payload_json='{"score":1}')
    ocr = SQLiteOCRJobRepository(data / 'state/ocr_jobs.sqlite3')
    ocr.register_book(book_id='book',original_name='教材.pdf',file_path=data/'source.pdf',source_sha256='sha')
    ocr.enqueue(book_id='book',output_dir=data/'ocr',max_attempts=3)
    from adaptive_learning.worker_presence import WorkerPresence
    WorkerPresence(data).refresh('historical-worker', {'ocr'}, now=1)
    monkeypatch.setenv('DATABASE_URL', pg)
    report = import_sqlite(data_dir=data,backup_dir=tmp_path/'backup',url=pg,writes_stopped=True)
    assert report['verified']
    assert 'worker_presence' not in report['tables']
    assert Accounts(community).session_owner(token) == LOCAL_OWNER
    assert native.progress(row['id'])['page'] == 3
    assert assessments.get_course('course').summary == '摘要'
    assert assessments.get_event_payload('s', 'event') == '{"score":1}'
    assert studio.note(LOCAL_OWNER, 'note_migration')['title'] == '迁移笔记'
    assert report['tables']['studio_note_editions']['rows'] == 1
    assert native.catalog_review_states(row['id'])[card['card_id']]
    native.save_progress(LOCAL_OWNER,'workspace',row['id'],1,'new',{'page':4})
    with community.connect() as db:
        assert db.execute("INSERT INTO chat_messages(lo,hi,sender,client_id,text,created) VALUES(?,?,?,?,?,1) RETURNING id", (*social.pair(LOCAL_OWNER, 'peer'), LOCAL_OWNER, 'm2', 'later')).fetchone()[0] == 41
    with pytest.raises(ValueError,match='not empty'):
        import_sqlite(data_dir=data,backup_dir=tmp_path/'reject-backup',url=pg,writes_stopped=True)
    assert (data/'state/native.sqlite3').exists()

def test_import_failure_is_atomic(pg, tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL')
    repo = CommunityRepository(tmp_path/'source/state/community.sqlite3')
    with repo.connect() as db:
        db.execute("INSERT INTO visitors VALUES('a','b',1)")
        db.execute('CREATE TABLE surprise(secret TEXT)')
    monkeypatch.setenv('DATABASE_URL',pg)
    with pytest.raises(ValueError,match='Unmapped'):
        import_sqlite(data_dir=tmp_path/'source',backup_dir=tmp_path/'backup',url=pg,writes_stopped=True)
    with psycopg.connect(pg) as db:
        assert db.execute('SELECT COUNT(*) FROM visitors').fetchone()[0] == 0


def test_external_worker_health_preserves_native_upload_gate(pg, tmp_path, monkeypatch):
    import importlib
    import subprocess
    import sys
    from dataclasses import replace

    import pymupdf
    from fastapi.testclient import TestClient

    from adaptive_learning.worker_presence import WorkerPresence

    api = importlib.import_module("adaptive_learning.api.app")
    monkeypatch.setattr(api, "settings", replace(api.settings, data_dir=tmp_path, ocr_worker_enabled=False, studio_worker_enabled=False))
    monkeypatch.setattr(api.ocr_worker, "is_alive", lambda: False)
    monkeypatch.setattr(Studio, "is_alive", lambda self: False)
    presence = WorkerPresence(tmp_path)
    process = subprocess.Popen([sys.executable, "-m", "adaptive_learning.worker", "--queue", "all"], env={**os.environ, "DATABASE_URL": pg, "APP_DATA_DIR": str(tmp_path), "OCR_WORKER_ENABLED": "0", "STUDIO_WORKER_ENABLED": "0"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(160):
            if presence.active() == {"ocr", "studio"}:
                break
            if process.poll() is not None:
                raise AssertionError(process.communicate()[1].decode())
            time.sleep(.05)
        assert presence.active() == {"ocr", "studio"}
        client = TestClient(api.app)
        health = client.get("/api/health").json()
        assert health["ok"]
        assert health["ocr_worker_enabled"] and health["ocr_worker_alive"]
        monkeypatch.setenv("NATIVE_LOCAL_LOGIN_ENABLED", "1")
        token = Accounts(CommunityRepository(tmp_path / "state/community.sqlite3")).local_session(None)
        client.cookies.set("zhiwo_account", token)
        document = pymupdf.open()
        document.new_page().insert_text((30,30), "Fixture PDF for upload only")
        upload = client.post("/api/native/uploads", files={"file": ("fixture.pdf", document.tobytes(), "application/pdf")})
        assert upload.status_code == 200, upload.text
        document.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)
    assert process.returncode == 0
    health = client.get("/api/health").json()
    assert not health["ocr_worker_alive"] and not health["ocr_worker_enabled"]
    assert not health["ok"]
    presence.refresh("crashed", {"ocr", "studio"}, now=time.time()-16)
    assert presence.active() == set()
    assert not client.get("/api/health").json()["ocr_worker_alive"]


def test_import_refuses_target_with_live_workers(pg, tmp_path, monkeypatch):
    from adaptive_learning.worker_presence import WorkerPresence

    monkeypatch.delenv("DATABASE_URL")
    CommunityRepository(tmp_path / "source/state/community.sqlite3")
    monkeypatch.setenv("DATABASE_URL", pg)
    WorkerPresence(tmp_path).refresh("still-running", {"ocr"})
    with pytest.raises(ValueError, match="Live workers"):
        import_sqlite(data_dir=tmp_path / "source", backup_dir=tmp_path / "backup", url=pg, writes_stopped=True)
