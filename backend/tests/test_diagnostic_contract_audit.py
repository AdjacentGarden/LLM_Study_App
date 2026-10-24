"""Offline contract/audit regressions, never a substitute for a real content review."""
import json
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_diagnostic_generation import FakeClient, make_generator, structure, valid_payload

from adaptive_learning.api.teaching_audit import PrivateTeachingAudit
from adaptive_learning.assessment.item_generation import (
    DiagnosticGenerationError,
    DiagnosticItemGenerator,
)


class ScriptedReview:
    def __init__(self, verdict='accept'):
        self.verdict = verdict
        self.calls = []

    def structured(self, *, system, user, **kwargs):
        payload = json.loads(user)
        self.calls.append((system, payload))
        assert '只有其他选项也在同一题设下回答本问且成立时才构成多解' in system
        assert '不能因它是其他条件下的教材真陈述就判多解' in system
        assert '关键条件或对象的单词差异可以构成实质误解' in payload['question_contract']
        rows = [{'knowledge_point_id': item['knowledge_point_id'],
                 'accepted': self.verdict == 'accept',
                 'issues': [] if self.verdict == 'accept' else ['当前题设下存在多解或纯命名；须依同一证据修复']}
                for item in payload['items']]
        if self.verdict == 'missing':
            rows = rows[:1]
        elif self.verdict == 'reordered':
            rows.reverse()
        elif self.verdict == 'duplicate':
            rows[1] = dict(rows[0])
        return {'reviews': rows}


def test_stem_relative_contract_is_shared_without_removing_true_general_distractors():
    value = structure()
    label = value.chapters[0].knowledge_points[0]
    quote = value.chapters[0].knowledge_point_evidence[label][0]
    quote.quote = '物理因素和化学因素均可诱发变异。本实验仅使用化学因素处理样本。'
    candidate = valid_payload()
    row = candidate['items'][0]
    row.update(prompt='本实验仅使用化学因素处理样本，哪项说明当前的处理方式？',
               expected_answer='本实验采用化学因素处理样本以诱发变异',
               options=['本实验采用化学因素处理样本以诱发变异',
                        '物理因素处理生物同样可能使样本发生变异',
                        '本实验只改变记录方式而未对样本作处理',
                        '本实验仅作自然观察并且未使用处理因素'])
    class Writer(FakeClient):
        def structured(self, *, system, user, **kwargs):
            payload = json.loads(user)
            assert '给出条件/前提，要求判断题干尚未给出的结论' in system
            assert '关系/应用题只抄完整定义让人命名仍须拒绝' in payload['question_contract']
            assert '同一教材中在其他条件下成立的一般真陈述' in system
            return super().structured()
    writer, reviewer = Writer(candidate), ScriptedReview()
    result = DiagnosticItemGenerator(writer, review_client=reviewer,
        allow_grounded_answer_rewrite=True, validation_retries=0).generate(value)
    source = reviewer.calls[0][1]
    assert source['source']['knowledge_points'][0]['evidence'][0]['quote'] == quote.quote
    assert set(source['items'][0]['options']) == set(row['options'])
    assert set(result[0].options) == set(row['options'])
    assert writer.calls == len(reviewer.calls) == 1


@pytest.mark.parametrize('reason', ['当前题设下两项同时成立，不能唯一判断', '题干给出完整定义后仅要求命名，答案直接泄漏'])
def test_shared_basic_understanding_contract_does_not_override_semantic_veto(reason):
    class Rejector(ScriptedReview):
        def structured(self, **kwargs):
            result = super().structured(**kwargs)
            result['reviews'][0].update(accepted=False, issues=[reason])
            return result
    reviewer = Rejector()
    with pytest.raises(DiagnosticGenerationError, match=reason):
        DiagnosticItemGenerator(FakeClient(valid_payload()), review_client=reviewer,
            allow_grounded_answer_rewrite=True, validation_retries=0).generate(structure())
    assert len(reviewer.calls) == 1
    assert '题干已经明示或同义给出完整答案才是泄漏' in reviewer.calls[0][1]['question_contract']


@pytest.mark.parametrize('verdict', ['missing', 'reordered', 'duplicate'])
def test_every_requested_item_still_requires_its_own_complete_review(verdict):
    with pytest.raises(DiagnosticGenerationError, match='review did not cover every item'):
        DiagnosticItemGenerator(FakeClient(valid_payload()), review_client=ScriptedReview(verdict),
            validation_retries=0).generate(structure())


