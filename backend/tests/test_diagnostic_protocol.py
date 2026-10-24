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
        def generate(self, value):
            self.calls += 1
            return super().generate(value)
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
