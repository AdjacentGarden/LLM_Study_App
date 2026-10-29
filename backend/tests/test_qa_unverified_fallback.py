"""Normal answers survive proof/format preferences without invented citations."""
from __future__ import annotations

import json
from collections import OrderedDict
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from adaptive_learning.api import qa_dependency
from adaptive_learning.llm.client import LLMError, LLMRefusalError, LLMTimeoutError
from adaptive_learning.rag.grounded_qa import (
    EvidenceChunk,
    GroundedAnswerGenerator,
    GroundedAnswerValidationError,
)
from adaptive_learning.rag.index import RAGIndexError
from adaptive_learning.rag.service import TextbookQAService


class Responses:
    def __init__(self, *values):
        self.values = iter(values)
        self.calls = []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


SOURCES = [EvidenceChunk(source_id="E1", page_number=3, text="模型用于说明特定条件下的关系。")]
TEXT = "该模型说明特定条件下的关系。"


def _draft(**changes):
    value = {"status": "supported", "claims": [{"text": TEXT,
             "citations": [{"source_id": "E1", "quote": SOURCES[0].text}]}]}
    value.update(changes)
    return value


def _assert_general(result, text):
    assert result.status == "general"
    assert result.answer == "教材引用未核验，以下为模型解答。\n\n" + text
    assert result.claims == [] and result.confidence == 0 and not result.semantic_checked


@pytest.mark.parametrize("citation", [
    {"source_id": "missing", "quote": "未找到的原文"},
    {"source_id": "E1", "quote": "被改写的原文"},
    {"source_id": "E1", "span_id": "missing"},
    {},
])
def test_unverifiable_citation_keeps_text_without_another_call(citation):
    value = _draft()
    value["claims"][0]["citations"] = [citation]
    client = Responses(value)
    result = GroundedAnswerGenerator(client, use_semantic_review=True).answer(
        question="说明模型", evidence=SOURCES, retrieval_score=1)
    _assert_general(result, TEXT)
    assert len(client.calls) == result.generation_attempts == 1


@pytest.mark.parametrize("bad_review", [
    {"relevant": True, "reviews": [{"claim_index": 0, "supported": False, "reason": "条件不完整"}]},
    {"relevant": True, "reviews": []},
])
def test_rejected_or_malformed_review_removes_proof_not_answer(bad_review):
    client = Responses(_draft(), bad_review)
    result = GroundedAnswerGenerator(client, use_semantic_review=True).answer(
        question="说明模型", evidence=SOURCES, retrieval_score=1)
    _assert_general(result, TEXT)
    assert len(client.calls) == 2 and result.generation_attempts == 1


@pytest.mark.parametrize("raw,text", [
    ({"answer": "长解答。" * 1500}, "长解答。" * 1500),
    ({"status": "unknown", "claims": [{"text": TEXT, "citations": []}]}, TEXT),
    ({"status": "supported", "claims": [{"text": TEXT * 40, "citations": []}]}, TEXT * 40),
])
def test_schema_or_length_preferences_do_not_truncate_readable_answer(raw, text):
    client = Responses(raw)
    result = GroundedAnswerGenerator(client).answer(question="问题", evidence=SOURCES, retrieval_score=1)
    _assert_general(result, text)
    assert len(client.calls) == 1


@pytest.mark.parametrize("raw", [{}, {"answer": ""}, {"answer": " \n "}, {"answer": 42}])
def test_general_bad_received_shape_gets_one_bounded_format_correction(raw):
    client = Responses(raw, {"answer": TEXT})
    result = GroundedAnswerGenerator(client).answer(question="问题", evidence=[], retrieval_score=0)
    _assert_general(result, TEXT)
    assert len(client.calls) == result.generation_attempts == 2
    corrected = json.loads(client.calls[1]["user"])
    assert corrected["previous_response_for_correction"] == raw
    assert "format_feedback" in corrected