def test_private_audit_keeps_all_attempts_exact_review_inputs_and_source_without_extra_calls(tmp_path):
    events = []
    audit = PrivateTeachingAudit(tmp_path / 'private-diagnostic-audits', secrets=('PRIVATE_PROVIDER_KEY',))
    def capture(event):
        events.append(event)
        audit(event)
    candidate = valid_payload()
    candidate['api_key'] = 'PRIVATE_PROVIDER_KEY'
    candidate['cookies'] = {'session': 'PRIVATE_COOKIE'}
    writer, reviewer = FakeClient(candidate), ScriptedReview('reject')
    with pytest.raises(DiagnosticGenerationError, match='review rejected') as failure:
        DiagnosticItemGenerator(writer, review_client=reviewer, audit_callback=capture,
            review_identity={'provider': 'independent', 'model': 'review-model'},
            validation_retries=2).generate(structure())
    assert writer.calls == len(reviewer.calls) == 3
    assert failure.value.feedback['stage'] == 'semantic_review'
    assert len({event['invocation_id'] for event in events}) == 1
    requests = [event for event in events if event['event'] == 'generation_request']
    assert [event['attempt'] for event in requests] == [1, 2, 3]
    assert 'repair_feedback' not in requests[0]['request']
    assert requests[1]['request']['repair_feedback']['reviews']
    responses = [event for event in events if event['event'] == 'generation_response']
    assert len(responses) == 3 and all(event['response'] == candidate for event in responses)
    for event in (event for event in events if event['event'] == 'review_request'):
        payload = event['request']
        assert payload['source']['knowledge_points'][0]['evidence']
        assert 'repair_feedback' not in payload['source']
        for item in payload['items']:
            assert item['options'][item['correct_index']] == item['expected_answer']
    assert len([event for event in events if event['event'] == 'review_verdict']) == 3
    assert events[-1]['event'] == 'bank_failed'
    paths = list((tmp_path / 'private-diagnostic-audits').rglob('*.json'))
    assert len(paths) == len(events)
    text = '\n'.join(path.read_text() for path in paths)
    assert 'PRIVATE_PROVIDER_KEY' not in text and 'PRIVATE_COOKIE' not in text
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)
    assert all(stat.S_IMODE(path.parent.stat().st_mode) == 0o700 for path in paths)


@pytest.mark.parametrize('verdict', ['accept', 'reject'])
def test_ancillary_audit_failure_never_changes_result_or_hides_real_rejection(verdict, caplog):
    def broken(event):
        raise OSError('PRIVATE_CREDENTIAL_AND_STORAGE_PATH')
    writer, reviewer = FakeClient(valid_payload()), ScriptedReview(verdict)
    generator = DiagnosticItemGenerator(writer, review_client=reviewer,
        audit_callback=broken, validation_retries=0)
    if verdict == 'accept':
        assert len(generator.generate(structure())) == 2
    else:
        with pytest.raises(DiagnosticGenerationError, match='review rejected'):
            generator.generate(structure())
    assert writer.calls == len(reviewer.calls) == 1
    assert 'PRIVATE' not in caplog.text


def test_audit_payload_is_bounded_and_marks_omission_without_fabricating_content():
    events = []
    generator = make_generator(FakeClient(valid_payload()), verify_items=False, audit_callback=events.append)
    generator._audit({'invocation_id': 'a' * 32}, 'generation_response', response='x' * 100_000)
    assert events[0]['payload_omitted'] is True
    assert events[0]['payload_characters'] > 96_000
    assert 'response' not in events[0]
    assert len(json.dumps(events[0])) < 1000
    assert generator.client.calls == 0


def test_concurrent_generations_do_not_share_invocation_or_mutable_repair_context():
    events = []
    generator = DiagnosticItemGenerator(FakeClient(valid_payload()), review_client=ScriptedReview(),
                                        audit_callback=events.append, validation_retries=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(generator.generate, structure()) for _ in range(2)]
        assert all(len(future.result(timeout=10)) == 2 for future in futures)
    started = [event for event in events if event['event'] == 'bank_started']
    accepted = [event for event in events if event['event'] == 'bank_accepted']
    assert len(started) == len(accepted) == 2
    assert {event['invocation_id'] for event in started} == {event['invocation_id'] for event in accepted}
    assert len({event['invocation_id'] for event in started}) == 2
    assert all('repair_feedback' not in event['request'] for event in events if event['event'] == 'generation_request')


def test_api_rejection_never_returns_private_candidates_reviews_or_audit_files(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from native_harness import api, configure

    app = configure(tmp_path, monkeypatch)
    monkeypatch.setenv('NATIVE_LOCAL_LOGIN_ENABLED', '1')
    app.post('/api/books/{book_id}/diagnostics')(api.generate_diagnostic_bank)
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF fixture')
    api.job_repository.register_book(book_id='private_book', original_name='private.pdf',
                                     file_path=source, source_sha256='fixture')
    api.job_repository.save_structure('private_book', structure())
    candidate = valid_payload()
    candidate['items'][0]['prompt'] = 'PRIVATE_CANDIDATE_CHOICE_QUESTION'
    class PrivateRejector(ScriptedReview):
        def structured(self, **kwargs):
            result = super().structured(**kwargs)
            result['reviews'][0].update(accepted=False, issues=['PRIVATE_REVIEW_REASON'])
            return result
    audit = PrivateTeachingAudit(tmp_path / 'private-diagnostic-audits')
    generator = DiagnosticItemGenerator(FakeClient(candidate), review_client=PrivateRejector(),
                                        audit_callback=audit, validation_retries=0)
    monkeypatch.setattr(api, 'item_generator', generator)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 5000)) as client:
        assert client.post('/api/native/local-login').status_code == 200
        owner = api.account_repository.session_owner(client.cookies.get('zhiwo_account'))
        assert api.account_repository.repo.bind_upload(owner, 'private_book')
        response = client.post('/api/books/private_book/diagnostics')
        assert response.status_code == 502
        assert 'PRIVATE' not in response.text and 'correct_option_ids' not in response.text
        paths = list(audit.directory.rglob('*.json'))
        assert paths
        assert any('PRIVATE_CANDIDATE_CHOICE_QUESTION' in path.read_text() for path in paths)
        assert any('PRIVATE_REVIEW_REASON' in path.read_text() for path in paths)
        relative = paths[0].relative_to(tmp_path).as_posix()
        assert client.get('/' + relative).status_code == 404
