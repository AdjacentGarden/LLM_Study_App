"""Protocol upgrades refresh new banks without rewriting a learner's old assessment."""
from dataclasses import replace

import pytest
from fastapi import HTTPException
from native_harness import FixtureBank, api, configure, structure
from pydantic import TypeAdapter
from starlette.requests import Request

from adaptive_learning.api.schemas import StartInterviewRequest
from adaptive_learning.assessment.engine import score_choice
from adaptive_learning.assessment.item_generation import diagnostic_bank_key, structure_fingerprint
from adaptive_learning.assessment.models import AssessmentResponse, DiagnosticItem, InterviewPhase


@pytest.fixture
def bank_environment(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    value = structure()
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF fixture')
    api.job_repository.register_book(book_id='book', original_name='book.pdf', file_path=source, source_sha256='source')
    api.job_repository.save_structure('book', value)
    class CountingBank(FixtureBank):
        calls = 0
        def generate(self, value, **kwargs):
            self.calls += 1
            return super().generate(value, **kwargs)
    generator = CountingBank()
    monkeypatch.setattr(api, 'item_generator', generator)
    return value, generator


def test_new_protocol_never_silently_uses_or_overwrites_legacy_bank(bank_environment, monkeypatch):
    value, generator = bank_environment
    legacy = FixtureBank().generate(value)
    api.assessment_repository.save_bank(book_id='book', structure_fingerprint=structure_fingerprint(value), items=legacy)
    assert api._items_for_book('book') == []
    api.generate_diagnostic_bank('book')
    assert generator.calls == 1
    assert api.assessment_repository.get_bank('book') == legacy
    assert api._items_for_book('book')
    api.generate_diagnostic_bank('book')
    assert generator.calls == 1
    pure_source_hash = structure_fingerprint(value)
    monkeypatch.setattr(api, 'settings', replace(api.settings,
        teaching_review_provider='pucoding', teaching_review_model='gpt-other'))
    assert api._items_for_book('book') == []
    api.generate_diagnostic_bank('book')
    assert generator.calls == 2
    assert structure_fingerprint(value) == pure_source_hash
    assert api.assessment_repository.get_bank('book') == legacy


def test_old_native_snapshot_pending_turn_and_evidence_survive_new_protocol(bank_environment, monkeypatch):
    value, _ = bank_environment
    items = FixtureBank().generate(value)
    session = api.orchestrator.start(user_id='reader', book_id='book', book_title=value.title,
        chapter_titles=[value.chapters[0].title], items=items)
    session.phase = InterviewPhase.ADAPTIVE_DIAGNOSIS
    session.pending_turn = None
    turn = api.orchestrator.next_diagnostic(session, items)
    assert turn.item is not None
    item = next(item for item in items if item.item_id == turn.item.item_id)
    answer = AssessmentResponse(item_id=item.item_id, answer='', selected_option_ids=item.correct_option_ids,
                                confidence=0.8, response_seconds=12)
    assert score_choice(item, answer).score == 1
    correct_public = next(option for option in turn.options if option['id'] == item.correct_option_ids[0])
    assert correct_public['label'] == item.options[int(item.correct_option_ids[0])]
    assert turn.item.options == [option['label'] for option in turn.options]
    api.assessment_repository.create_session(session)
    api.native_repository.save_workspace('reader', 'workspace', '课程', {})
    resource = api.native_repository.add_resource('reader', 'workspace', 'book')
    api.native_repository.attach_session(resource['id'], 0, session.session_id, value,
                                        TypeAdapter(list[DiagnosticItem]).dump_json(items).decode())
    before_context = api.native_repository.session_context(session.session_id)
    before_session = api.assessment_repository.get_session(session.session_id).model_dump_json()
    api.generate_diagnostic_bank('book')
    monkeypatch.setattr(api, 'settings', replace(api.settings,
        teaching_review_provider='pucoding', teaching_review_model='gpt-other'))
    api.generate_diagnostic_bank('book')
    restored = api.assessment_repository.get_session(session.session_id)
    assert restored.model_dump_json() == before_session
    assert api.native_repository.session_context(session.session_id) == before_context
    assert api._items_for_session(restored) == items
    # Saved public order/IDs are restored verbatim, not reshuffled on upgrade.
    assert api.orchestrator.next_diagnostic(restored, api._items_for_session(restored)) == turn
    evidence = api.orchestrator.record_diagnostic(restored, answer, api._items_for_session(restored))
    assert evidence.score == 1 and restored.responses == [answer]
    answered_evidence = restored.profile.model_dump_json()
    api.generate_diagnostic_bank('book')
    assert restored.profile.model_dump_json() == answered_evidence


def test_new_non_native_interview_keeps_private_option_snapshot_after_refresh(bank_environment, monkeypatch):
    value, _ = bank_environment
    api.generate_diagnostic_bank('book')
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/interviews', 'headers': []})
    response = api.start_interview(StartInterviewRequest(book_id='book'), request)
    session = api.assessment_repository.get_session(response.session_id)
    before = api._items_for_session(session)
    changed = [item.model_copy(update={'options': list(reversed(item.options)), 'correct_option_ids': ['3']})
               for item in before]
    api.assessment_repository.save_bank(book_id=diagnostic_bank_key('book'),
        structure_fingerprint=api.diagnostic_fingerprint(value), items=changed)
    assert api._items_for_book('book') == changed
    assert api._items_for_session(session) == before


def test_unversioned_non_native_session_still_reads_its_legacy_bank(bank_environment):
    value, _ = bank_environment
    legacy = FixtureBank().generate(value)
    api.assessment_repository.save_bank(book_id='book', structure_fingerprint=structure_fingerprint(value), items=legacy)
    session = api.orchestrator.start(user_id='reader', book_id='book', book_title=value.title,
        chapter_titles=[value.chapters[0].title], items=legacy)
    api.generate_diagnostic_bank('book')
    assert api._items_for_session(session) == legacy


def test_missing_independent_reviewer_never_generates_or_claims_a_bank(bank_environment, monkeypatch):
    value, _ = bank_environment
    writer = object()
    assert api._create_diagnostic_generator(api.settings, writer, None) is None
    monkeypatch.setattr(api, 'item_generator', None)
    with pytest.raises(HTTPException) as failure:
        api.generate_diagnostic_bank('book')
    assert failure.value.status_code == 503
    assert api.assessment_repository.get_bank(diagnostic_bank_key('book')) is None


def test_book_submission_block_is_actionable_409_and_never_publishes_partial_bank(bank_environment, monkeypatch):
    from adaptive_learning.assessment.diagnostic_cache import (
        DiagnosticChapterSubmissionBlocked,
        RepositoryDiagnosticChapterCache,
    )

    class BlockedBank:
        def generate(self, value, *, chapter_cache, cache_scope):
            assert isinstance(chapter_cache, RepositoryDiagnosticChapterCache)
            assert cache_scope == 'book'
            raise DiagnosticChapterSubmissionBlocked('uncertain private submission')
    monkeypatch.setattr(api, 'item_generator', BlockedBank())
    with pytest.raises(HTTPException) as failure:
        api.generate_diagnostic_bank('book')
    assert failure.value.status_code == 409
    assert '显式重试' in failure.value.detail
    assert failure.value.headers['X-CloudPath-Error-Code'] == 'diagnostic_submission_uncertain'
    assert api.assessment_repository.get_bank(diagnostic_bank_key('book')) is None


def test_native_submission_block_is_409_without_attaching_or_changing_a_session(bank_environment, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from adaptive_learning.api.native_routes import native_router
    from adaptive_learning.assessment.diagnostic_cache import DiagnosticChapterSubmissionBlocked

    class BlockedBank:
        def generate(self, value, *, chapter_cache, cache_scope):
            assert cache_scope == 'book'
            raise DiagnosticChapterSubmissionBlocked('unknown')
    monkeypatch.setattr(api, 'item_generator', BlockedBank())
    app = FastAPI()
    app.include_router(native_router(api))
    monkeypatch.setenv('NATIVE_LOCAL_LOGIN_ENABLED', '1')
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 5000)) as client:
        assert client.post('/api/native/local-login').status_code == 200
        actor = api.account_repository.session_owner(client.cookies.get('zhiwo_account'))
        api.account_repository.repo.bind_upload(actor, 'book')
        api.native_repository.save_workspace(actor, 'workspace_block', '课程', {})
        resource = api.native_repository.add_resource(actor, 'workspace_block', 'book')
        response = client.post(f"/api/workspaces/workspace_block/resources/{resource['id']}/session")
        assert response.status_code == 409 and '重试诊断' in response.text
        assert response.headers['X-CloudPath-Error-Code'] == 'diagnostic_submission_uncertain'
        assert api.native_repository.resource(actor, 'workspace_block', resource['id'])['session_id'] is None