def test_grounded_bad_shape_repaired_to_answer_is_general_not_fake_supported():
    client = Responses({"claims": None}, {"answer": TEXT})
    result = GroundedAnswerGenerator(client).answer(question="问题", evidence=SOURCES, retrieval_score=1)
    _assert_general(result, TEXT)
    assert len(client.calls) == 2


def test_repeated_missing_body_is_typed_failure_not_validation_error_500():
    client = Responses({}, {})
    with pytest.raises(GroundedAnswerValidationError, match="no readable answer"):
        GroundedAnswerGenerator(client, max_validation_retries=10).answer(
            question="问题", evidence=[], retrieval_score=0)
    assert len(client.calls) == 2


@pytest.mark.parametrize("error", [LLMRefusalError("refused"), LLMTimeoutError("unknown"), LLMError("bad JSON")])
@pytest.mark.parametrize("evidence", [[], SOURCES])
def test_provider_failure_never_retried_or_served_as_an_answer(error, evidence):
    client = Responses(error)
    with pytest.raises(type(error)):
        GroundedAnswerGenerator(client).answer(question="问题", evidence=evidence, retrieval_score=1)
    assert len(client.calls) == 1


def test_received_explicit_refusal_cannot_be_normalized_to_answer():
    client = Responses({"refusal": "declined", "answer": "provider message"})
    with pytest.raises(LLMRefusalError):
        GroundedAnswerGenerator(client).answer(question="问题", evidence=[], retrieval_score=0)
    assert len(client.calls) == 1


def test_runtime_retrieval_failure_uses_same_cache_and_account_scope():
    class BrokenIndex:
        def search(self, *_args, **_kwargs):
            raise RAGIndexError("unavailable index")

    client = Responses({"answer": TEXT}, {"answer": "其他账号的解答。"})
    service = TextbookQAService(book_id="book", index=BrokenIndex(), generator=GroundedAnswerGenerator(client))
    first = service.answer("问题", cache_scope="alice")
    _assert_general(first, TEXT)
    assert first.evidence_pages == [] and not service.ready
    assert service.answer("问题", cache_scope="alice").cache_hit
    assert not service.answer("问题", cache_scope="bob").cache_hit
    assert len(client.calls) == 2


@pytest.mark.parametrize("unavailable_error", [
    qa_dependency.QAConfigurationError("embedding is unavailable"),
    HTTPException(409, "教材仍在解析"),
])
def test_indexless_dependency_requires_known_book_and_preserves_ownership(monkeypatch, tmp_path, unavailable_error):
    from adaptive_learning.accounts import Accounts
    from adaptive_learning.community import CommunityRepository
    from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository

    settings = SimpleNamespace(data_dir=tmp_path, rag_book_id="default", published_book_ids=(),
                               rag_generation_timeout_seconds=45)
    monkeypatch.setattr(qa_dependency, "get_settings", lambda: settings)
    accounts = Accounts(CommunityRepository(tmp_path / "state/community.sqlite3"))
    actor, token, _ = accounts.repo.visitor(None, [])
    _, other_token, _ = accounts.repo.visitor(None, [])
    accounts.repo.bind_upload(actor, "owned")
    accounts.repo.bind_upload(actor, "missing")
    jobs = SQLiteOCRJobRepository(tmp_path / "state/ocr_jobs.sqlite3")
    # Persist a real book record even while OCR/index resources are unavailable.
    jobs.register_book(book_id="owned", original_name="book.pdf", file_path=tmp_path / "book.pdf", source_sha256="a" * 64)
    client = Responses({"answer": TEXT})
    monkeypatch.setattr(qa_dependency, "_generator", GroundedAnswerGenerator(client))
    monkeypatch.setattr(qa_dependency, "_direct_services", OrderedDict())
    def unavailable(_book):
        raise unavailable_error
    monkeypatch.setattr(qa_dependency, "build_book_qa_service", unavailable)
    def request(value):
        return Request({"type": "http", "headers": [(b"cookie", f"zhiwo_visitor={value}".encode())]})
    with pytest.raises(HTTPException) as error:
        qa_dependency.require_qa_service("owned", request(other_token))
    assert error.value.status_code == 403 and client.calls == []
    with pytest.raises(HTTPException) as error:
        qa_dependency.require_qa_service("missing", request(token))
    assert error.value.status_code == 404 and client.calls == []
    service = qa_dependency.require_qa_service("owned", request(token))
    assert service.index is None and client.calls == []
    _assert_general(service.answer("问题", cache_scope=actor), TEXT)
    assert len(client.calls) == 1


