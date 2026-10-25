"""Real F3405 evidence already arrived; a located semantic veto must survive."""

import json
from copy import deepcopy
from pathlib import Path

from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
from adaptive_learning.personalization.teaching_fact_wire import restore_fact_payload


def test_actual_multiple_assertions_keep_p25_context_in_the_request_and_a_negative_reply_rejects():
    data = json.loads((Path(__file__).parent / 'fixtures/teaching_f3405_incidental_scope.json').read_text())
    payload = restore_fact_payload(data['request'])
    point = teaching._Point.model_validate({key: value for key, value in payload['candidate']['points'][0].items()
        if key in teaching._Point.model_fields})
    condition = data['condition_source_id']
    evidence = TeachingEvidence({key: SourceCitation(**value) for key, value in payload['sources'].items()},
                                {}, [], 'actual-f3405-incidental-scope')
    calls = []

    class IndependentReviewer:
        def structured(self, *, system, user, **kwargs):
            assert '不能用已经限缩的主旨断言替其余断言过关' in system
            assert '不能仅按planner的condition解读整段原文' in system
            actual = restore_fact_payload(json.loads(user))
            calls.append(actual)
            assert condition in actual['knowledge_points'][0]['scope_context_source_ids']
            assert '位于非同源染色体上' in actual['sources'][condition]['quote']
            boundaries = actual['candidate']['points'][0]['scope_boundaries']
            assert any(row['source_id'] == condition for row in boundaries[0]['actual_source_text'])
            response = deepcopy(data['observed_response'])
            for row in response['components']:
                component = row['component_id']
                if component not in ('explanation', 'flashcard'):
                    continue
                row['scope_exceptions'] = False
                row['findings'] = [{
                    'kind': 'missing_condition',
                    'candidate_field': 'explanation' if component == 'explanation' else 'back',
                    'candidate_quote': '非等位基因形成配子时自由组合' if component == 'explanation' else '非等位基因自由组合',
                    'issue': '已限定主旨的证据强度，仍不能代替旁带断言所需的对象位置条件。',
                    'evidence_source_ids': [condition],
                    'missing_text': '位于非同源染色体上\n的非等位基因',
                }]
            return response

    result = teaching.TeachingContentGenerator(IndependentReviewer())._request_fact_batch(
        payload=payload, batch=[point], lesson=None, evidence=evidence,
        spans=payload['support_spans'], include_lesson=False,
    )
    assert len(calls) == 1  # Same factual call, no new model pass or repair budget.
    assert not result.accepted
    assert set(result.points[0].failed_components) == {'explanation', 'flashcard'}
    assert all(not getattr(result.points[0].components, name).scope_exceptions for name in ('explanation', 'flashcard'))
    assert result.points[0].components.practice.scope_exceptions
