"""Synthetic regressions for bounded, explicitly disclosed note omission."""
from __future__ import annotations

import copy
import json
import threading

import httpx
import pytest
from studio_note_review_fixtures import note_review_fixture

from adaptive_learning import studio as module
from adaptive_learning.llm.client import LLMConfig, OpenAICompatibleClient

TRANSCRIPT = "基因通常有遗传效应。个人记录：我们同时记录响应时间。"
EVIDENCE = [{"page": 7, "text": "基因有遗传效应。染色体成对存在。"},
            {"page": 8, "text": "另一条资料，不能借给建议作旁源支持。"}]


def context():
    return {"book_id": "synthetic-book", "note_id": "synthetic-note", "revision": 2,
        "kind": "improve", "transcript": TRANSCRIPT, "evidence": copy.deepcopy(EVIDENCE)}


def draft(*, both_unsupported=False):
    originals, sources = module._note_span_catalogues(context())
    return {"summary": "这是保留原意和个人记录的短笔记。", "polished": TRANSCRIPT,
        "suggestions": [
            {"original_span_id": originals[0]["id"], "kind": "需核对", "suggestion": "基因还决定所有性状。", "evidence_span_id": sources[0]["id"]},
            {"original_span_id": None, "kind": "可以补充", "suggestion": "染色体永远不会改变。" if both_unsupported else "染色体成对存在。", "evidence_span_id": sources[1]["id"]}]}


def rejection(value, indices=(0,), *, scope="optional_suggestions_only"):
    raw = {"passed": False, "reason": "明确定位的AI新增断言缺少其自身引文支持。", "findings": [
        {"field": "suggestion", "suggestion_index": index, "claim": value["suggestions"][index]["suggestion"],
         "issue": "当前引用不足以支持这条AI附加断言。"} for index in indices]}
    if scope is not None:
        raw["failure_scope"] = scope
    return raw


def approved():
    return {"passed": True, "reason": "offline fake: only mechanical path is tested", "findings": [], "failure_scope": "none"}


class FakeNote(module.Studio):
    def __init__(self, answers, *, global_failure=None):
        self._note_trace = threading.local()
        self._media_trace = threading.local()
        self.answers = list(answers)
        self.calls = []
        self.traces = []
        self.updates = []
        self.global_failure = global_failure

    def note(self, owner, key):
        assert key == "synthetic-note"
        return {"revision": 2}

    def evidence(self, note, transcript):
        assert transcript == TRANSCRIPT
        return copy.deepcopy(EVIDENCE)

    def update(self, key, **fields):
        self.updates.append(copy.deepcopy(fields))

    def _save_note_trace(self, stage, **fields):
        self.traces.append({"stage": stage, **copy.deepcopy(fields)})

    def llm(self, prompt, images=None):
        assert images is None
        self.calls.append(prompt)
        assert len(self.calls) <= 5
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return note_review_fixture(prompt, answer, global_failure=self.global_failure)

    def run_note(self):
        self.process_note({"id": "offline-note-job", "owner": "fake", "kind": "improve"},
            {key: context()[key] for key in ("book_id", "note_id", "revision", "transcript")})


