"""Repair permissions cannot rewrite accepted facts or leave a bad premise intact.

Offline controls model already-materialized independent verdicts, not source truth.
"""
from copy import deepcopy

import pytest
from test_teaching_content import course, lesson_candidate, point_candidate
from test_teaching_precise_citations import teacher_for

from adaptive_learning.personalization import teaching_content as teaching


@pytest.fixture
def state():
    bundle, _, evidence = course(1)
    generator = teaching.TeachingContentGenerator(object())
    point = teaching._Point.model_validate(point_candidate(generator._inputs(bundle, evidence)[0]))
    # Actual F failure pattern: rubric repair must not erase the correct
    # chromosome-halving condition from an independently accepted answer.
    point.practice.prompt = '某同学说只要是细胞分裂就一定是减数分裂，请依据概念判断并说明理由。'
    point.practice.expected_answer = ('这种说法不正确。减数分裂是进行有性生殖的生物产生成熟生殖细胞时的细胞分裂，'
                                      '成熟生殖细胞中的染色体数目比原始生殖细胞减少一半，不能只看细胞数目变化。')
    point.practice.rubric = ['指出该说法不正确', '说明减数分裂的适用范围', '另述染色体数目减半']
    lesson = teaching._Lesson.model_validate(lesson_candidate())
    proof = {'source_entailment': True, 'scope_exceptions': True, 'source_ids': point.source_ids,
             'findings': [], 'scope_checks': [], 'rationale': '受控已物化独立结果'}
    verdict = {'accepted': True, 'source_entailment': True, 'scope_exceptions': True,
               'issues': [], 'support_summary': '该受控独立回执仅用于验证修复权限与字段冻结机制。'}
    fact = teaching._FactReview.model_validate({'accepted': True, 'issues': [], 'points': [{**verdict, 'point_id': point.point_id,
        'failed_components': [], 'components': {name: deepcopy(proof) for name in ('explanation', 'flashcard', 'practice')}}],
        'lesson': {**verdict, 'components': {name: deepcopy(proof) for name in generator._lesson_components(lesson)}}})
    teacher = teacher_for(point)
    teacher._pre_rubric_review = teacher.model_dump()
    teacher.points[0].practice_ok = False
    teacher.points[0].issues = [f'practice_{point.practice.item_id} 评分要求超出题目，需要移除额外评分要求。']
    teacher.accepted = False
    teacher._rubric_alignment = {'items': [{'item_id': point.practice.item_id, 'rubric_checks': [
        {'required': False, 'repair_scope': 'rubric_only'}]}]}
    return generator, point, lesson, fact, teacher


def plan_for(state):
    generator, point, lesson, fact, teacher = state
    return generator._build_repair_plan(fact, teacher, [point], lesson)


def test_verified_rubric_only_repair_freezes_actual_f_answer_prompt_identity_and_sources(state):
    generator, point, lesson, _, _ = state
    plan = plan_for(state)
    assert plan.requirements.rubric_only_points == {point.point_id}
    contract = generator._repair_contract({point.point_id}, {point.point_id: point}, plan.allowed_points,
        lesson, plan.allowed_lesson, False, requirements=plan.requirements)
    assert set(contract['output_shape']['points'][0]['practice']) == {'rubric'}
    response = {'points': [{'point_id': point.point_id, 'practice': {'rubric': point.practice.rubric[:2]}}], 'lesson': None}
    merged = generator._materialize_generation_patch(response, contract, {point.point_id: point}, None).points[0]
    assert merged.practice.rubric == point.practice.rubric[:2]
    assert {key: value for key, value in merged.practice.model_dump().items() if key != 'rubric'} == {
        key: value for key, value in point.practice.model_dump().items() if key != 'rubric'}
    assert '减少一半' in merged.practice.expected_answer
    generator._validate_point_repair(merged, point, plan.requirements)
    response['points'][0]['practice']['expected_answer'] = '被错误删去染色体减半条件的另一个答案。'
    with pytest.raises(teaching.TeachingContentError, match='只能返回rubric'):
        generator._materialize_generation_patch(response, contract, {point.point_id: point}, None)
    merged.practice.expected_answer = response['points'][0]['practice']['expected_answer']
    with pytest.raises(teaching.TeachingContentError, match='仅评分修复不得'):
        generator._validate_point_repair(merged, point, plan.requirements)