@pytest.mark.parametrize('failure,status,code', [
    ('rejected', 502, 'diagnostic_generation_rejected'),
    ('unknown', 409, 'diagnostic_submission_uncertain'),
    ('cooldown', 503, 'diagnostic_generation_not_admitted'),
])
def test_first_native_diagnostic_failure_reports_stable_recovery_code_without_attaching_session(
    bank_environment, monkeypatch, failure, status, code
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from adaptive_learning.api.native_routes import native_router
    from adaptive_learning.assessment.item_generation import DiagnosticGenerationError
    from adaptive_learning.llm.client import LLMCooldownError, LLMError

    class FailedBank:
        calls = 0
        def generate(self, value, *, chapter_cache, cache_scope):
            self.calls += 1
            if failure == 'rejected':
                raise DiagnosticGenerationError('completed quality rejection')
            if failure == 'cooldown':
                raise LLMCooldownError(1)
            raise LLMError('transport outcome unknown')
    generator = FailedBank()
    monkeypatch.setattr(api, 'item_generator', generator)
    app = FastAPI()
    app.include_router(native_router(api))
    monkeypatch.setenv('NATIVE_LOCAL_LOGIN_ENABLED', '1')
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 5000)) as client:
        assert client.post('/api/native/local-login').status_code == 200
        actor = api.account_repository.session_owner(client.cookies.get('zhiwo_account'))
        api.account_repository.repo.bind_upload(actor, 'book')
        api.native_repository.save_workspace(actor, 'workspace_failure', '课程', {})
        resource = api.native_repository.add_resource(actor, 'workspace_failure', 'book')
        response = client.post(f"/api/workspaces/workspace_failure/resources/{resource['id']}/session")
        assert response.status_code == status and response.headers['X-CloudPath-Error-Code'] == code
        assert '重试诊断' in response.json()['detail']
        assert generator.calls == 1
        assert api.native_repository.resource(actor, 'workspace_failure', resource['id'])['session_id'] is None
        assert api.assessment_repository.get_bank(diagnostic_bank_key('book')) is None


