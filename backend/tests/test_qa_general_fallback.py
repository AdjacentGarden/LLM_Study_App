import json

import pytest

from adaptive_learning.llm.client import (
    LLMTimeoutError,
    model_time_budget,
    remaining_model_time,
)
from adaptive_learning.rag.grounded_qa import (
    EvidenceChunk,
    GroundedAnswerGenerator,
)

SOURCE = EvidenceChunk(source_id='E1', page_number=11, text='染色体只复制一次，而细胞分裂两次。')
NOTICE = '教材引用未核验，以下为模型解答。\n\n'


class SequenceClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.remaining = []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        self.remaining.append(remaining_model_time())
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.mark.parametrize('mode', ['empty', 'low_score', 'skipped_only'])
def test_unavailable_evidence_uses_one_general_call_with_followup_context(mode):
    client = SequenceClient([{'answer': '男女比例差异本身不能充分确定基因所在的位置。'}])
    conversation = {'history': [
        {'role': 'user', 'text': '男女发病比例不同就一定是伴性遗传吗？'},
        {'role': 'assistant', 'text': '还需要其他依据。'},
    ]}
    preferences = {'guidance_styles': ['examples']}
    result = GroundedAnswerGenerator(client, use_semantic_review=True).answer(
        question='为什么不能只凭这个现象确定？',
        evidence=[] if mode == 'empty' else [SOURCE],
        retrieval_score=-1 if mode == 'low_score' else 2,
        teaching_context=preferences, conversation_context=conversation,
        source_coverage={'skipped_page_ranges': [[11, 11]]} if mode == 'skipped_only' else {},
    )
    assert result.status == 'general' and result.answer.startswith(NOTICE)
    assert result.claims == [] and result.confidence == 0 and not result.semantic_checked
    assert len(client.calls) == result.generation_attempts == 1
    assert result.stage_duration_ms['general'] >= 0
    assert result.stage_duration_ms['draft'] == result.stage_duration_ms['review'] == 0
    payload = json.loads(client.calls[0]['user'])
    assert payload['conversation'] == conversation and payload['preferences'] == preferences
    assert payload['question'] == '为什么不能只凭这个现象确定？'
    assert '通用知识' in client.calls[0]['system'] and 'conversation.history' in client.calls[0]['system']


def test_model_insufficiency_calls_general_once_and_retains_shared_deadline():
    client = SequenceClient([
        {'status': 'insufficient', 'claims': [], 'insufficiency_reason': '缺少完整判定依据'},
        {'answer': '还需要结合家系遗传和基因位置等信息判断。'},
    ])
    with model_time_budget(2):
        answer = GroundedAnswerGenerator(
            client, use_semantic_review=True, max_validation_retries=2,
            draft_budget_seconds=25,
        ).answer(question='为什么不能单凭男女比例确定？', evidence=[SOURCE], retrieval_score=3)
    assert answer.answer == NOTICE + '还需要结合家系遗传和基因位置等信息判断。'
    assert answer.status == 'general' and answer.claims == [] and not answer.semantic_checked
    assert answer.generation_attempts == len(client.calls) == 2
    assert answer.stage_duration_ms['review'] == 0 and 'general' in answer.stage_duration_ms
    assert all(value is not None and 0 < value <= 2 for value in client.remaining)
    assert client.remaining[1] <= client.remaining[0]
    assert 'support_spans' in json.loads(client.calls[0]['user'])
    assert '通用知识' in client.calls[1]['system']


@pytest.mark.parametrize('reply,error', [
    (LLMTimeoutError('deadline exhausted'), LLMTimeoutError),
])
def test_failed_general_answer_never_restarts_grounded_draft_or_repeats_fallback(reply, error):
    client = SequenceClient([{'status': 'insufficient', 'claims': []}, reply])
    with pytest.raises(error):
        GroundedAnswerGenerator(client, max_validation_retries=2).answer(
            question='请解释', evidence=[SOURCE], retrieval_score=2,
        )
    assert len(client.calls) == 2
