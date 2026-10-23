"""Portable production regression: public excerpt and synthetic note, no private files."""
from __future__ import annotations

import copy
import json
import threading

import pytest
from studio_note_review_fixtures import note_review_fixture

from adaptive_learning import studio

PUBLIC_SOURCE = (
    "萨顿由此推论：基因是由染色体携带着从亲代传递给\n"
    "下一代的。也就是说，基因就在染色体上，因为基因和染\n"
    "色体行为存在着明显的平行关系。\n"
    "（1)基因在杂交过程中保持完整性和独立性。染色体\n"
    "在配子形成和受精过程中，也有相对稳定的形态结构。\n"
    "(2)在体细胞中基因成对存在，染色体也是成对的。在\n"
    "配子中成对的基因只有一个，同样，成对的染色体也只有\n"
    "一条。"
)


def source_context():
    return {"book_id": "synthetic-book", "note_id": "synthetic-note", "revision": 2,
        "kind": "improve", "transcript": "基因通常有遗传效应。个人记录：我们同时记录响应时间。",
        "evidence": [{"page": 22, "text": PUBLIC_SOURCE},
            {"page": 23, "text": "非等位基因在形成配子时自由组合，非同源染色体在减数第一次分裂后期自由组合。"}]}


def case():
    context = source_context()
    originals, sources = studio._note_span_catalogues(context)
    selected = next(item for item in sources if item.get("context_window")
        and item["source_hit"] == 0 and item["end"] == len(PUBLIC_SOURCE) and "萨顿" not in item["text"])
    assert len(selected["text"]) == 84
    wire = {"summary": "这是一页合成测试笔记。", "polished": context["transcript"], "suggestions": [{
        "original_span_id": None, "kind": "可以补充", "suggestion": "可补充萨顿的平行关系依据：配子中成对基因只有一个，成对染色体也只有一条。",
        "evidence_span_id": selected["id"]}]}
    value = studio.Improvement.model_validate(studio._restore_note_spans(wire, originals, sources))
    feedback = studio._note_review_feedback({"passed": False, "reason": "新增归属缺少本条引文中的先行词。", "findings": [{
        "field": "suggestion", "suggestion_index": 0, "claim": "萨顿的", "issue": "本条选段未包含名字或先行词。"}]}, value)[1]
    return context, sources, wire, value, feedback


def test_context_is_registered_only_on_demand_with_raw_literal_antecedent():
    context, sources, wire, value, feedback = case()
    before = copy.deepcopy(sources)
    assert not any(item["text"] == PUBLIC_SOURCE for item in sources)
    result = studio._note_repair_context_feedback(feedback, value, wire, sources, context)
    candidates = result["repair_targets"][0]["context_candidates"]
    assert 1 <= len(candidates) <= 2
    assert any("萨顿由此推论" in item["text"] for item in candidates)
    assert sources[:len(before)] == before and len(sources) <= len(before) + 2
    for item in candidates:
        assert item["source_hit"] == 0 and item["page"] == 22 and 0 < len(item["text"]) <= 600
        assert item["text"] == PUBLIC_SOURCE[item["start"]:item["end"]]
        assert value.suggestions[0].evidence in item["text"]
        assert "自由组合" not in item["text"]  # The other hit must not supply a compound tail.
    assert "萨顿" not in value.suggestions[0].evidence


@pytest.mark.parametrize("bad", [True, None, "foreign-id"])
def test_unknown_current_id_rejected_without_mutating_registry(bad):
    context, sources, wire, value, feedback = case()
    before = copy.deepcopy(sources)
    wire["suggestions"][0]["evidence_span_id"] = bad
    with pytest.raises(studio.StudioNoteContractError):
        studio._note_repair_context_feedback(feedback, value, wire, sources, context)
    assert sources == before


@pytest.mark.parametrize("field,bad", [("start", True), ("end", 100000), ("page", 23), ("text", "拼接的假引文。")])
def test_selected_id_requires_exact_raw_offsets_page_and_literal_text(field, bad):
    context, sources, wire, value, feedback = case()
    selected = next(item for item in sources if item["id"] == wire["suggestions"][0]["evidence_span_id"])
    selected[field] = bad
    before = copy.deepcopy(sources)
    with pytest.raises(studio.StudioNoteContractError):
        studio._note_repair_context_feedback(feedback, value, wire, sources, context)
    assert sources == before