def test_native_explicit_retry_nonce_is_replay_safe_across_new_tokens_and_resource_revisions(bank_environment, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_diagnostic_chapter_cache import identity

    from adaptive_learning.api.native_routes import native_router
    from adaptive_learning.assessment.diagnostic_cache import DiagnosticChapterSubmissionBlocked

    class UnknownBank:
        calls = 0
        def generate(self, value, *, chapter_cache, cache_scope):
            claim = chapter_cache.claim(identity(scope=cache_scope))
            if claim['status'] == 'claimed':
                self.calls += 1
                chapter_cache.fail(identity(scope=cache_scope), claim['token'], 'unknown')
            raise DiagnosticChapterSubmissionBlocked('outcome unknown')
    generator = UnknownBank()
    monkeypatch.setattr(api, 'item_generator', generator)
    app = FastAPI()
    app.include_router(native_router(api))
    monkeypatch.setenv('NATIVE_LOCAL_LOGIN_ENABLED', '1')
    value, _ = bank_environment
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 5000)) as client:
        assert client.post('/api/native/local-login').status_code == 200
        actor = api.account_repository.session_owner(client.cookies.get('zhiwo_account'))
        api.account_repository.repo.bind_upload(actor, 'book')
        api.native_repository.save_workspace(actor, 'workspace_retry', '课程', {})
        resource = api.native_repository.add_resource(actor, 'workspace_retry', 'book')
        url = f"/api/workspaces/workspace_retry/resources/{resource['id']}/session"
        assert client.post(url).status_code == 409 and generator.calls == 1
        assert client.post(url).status_code == 409 and generator.calls == 1
        assert client.post(url + '?retry=true').status_code == 422 and generator.calls == 1
        first_action = url + '?retry=true&retry_request_id=retry_action_one'
        second_action = url + '?retry=true&retry_request_id=retry_action_two'
        assert client.post(first_action).status_code == 409 and generator.calls == 2
        assert client.post(first_action).status_code == 409 and generator.calls == 2
        assert client.post(second_action).status_code == 409 and generator.calls == 3
        assert client.post(first_action).status_code == 409 and generator.calls == 3
        api.native_repository.save_structure(actor, 'workspace_retry', resource['id'], 0, value)
        assert client.post(first_action).status_code == 409 and generator.calls == 3
        assert api.assessment_repository.get_bank(diagnostic_bank_key('book')) is None


