"""Source-named fallback remains an unapproved task with full review gates."""
from copy import deepcopy

import pytest
from test_teaching_content import positive_rubric_alignment_response
from test_teaching_unit_engine_rubric_only import unit_case as unit_case

from adaptive_learning.personalization.rubric_alignment import parse_rubric_alignment_response
from adaptive_learning.personalization.teaching_unit_engine import _UnitRejected

GOAL = '位于性染色体上的基因控制的性状在遗传中总是与性别相关联，这种现象称为伴性遗传。'
# Exact continuous p28 OCR excerpt with its interleaved sidebar. The complete
# goal is deliberately not represented as a verbatim repaired source quote.
SOURCE = '它们的基因位于性染色体上，所\n什么是伴性遗传？\n以遗传上总是和性别相关联，这种现象叫做伴性遗传（sex-'


def prepare(case, quote=SOURCE):
    case.unit['work'].update(label=GOAL, known_failures=3, phase='write', mode='repair')
    owner = case.unit['work']['owner_id']
    key = case.engine.evidence.point_sources[owner][0]
    case.engine.evidence.sources[key] = case.engine.evidence.sources[key].model_copy(update={'quote': quote})
    return key


def test_named_term_keeps_complete_goal_conditions_and_exact_answer(unit_case):
    key = prepare(unit_case)
    candidate = unit_case.engine._source_fallback(unit_case.unit)
    assert candidate['prompt'] == '知识点术语回顾：' + GOAL.replace('伴性遗传', '____')
    assert '位于性染色体上的基因' in candidate['prompt']
    assert candidate['expected_answer'] == '伴性遗传' and candidate['expected_answer'] in SOURCE
    assert candidate['source_ids'] == [key] and candidate['rubric'] == ['填入术语“伴性遗传”。']
    assert unit_case.unit['candidate'] != candidate and 'approval' not in unit_case.unit['work']
    unit_case.engine._validate_one(unit_case.unit, candidate)
    assert unit_case.engine._source_fallback(unit_case.unit) is None


@pytest.mark.parametrize('quote', [
    '这种遗传特点，在遗传学上叫做交叉遗传。',
    '伴性遗传是本页的标题。原文基因型XXD、XX不清楚。',
    '这种现象叫做XDXD。',
])
def test_wrong_target_or_no_clear_source_naming_cannot_create_a_quiz(unit_case, quote):
    prepare(unit_case, quote)
    assert unit_case.engine._source_fallback(unit_case.unit) is None
    assert not unit_case.unit['work'].get('source_fallback_attempted')


@pytest.mark.parametrize('fault', ['too_early', 'lost_review', 'other_point', 'unnamed_goal'])
def test_only_known_failure_and_own_named_goal_are_eligible(unit_case, fault):
    key = prepare(unit_case)
    if fault == 'too_early':
        unit_case.unit['work']['known_failures'] = 2
    elif fault == 'lost_review':
        unit_case.unit['work']['lost_review_requires_new_question'] = True
    elif fault == 'other_point':
        unit_case.engine.evidence.sources['foreign'] = unit_case.engine.evidence.sources[key]
        unit_case.engine.evidence.sources[key] = unit_case.engine.evidence.sources[key].model_copy(update={'quote': '没有命名定义。'})
    else:
        unit_case.unit['work']['label'] = '只提到伴性遗传这个名字，没有完整的命名知识目标。'
    assert unit_case.engine._source_fallback(unit_case.unit) is None


@pytest.mark.parametrize('gate', ['fact', 'teacher', 'rubric', 'all_pass'])
def test_fallback_skips_writer_but_requires_every_independent_gate(unit_case, gate):
    case = unit_case
    prepare(case)
    case.engine._write_one = lambda row: pytest.fail('Fallback must not pay for a writer')
    seen = []

    def fact(row):
        seen.append('fact')
        if gate == 'fact':
            raise _UnitRejected({'issues': ['命名句不足以支持该目标的必要条件。']}, 'fact')
        return {'source_entailment': True, 'scope_exceptions': True, 'findings': []}

    def teacher(**kwargs):
        assert kwargs.get('_review') is True
        seen.append('teacher')
        return {'accepted': gate != 'teacher', 'redesign_ok': True,
                'issues': ['该术语回顾仍缺真实必要条件。'] if gate == 'teacher' else []}

    def rubric(batch):
        seen.append('rubric')
        raw = positive_rubric_alignment_response(batch.to_payload())
        if gate == 'rubric':
            raw['items'][0]['rubric_checks'][0].update(required=False, repair_scope='task_revision',
                reason='该问项未完整确定待填术语。', counterexample_answer='另一术语也符合所给的不完整条件。',
                repair='依原文补足必要条件。')
        return parse_rubric_alignment_response(raw, batch)

    case.engine._fact_one = fact
    case.generator._structured = teacher
    case.generator._request_rubric_review = rubric
    case.engine._advance_one(case.unit)
    expected = {'fact': ['fact'], 'teacher': ['fact', 'teacher'],
                'rubric': ['fact', 'teacher', 'rubric'], 'all_pass': ['fact', 'teacher', 'rubric']}
    assert seen == expected[gate]
    assert case.unit['work']['source_fallback_attempted'] is True
    assert (case.unit['status'] == 'approved') == (gate == 'all_pass')
    assert 'published_candidate_hash' not in case.run.load()
    if gate != 'all_pass':
        assert case.unit['work']['failure_stage'] == gate and 'approval' not in case.unit['work']


def test_unknown_submission_fence_precedes_named_fallback(unit_case):
    case = unit_case
    prepare(case)
    before = deepcopy(case.unit['candidate'])
    case.run.submission_blocked = lambda key: True
    case.engine._source_fallback = lambda row: pytest.fail('Unknown must be fenced before draft creation')
    case.engine._advance_one(case.unit)
    assert case.unit['status'] == 'waiting' and case.unit['candidate'] == before
    assert not case.unit['work'].get('source_fallback_attempted')
