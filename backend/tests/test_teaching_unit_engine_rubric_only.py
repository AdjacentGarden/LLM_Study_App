"""Real unit orchestration freezes accepted task fields during rubric repairs."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_teaching_content import course, point_candidate, positive_rubric_alignment_response

from adaptive_learning.personalization import teaching_repairs as repairs
from adaptive_learning.personalization.rubric_alignment import (
    make_rubric_alignment_batch,
    parse_rubric_alignment_response,
)
from adaptive_learning.personalization.teaching_content import TeachingContentGenerator
from adaptive_learning.personalization.teaching_unit_engine import (
    ENGINE_VERSION,
    UnitEngine,
    _UnitRejected,
)


@pytest.fixture
def unit_case(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('DATABASE_HOST', raising=False)
    bundle, chapter, evidence = course(1)
    store = repairs.TeachingRepairStore(tmp_path / 'units.sqlite3')
    run = store.open('user-and-book', chapter.chapter_id,
                     {'engine': ENGINE_VERSION, 'evidence_fingerprint': evidence.fingerprint})
    generator = TeachingContentGenerator(object(), review_client=object(),
        review_identity={'provider': 'controlled', 'model': 'offline'})
    token = repairs.current_repair.set(run)
    try:
        engine = UnitEngine(generator, bundle, chapter, evidence)
    finally:
        repairs.current_repair.reset(token)
    for item in engine.inputs:
        item['scope_conditions'] = []
    spec = next(row for row in engine._specs() if row['kind'] == 'practice')
    run.initialize_units([spec])
    unit = run.units()[0]
    unit['candidate'] = point_candidate(engine.by_id[spec['owner_id']])['practice']
    unit['work'].update(mode='repair', phase='review', attempt_nonce='controlled-offline')
    seen = []

    def fact(row):
        seen.append('fact')
        return {'source_entailment': True, 'scope_exceptions': True, 'findings': []}

    def teacher(**kwargs):
        seen.append('teacher')
        return {'accepted': True, 'redesign_ok': True, 'issues': []}

    engine._fact_one = fact
    generator._structured = teacher
    return SimpleNamespace(engine=engine, generator=generator, unit=unit, run=run, seen=seen)


def alignment(unit, scope='rubric_only'):
    batch = make_rubric_alignment_batch([
        {key: unit['candidate'][key] for key in ('item_id', 'prompt', 'rubric')}])
    raw = positive_rubric_alignment_response(batch.to_payload())
    row = raw['items'][0]['rubric_checks'][0]
    row.update(required=False, repair_scope=scope,
        reason='此项为题干未问的扩展，删除后仍可完整回答。',
        counterexample_answer='原结论的温度前提已经改变，应重新测量。',
        repair='只删除额外评分要求，保持题干与有据答案。')
    if scope is None:
        row.pop('repair_scope')
    return parse_rubric_alignment_response(raw, batch)


def reject_rubric(case, scope='rubric_only'):
    def rubric(batch):
        case.seen.append('rubric')
        return alignment(case.unit, scope)
    case.generator._request_rubric_review = rubric
    with pytest.raises(_UnitRejected) as failure:
        case.engine._review_one(case.unit)
    assert failure.value.stage == 'rubric'
    case.engine._failed(case.unit, failure.value, 'repair')


def test_typed_rubric_only_rejection_persists_exact_field_lock_after_both_reviews(unit_case):
    reject_rubric(unit_case)
    assert unit_case.seen == ['fact', 'teacher', 'rubric']
    assert unit_case.unit['work'].get('rubric_only_lock')
    reopened = repairs.TeachingRepairStore(unit_case.run.store.path).open(
        unit_case.run.scope, unit_case.run.chapter_id,
        {key: value for key, value in unit_case.run.identity.items() if key != 'epoch'})
    assert reopened.units()[0]['work']['rubric_only_lock'] == unit_case.unit['work']['rubric_only_lock']


def test_rubric_patch_merges_original_task_and_is_still_reviewed(unit_case):
    reject_rubric(unit_case)
    baseline = deepcopy(unit_case.unit['candidate'])
    calls = []

    def writer(**kwargs):
        calls.append(json.loads(kwargs['user']))
        return {'component': {'rubric': ['说明条件改变后必须重新测量']}}

    unit_case.generator._structured = writer
    revised = unit_case.engine._write_one(unit_case.unit)
    assert calls[0]['repair_task']['editable_fields'] == ['rubric']
    assert set(calls[0]['output_shape']['component']) == {'rubric'}
    assert isinstance(calls[0]['repair_task']['instruction'], str)
    assert {key: value for key, value in revised.items() if key != 'rubric'} == {
        key: value for key, value in baseline.items() if key != 'rubric'}
    unit_case.engine._validate_one(unit_case.unit, revised)
    unit_case.unit['candidate'] = revised

    def veto(row):
        raise _UnitRejected({'issue': '新的评分要求仍有事实错误。'}, 'fact')
    unit_case.engine._fact_one = veto
    with pytest.raises(_UnitRejected) as failure:
        unit_case.engine._review_one(unit_case.unit)
    unit_case.engine._failed(unit_case.unit, failure.value, 'repair')
    assert 'rubric_only_lock' not in unit_case.unit['work']
    assert unit_case.unit['status'] != 'approved'


@pytest.mark.parametrize('scope', ['task_revision', None])
def test_non_explicit_rubric_only_scope_cannot_lock_a_task(unit_case, scope):
    reject_rubric(unit_case, scope)
    assert 'rubric_only_lock' not in unit_case.unit['work']


def test_mixed_rejected_criteria_do_not_grant_a_rubric_only_lock(unit_case):
    def mixed(batch):
        raw = positive_rubric_alignment_response(batch.to_payload())
        for row, scope in zip(raw['items'][0]['rubric_checks'], ('rubric_only', 'task_revision'), strict=True):
            row.update(required=False, repair_scope=scope, reason='仍有实际问项需要调整。',
                       counterexample_answer='条件改变后应重新测量。', repair='按实际问项和原文修复。')
        return parse_rubric_alignment_response(raw, batch)
    unit_case.generator._request_rubric_review = mixed
    with pytest.raises(_UnitRejected):
        unit_case.engine._review_one(unit_case.unit)
    assert 'rubric_only_lock' not in unit_case.unit['work']


@pytest.mark.parametrize('field,value', [
    ('prompt', '偷偷更换原有题干及其问题'), ('expected_answer', '偷偷改写正确答案'),
    ('item_id', 'other-question'), ('source_ids', ['other-source']),
])
def test_locked_writer_cannot_smuggle_nonrubric_fields(unit_case, field, value):
    reject_rubric(unit_case)
    before = deepcopy(unit_case.unit['candidate'])
    unit_case.generator._structured = lambda **kwargs: {
        'component': {'rubric': ['只回答题干问项'], field: value}}
    with pytest.raises(_UnitRejected, match='当前组件需要修正') as failure:
        unit_case.engine._write_one(unit_case.unit)
    assert failure.value.stage == 'repair_lock'
    assert unit_case.unit['candidate'] == before


def test_full_materialized_candidate_cannot_bypass_field_freeze(unit_case):
    reject_rubric(unit_case)
    forged = deepcopy(unit_case.unit['candidate'])
    forged['expected_answer'] = '删除了原来有据的必要条件。'
    with pytest.raises(_UnitRejected) as failure:
        unit_case.engine._validate_one(unit_case.unit, forged)
    assert failure.value.stage == 'repair_lock'


@pytest.mark.parametrize('stage', ['fact', 'teacher', 'whole_course', 'assembly'])
def test_real_additional_veto_invalidates_the_rubric_only_permission(unit_case, stage):
    reject_rubric(unit_case)
    assert unit_case.unit['work']['rubric_only_lock']
    unit_case.engine._failed(unit_case.unit, _UnitRejected({'issue': '真实内容需要修改。'}, stage), 'repair')
    assert 'rubric_only_lock' not in unit_case.unit['work']


@pytest.mark.parametrize('gate', ['fact', 'teacher'])
def test_alignment_cannot_authorize_a_lock_before_other_gates_pass(unit_case, gate):
    unit_case.generator._request_rubric_review = lambda batch: pytest.fail('Rubric must not run after an earlier veto')
    if gate == 'fact':
        def veto(unit):
            raise _UnitRejected({'issue': '实际事实不通过。'}, 'fact')
        unit_case.engine._fact_one = veto
    else:
        unit_case.generator._structured = lambda **kwargs: {
            'accepted': False, 'redesign_ok': True, 'issues': ['题干仍有错误。']}
    with pytest.raises(_UnitRejected) as failure:
        unit_case.engine._review_one(unit_case.unit)
    assert failure.value.stage == gate
    assert 'rubric_only_lock' not in unit_case.unit['work']


@pytest.mark.parametrize('fault', ['candidate_hash', 'evidence', 'changed_task'])
def test_damaged_or_wrong_context_lock_fails_closed_before_writer(unit_case, fault):
    reject_rubric(unit_case)
    if fault == 'candidate_hash':
        unit_case.unit['work']['rubric_only_lock']['candidate_sha256'] = 'wrong'
    elif fault == 'evidence':
        unit_case.unit['work']['rubric_only_lock']['evidence_fingerprint'] = 'other-book'
    else:
        unit_case.unit['candidate']['expected_answer'] += '外来修改。'
    unit_case.generator._structured = lambda **kwargs: pytest.fail('Invalid lock cannot authorize a writer')
    with pytest.raises(_UnitRejected) as failure:
        unit_case.engine._write_one(unit_case.unit)
    assert failure.value.stage == 'repair_lock'


def test_explicit_redesign_releases_lock_and_still_requires_fresh_review(unit_case):
    reject_rubric(unit_case)
    # A genuine additional task veto invalidates deletion-only permission.
    unit_case.engine._failed(unit_case.unit, _UnitRejected({'issue': '题设需要重新设计。'}, 'teacher'), 'repair')
    unit_case.unit['work']['mode'] = 'redesign'
    revised = deepcopy(unit_case.unit['candidate'])
    revised['prompt'] = '如何依据温度变化判断密闭容器中的原测量结论是否还能应用？'
    payloads = []

    def provider(**kwargs):
        payload = json.loads(kwargs['user'])
        payloads.append(payload)
        if 'mode' in payload:
            return {'component': revised}
        assert payload['must_check_redesign'] is True
        return {'accepted': True, 'redesign_ok': True, 'issues': []}

    def rubric(batch):
        unit_case.seen.append('new-rubric')
        return parse_rubric_alignment_response(positive_rubric_alignment_response(batch.to_payload()), batch)

    unit_case.generator._structured = provider
    unit_case.generator._request_rubric_review = rubric
    unit_case.engine._advance_one(unit_case.unit)
    assert set(payloads[0]['repair_task']['editable_fields']) == set(revised)
    assert unit_case.unit['candidate'] == revised and unit_case.unit['status'] == 'approved'
    assert unit_case.seen[-2:] == ['fact', 'new-rubric']
    assert 'rubric_only_lock' not in unit_case.unit['work']


def test_redesign_cannot_unlock_fields_and_keep_the_same_question(unit_case):
    reject_rubric(unit_case)
    # A genuine additional task veto invalidates deletion-only permission.
    unit_case.engine._failed(unit_case.unit, _UnitRejected({'issue': '题设需要重新设计。'}, 'teacher'), 'repair')
    unit_case.unit['work']['mode'] = 'redesign'
    unchanged_question = deepcopy(unit_case.unit['candidate'])
    unchanged_question['expected_answer'] += '只替换答案不能完成重设问项。'
    unit_case.generator._structured = lambda **kwargs: {'component': unchanged_question}
    unit_case.engine._fact_one = lambda unit: pytest.fail('An unchanged redesign must be rejected before review')
    unit_case.engine._advance_one(unit_case.unit)
    assert unit_case.unit['status'] != 'approved'
    assert unit_case.unit['work']['failure_stage'] == 'redesign_unchanged'
