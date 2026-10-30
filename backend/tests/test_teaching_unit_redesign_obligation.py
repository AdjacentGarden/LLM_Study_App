"""Lost-review recovery retains its dedicated, still-unapproved obligation."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_learning.personalization.teaching_content import TeachingContentGenerator
from adaptive_learning.personalization.teaching_unit_engine import UnitEngine, _question_shape


@pytest.fixture
def case():
    recorded = json.loads((Path(__file__).parent / 'fixtures/teaching_h_example1_redesign_obligation.json').read_text())
    unit = {'unit_id': recorded['unit_id'], 'candidate': deepcopy(recorded['candidate']),
            'attempts': 29, 'status': 'pending', 'work': {
                'kind': 'example_1', 'owner_id': None, 'focus_ids': [],
                'label': recorded['knowledge_goal'], 'mode': recorded['mode'], 'revision': 7,
                'attempt_nonce': 'controlled-offline', 'redesign_pending_review': True,
                'lost_review_requires_new_question': True,
                'rejected_design': deepcopy(recorded['rejected_design']),
                'feedback': deepcopy(recorded['feedback']), 'phase': 'review',
            }}
    calls = []

    def structured(**kwargs):
        payload = json.loads(kwargs['user'])
        calls.append((bool(kwargs.get('_review')), payload))
        if kwargs.get('_review'):
            return {'accepted': True, 'redesign_ok': True, 'issues': []}
        return {'component': deepcopy(unit['candidate'])}

    generator = TeachingContentGenerator(None, review_identity={'provider': 'controlled', 'model': 'offline'})
    generator._structured = structured
    engine = UnitEngine.__new__(UnitEngine)
    engine.g, engine.by_id = generator, {}
    engine.chapter = SimpleNamespace(title='受控离线回归')
    sources = {source_id: SimpleNamespace(model_dump=lambda: {'quote': '受控原文边界'})
               for source_id in unit['candidate']['source_ids']}
    engine.evidence = SimpleNamespace(sources=sources, fingerprint='controlled-evidence')
    engine._pack = lambda value: {'sources': {}}
    engine._fact_one = lambda value: {'source_entailment': True, 'scope_exceptions': True, 'findings': []}

    def write(value, *, status, candidate=None, delay=0):
        value['status'] = status
        if candidate is not None:
            value['candidate'] = candidate

    engine.run = SimpleNamespace(identity={'epoch': 'initial'}, submission_blocked=lambda value: False,
                                 write_unit=write)
    return SimpleNamespace(engine=engine, unit=unit, calls=calls)


def test_actual_changed_surface_keeps_writer_and_teacher_redesign_obligation(case):
    before = deepcopy(case.unit)
    assert _question_shape(case.unit['candidate']) != _question_shape(case.unit['work']['rejected_design'])
    case.engine._write_one(case.unit)
    case.engine._review_one(case.unit)
    writer, teacher = case.calls
    assert not writer[0] and teacher[0]
    assert writer[1]['mode'] == 'repair'
    assert writer[1]['repair_task']['must_redesign_question'] is True
    assert teacher[1]['must_check_redesign'] is True
    assert writer[1]['repair_task']['failure'] == before['work']['feedback']
    assert writer[1]['previous_candidate'] == before['candidate']
    assert case.unit['candidate'] == before['candidate']


def test_native_accepted_review_clears_obligation_before_later_repair(case):
    case.engine._advance_one(case.unit)
    assert case.calls[0][0]
    assert case.calls[0][1]['must_check_redesign'] is True
    assert case.unit['status'] == 'approved'
    assert case.unit['work']['redesign_pending_review'] is False
    case.engine._write_one(case.unit)
    assert case.calls[-1][1]['repair_task']['must_redesign_question'] is False


def test_ordinary_redesign_mode_does_not_require_new_question(case):
    case.unit['work'].update(mode='redesign', redesign_pending_review=False,
                            lost_review_requires_new_question=False)
    case.engine._write_one(case.unit)
    assert case.calls[-1][1]['repair_task']['must_redesign_question'] is False


def test_ordinary_repair_without_pending_obligation_does_not_require_redesign(case):
    case.unit['work'].pop('redesign_pending_review')
    case.unit['work']['lost_review_requires_new_question'] = False
    case.engine._write_one(case.unit)
    assert case.calls[-1][1]['repair_task']['must_redesign_question'] is False
