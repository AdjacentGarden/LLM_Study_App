"""Production v10 boundary, using scripted clients rather than model-quality claims.

The two F examples are actual published v9 inputs. No test imports approval from
an audit. Controlled verdicts exercise mandatory independent vetoes and exact
approval reuse; they do not certify the semantic quality of the test candidate.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from diagnostic_test_inputs import controlled_application
from test_diagnostic_chapter_generation_cache import MemoryApprovals
from test_diagnostic_v8_quality_patterns import case_input

from adaptive_learning.assessment import item_generation as generation
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote


def source_book():
    labels = ['受控基本概念', '受控条件关系', '受控过程的适用性']
    quotes = [SourceQuote(page_number=1, quote='条件甲成立时过程乙适用；条件甲不成立时过程乙不适用。'),
              SourceQuote(page_number=1, quote='记录可以使用编号标记，但适用性取决于条件甲是否成立。')]
    chapter = ChapterDraft(chapter_id='controlled_v10', order=1, title='Controlled conditional source',
        start_page=1, end_page=1, summary='Explicit offline fixture', knowledge_points=labels,
        source_block_ids=[], evidence=quotes, knowledge_point_evidence={label: quotes for label in labels})
    return BookStructure(title='Controlled v10', summary='No model-quality certification',
                         source_page_count=1, chapters=[chapter])


def response_for(request):
    rows = []
    for point in request['knowledge_points'][:2]:
        rows.append({'knowledge_point_id': point['knowledge_point_id'], 'type': 'choice',
            'prompt': '哪个判断与给定受控原文相符？',
            'options': [point['label'], '受控干扰判断一', '受控干扰判断二', '受控干扰判断三'],
            'correct_index': 0, 'expected_answer': point['label'], 'rubric': [],
            'difficulty': 0, 'estimated_seconds': 40})
    rows.append(controlled_application(request))
    return {'items': rows}


class Writer:
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    def structured(self, *, system, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        assert generation._APPLICATION_CONTRACT in system
        assert request['application_contract'] == generation._APPLICATION_CONTRACT
        response = response_for(request)
        return self.transform(request, response, len(self.calls)) if self.transform else response


class Reviewer:
    def __init__(self, verdict=None):
        self.calls = []
        self.verdict = verdict

    def structured(self, *, system, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        assert request['application_contract'] in system
        return {'reviews': [self.verdict(row, len(self.calls)) if self.verdict else {
            'knowledge_point_id': row['knowledge_point_id'], 'accepted': True, 'issues': []}
            for row in request['items']]}


def subject(writer, reviewer, retries=0, **kwargs):
    return generation.DiagnosticItemGenerator(writer, review_client=reviewer,
        allow_grounded_answer_rewrite=True, validation_retries=retries, **kwargs)


def test_structured_task_is_rendered_into_every_choice_then_reviewed_and_published():
    writer, reviewer, cache = Writer(), Reviewer(), MemoryApprovals()
    value = source_book()
    bank = subject(writer, reviewer).generate(value, chapter_cache=cache, cache_scope='controlled')
    application = bank[2]
    assert application.prompt == ('记录甲：受控输入条件甲成立；记录乙：受控输入条件甲不成立。'
        '依据原文，两条记录的受控过程的适用性如何对应？')
    assert set(application.options) == {
        '甲：适用；乙：适用', '甲：适用；乙：不适用', '甲：不适用；乙：适用', '甲：不适用；乙：不适用'}
    assert application.expected_answer == '甲：适用；乙：不适用'
    assert application.options[int(application.correct_option_ids[0])] == application.expected_answer
    checked = reviewer.calls[0]['items'][2]
    assert checked['application_task'] == controlled_application(writer.calls[0])['application_task']
    assert set(checked['options']) == set(application.options)
    assert checked['options'][checked['correct_index']] == application.expected_answer
    assert 'application_task' not in application.model_dump(mode='json')
    assert 'application_source_refs' not in application.model_dump(mode='json')
    assert writer.calls[0]['application_source_refs'] == reviewer.calls[0]['application_source_refs']
    assert len(writer.calls) == len(reviewer.calls) == 1
    # Existing private durable storage keeps the unchanged public schema, under
    # exact source/protocol/reviewer identities. No audit is read on a hit.
    assert subject(writer, reviewer).generate(value, chapter_cache=cache, cache_scope='controlled') == bank
    assert len(writer.calls) == len(reviewer.calls) == 1


@pytest.mark.parametrize('mapping,answer', [(['a', 'a'], '甲：适用；乙：适用'),
    (['a', 'b'], '甲：适用；乙：不适用'), (['b', 'a'], '甲：不适用；乙：适用'),
    (['b', 'b'], '甲：不适用；乙：不适用')])
def test_mapping_is_a_candidate_answer_not_a_fixed_correct_position(mapping, answer):
    def transform(request, response, call):
        response['items'][2]['application_task']['mapping'] = mapping
        return response
    writer, reviewer = Writer(transform), Reviewer()
    bank = subject(writer, reviewer).generate(source_book())
    assert bank[2].expected_answer == answer
    assert bank[2].options[int(bank[2].correct_option_ids[0])] == answer
    assert len(reviewer.calls) == 1


@pytest.mark.parametrize('defect', ['absent', 'unknown_ref', 'sibling_ref', 'duplicate_ref',
    'record_ids', 'duplicate_conditions', 'duplicate_outcomes', 'mapping', 'approval_spoof',
    'free_prompt', 'free_options', 'free_answer'])
def test_malformed_or_divergent_tasks_cannot_reach_review_or_private_approval(defect):
    def transform(request, response, call):
        row = response['items'][2]
        task = row['application_task']
        if defect == 'absent':
            del row['application_task']
        elif defect == 'unknown_ref':
            task['source_refs'] = ['invented_ref']
        elif defect == 'sibling_ref':
            sibling = request['knowledge_points'][0]['knowledge_point_id']
            task['source_refs'] = [request['application_source_refs'][sibling][0]['ref']]
        elif defect == 'duplicate_ref':
            task['source_refs'] *= 2
        elif defect == 'record_ids':
            task['records'].reverse()
        elif defect == 'duplicate_conditions':
            task['records'][1]['conditions'] = task['records'][0]['conditions']
        elif defect == 'duplicate_outcomes':
            task['outcomes'] = ['同一结果', '同一结果']
        elif defect == 'mapping':
            task['mapping'][1] = 'c'
        elif defect == 'approval_spoof':
            task['accepted'] = True
        elif defect == 'free_prompt':
            row['prompt'] = '下面哪个原文事实正确？'
        elif defect == 'free_options':
            row['options'] = ['事实甲', '事实乙', '事实丙', '事实丁']
        else:
            row['expected_answer'] = '模型自行指定的另一答案'
        return response
    writer, reviewer, cache = Writer(transform), Reviewer(), MemoryApprovals()
    with pytest.raises(generation.DiagnosticGenerationError, match='batch validation failed'):
        subject(writer, reviewer).generate(source_book(), chapter_cache=cache, cache_scope='controlled')
    assert len(writer.calls) == 1 and reviewer.calls == []
    assert cache.outcomes == ['rejected'] and not cache.records


@pytest.fixture(params=[0, 1], ids=['F-ordinal6-shell', 'F-ordinal18-incomplete-threshold'])
def observed_f(request):
    return json.loads((Path(__file__).parent / 'fixtures' /
        'diagnostic_v9_structured_application_cases.json').read_text())['cases'][request.param]


def test_actual_published_free_form_f_candidates_are_not_v10_tasks(observed_f):
    value, response = case_input(observed_f)
    class ArchivedWriter:
        calls = 0
        def structured(self, **kwargs):
            self.calls += 1
            return deepcopy(response)
    writer, reviewer, cache = ArchivedWriter(), Reviewer(), MemoryApprovals()
    with pytest.raises(generation.DiagnosticGenerationError, match='_ApplicationTask'):
        subject(writer, reviewer).generate(value, chapter_cache=cache, cache_scope='F-regression')
    assert writer.calls == 1 and reviewer.calls == [] and cache.outcomes == ['rejected']


@pytest.mark.parametrize('failure', ['wrong_mapping', 'two_fact_shell', 'unsupported_mechanism', 'incomplete_threshold'])
def test_structural_completeness_never_overrides_independent_semantic_veto(failure):
    def transform(request, response, call):
        task = response['items'][2]['application_task']
        if failure == 'wrong_mapping':
            task['mapping'].reverse()
        elif failure == 'two_fact_shell':
            task['records'] = [{'id': 'r1', 'conditions': '条件甲成立时过程乙适用'},
                               {'id': 'r2', 'conditions': '条件甲不成立时过程乙不适用'}]
            task['outcomes'] = ['说法正确', '说法错误']
            task['decision'] = '所述教材事实真伪'
            task['mapping'] = ['a', 'a']
        elif failure == 'unsupported_mechanism':
            task['records'][0]['conditions'] = '题设给定：未知装置会提供额外能量'
        else:
            task['records'][0]['conditions'] = '某分离过程正逐步发生，尚未说明完成'
            task['outcomes'] = ['过程已形成', '过程尚未形成']
        response['items'][2]['source_supported'] = True
        return response
    def veto(row, call):
        accepted = row['cognitive_target']['id'] != 'source_application'
        return {'knowledge_point_id': row['knowledge_point_id'], 'accepted': accepted,
                'issues': [] if accepted else [f'Controlled independent veto: {failure}']}
    writer, reviewer, cache = Writer(transform), Reviewer(veto), MemoryApprovals()
    with pytest.raises(generation.DiagnosticGenerationError, match=failure):
        subject(writer, reviewer).generate(source_book(), chapter_cache=cache, cache_scope='controlled')
    assert len(writer.calls) == len(reviewer.calls) == 1
    assert cache.outcomes == ['rejected'] and not cache.records
    assert 'source_supported' not in reviewer.calls[0]['items'][2]
    contract = reviewer.calls[0]['application_contract']
    assert '未完成状态当已完成' in contract
    assert '仅用两条原文事实作真假分类' in contract


def test_f18_progressive_condition_remains_visible_to_the_independent_judge():
    cases = json.loads((Path(__file__).parent / 'fixtures' /
        'diagnostic_v9_structured_application_cases.json').read_text())['cases']
    case = cases[1]
    value, _ = case_input(case)
    def transform(request, response, call):
        task = response['items'][2]['application_task']
        task.update(records=[{'id': 'r1', 'conditions': '同种地雀两种群正逐步出现生殖隔离'},
                             {'id': 'r2', 'conditions': '同种地雀两种群生殖隔离已经形成'}],
                    decision='物种形成状态', outcomes=['已形成不同物种', '仍未说明物种形成'], mapping=['a', 'a'])
        return response
    def verdict(row, call):
        issues = []
        if row['cognitive_target']['id'] == 'source_application':
            assert '正逐步出现' in row['application_task']['records'][0]['conditions']
            assert row['application_task']['mapping'] == ['a', 'a']
            issues = ['逐步出现未说明隔离已经形成，不能套用一旦形成的完成阈值。']
        return {'knowledge_point_id': row['knowledge_point_id'], 'accepted': not issues, 'issues': issues}
    reviewer, writer = Reviewer(verdict), Writer(transform)
    with pytest.raises(generation.DiagnosticGenerationError, match='完成阈值'):
        subject(writer, reviewer).generate(value)
    assert '生殖隔离一旦形成' in json.dumps(reviewer.calls[0]['application_source_refs'], ensure_ascii=False)
    assert len(writer.calls) == len(reviewer.calls) == 1


@pytest.mark.parametrize('change', [None, 'source_ref', 'record', 'mapping', 'outcome'])
def test_accepted_task_is_exactly_preserved_and_cannot_silently_change(change):
    writer_requests, calls = [], []
    def transform(request, response, call):
        writer_requests.append(request)
        if call > 1:
            assert len(request['preserve_accepted_items']) == 2
            approved = next(row for row in request['preserve_accepted_items'] if 'application_task' in row)
            response['items'][2] = deepcopy(approved)
            task = response['items'][2]['application_task']
            if change:
                # Re-render the changed candidate, so rejection is due to exact
                # preservation rather than inconsistent redundant public fields.
                for key in ('prompt', 'options', 'correct_index', 'expected_answer', 'rubric'):
                    response['items'][2].pop(key, None)
                if change == 'source_ref':
                    point = request['knowledge_points'][2]['knowledge_point_id']
                    task['source_refs'] = [request['application_source_refs'][point][1]['ref']]
                elif change == 'record':
                    task['records'][0]['conditions'] = '编号甲明确满足条件甲'
                elif change == 'mapping':
                    task['mapping'].reverse()
                else:
                    task['outcomes'][0] = '过程乙适用'
        return response
    def verdict(row, call):
        accepted = call > 1 or row['cognitive_target']['id'] != 'relation_understanding'
        calls.append((call, row['cognitive_target']['id']))
        return {'knowledge_point_id': row['knowledge_point_id'], 'accepted': accepted,
                'issues': [] if accepted else ['Controlled relationship repair required']}
    writer, reviewer = Writer(transform), Reviewer(verdict)
    if change:
        with pytest.raises(generation.DiagnosticGenerationError, match='changed previously accepted'):
            subject(writer, reviewer, retries=1).generate(source_book())
        assert len(reviewer.calls) == 1
    else:
        assert len(subject(writer, reviewer, retries=1).generate(source_book())) == 3
        assert len(reviewer.calls) == 2
        assert [row['cognitive_target']['id'] for row in reviewer.calls[1]['items']] == ['relation_understanding']
    assert len(writer.calls) == 2


def test_full_task_and_immutable_source_directory_bind_the_approval_hash():
    writer, reviewer, events = Writer(), Reviewer(), []
    instance = subject(writer, reviewer, audit_callback=events.append)
    instance.generate(source_book())
    source = writer.calls[0]
    candidate = next(event['items'][2] for event in events if event['event'] == 'candidate')
    chapter = source_book().chapters[0]
    by_id = {point[0]: point for point in instance._select_points(chapter)}
    item = instance._validate_item(generation.ItemDraft.model_validate(candidate), chapter, by_id, allow_rewrite=True)
    original = instance._approval_key(source, item, candidate['application_task'])
    for field in ('source_refs', 'records', 'decision', 'outcomes', 'mapping'):
        changed = deepcopy(candidate['application_task'])
        changed[field] = ['different']
        assert instance._approval_key(source, item, changed) != original
    changed_source = deepcopy(source)
    point = item.knowledge_point_ids[0]
    changed_source['application_source_refs'][point][0]['evidence']['quote'] += ' changed source condition'
    assert instance._approval_key(changed_source, item, candidate['application_task']) != original
    assert generation.DIAGNOSTIC_BANK_VERSION.startswith('grounded-independent-review-v10+')


def test_legacy_label_mode_keeps_non_application_answers_but_never_overwrites_task_mapping():
    writer, reviewer = Writer(), Reviewer()
    instance = generation.DiagnosticItemGenerator(writer, review_client=reviewer, validation_retries=0)
    bank = instance.generate(source_book())
    assert bank[0].expected_answer == source_book().chapters[0].knowledge_points[0]
    assert bank[1].expected_answer == source_book().chapters[0].knowledge_points[1]
    assert bank[2].expected_answer == '甲：适用；乙：不适用'
    assert bank[2].expected_answer != source_book().chapters[0].knowledge_points[2]
    for rewrite in (True, False):
        system = generation._item_system(rewrite)
        assert '以下输出字段规则仅适用于非source_application项' in system
        assert '应用项的正确文字由task的mapping对应程序配对选项，永远不复制label作答案' in system
        assert ('correct_index 指向的选项文字必须与输入的 label 逐字相同' in system) is not rewrite


def test_negative_given_conditions_are_not_mistaken_for_a_negative_question():
    def transform(request, response, call):
        response['items'][2]['application_task']['records'][1]['conditions'] = '不符合条件甲的明确受控输入'
        return response
    writer, reviewer = Writer(transform), Reviewer()
    bank = subject(writer, reviewer).generate(source_book())
    assert '不符合条件甲' in bank[2].prompt and bank[2].prompt.endswith('如何对应？')
    assert len(reviewer.calls) == 1


@pytest.mark.parametrize('accepted', [True, False])
def test_f6_actual_source_can_supply_record_comparison_but_requires_fresh_independent_review(accepted):
    case = json.loads((Path(__file__).parent / 'fixtures' /
        'diagnostic_v9_structured_application_cases.json').read_text())['cases'][0]
    value, _ = case_input(case)
    def transform(request, response, call):
        task = response['items'][2]['application_task']
        task.update(records=[{'id': 'r1', 'conditions': '记录提供原料、能量和酶，暂不提供模板'},
                             {'id': 'r2', 'conditions': '记录提供模板、原料、能量和酶'}],
                    decision='列明基本条件的齐备情况',
                    outcomes=['尚缺原文列明的基本条件', '原文列明的基本条件齐备'], mapping=['a', 'b'])
        return response
    def verdict(row, call):
        issues = []
        if row['cognitive_target']['id'] == 'source_application':
            assert '原料、能量和酶' in row['prompt']
            assert row['expected_answer'] == '甲：尚缺原文列明的基本条件；乙：原文列明的基本条件齐备'
            assert '复制能否成功' not in row['application_task']['decision']
            if not accepted:
                issues = ['Controlled independent judge may still reject this comparison as insufficient application.']
        return {'knowledge_point_id': row['knowledge_point_id'], 'accepted': not issues, 'issues': issues}
    writer, reviewer = Writer(transform), Reviewer(verdict)
    if accepted:
        bank = subject(writer, reviewer).generate(value)
        assert bank[2].prompt != case['candidate']['prompt']
        assert '记录甲' in bank[2].prompt and '记录乙' in bank[2].prompt
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match='insufficient application'):
            subject(writer, reviewer).generate(value)
    assert len(writer.calls) == len(reviewer.calls) == 1
    assert '复制需' in json.dumps(reviewer.calls[0]['application_source_refs'], ensure_ascii=False)


def test_actual_grounded_prompt_retains_design_guidance_and_separates_task_answer_rules():
    system = generation._item_system(True)
    assert '设计顺序：先按cognitive_target确定最小任务' in system
    assert 'label 仅用于标识待测知识点，不能当作事实依据' in system
    assert '正确选项必须与 expected_answer 逐字相同' in system
    assert '应用项的正确文字由task的mapping对应程序配对选项' in system
    assert '非应用项继续使用原有prompt/options/expected_answer格式' in system


def test_application_requires_an_independent_judge_even_in_unverified_legacy_mode():
    writer = Writer()
    instance = generation.DiagnosticItemGenerator(writer, verify_items=False,
        allow_grounded_answer_rewrite=False, validation_retries=2)
    with pytest.raises(generation.DiagnosticGenerationError, match='require independent semantic review'):
        instance.generate(source_book())
    assert writer.calls == []


def test_legacy_repair_payload_distinguishes_task_mapping_from_non_application_labels():
    def transform(request, response, call):
        assert request['choice_length_rule']['applies_to'] == 'non-source_application'
        assert request['choice_length_rule']['correct_answer_source'] == 'label'
        if call == 1:
            response['items'][2]['application_task']['source_refs'] = ['unknown']
        else:
            assert '非source_application项的正确选项必须逐字复制输入label' in request['validation_feedback']
            assert '答案仅由mapping对应配对选项，不能复制label' in request['validation_feedback']
        return response
    writer, reviewer = Writer(transform), Reviewer()
    bank = generation.DiagnosticItemGenerator(writer, review_client=reviewer, validation_retries=1).generate(source_book())
    assert bank[2].expected_answer == '甲：适用；乙：不适用'
    assert len(writer.calls) == 2 and len(reviewer.calls) == 1
