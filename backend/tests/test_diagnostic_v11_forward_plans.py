"""Archived v11 G source/wire inputs exercise old closed plans and independent vetoes.

Scripted judges only test mechanical boundaries and rejection persistence, never
certify semantic quality. Archived v10 replays cannot enter production approvals.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from diagnostic_test_inputs import ArchivedV10StructuredGenerator, ArchivedV11PlanGenerator
from test_diagnostic_chapter_generation_cache import MemoryApprovals
from test_diagnostic_v6_repair import structure_for

from adaptive_learning.assessment import item_generation as generation
from adaptive_learning.llm.client import LLMError, LLMNotSubmittedError, LLMTimeoutError


@pytest.fixture
def recorded():
    return json.loads((Path(__file__).parent / 'fixtures' /
        'diagnostic_v10_forward_plan_regression.json').read_text())['cases']


def plan_for(request, index):
    point = request['knowledge_points'][2]
    evidence = request['application_source_refs'][point['knowledge_point_id']][0]
    basis = {'source_ref': evidence['ref'], 'quote': evidence['evidence']['quote'],
             'input_role': '来源明确给定的条件', 'output_role': '原文正向结果'}
    if index == 0:
        basis.update(input_role='原文列明的基本条件', output_role='原文条件齐备情况')
        return {'mode': 'required_conditions', 'basis': basis,
            'required': ['模板', '原料', '能量', '酶'],
            'records': [{'id': 'r1', 'available': [0, 1, 2, 3]},
                        {'id': 'r2', 'available': [0, 1, 3]}]}
    if index == 1:
        basis.update(input_role='减数分裂形成配子时的染色体组合', output_role='非等位基因的组合情况')
        return {'mode': 'forward_instances', 'basis': basis,
            'input_condition': '在生物体通过减数分裂形成配子时，随着非同源染色体的自由组合',
            'positive_result': '非等位基因也自由组合', 'alternative_result': '非等位基因不随之自由组合',
            'instances': ['实例甲', '实例乙']}
    basis.update(input_role='基因提取和跨生物转入的操作', output_role='遗传性状被定向改造的对象')
    return {'mode': 'role_transfer', 'basis': basis,
        'record_template': '将生物{input}的基因提取修饰后放入生物{output}细胞',
        'bindings': [{'id': 'r1', 'input': 'a', 'output': 'b'},
                     {'id': 'r2', 'input': 'b', 'output': 'a'}], 'result_role': 'output'}


class Writer:
    def __init__(self, case, index, transform=None):
        self.case, self.index, self.transform = case, index, transform
        self.calls = []

    def structured(self, *, system, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        assert request['application_plan_contract'] in system
        response = deepcopy(self.case['attempts'][0]['generation_response'])
        point = request['knowledge_points'][2]
        response['items'][2] = {'knowledge_point_id': point['knowledge_point_id'], 'type': 'choice',
            'difficulty': 0, 'estimated_seconds': 45, 'application_task': plan_for(request, self.index)}
        return self.transform(request, response, len(self.calls)) if self.transform else response


class Reviewer:
    def __init__(self, accepted=True, verdict=None):
        self.calls, self.accepted, self.verdict = [], accepted, verdict

    def structured(self, *, system, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        assert request['application_plan_contract'] in system
        return {'reviews': [self.verdict(row, len(self.calls)) if self.verdict else {
            'knowledge_point_id': row['knowledge_point_id'],
            'accepted': self.accepted or row['cognitive_target']['id'] != 'source_application',
            'issues': [] if self.accepted or row['cognitive_target']['id'] != 'source_application'
                else ['Controlled independent semantic veto; structure alone is not approval.']}
            for row in request['items']]}


def subject(writer, reviewer, retries=0, **kwargs):
    return ArchivedV11PlanGenerator(writer, review_client=reviewer,
        allow_grounded_answer_rewrite=True, validation_retries=retries, **kwargs)


@pytest.mark.parametrize('index', [0, 1, 2], ids=['G-DNA-condition-check', 'G-forward-recombination', 'G-object-transfer'])
@pytest.mark.parametrize('accepted', [True, False])
def test_actual_g_sources_support_closed_design_paths_but_only_a_returned_review_can_approve(recorded, index, accepted):
    case = recorded[index]
    writer, reviewer, cache = Writer(case, index), Reviewer(accepted), MemoryApprovals()
    instance = subject(writer, reviewer)
    if accepted:
        bank = instance.generate(structure_for(case['source']), chapter_cache=cache, cache_scope='G-offline')
        app = bank[2]
        checked = reviewer.calls[0]['items'][2]
        assert '记录甲：' in app.prompt and '记录乙：' in app.prompt
        assert set(checked['options']) == set(app.options)
        assert app.options[int(app.correct_option_ids[0])] == app.expected_answer
        assert len(app.prompt) <= 140 and all(len(option) <= 180 for option in app.options)
        assert 'application_task' not in app.model_dump()
        task = checked['application_task']
        assert task['basis']['quote'] in case['source']['knowledge_points'][2]['evidence'][0]['quote']
        if index == 0:
            assert app.expected_answer == '甲：原文列明条件齐备；乙：仍缺原文列明条件'
            assert '正常进行' not in app.prompt + ''.join(app.options)
        elif index == 1:
            assert task['derivation']['mapping'] == ['a', 'a']
            assert app.expected_answer == '甲：非等位基因也自由组合；乙：非等位基因也自由组合'
            assert all('非同源染色体的自由组合' in row['conditions'] for row in task['derivation']['records'])
        else:
            assert task['derivation']['mapping'] == ['b', 'a']
            assert app.expected_answer == '甲：对象乙；乙：对象甲'
            assert '基因工程' not in app.options
        assert len([row for row in cache.records.values() if row['status'] == 'approved']) == 1
        assert instance.generate(structure_for(case['source']), chapter_cache=cache, cache_scope='G-offline') == bank
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match='independent semantic veto'):
            instance.generate(structure_for(case['source']), chapter_cache=cache, cache_scope='G-offline')
        assert cache.outcomes == ['rejected'] and not cache.records
    assert len(writer.calls) == len(reviewer.calls) == 1


@pytest.mark.parametrize('index', [0, 1, 2])
def test_actual_g_old_free_mapping_tasks_are_not_accepted_as_forward_plans(recorded, index):
    case = recorded[index]
    class OldWriter:
        calls = 0
        def structured(self, **kwargs):
            self.calls += 1
            return deepcopy(case['attempts'][-1]['generation_response'])
    writer, reviewer, cache = OldWriter(), Reviewer(), MemoryApprovals()
    with pytest.raises(generation.DiagnosticGenerationError, match='batch validation failed'):
        subject(writer, reviewer).generate(structure_for(case['source']), chapter_cache=cache, cache_scope='G-archive')
    assert writer.calls == 1 and reviewer.calls == [] and cache.outcomes == ['rejected']


@pytest.mark.parametrize('index', [1, 2])
def test_archived_g_repair_wire_did_not_drop_feedback_or_previous_accepted_items(recorded, index):
    case = recorded[index]
    writes, reviews = [], []
    class ArchivedWriter:
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            writes.append(request)
            if len(writes) > 1:
                previous = case['attempts'][len(writes) - 2]['review_verdict']['reviews']
                assert request['repair_feedback']['reviews'] == previous
                assert request['repair_feedback'] == case['attempts'][len(writes) - 1]['received_repair_feedback']
            return deepcopy(case['attempts'][len(writes) - 1]['generation_response'])
    class ArchivedReviewer:
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            reviews.append(request)
            verdict = deepcopy(case['attempts'][len(reviews) - 1]['review_verdict']['reviews'])
            return {'reviews': [row for row in verdict if row['knowledge_point_id'] in {
                item['knowledge_point_id'] for item in request['items']}]}
    with pytest.raises(generation.DiagnosticGenerationError, match='review rejected'):
        ArchivedV10StructuredGenerator(ArchivedWriter(), review_client=ArchivedReviewer(),
            allow_grounded_answer_rewrite=True, validation_retries=2).generate(structure_for(case['source']))
    assert len(writes) == len(reviews) == 3


@pytest.mark.parametrize('defect', ['unknown_ref', 'sibling_ref', 'fabricated_quote', 'stitched_quote',
    'inverse_input', 'invented_result', 'mapping_override', 'readonly_derivation', 'duplicate_instances'])
def test_exact_source_and_forward_plan_cannot_be_overridden(recorded, defect):
    def transform(request, response, call):
        task = response['items'][2]['application_task']
        if defect == 'unknown_ref':
            task['basis']['source_ref'] = 'fabricated'
        elif defect == 'sibling_ref':
            sibling = request['knowledge_points'][0]['knowledge_point_id']
            task['basis']['source_ref'] = request['application_source_refs'][sibling][0]['ref']
        elif defect == 'fabricated_quote':
            task['basis']['quote'] += '教材未给出的新结论。'
        elif defect == 'stitched_quote':
            task['basis']['quote'] = task['basis']['quote'][:12] + task['basis']['quote'][-20:]
        elif defect == 'inverse_input':
            task['input_condition'] = '非同源染色体不发生自由组合'
        elif defect == 'invented_result':
            task['positive_result'] = '子代一定没有变异'
        elif defect == 'mapping_override':
            task['mapping'] = ['a', 'b']
        elif defect == 'readonly_derivation':
            task['derivation'] = {'mapping': ['a', 'b'], 'approved': True}
        else:
            task['instances'][1] = task['instances'][0]
        return response
    writer, reviewer = Writer(recorded[1], 1, transform), Reviewer()
    with pytest.raises(generation.DiagnosticGenerationError, match='batch validation failed'):
        subject(writer, reviewer).generate(structure_for(recorded[1]['source']))
    assert len(writer.calls) == 1 and reviewer.calls == []


@pytest.mark.parametrize('template', ['{input.__class__}转入{output}', '{input!r}转入{output}',
    '{input:>10}转入{output}', '{{input}}转入{output}', '{input}与{input}转入{output}',
    '{input}转入{other}', '没有任何角色槽的操作', '{input}转入{output}及{unknown}'])
def test_role_templates_only_replace_two_literal_tokens_without_format_execution(recorded, template):
    def transform(request, response, call):
        response['items'][2]['application_task']['record_template'] = template
        return response
    writer, reviewer = Writer(recorded[2], 2, transform), Reviewer()
    with pytest.raises(generation.DiagnosticGenerationError, match='literal .*input.*output'):
        subject(writer, reviewer).generate(structure_for(recorded[2]['source']))
    assert reviewer.calls == []


@pytest.mark.parametrize('defect', ['non_source_term', 'unknown_index', 'bool_index', 'duplicate_index',
                                   'identical_records', 'process_success_override'])
def test_required_conditions_only_use_source_terms_and_never_accept_free_process_outcomes(recorded, defect):
    def transform(request, response, call):
        task = response['items'][2]['application_task']
        if defect == 'non_source_term':
            task['required'][0] = '无源装置'
        elif defect == 'unknown_index':
            task['records'][1]['available'] = [99]
        elif defect == 'bool_index':
            task['records'][1]['available'] = [True]
        elif defect == 'duplicate_index':
            task['records'][1]['available'] = [0, 0]
        elif defect == 'identical_records':
            task['records'][1]['available'] = [3, 2, 1, 0]
        else:
            task['outcomes'] = ['一定正常进行', '不能正常进行']
        return response
    writer, reviewer = Writer(recorded[0], 0, transform), Reviewer()
    with pytest.raises(generation.DiagnosticGenerationError, match='batch validation failed'):
        subject(writer, reviewer).generate(structure_for(recorded[0]['source']))
    assert reviewer.calls == []


@pytest.mark.parametrize('index', [0, 1, 2])
def test_literal_source_membership_does_not_programmatically_approve_semantic_plan(recorded, index):
    def transform(request, response, call):
        task = response['items'][2]['application_task']
        if index == 0:
            task['required'] = ['模板', '原料', '能量']  # Deliberately omits a source requirement.
            task['records'][0]['available'] = [0, 1, 2]
            task['records'][1]['available'] = [0, 1]
        elif index == 1:
            # Literal words do not prove the chosen input is an adequate premise.
            task['input_condition'] = '非等位基因也自由组合'
        else:
            task['result_role'] = 'input'  # Wrong result recipient must still be rejected by review.
        return response
    writer, reviewer, cache = Writer(recorded[index], index, transform), Reviewer(False), MemoryApprovals()
    with pytest.raises(generation.DiagnosticGenerationError, match='independent semantic veto'):
        subject(writer, reviewer).generate(structure_for(recorded[index]['source']), chapter_cache=cache, cache_scope='G')
    assert len(writer.calls) == len(reviewer.calls) == 1 and cache.outcomes == ['rejected']
    assert not cache.records


@pytest.mark.parametrize('stage', ['writer', 'reviewer'])
@pytest.mark.parametrize('error,outcome', [(LLMNotSubmittedError('confirmed no request submitted'), 'not_admitted'),
    (LLMTimeoutError('read result unknown'), 'unknown'), (LLMError('connection result ambiguous'), 'unknown')])
def test_only_explicit_not_submitted_error_releases_claim_without_reclassifying_unknown(recorded, stage, error, outcome):
    writer, reviewer, cache = Writer(recorded[0], 0), Reviewer(), MemoryApprovals()
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise error
    (writer if stage == 'writer' else reviewer).structured = fail
    with pytest.raises(type(error)):
        subject(writer, reviewer, retries=2).generate(structure_for(recorded[0]['source']),
            chapter_cache=cache, cache_scope='G-transport')
    assert len(calls) == 1 and cache.outcomes == [outcome]
    if outcome == 'not_admitted':
        assert not cache.records
    else:
        assert next(iter(cache.records.values()))['status'] == 'unknown'
        with pytest.raises(generation.DiagnosticChapterSubmissionBlocked):
            subject(Writer(recorded[0], 0), Reviewer()).generate(structure_for(recorded[0]['source']),
                chapter_cache=cache, cache_scope='G-transport')
        assert cache.outcomes == ['unknown']


def test_full_plan_derivation_and_basis_are_preserved_and_bound_to_approval(recorded):
    def transform(request, response, call):
        if call > 1:
            approved = next(row for row in request['preserve_accepted_items'] if 'application_task' in row)
            response['items'][2] = deepcopy(approved)
        return response
    def verdict(row, call):
        accepted = call > 1 or row['cognitive_target']['id'] != 'relation_understanding'
        return {'knowledge_point_id': row['knowledge_point_id'], 'accepted': accepted,
                'issues': [] if accepted else ['Controlled relationship repair required']}
    writer, reviewer, events = Writer(recorded[2], 2, transform), Reviewer(verdict=verdict), []
    instance = subject(writer, reviewer, retries=1, audit_callback=events.append)
    value = structure_for(recorded[2]['source'])
    assert len(instance.generate(value)) == 3
    assert len(writer.calls) == len(reviewer.calls) == 2
    assert [row['cognitive_target']['id'] for row in reviewer.calls[1]['items']] == ['relation_understanding']
    candidate = next(event['items'][2] for event in events if event['event'] == 'candidate')
    chapter = value.chapters[0]
    by_id = {point[0]: point for point in instance._select_points(chapter)}
    item = instance._validate_item(generation.ItemDraft.model_validate(candidate), chapter, by_id,
                                  allow_rewrite=True, structured_application=True)
    original = instance._approval_key(writer.calls[0], item, candidate['application_task'])
    for key in ('basis', 'bindings', 'record_template', 'result_role', 'derivation'):
        task = deepcopy(candidate['application_task'])
        task[key] = 'different private plan component'
        assert instance._approval_key(writer.calls[0], item, task) != original


def test_v11_protocol_retains_old_approval_history_but_requires_a_new_source_protocol_claim(recorded, monkeypatch):
    writer, reviewer, cache = Writer(recorded[0], 0), Reviewer(), MemoryApprovals()
    instance = subject(writer, reviewer)
    current = generation.DIAGNOSTIC_BANK_VERSION
    prior = 'grounded-independent-review-v10+structured-application-v1+chapter-approvals-v1+balanced-options-v1'
    value = structure_for(recorded[0]['source'])
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', prior)
    instance.generate(value, chapter_cache=cache, cache_scope='G-history')
    previous = deepcopy(cache.claims[-1])
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', current)
    instance.generate(value, chapter_cache=cache, cache_scope='G-history')
    assert current.startswith('grounded-independent-review-v14+')
    assert previous['source_fingerprint'] == cache.claims[-1]['source_fingerprint']
    assert previous['protocol_fingerprint'] != cache.claims[-1]['protocol_fingerprint']
    assert previous['review_fingerprint'] != cache.claims[-1]['review_fingerprint']
    assert len(cache.records) == 2 and all(row['status'] == 'approved' for row in cache.records.values())
    assert len(writer.calls) == len(reviewer.calls) == 2