def test_global_reason_preserved_four_quotes_bounded_at_eight_windows():
    context = source_context()
    context["evidence"] = [{"page": 7, "text": "主体。\n" + "长" * 1300 + "。\n" + "条件。\n" * 220},
        *[{"page": 7, "text": f"第{index}个主体。\n" + "条件。\n" * 220} for index in range(3)]]
    originals, sources = studio._note_span_catalogues(context)
    selected = [next(item for item in sources if item.get("context_window") and item["source_hit"] == hit
                     and item["start"] > 10) for hit in range(4)]
    wire = {"summary": "合成摘要。", "polished": context["transcript"], "suggestions": [
        {"original_span_id": None, "kind": "可以补充", "suggestion": f"第{index}项需全稿校对。", "evidence_span_id": item["id"]}
        for index, item in enumerate(selected)]}
    value = studio.Improvement.model_validate(studio._restore_note_spans(wire, originals, sources))
    before = copy.deepcopy(sources)
    feedback = {"reason": "尚有未定位的全稿问题。", "findings": [], "feedback_scope": "legacy_reason_only"}
    result = studio._note_repair_context_feedback(feedback, value, wire, sources, context)
    assert result["reason"] == feedback["reason"] and result["findings"] == []
    assert len(result["repair_targets"]) == 4 and all(len(item["context_candidates"]) <= 2 for item in result["repair_targets"])
    added = sources[len(before):]
    assert len(added) <= 8 and sum(len(item["text"]) for item in added) <= 4800
    for item in added:
        hit = context["evidence"][item["source_hit"]]
        assert item["text"] == hit["text"][item["start"]:item["end"]] and len(item["text"]) <= 600
    studio._note_repair_context_feedback(feedback, value, wire, sources, context)
    assert sources == before + added


def test_precise_feedback_does_not_expand_unrelated_selected_quotes():
    context, sources, wire, value, feedback = case()
    raw = copy.deepcopy(wire["suggestions"][0])
    raw["evidence_span_id"] = next(item["id"] for item in sources if item["source_hit"] == 1)
    wire["suggestions"].append(raw)
    originals = studio._note_span_catalogues(context)[0]
    value = studio.Improvement.model_validate(studio._restore_note_spans(wire, originals, sources))
    before = len(sources)
    result = studio._note_repair_context_feedback(feedback, value, wire, sources, context)
    assert [item["suggestion_index"] for item in result["repair_targets"]] == [0]
    assert all(item["source_hit"] == 0 for item in sources[before:])


@pytest.mark.parametrize("final_passed", [True, False])
def test_process_note_uses_new_registry_in_repair_decode_and_final_own_quote_review(final_passed):
    context, _sources, wire, _value, feedback = case()
    calls, updates = [], []

    class Fake(studio.Studio):
        def __init__(self):
            self._note_trace = threading.local()

        def note(self, owner, key):
            return {"revision": 2}

        def evidence(self, note, transcript):
            return copy.deepcopy(context["evidence"])

        def update(self, key, **fields):
            updates.append(fields)

        def _save_note_trace(self, stage, **fields):
            pass

        def llm(self, prompt, images=None):
            calls.append(prompt)
            if len(calls) == 1:
                assert not any(item["text"] == PUBLIC_SOURCE for item in self._note_trace.context["evidence_spans"])
                return copy.deepcopy(wire)
            if len(calls) == 2:
                return note_review_fixture(prompt, {"passed": False, "reason": feedback["reason"], "findings": feedback["findings"]}, issue_code="A")
            if len(calls) == 3:
                actual = json.loads(prompt.split("\n审核意见（仅供校对，编号和claim均已与当前稿核验）:")[1])
                chosen = next(item for item in actual["repair_targets"][0]["context_candidates"] if "萨顿由此推论" in item["text"])
                repaired = copy.deepcopy(wire)
                repaired["suggestions"][0]["evidence_span_id"] = chosen["evidence_span_id"]
                return repaired
            assert len(calls) == 4
            actual = json.loads(prompt.split("\n待审核数据：")[1])
            assert set(actual) == {"confirmed_transcript", "summary", "polished", "suggestions", "claim_coverage"}
            item = actual["suggestions"][0]
            assert "萨顿由此推论" in item["evidence"] and item["evidence"] in PUBLIC_SOURCE and item["page"] == 22
            assert "自由组合" not in item["evidence"]
            assert actual["polished"] == context["transcript"]  # Qualifier and personal record preserved.
            return note_review_fixture(prompt, {"passed": final_passed, "reason": "offline fake", "findings": []}, global_failure="fidelity")

    fake = Fake()
    row = {"id": "fake-job", "owner": "fake", "kind": "improve"}
    data = {key: context[key] for key in ("book_id", "note_id", "revision", "transcript")}
    if final_passed:
        fake.process_note(row, data)
        assert updates[-1]["status"] == "succeeded"
    else:
        with pytest.raises(ValueError, match="note review failed"):
            fake.process_note(row, data)
        assert not any(item.get("status") == "succeeded" for item in updates)
    assert len(calls) == 4  # No extra model stage, no constructor/worker/network/provider call.
