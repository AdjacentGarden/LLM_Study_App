"""No new model POST or durable claim is made in the final seconds of a budget."""
import json
from copy import deepcopy
from types import MethodType

import pytest
from test_teaching_unit_engine_rubric_only import unit_case as unit_case

from adaptive_learning.llm.client import model_time_budget
from adaptive_learning.personalization import teaching_content as content
from adaptive_learning.personalization import teaching_repairs as repairs
from adaptive_learning.personalization.teaching_content import TeachingContentGenerator


class RecordingClient:
    def __init__(self):
        self.calls = []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        return {'controlled': True}


@pytest.mark.parametrize('budget', [None, 30])
def test_unbounded_or_ample_budget_preserves_ordinary_submission(budget):
    client = RecordingClient()
    events = []
    generator = TeachingContentGenerator(client, audit_callback=events.append)
    if budget is None:
        result = generator._structured(system='controlled', user='{}', max_tokens=10)
    else:
        with model_time_budget(budget):
            result = generator._structured(system='controlled', user='{}', max_tokens=10)
    assert result == {'controlled': True} and len(client.calls) == 1
    assert [event['event'] for event in events] == ['model_request', 'model_response']


@pytest.mark.parametrize('remaining,submitted', [(0, False), (3, False), (14.99, False), (15, True)])
def test_exact_minimum_boundary_is_checked_before_any_client_or_paid_ledger(monkeypatch, unit_case, remaining, submitted):
    client = RecordingClient()
    events = []
    generator = TeachingContentGenerator(client, review_client=client, audit_callback=events.append)
    monkeypatch.setattr(content, 'remaining_model_time', lambda: remaining)
    token = repairs.current_repair.set(unit_case.run)
    unit_token = repairs.current_unit.set(unit_case.unit['unit_id'])
    try:
        if submitted:
            assert generator._structured(_review=True, system='controlled', user='{}') == {'controlled': True}
        else:
            with pytest.raises(repairs.TeachingRepairPending) as error:
                generator._structured(_review=True, system='controlled', user='{}')
            assert error.value.retry_after == 5
    finally:
        repairs.current_unit.reset(unit_token)
        repairs.current_repair.reset(token)
    with repairs.connect(unit_case.run.store.path) as db:
        count = db.execute('SELECT COUNT(*) AS n FROM teaching_course_submissions').fetchone()['n']
    assert count == int(submitted) and len(client.calls) == int(submitted)
    if not submitted:
        assert [event['event'] for event in events] == ['model_submission_deferred']
        assert events[0]['paid_submission'] is False


def test_short_shared_deadline_preserves_existing_candidate_without_a_submission(unit_case):
    case = unit_case
    before = deepcopy(case.unit['candidate'])
    case.run.write_unit(case.unit, status='pending', candidate=before)
    client = RecordingClient()
    case.generator.client = case.generator.review_client = client
    case.generator._structured = MethodType(TeachingContentGenerator._structured, case.generator)
    events = []
    case.generator.audit_callback = events.append
    token = repairs.current_repair.set(case.run)
    try:
        with model_time_budget(3):
            case.engine._advance_one(case.unit)
    finally:
        repairs.current_repair.reset(token)
    stored = case.run.units()[0]
    assert stored['candidate'] == before and stored['status'] != 'approved'
    assert stored['work']['phase'] == 'review' and 'approval' not in stored['work']
    with repairs.connect(case.run.store.path) as db:
        assert db.execute('SELECT COUNT(*) AS n FROM teaching_course_submissions').fetchone()['n'] == 0
    assert client.calls == []
    assert any(event['event'] == 'model_submission_deferred' for event in events)
    assert not any(event['event'] in ('model_request', 'model_response', 'model_error') for event in events)


def test_writer_directive_abandons_unreadable_formula_without_changing_global_protocol(unit_case):
    seen = []
    candidate = deepcopy(unit_case.unit['candidate'])
    unit_case.generator._structured = lambda **kwargs: seen.append(kwargs) or {'component': candidate}
    unit_case.engine._write_one(unit_case.unit)
    prompt = seen[0]['system']
    assert '符号、上下标、公式或对应关系残缺不清' in prompt
    assert '必须放弃该结论和依赖它的原题设' in prompt
    assert '基础概念辨析题' in prompt
    assert json.loads(seen[0]['user'])['previous_candidate'] == candidate
    # Exact protocol captured from the existing ae950/7a durable run.
    assert content._teaching_protocol_fingerprint() == '9a1db49abfec6f512b6e8d8715305ab1af1b7b1fb0f057b29258dbca3fd3538c'


def test_deferred_review_does_not_invalidate_the_previous_successful_writer(unit_case):
    case = unit_case
    before = deepcopy(case.unit['candidate'])
    case.run.write_unit(case.unit, status='pending', candidate=before)
    client = RecordingClient()
    case.generator.client = case.generator.review_client = client
    case.generator._structured = MethodType(TeachingContentGenerator._structured, case.generator)
    token = repairs.current_repair.set(case.run)
    unit_token = repairs.current_unit.set(case.unit['unit_id'])
    last_token = repairs.last_submission.set(None)
    try:
        assert case.run.structured(lambda: {'known_writer': True}, {'existing_writer': 1}) == {'known_writer': True}
        with model_time_budget(3):
            case.engine._advance_one(case.unit)
        with repairs.connect(case.run.store.path) as db:
            rows = db.execute('SELECT status,response_json FROM teaching_course_submissions').fetchall()
        assert len(rows) == 1 and rows[0]['status'] == 'responded'
        assert json.loads(rows[0]['response_json']) == {'known_writer': True}
        assert case.run.units()[0]['candidate'] == before
        assert client.calls == []
    finally:
        repairs.last_submission.reset(last_token)
        repairs.current_unit.reset(unit_token)
        repairs.current_repair.reset(token)