def test_native_scoped_bank_refreshes_protocol_but_attached_session_stays_pinned(bank_environment, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from adaptive_learning.api.native_routes import native_router

    value, generator = bank_environment
    app = FastAPI()
    app.include_router(native_router(api))
    monkeypatch.setenv('NATIVE_LOCAL_LOGIN_ENABLED', '1')
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 5000)) as client:
        assert client.post('/api/native/local-login').status_code == 200
        actor = api.account_repository.session_owner(client.cookies.get('zhiwo_account'))
        assert api.account_repository.repo.bind_upload(actor, 'book')
        api.native_repository.save_workspace(actor, 'workspace_scope', '课程', {})
        resource = api.native_repository.add_resource(actor, 'workspace_scope', 'book')
        legacy_key = f"native:{resource['id']}:0"
        api.assessment_repository.save_bank(book_id=legacy_key, structure_fingerprint=structure_fingerprint(value),
                                            items=FixtureBank().generate(value))
        url = f"/api/workspaces/workspace_scope/resources/{resource['id']}/session"
        first = client.post(url)
        assert first.status_code == 200, first.text
        assert generator.calls == 1  # An old same-structure bank is not current protocol.
        sid = first.json()['session_id']
        context = api.native_repository.session_context(sid)
        current_key = diagnostic_bank_key(legacy_key)
        assert api.assessment_repository.get_bank(current_key, expected_fingerprint=api.diagnostic_fingerprint(value))
        monkeypatch.setattr(api, 'settings', replace(api.settings,
            teaching_review_provider='pucoding', teaching_review_model='gpt-other'))
        assert client.post(url).json()['session_id'] == sid
        assert api.native_repository.session_context(sid) == context and generator.calls == 1
        # Detaching in the repository models an explicit fresh assessment; old
        # session_context is retained and must never be rewritten by cache refresh.
        with api.native_repository.connect() as db:
            db.execute('UPDATE native_resources SET session_id=NULL WHERE id=?', (resource['id'],))
        second = client.post(url)
        assert second.status_code == 200, second.text
        assert second.json()['session_id'] != sid and generator.calls == 2
        assert api.native_repository.session_context(sid) == context