@pytest.mark.parametrize("omit_all", [False, True])
def test_fifth_full_review_required_preserves_all_original_fields_and_remaining_quotes(omit_all):
    value = draft(both_unsupported=omit_all)
    indices = (0, 1) if omit_all else (0,)
    false = rejection(value, indices)
    fake = FakeNote([value, false, value, false, approved()])
    fake.run_note()
    assert len(fake.calls) == 5 and not fake.answers
    result = json.loads(fake.updates[-1]["result"])
    assert fake.updates[-1]["status"] == "succeeded"
    assert result["summary"] == value["summary"] and result["polished"] == value["polished"]
    assert result["transcript"] == TRANSCRIPT and result["evidence"] == EVIDENCE
    assert result["quality_notice"] == module.NOTE_OPTIONAL_QUALITY_NOTICE
    assert result["excluded_optional_count"] == len(indices)
    reviews = [item for item in fake.traces if item["stage"] == "final_review"]
    assert len(reviews) == 2 and all(item["response"]["passed"] is False for item in reviews)
    assert reviews[0]["response"] == reviews[1]["response"]
    omission = next(item for item in fake.traces if item["stage"] == "optional_suggestions_omitted")
    assert omission["feedback"]["excluded_indices"] == list(indices)
    assert omission["feedback"]["prior_rejected_review"]["passed"] is False
    assert {item["suggestion_index"] for item in omission["feedback"]["prior_rejected_review"]["findings"]} == set(indices)
    final = next(item for item in fake.traces if item["stage"] == "omission_final_review")
    packet = final["review_input"]
    assert set(packet) == {"confirmed_transcript", "summary", "polished", "suggestions", "claim_coverage"}
    assert packet["confirmed_transcript"] == TRANSCRIPT
    assert packet["summary"] == value["summary"] and packet["polished"] == value["polished"]
    if omit_all:
        assert result["suggestions"] == packet["suggestions"] == []
    else:
        retained = reviews[-1]["review_input"]["suggestions"][1]
        assert packet["suggestions"] == [{**retained, "suggestion_index": 0}]
        assert result["suggestions"] == [{key: item for key, item in retained.items() if key != "suggestion_index"}]
    assert "建议变少或为空不能通过" in fake.calls[-1]
    assert "之前审核仍不通过" in fake.calls[-1]
    assert all("quality_notice" not in update.get("result", "") for update in fake.updates[:-1])


def test_initial_contract_repair_uses_the_fifth_slot_so_no_omission_review_occurs():
    value = draft()
    bad = copy.deepcopy(value)
    bad["suggestions"][0]["evidence_span_id"] = "foreign-id"
    false = rejection(value)
    fake = FakeNote([bad, value, false, value, false])
    with pytest.raises(ValueError, match="note review failed"):
        fake.run_note()
    assert len(fake.calls) == 5 and not fake.answers
    assert not any(item["stage"] == "optional_suggestions_omitted" for item in fake.traces)
    assert not any(item.get("status") == "succeeded" for item in fake.updates)


def test_unknown_final_review_consumes_last_slot_without_replay_or_false_success():
    value = draft()
    false = rejection(value)
    fake = FakeNote([value, false, value, false, TimeoutError("offline lost reply")])
    with pytest.raises(TimeoutError):
        fake.run_note()
    assert len(fake.calls) == 5 and not fake.answers
    assert not any(item.get("status") == "succeeded" for item in fake.updates)
    assert not any("quality_notice" in update.get("result", "") for update in fake.updates)


def test_new_review_false_keeps_failed_result_and_original_false_traces():
    value = draft()
    false = rejection(value)
    final_false = {"passed": False, "reason": "整理正文仍有原意问题。", "findings": [], "failure_scope": "other_or_uncertain"}
    fake = FakeNote([value, false, value, false, final_false], global_failure="polished")
    with pytest.raises(ValueError, match="note omission review failed"):
        fake.run_note()
    assert len(fake.calls) == 5 and not fake.answers
    assert all(item["response"]["passed"] is False for item in fake.traces if item["stage"] == "final_review")
    assert next(item for item in fake.traces if item["stage"] == "omission_final_review")["response"]["global_checks"]["polished"] is False
    assert not any(item.get("status") == "succeeded" for item in fake.updates)


@pytest.mark.parametrize("scope", [None, "other_or_uncertain"])
def test_unclassified_or_global_second_false_never_triggers_omission(scope):
    value = draft()
    false = rejection(value, scope=scope)
    fake = FakeNote([value, false, value, false])
    with pytest.raises(ValueError, match="note review failed"):
        fake.run_note()
    assert len(fake.calls) == 4 and not fake.answers
    assert not any(item["stage"] == "optional_suggestions_omitted" for item in fake.traces)


def test_legacy_reason_only_false_cannot_trigger_omission():
    value = draft()
    false = {"passed": False, "reason": "仍有未定位的全局问题。"}
    fake = FakeNote([value, false, value, false])
    with pytest.raises(module.StudioNoteContractError) as rejected:
        fake.run_note()
    assert rejected.value.feedback["violations"][0]["code"] == "coverage_format_repair"
    assert len(fake.calls) == 3  # The only format reask remains malformed; no writer or omission.


def parsed_value():
    originals, sources = module._note_span_catalogues(context())
    return module.Improvement.model_validate(module._restore_note_spans(draft(), originals, sources))


