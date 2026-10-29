"""Expanded teaching is unapproved until the unchanged independent gates pass."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_teaching_content import lesson_candidate, point_candidate
from test_teaching_unit_engine_rubric_only import unit_case as unit_case

from adaptive_learning.personalization import models
from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.teaching_repair_evidence import repair_evidence_pack
from adaptive_learning.personalization.teaching_unit_engine import _UnitRejected


def test_expanded_public_and_private_payloads_preserve_complete_long_content(unit_case):
    point = point_candidate(next(iter(unit_case.engine.by_id.values())))
    point['explanation'] = '条件成立时，按原文给出的关系逐步分析，并保留适用范围。' * 80
    point['practice']['prompt'] = '请根据本题实际条件解释适用范围。' * 40
    point['practice']['expected_answer'] = '先确认必要条件，再依据原文关系得出结论。' * 80
    point['flashcard']['back'] = '在所给范围内成立，并保留该原文条件。' * 30
    parsed = teaching._Point.model_validate(point)
    assert parsed.explanation == point['explanation'] and len(parsed.explanation) > 1200
    public = unit_case.engine.bundle.knowledge_points[0].model_copy(
        update={'explanation': parsed.explanation})
    assert models.KnowledgePoint.model_validate(public.model_dump()).explanation == parsed.explanation
    assert parsed.practice.prompt == point['practice']['prompt']
    assert parsed.practice.expected_answer == point['practice']['expected_answer']
    with pytest.raises(ValidationError):
        teaching._ExplanationPatch.model_validate({'title': '关系',
            'explanation': '字' * (models.EXPLANATION_MAX_CHARS + 1), 'source_ids': ['s1']})


def test_writer_budget_and_contract_allow_explanation_not_forced_brevity(unit_case):
    case = unit_case
    seen = []
    case.generator._structured = lambda **kw: seen.append(kw) or {'component': case.unit['candidate']}
    case.engine._write_one(case.unit)
    assert seen[0]['max_tokens'] == 12000
    assert '宁可简洁完整' not in seen[0]['system']
    assert '问项少' not in seen[0]['system']
    assert '适用范围' in seen[0]['system'] and '中间过程' in seen[0]['system']
    assert '完整讲解（1–4000字）' == teaching.TeachingContentGenerator._writer_shapes()['explanation']['explanation']


def source_registry(count):
    return {f's{i}': SimpleNamespace(quote=f'第{i}段独立原文条件：' + '条件必须保留。' * 50,
        page_number=i // 3 + 1, model_dump=lambda i=i: {'quote': f'第{i}段独立原文条件：' + '条件必须保留。' * 50,
                                                    'page_number': i // 3 + 1}) for i in range(count)}


def test_chapter_registry_is_not_silently_reduced_to_eight_ranked_fragments():
    evidence = SimpleNamespace(sources=source_registry(30), fingerprint='immutable')
    pack = repair_evidence_pack(evidence, label='第一段条件', candidate={}, feedback=[], anchors=['s0'])
    assert set(pack['sources']) == set(evidence.sources)
    assert pack['sources']['s29']['quote'] == evidence.sources['s29'].quote
    assert pack['retrieval']['all_registered_sources_included'] is True
    assert pack['retrieval']['registry_size'] == pack['retrieval']['selected'] == 30


def test_source_budget_keeps_whole_neighbors_before_unrelated_ranked_material():
    evidence = SimpleNamespace(sources=source_registry(200), fingerprint='immutable')
    pack = repair_evidence_pack(evidence, label='独立原文', candidate={}, feedback=[], anchors=['s100', 's101', 's102', 's199'])
    assert {'s99', 's100', 's101', 's102', 's103', 's198', 's199'} <= pack['sources'].keys()
    assert sum(len(v['quote']) for v in pack['sources'].values()) <= 50000
    assert all(v['quote'] == evidence.sources[k].quote for k, v in pack['sources'].items())


def explanation_unit(case):
    spec = next(row for row in case.engine._specs() if row['kind'] == 'explanation')
    case.run.initialize_units([spec])
    unit = next(row for row in case.run.units() if row['unit_id'] == spec['unit_id'])
    unit['work'].update(known_failures=3, attempt_nonce='offline-known-rejections')
    return unit


@pytest.mark.parametrize('gate', ['fact', 'teacher'])
def test_source_fallback_uses_literal_draft_and_cannot_bypass_either_review(unit_case, gate):
    case = unit_case
    unit = explanation_unit(case)
    case.engine._write_one = lambda row: pytest.fail('fallback must not make another paid writer call')
    def fact(row):
        case.seen.append('fact')
        if gate == 'fact':
            raise _UnitRejected({'issues': ['原文中的关系仍不支持当前教学目标。']}, 'fact')
        return {'source_entailment': True, 'scope_exceptions': True, 'findings': []}
    case.engine._fact_one = fact
    case.generator._structured = lambda **kw: {'accepted': False, 'redesign_ok': True,
        'issues': ['该原文摘录尚缺必要条件。']}
    case.engine._advance_one(unit)
    saved = next(row for row in case.run.units() if row['unit_id'] == unit['unit_id'])
    assert saved['status'] != 'approved' and 'approval' not in saved['work']
    assert saved['work']['source_fallback_attempted'] is True
    expected = '\n\n'.join(case.engine.evidence.sources[k].quote for k in saved['candidate']['source_ids'])
    assert saved['candidate']['explanation'] == expected
    assert case.engine._source_fallback(saved) is None  # one extractive attempt, not an approval loop


def test_unknown_submission_fence_precedes_source_fallback(unit_case):
    case = unit_case
    unit = explanation_unit(case)
    case.run.submission_blocked = lambda key: True
    case.engine._write_one = lambda row: pytest.fail('unknown submission must not replay')
    case.engine._advance_one(unit)
    saved = next(row for row in case.run.units() if row['unit_id'] == unit['unit_id'])
    assert saved['status'] == 'waiting' and not saved['work'].get('source_fallback_attempted')


def test_production_variety_is_advisory_but_answer_leak_is_still_rejected(unit_case):
    case = unit_case
    point = teaching._Point.model_validate(point_candidate(next(iter(case.engine.by_id.values()))))
    lesson = teaching._Lesson.model_validate(lesson_candidate())
    lesson.checkpoints[0].question = point.practice.prompt
    lesson.checkpoints[0].expected_answer = point.practice.expected_answer
    teaching.TeachingContentGenerator._validate_lesson(lesson, [point], case.engine.evidence, enforce_variety=False)
    bad = deepcopy(lesson)
    bad.checkpoints[0].question = '已给出的答案是' + bad.checkpoints[0].expected_answer
    with pytest.raises(teaching.TeachingContentError, match='泄露答案'):
        teaching.TeachingContentGenerator._validate_lesson(bad, [point], case.engine.evidence, enforce_variety=False)