@pytest.mark.parametrize("error", [LLMTimeoutError("review unknown"), LLMError("review unavailable"),
                                  LLMRefusalError("review refused")])
def test_review_transport_or_refusal_preserves_received_answer_without_retry(error):
    client = Responses(_draft(), error)
    result = GroundedAnswerGenerator(client, use_semantic_review=True).answer(
        question="问题", evidence=SOURCES, retrieval_score=1)
    _assert_general(result, TEXT)
    assert len(client.calls) == 2 and result.generation_attempts == 1
    assert set(result.stage_duration_ms) == {"planning", "draft", "review"}


@pytest.mark.parametrize("responses,status", [
    ([{}, {"answer": TEXT}], 200),
    ([{"answer": "长正文。" * 1500}], 200),
    ([{}, {}], 502),
    ([LLMRefusalError("refused")], 422),
    ([LLMTimeoutError("unknown response")], 504),
])
def test_real_http_general_format_and_provider_error_mapping(responses, status):
    from fastapi.testclient import TestClient

    from adaptive_learning.api.app import app
    from adaptive_learning.api.qa_dependency import require_qa_service

    client = Responses(*responses)
    service = TextbookQAService(book_id="biology-required-2", index=None, generator=GroundedAnswerGenerator(client))
    def dependency():
        return service
    app.dependency_overrides[require_qa_service] = dependency
    try:
        with TestClient(app) as http:
            response = http.post("/api/books/biology-required-2/qa", json={"question": "问题"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == status
    assert len(client.calls) == len(responses)
    if status == 200:
        value = response.json()
        assert value["status"] == "general" and value["claims"] == []
        assert value["evidence_pages"] == [] and not value["semantic_checked"]
        assert value["answer"].startswith("教材引用未核验")
    else:
        assert "provider" not in response.text and "ValidationError" not in response.text


def test_real_default_dependency_can_build_text_client_without_gpu_or_index(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        data_dir=tmp_path, rag_book_id="default", published_book_ids=(), rag_index_dir=None,
        rag_generation_timeout_seconds=45, text_api_key="test-key", text_model="deepseek-flash",
        text_base_url="https://api.deepseek.com/v1", llm_https_proxy=None,
        text_thinking_enabled=False, text_reasoning_effort=None,
        rag_refusal_score_threshold=0, rag_use_evidence_planner=False,
        rag_use_semantic_review=True, rag_max_output_tokens=10000,
    )
    monkeypatch.setattr(qa_dependency, "get_settings", lambda: settings)
    monkeypatch.setattr(qa_dependency, "_service", None)
    monkeypatch.setattr(qa_dependency, "_generator", None)
    monkeypatch.setattr(qa_dependency, "_direct_services", OrderedDict())
    client = Responses({"answer": TEXT})
    configs = []
    def text_client(config):
        configs.append(config)
        return client
    def gpu_unavailable(*_args, **_kwargs):
        pytest.fail("an unindexed answer must not construct retrieval GPU models")
    monkeypatch.setattr(qa_dependency, "OpenAICompatibleClient", text_client)
    monkeypatch.setattr(qa_dependency, "TransformerQueryEncoder", gpu_unavailable)
    monkeypatch.setattr(qa_dependency, "TransformerPairReranker", gpu_unavailable)
    service = qa_dependency.require_qa_service("default")
    assert service.index is None and not client.calls
    _assert_general(service.answer("问题"), TEXT)
    assert len(client.calls) == len(configs) == 1
    assert configs[0].model == "deepseek-flash" and configs[0].max_retries == 0
    assert configs[0].base_url == settings.text_base_url
