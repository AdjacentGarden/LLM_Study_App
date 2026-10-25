"""Point components must cover every real assertion, not just a limited main idea.

Uses the recorded F source/candidate, with explicit offline reviewer verdicts.
A mechanically valid positive control is not a claim about model quality.
"""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from adaptive_learning.ingestion.models import ChapterDraft, SourceQuote
from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
from adaptive_learning.personalization.teaching_fact_wire import restore_fact_payload
from adaptive_learning.personalization.teaching_review_reuse import VerifiedComponentReuse


@pytest.fixture
def observed():
    data = json.loads((Path(__file__).parent / 'fixtures/teaching_f3405_incidental_scope.json').read_text())
    original = restore_fact_payload(data['request'])
    raw = original['candidate']['points'][0]
    point = teaching._Point.model_validate({key: value for key, value in raw.items()
                                          if key in teaching._Point.model_fields})
    evidence = TeachingEvidence({key: SourceCitation(**value) for key, value in original['sources'].items()},
        {point.point_id: list(point.source_ids)}, [], 'actual-F-point-assertions')
    label = original['knowledge_points'][0]['label']
    chapter = ChapterDraft(chapter_id='actual-F-component-control', order=1, title=original['chapter_title'],
        start_page=10, end_page=35, summary='Offline proof-contract control', knowledge_points=[label],
        source_block_ids=[], evidence=[SourceQuote(page_number=22, quote='实际来源均保留在证据目录中。')],
        knowledge_point_evidence={label: [SourceQuote(page_number=22, quote='实际来源均保留在证据目录中。')]})
    return data, original, point, evidence, chapter


def job_for(observed, *, point=None, client=None):
    _, original, initial, evidence, chapter = observed
    instance = teaching.TeachingContentGenerator(client or object())
    jobs = instance._build_fact_jobs([point or initial], None, original['knowledge_points'],
                                    chapter, evidence, [])
    assert len(jobs) == 1
    return instance, jobs[0]


def part_for(job, name='explanation'):
    return job['payload']['candidate']['points'][0]['component_evidence'][name]


def positive_row(job, name='explanation'):
    """Complete typed mechanical control; source truth is deliberately reviewer-owned."""
    part = part_for(job, name)
    assertions = part['assertion_contract']['assertions']
    spans = part['assertion_contract']['allowed_support_span_ids']
    return {'point_id': job['batch'][0].point_id, 'component_id': name,
        'assertion_checks': [{'assertion_id': item['assertion_id'], 'is_factual': True,
            'source_entailment': True, 'support_span_ids': spans[:1], 'findings': []}
            for item in assertions],
        'scope_matrix': [{'assertion_id': item['assertion_id'], 'checks': [
            [False, False, False, False, None] for _ in part['scope_matrix_contract']['boundary_ids']]}
            for item in assertions]}


def normalize(job, row, **kwargs):
    teaching.TeachingContentGenerator._normalize_component_selection(
        row, job['payload'], job['evidence'], job['spans'], **kwargs)


def incidental_assertion(job, name='explanation'):
    return next(item for item in part_for(job, name)['assertion_contract']['assertions']
                if '非等位基因' in item['text'] and '自由组合' in item['text'])


def negative_row(observed, job, name='explanation', *, repaired=False):
    row = positive_row(job, name)
    assertion = incidental_assertion(job, name)
    check = next(check for check in row['assertion_checks'] if check['assertion_id'] == assertion['assertion_id'])
    check.update(source_entailment=False, support_span_ids=[], findings=[{
        'kind': 'unsupported_claim' if repaired else 'missing_condition',
        'quote': assertion['text'],
        'issue': '受控独立回执保留这一断言的真实来源否决；引用或另一已限缩主旨不能自动批准它。',
        'evidence_source_ids': [observed[0]['condition_source_id']],
        **({} if repaired else {'missing_text': '位于非同源染色体上'}),
    }])
    return row


def test_compiler_covers_every_field_in_each_of_the_three_real_point_components(observed):
    _, job = job_for(observed)
    point = job['batch'][0]
    for name in ('explanation', 'flashcard', 'practice'):
        part = part_for(job, name)
        fields = teaching.TeachingContentGenerator._point_component_fields(point, name)
        assertions = part['assertion_contract']['assertions']
        for field, text in fields.items():
            assert ''.join(item['text'] for item in assertions if item['field'] == field) == text
        assert part['source_ids']
        assert len(assertions) > 1
        row = positive_row(job, name)
        normalize(job, row)
        assert row['assertions'] == assertions
        assert len(row['assertion_checks']) == len(row['scope_matrix']) == len(assertions)
        assert row['source_entailment'] and row['scope_exceptions']
    assert incidental_assertion(job)


@pytest.mark.parametrize('damage', ['aggregate_only', 'omit_incidental', 'unknown_assertion',
    'duplicate_assertion', 'omit_matrix', 'omit_matrix_cell', 'parent_true'])
def test_point_proof_cannot_omit_the_incidental_claim_or_fall_back_to_aggregate_flags(observed, damage):
    _, job = job_for(observed)
    row = positive_row(job)
    if damage == 'aggregate_only':
        row = deepcopy(observed[0]['observed_response']['components'][0])
    elif damage == 'omit_incidental':
        target = incidental_assertion(job)['assertion_id']
        row['assertion_checks'] = [check for check in row['assertion_checks'] if check['assertion_id'] != target]
    elif damage == 'unknown_assertion':
        row['assertion_checks'][0]['assertion_id'] = 'unrecognized_assertion'
    elif damage == 'duplicate_assertion':
        row['assertion_checks'].append(deepcopy(row['assertion_checks'][0]))
    elif damage == 'omit_matrix':
        row['scope_matrix'].pop()
    elif damage == 'omit_matrix_cell':
        assert row['scope_matrix'][0]['checks']
        row['scope_matrix'][0]['checks'].pop()
    else:
        row['source_entailment'] = True
    with pytest.raises((ValueError, teaching.TeachingContentError)):
        normalize(job, row)


