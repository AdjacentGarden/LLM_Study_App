import json

import pytest

from adaptive_learning.rag.grounded_qa import (
    AnswerDraft,
    EvidenceChunk,
    GroundedAnswerGenerator,
    GroundedAnswerValidationError,
    _support_spans,
)


def evidence():
    return [EvidenceChunk(source_id='E1', page_number=3,
        text='细胞内的这项过程受到条件限制。\n染色体只复制一\n次，而细胞分裂两次。')]


def draft(span, *, source='E1', text='染色体只复制一次，细胞分裂两次。', quote=None):
    citation={'source_id':source,'span_id':span}
    if quote is not None:
        citation['quote']=quote
    return {'status':'supported','claims':[{'text':text,'citations':[citation]}]}


def test_selected_proof_retains_real_ocr_line_breaks_and_semantic_review():
    class Client:
        calls=0
        def structured(self, **kwargs):
            self.calls += 1
            data=json.loads(kwargs['user'])
            if self.calls==1:
                spans=data['support_spans']
                key=next(key for key,row in spans.items() if row['source_id']=='E1')
                return draft(key)
            assert data['claims'][0]['citations'][0]['quote'] == evidence()[0].text
            return {'relevant':True,'reviews':[{'claim_index':0,'supported':True,'reason':'原文明确支持该陈述'}]}
    client=Client()
    answer=GroundedAnswerGenerator(client,use_semantic_review=True).answer(
        question='过程的特点？', evidence=evidence(), retrieval_score=2)
    assert answer.semantic_checked and client.calls==2
    assert answer.claims[0].citations[0].quote==evidence()[0].text
    assert 'span_id' not in answer.model_dump_json()


def test_proof_address_is_bound_to_exact_source_content_and_parent():
    original=evidence()
    key=next(iter(_support_spans(original)))
    modified=[original[0].model_copy(update={'text':original[0].text+'新增条件'})]
    other=EvidenceChunk(source_id='E2',page_number=4,text='不同教材片段')
    for parsed, sources in ((draft('invented'),original), (draft(key,source='E2'),original+[other]),
                            (draft(key),modified), (draft(key,quote='染色体复制十次'),original)):
        with pytest.raises(GroundedAnswerValidationError):
            GroundedAnswerGenerator._verify(AnswerDraft.model_validate(parsed), sources)


def test_existing_proof_never_licenses_an_unsupported_claim():
    key=next(iter(_support_spans(evidence())))
    class Client:
        calls=0
        def structured(self,**_):
            self.calls+=1
            if self.calls==1:
                return draft(key,text='染色体复制十次。')
            return {'relevant':True,'reviews':[{'claim_index':0,'supported':False,'reason':'原文说只复制一次，答案改成十次'}]}
    with pytest.raises(GroundedAnswerValidationError,match='semantic review rejected'):
        GroundedAnswerGenerator(Client(),use_semantic_review=True,max_validation_retries=0).answer(
            question='复制几次？',evidence=evidence(),retrieval_score=2)


@pytest.mark.parametrize("address", ["unknown_span", "wrong_parent", "unknown_source"])
def test_invalid_proof_address_returns_visible_unverified_answer_without_retry(address):
    sources = evidence() + [EvidenceChunk(source_id="E2", page_number=4, text="其他教材片段")]
    key = next(iter(_support_spans(sources)))
    source = {"unknown_span": "E1", "wrong_parent": "E2", "unknown_source": "missing"}[address]

    class Client:
        calls = 0

        def structured(self, **_):
            self.calls += 1
            assert self.calls == 1, "address errors must not cause another model call"
            return draft("invented" if address == "unknown_span" else key, source=source)

    client = Client()
    answer = GroundedAnswerGenerator(
        client, use_semantic_review=True, max_validation_retries=2
    ).answer(question="过程的特点？", evidence=sources, retrieval_score=2)

    assert answer.status == "general"
    assert answer.answer == "教材引用未核验，以下为模型解答。\n\n染色体只复制一次，细胞分裂两次。"
    assert answer.claims == [] and answer.confidence == 0
    assert not answer.semantic_checked
    assert answer.generation_attempts == client.calls == 1
    assert set(answer.stage_duration_ms) == {"planning", "draft", "review"}
    assert answer.stage_duration_ms["review"] == 0


def test_valid_address_with_rewritten_quote_still_requires_literal_proof():
    key = next(iter(_support_spans(evidence())))

    class Client:
        def structured(self, **_):
            return draft(key, quote="染色体复制十次")

    with pytest.raises(GroundedAnswerValidationError, match="must not rewrite"):
        GroundedAnswerGenerator(Client(), max_validation_retries=0).answer(
            question="复制几次？", evidence=evidence(), retrieval_score=2
        )