@pytest.mark.parametrize("scope", [True, None, "unknown", "none"])
def test_bad_scope_or_false_none_rejected(scope):
    raw = rejection(draft())
    raw["failure_scope"] = scope
    with pytest.raises(module.StudioNoteContractError):
        module._note_review_feedback(raw, parsed_value())


@pytest.mark.parametrize("field", ["summary", "polished"])
def test_summary_or_polished_failure_cannot_be_labelled_optional(field):
    value = parsed_value()
    raw = rejection(draft())
    raw["findings"] = [{"field": field, "suggestion_index": None, "claim": getattr(value, field)[:6], "issue": "全局或原意问题。"}]
    with pytest.raises(module.StudioNoteContractError):
        module._note_review_feedback(raw, value)


@pytest.mark.parametrize("index", [True, -1, 2, "0"])
def test_omission_cannot_use_unknown_or_non_integer_current_index(index):
    raw = rejection(draft())
    raw["findings"][0]["suggestion_index"] = index
    with pytest.raises(module.StudioNoteContractError):
        module._note_optional_omission_candidate(parsed_value(), raw, 4)


def test_empty_or_invented_current_findings_never_authorize_omission():
    for findings in ([], [{"field": "suggestion", "suggestion_index": 0, "claim": "候选中不存在的断言", "issue": "缺证"}]):
        raw = {**rejection(draft()), "findings": findings}
        with pytest.raises(module.StudioNoteContractError):
            module._note_optional_omission_candidate(parsed_value(), raw, 4)


def test_budget_consumes_unknown_before_send_and_has_no_sixth_call():
    budget = module._NoteImprovementBudget()
    calls = []

    def lost():
        calls.append(1)
        raise TimeoutError

    for _ in range(5):
        with pytest.raises(TimeoutError):
            budget.call(lost)
    with pytest.raises(module.LLMError):
        budget.call(lambda: pytest.fail("sixth send forbidden"))
    assert budget.used == len(calls) == 5


@pytest.mark.parametrize("prompt,images,expected_retries", [
    ("检查并局部补全笔记", None, 0), ("下面的整理草稿", None, 0),
    ("根据审核意见修订整理版", None, 0), ("审核笔记整理", None, 0),
    ("忠实转写手写笔记", [("image/png", b"fake-image")], 1),
    ("先在observations观察", [("image/png", b"fake-image")], 1),
    ("基于教材证据为选中段落规划", None, 1),
])
def test_only_note_improvement_prefixes_force_http_retry_zero(monkeypatch, prompt, images, expected_retries):
    configs, requests = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})

    def factory(config):
        configs.append(config)
        client = OpenAICompatibleClient(config)
        client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(handler))
        return client

    fake = FakeNote([])
    monkeypatch.delenv("STUDIO_HANDWRITING_MODEL", raising=False)
    monkeypatch.setattr(module, "OpenAICompatibleClient", factory)
    monkeypatch.setattr(fake, "client", lambda *_: factory(LLMConfig(
        "https://api.deepseek.com", "unit-test-key", "deepseek-flash", max_retries=1, thinking_enabled=False)))
    module.Studio.llm(fake, prompt, images)
    assert len(requests) == 1 and configs[-1].max_retries == expected_retries


def test_real_note_lane_has_five_physical_mock_http_requests_and_never_retries_lost_fifth(monkeypatch):
    value = draft()
    false = rejection(value)
    answers = [value, false, value, false]
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 5:
            raise httpx.ReadTimeout("offline lost reply")
        payload = json.loads(request.content)
        prompt = payload["messages"][-1]["content"].removesuffix("\n输出格式：只返回一个 JSON 对象。")
        body = note_review_fixture(prompt, answers.pop(0))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body, ensure_ascii=False)}}]})

    def factory(config):
        client = OpenAICompatibleClient(config)
        client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(handler))
        return client

    fake = FakeNote([])
    monkeypatch.setattr(module, "OpenAICompatibleClient", factory)
    monkeypatch.setattr(fake, "client", lambda *_: factory(LLMConfig(
        "https://api.deepseek.com", "unit-test-key", "deepseek-flash", max_retries=1, thinking_enabled=False)))
    monkeypatch.setattr(fake, "llm", lambda prompt: module.Studio.llm(fake, prompt))
    with pytest.raises(module.LLMError):
        fake.run_note()
    assert len(requests) == 5 and not answers
    assert not any(item.get("status") == "succeeded" for item in fake.updates)