def test_actual_p25_condition_negative_survives_without_adding_a_citation_or_parent_approval(observed):
    _, job = job_for(observed)
    row = negative_row(observed, job)
    source_ids = list(part_for(job)['source_ids'])
    normalize(job, row)
    assert not row['source_entailment']
    assert any(finding['kind'] == 'missing_condition' and finding['missing_text'] == '位于非同源染色体上'
               for finding in row['findings'])
    assert part_for(job)['source_ids'] == source_ids
    assert observed[0]['condition_source_id'] not in source_ids


def test_fixing_one_condition_still_runs_the_independent_review_and_obeys_a_new_negative(observed):
    calls = []

    class IndependentReviewer:
        def structured(self, *, user, **kwargs):
            request = restore_fact_payload(json.loads(user))
            calls.append(request)
            # A fully specified independent negative, never an inferred approval.
            active_job = jobs[-1]
            return {'components': [negative_row(observed, active_job, repaired=len(calls) > 1),
                positive_row(active_job, 'flashcard'), positive_row(active_job, 'practice')]}

    reviewer, jobs = IndependentReviewer(), []
    instance, first = job_for(observed, client=reviewer)
    jobs.append(first)
    before = instance._run_fact_job(first)
    fixed = observed[2].model_copy(deep=True)
    fixed.explanation = fixed.explanation.replace('非等位基因', '位于非同源染色体上的非等位基因')
    fixed.source_ids.append(observed[0]['condition_source_id'])
    instance, second = job_for(observed, point=fixed, client=reviewer)
    jobs.append(second)
    after = instance._run_fact_job(second)
    assert len(calls) == 2 and not before.accepted and not after.accepted
    assert '位于非同源染色体上' in calls[1]['candidate']['points'][0]['explanation']
    assert any(finding.kind == 'unsupported_claim' for finding in after.points[0].components.explanation.findings)


def test_old_cached_point_aggregate_cannot_be_reentered_as_a_new_exact_proof(observed):
    _, job = job_for(observed)
    old = deepcopy(observed[0]['observed_response']['components'][0])
    with pytest.raises((ValueError, teaching.TeachingContentError)):
        normalize(job, old, point_id=job['batch'][0].point_id, component_id='explanation')
    part = part_for(job)
    component = {'owner_id': job['batch'][0].point_id, 'component_id': 'explanation',
        'fields': part['component_fields'], 'source_ids': part['source_ids'], 'boundaries': part['scope_boundaries']}
    texts = {key: value.quote for key, value in job['evidence'].sources.items()}
    context = {'chapter_id': 'same', 'evidence_fingerprint': 'same', 'review_identity': 'same-independent',
        'prompt_sha256': hashlib.sha256(teaching.FACT_REVIEW_PROMPT.encode()).hexdigest(),
        'version': teaching.TEACHING_CONTENT_VERSION}
    current = VerifiedComponentReuse(source_texts=texts, context=context)
    historical = VerifiedComponentReuse(source_texts=texts, context={**context, 'version': 'pre-point-assertions'})
    assert current.key(component) != historical.key(component)


def test_identical_sentences_in_two_points_have_disjoint_proof_ids_and_cannot_borrow_verdicts(observed):
    _, original, point, evidence, chapter = observed
    other = point.model_copy(update={'point_id': point.point_id + '_other'}, deep=True)
    inputs = [deepcopy(original['knowledge_points'][0]) for _ in range(2)]
    inputs[1]['point_id'] = other.point_id
    evidence.point_sources[other.point_id] = list(other.source_ids)
    instance = teaching.TeachingContentGenerator(object())
    jobs = instance._build_fact_jobs([point, other], None, inputs, chapter, evidence, [])
    assert len(jobs) == 2
    first = part_for(jobs[0])['assertion_contract']['assertions']
    second = part_for(jobs[1])['assertion_contract']['assertions']
    assert [row['text'] for row in first] == [row['text'] for row in second]
    assert not ({row['assertion_id'] for row in first} & {row['assertion_id'] for row in second})
    reply = positive_row(jobs[0])
    reply['assertion_checks'][0]['assertion_id'] = second[0]['assertion_id']
    with pytest.raises((ValueError, teaching.TeachingContentError)):
        normalize(jobs[0], reply)


def test_point_and_lesson_provider_contracts_share_only_the_four_assertion_envelope_keys():
    expected = {'point_id', 'component_id', 'assertion_checks', 'scope_matrix'}
    for lesson_ids, has_points in ((set(), True), ({'example_1'}, False)):
        schema = teaching.TeachingContentGenerator._fact_response_schema(lesson_ids, has_points)
        component = schema['$defs']['_FlatFactComponent']
        assert set(component['properties']) == set(component['required']) == expected
        assert component['additionalProperties'] is False
        assertion = component['properties']['assertion_checks']['items']
        assert set(assertion['required']) == {
            'assertion_id', 'is_factual', 'source_entailment', 'support_span_ids', 'findings'}
    prompt = teaching.FACT_REVIEW_PROMPT
    assert '每行仅含point_id、component_id、assertion_checks、scope_matrix' in prompt
    for retired_command in ('每组件scope_checks必须逐条覆盖', '每项单独输出source_entailment/scope_exceptions',
                            '每行包含point_id、component_id、source_entailment'):
        assert retired_command not in prompt
