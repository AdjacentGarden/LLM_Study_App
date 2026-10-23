import json

import pytest
from studio_note_review_fixtures import note_review_fixture
from test_community import community as community
from test_studio import note, run
from test_studio import setup as setup

from adaptive_learning.studio import (
    StudioNoteContractError,
    _note_span_catalogues,
    _note_writer_schema,
    _restore_note_spans,
    normalize,
)
from adaptive_learning.studio_models import Improvement

TRANSCRIPT = (
    "现在开始测试语音识别。基因通常是有遗传效应的DNA片段。染色体是遗传物质的主要载体。"
    "孟德尔通过豌豆杂交实验提出了分离定律。"
    "在减数分裂过程中，同源染色体相互分离，配子只获得每对等位基因中的一个。"
    "我们同时进行教材识别、语音转写和知识检索，记录显存占用与响应时间。"
)
GENE = "基因位于染色体上，基因是有遗传效应的DNA片段。"
DNA = "DNA，而非蛋白质，是遗传物质。"
EVIDENCE = [{"page": 54, "text": DNA + "\n中间另有内容。\n" + GENE}]


def context(**changes):
    return {"book_id": "book-0", "note_id": "note_123456", "revision": 1,
            "kind": "improve", "transcript": TRANSCRIPT, "evidence": EVIDENCE, **changes}


def wire(originals, sources):
    return {"summary": "保留原笔记已有的限定和个人记录，仅提供有教材支持的可选补充。",
            "polished": TRANSCRIPT,
            "suggestions": [{"original_span_id": next(s["id"] for s in originals if "通常" in s["text"]),
                             "kind": "可以补充", "suggestion": "可结合教材继续核对基因和DNA的关系。",
                             "evidence_span_id": next(s["id"] for s in sources if s["text"] == GENE)}]}


def submit(a, request_id):
    saved = a.post("/api/studio/notes", json=note()).json()
    response = a.post("/api/studio/notes/note_123456/analyze", json={
        "revision": saved["revision"], "action": "improve", "transcript": TRANSCRIPT,
        "consent": True, "request_id": request_id})
    assert response.status_code == 200
    return response.json()["id"]


def test_registered_note_spans_cover_every_input_and_are_bound_to_current_source():
    long = "页眉\n" + "甲" * 1301 + "。\n另一句！无标点尾行\n"
    value = context(evidence=[{"page": 9, "text": long}])
    originals, sources = _note_span_catalogues(value)
    assert normalize("".join(s["text"] for s in originals)) == normalize(TRANSCRIPT)
    assert normalize("".join(s["text"] for s in sources if not s.get("context_window"))) == normalize(long)
    assert all(s["text"] in long and len(s["text"]) <= 600 and s["page"] == 9 for s in sources)
    other_originals, other_sources = _note_span_catalogues({**value, "book_id": "other-book"})
    assert {s["id"] for s in originals}.isdisjoint(s["id"] for s in other_originals)
    assert {s["id"] for s in sources}.isdisjoint(s["id"] for s in other_sources)


def test_note_id_wire_restores_exact_contiguous_source_and_public_contract_without_mutation():
    originals, sources = _note_span_catalogues(context())
    candidate = wire(originals, sources)
    snapshot = json.dumps([candidate, originals, sources], sort_keys=True)
    restored = Improvement.model_validate(_restore_note_spans(candidate, originals, sources))
    item = restored.suggestions[0]
    assert item.original == "基因通常是有遗传效应的DNA片段。"
    assert item.evidence == GENE and item.page == 54
    assert "……" not in item.evidence and item.evidence in EVIDENCE[0]["text"]
    assert json.dumps([candidate, originals, sources], sort_keys=True) == snapshot
    assert set(item.model_dump()) == {"original", "kind", "suggestion", "evidence", "page"}
    model_fields = _note_writer_schema()["$defs"]["Suggestion"]["properties"]
    assert set(model_fields) == {"original_span_id", "kind", "suggestion", "evidence_span_id"}
    candidate["suggestions"][0]["original_span_id"] = None
    assert _restore_note_spans(candidate, originals, sources)["suggestions"][0]["original"] == ""


@pytest.mark.parametrize("fault,code", [
    ({"original_span_id": "original_foreign_0"}, "unknown_original_span_id"),
    ({"original_span_id": True}, "unknown_original_span_id"),
    ({"evidence_span_id": "evidence_foreign_0"}, "unknown_evidence_span_id"),
    ({"evidence_span_id": None}, "unknown_evidence_span_id"),
    ({"evidence_span_id": ""}, "unknown_evidence_span_id"),
    ({"original": "基因通常是有遗传效应的DNA片段。", "evidence": GENE, "page": 54}, "span_wire_shape"),
])
def test_bad_or_mixed_span_wire_never_falls_back_to_otherwise_valid_literal_fields(fault, code):
    originals, sources = _note_span_catalogues(context())
    candidate = wire(originals, sources)
    candidate["suggestions"][0].update(fault)
    with pytest.raises(StudioNoteContractError) as failed:
        _restore_note_spans(candidate, originals, sources)
    assert failed.value.feedback["violations"][0]["code"] == code