@pytest.mark.parametrize('veto', ['fact', 'fact_unlocated', 'original_teacher', 'unknown_issue', 'task_revision', 'missing_snapshot'])
def test_rubric_only_never_masks_other_factual_task_or_unlocated_rejections(state, veto):
    _, _, _, fact, teacher = state
    if veto == 'fact':
        fact.points[0].components.practice.source_entailment = False
        fact.points[0].accepted = fact.points[0].source_entailment = False
        fact.points[0].failed_components = ['practice']
        fact.accepted = False
    elif veto == 'fact_unlocated':
        fact.points[0].accepted = fact.accepted = False
        fact.points[0].issues = ['该知识点仍有一条尚未定位到具体组件的事实问题。']
    elif veto == 'original_teacher':
        teacher._pre_rubric_review['points'][0]['practice_ok'] = False
        teacher._pre_rubric_review['points'][0]['issues'] = ['练习的事实前提本身需要修正。']
    elif veto == 'unknown_issue':
        teacher._pre_rubric_review['issues'] = ['这是一条无法可靠定位的独立事实问题。']
        teacher.issues = list(teacher._pre_rubric_review['issues'])
    elif veto == 'task_revision':
        # F chicken task ambiguity cannot be fixed by deleting rubric alone.
        teacher._rubric_alignment['items'][0]['rubric_checks'][0]['repair_scope'] = 'task_revision'
    else:
        teacher._pre_rubric_review = {}
    if veto == 'unknown_issue':
        with pytest.raises(teaching._TeachingReviewFormatError, match='未定位'):
            plan_for(state)
        return
    assert not plan_for(state).requirements.rubric_only_points


def test_other_precisely_located_component_repair_is_unioned_without_freeing_practice_answer(state):
    _, point, _, fact, teacher = state
    teacher._pre_rubric_review['points'][0]['explanation_ok'] = False
    teacher._pre_rubric_review['points'][0]['issues'] = [f'explanation（{point.point_id}）：该讲解需要补充分量条件。']
    teacher.points[0].explanation_ok = False
    teacher.points[0].issues += teacher._pre_rubric_review['points'][0]['issues']
    plan = plan_for(state)
    assert plan.allowed_points[point.point_id] == {'practice', 'explanation'}
    assert plan.requirements.rubric_only_points == {point.point_id}
    assert fact.points[0].components.practice.source_entailment


@pytest.mark.parametrize('kind,required', [('contradiction', True), ('invalid_inference', True), ('unsupported_claim', False)])
def test_only_structurally_located_bad_question_findings_force_the_question_itself_to_change(state, kind, required):
    _, _, lesson, fact, teacher = state
    proof = fact.lesson.components['example_1']
    proof.source_entailment = False
    proof.findings = [teaching._ComponentFinding(kind=kind, candidate_field='problem',
        candidate_quote=lesson.worked_examples[0].problem[:25],
        issue='这是已由物化校验定位到题设本身的受控事实或推理否决。', evidence_source_ids=proof.source_ids)]
    fact.lesson.accepted = fact.lesson.source_entailment = fact.accepted = False
    plan = plan_for(state)
    assert (plan.requirements.lesson_question_fields.get('example_1') == 'problem') is required
    assert 'example_1' in plan.allowed_lesson
    assert teacher.points[0].practice_ok is False


def test_actual_f_fruitfly_bad_premise_cannot_be_repaired_only_by_changing_solution_steps(state):
    generator, point, lesson, _, _ = state
    lesson.worked_examples[0].problem = ('一只红眼雌蝇与白眼雄蝇交配，子代雌蝇全红眼、雄蝇全白眼，'
                                        '请说明子代基因型并判断这个结果。')
    baseline = {'points': [point.model_dump()], 'lesson': lesson.model_dump()}
    requirements = teaching._RepairRequirements(exact_lesson={'example_1'},
        lesson_question_fields={'example_1': 'problem'})
    lesson.worked_examples[0].steps[0] += '先重新列出全部可能配子，不能按子代性别指定亲本配子。'
    with pytest.raises(teaching._LessonRepairUnchangedError, match='题设或问项'):
        generator._validate_candidate_repair([point], lesson, baseline, requirements)
    lesson.worked_examples[0].problem = lesson.worked_examples[0].problem.replace('子代雌蝇全红眼、雄蝇全白眼，',
        '现需重新判断各类子代，')
    generator._validate_candidate_repair([point], lesson, baseline, requirements)
    # This only accepts a real textual edit; independent factual, teacher and
    # rubric reviews must still decide whether the revised exercise is valid.


def test_teacher_explicit_question_change_must_locate_a_rejected_category(state):
    _, _, _, _, teacher = state
    teacher.lesson.required_question_edits = ['example_1']
    with pytest.raises(teaching.TeachingContentError, match='真实被拒绝|通过类别矛盾'):
        plan_for(state)
    teacher.lesson.examples_ok = False
    plan = plan_for(state)
    assert plan.requirements.lesson_question_fields == {'example_1': 'problem'}