@pytest.mark.parametrize("last_review_passes", [False, True])
def test_note_writer_and_semantic_revision_share_ids_but_review_false_still_blocks(setup, monkeypatch, last_review_passes):
    a, _, studio = setup
    key = submit(a, "note_span_review_111")
    prompts = []
    generation_ids = []

    def llm(prompt, images=None):
        prompts.append(prompt)
        if prompt.startswith("审核笔记整理"):
            passed = last_review_passes and len(prompts) == 4
            return note_review_fixture(prompt, {"passed": passed, "reason": "独立检查原意和新增事实"}, global_failure="fidelity")
        registered = studio._note_trace.context
        generation_ids.append([s["id"] for s in registered["evidence_spans"]])
        assert "original_span_id" in prompt and "evidence_span_id" in prompt
        return wire(registered["original_spans"], registered["evidence_spans"])

    monkeypatch.setattr(studio, "evidence", lambda *_: EVIDENCE)
    monkeypatch.setattr(studio, "llm", llm)
    if last_review_passes:
        run(studio, a, key)
        result = a.get("/api/studio/jobs/" + key).json()
        assert result["status"] == "succeeded" and result["result"]["polished"] == TRANSCRIPT
        assert result["result"]["suggestions"][0]["evidence"] == GENE
    else:
        with pytest.raises(ValueError, match="note review failed"):
            run(studio, a, key)
        assert a.get("/api/studio/jobs/" + key).json()["status"] != "succeeded"
    assert len(prompts) == 4 and generation_ids[0] == generation_ids[1]
    assert all("正面的矛盾证据" in p and "用户原已有的限定" in p and "个人实验/操作记录" in p for p in prompts)


def test_actual_ellipsis_original_is_rejected_then_empty_advice_preserves_personal_record(setup, monkeypatch):
    a, _, studio = setup
    key = submit(a, "note_actual_original_ellipsis_111")
    invented = "现在开始测试语音识别。……我们同时进行教材识别、语音转写和知识检索，记录显存占用与响应时间。"
    calls = []

    def llm(prompt, images=None):
        calls.append(prompt)
        if len(calls) == 1:
            return {"summary": "测试笔记", "polished": TRANSCRIPT, "suggestions": [
                {"original": invented, "kind": "需核对", "suggestion": "操作记录另存。", "evidence": GENE, "page": 54}]}
        if len(calls) == 2:
            assert "invented_original" in prompt and invented in prompt
            assert "original_span_id" in prompt and "evidence_span_id" in prompt
            return {"summary": "保留用户已有笔记和个人操作记录，摘录不足处暂不纠错。", "suggestions": [], "polished": TRANSCRIPT}
        return note_review_fixture(prompt, {"passed": True, "reason": "未凑无关教材引用，原意已保留"})

    monkeypatch.setattr(studio, "evidence", lambda *_: EVIDENCE)
    monkeypatch.setattr(studio, "llm", llm)
    run(studio, a, key)
    result = a.get("/api/studio/jobs/" + key).json()
    assert result["status"] == "succeeded" and result["result"]["suggestions"] == []
    assert result["result"]["polished"] == TRANSCRIPT and len(calls) == 3


def test_actual_review_repair_ellipsis_quote_cannot_pass_legacy_compatibility(setup, monkeypatch):
    a, _, studio = setup
    key = submit(a, "note_actual_evidence_ellipsis_111")
    joined = DNA + "……" + GENE
    calls = []

    def llm(prompt, images=None):
        calls.append(prompt)
        if len(calls) == 1:
            registered = studio._note_trace.context
            return wire(registered["original_spans"], registered["evidence_spans"])
        if len(calls) == 2:
            return note_review_fixture(prompt, {"passed": False, "reason": "需要独立修订"}, global_failure="fidelity")
        assert "original_span_id" in prompt and "evidence_span_id" in prompt
        return {"summary": "修订稿", "polished": TRANSCRIPT, "suggestions": [
            {"original": "基因通常是有遗传效应的DNA片段。", "kind": "需核对", "suggestion": "核对教材。", "evidence": joined, "page": 54}]}

    monkeypatch.setattr(studio, "evidence", lambda *_: EVIDENCE)
    monkeypatch.setattr(studio, "llm", llm)
    with pytest.raises(StudioNoteContractError, match="unsupported suggestion") as rejected:
        run(studio, a, key)
    violation = rejected.value.feedback["violations"][0]
    assert violation["code"] == "unsupported_evidence" and violation["pages_with_matching_evidence"] == []
    assert len(calls) == 3 and a.get("/api/studio/jobs/" + key).json()["status"] != "succeeded"
