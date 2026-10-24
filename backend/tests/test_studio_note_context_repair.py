"""Actual followup-03 antecedent gap and targeted own-quote repair contracts.

These are offline mechanical regressions of the observed trace, not evidence
that a model will always select the appropriate new context window.
"""
import json

import pytest
from studio_note_review_fixtures import note_review_fixture
from test_community import community as community
from test_studio import run
from test_studio import setup as setup
from test_studio_note_span_wire import GENE, TRANSCRIPT, context, submit

from adaptive_learning.studio import (
    StudioNoteContractError,
    _note_repair_context_feedback,
    _note_review_feedback,
    _note_span_catalogues,
    _restore_note_spans,
)
from adaptive_learning.studio_models import Improvement

# Verbatim adjacent source text recorded by followup-03 (PDF page 22). The
# sidebar/newlines are deliberately retained; they must never be reconstructed.
ANTECEDENT = (
    "1903年，美国遗传学家萨顿（W.Sutton，1877—1916)\n本节聚焦\n"
    "用蝗虫细胞作材料，研究精子和卵细胞的形成过程。"
)
COMPARISON = (
    "他发\n现孟德尔假设的一对遗传因子，也就是等位基因，它们的\n"
    "·科学家发现基因与染色\n分离与减数分裂中同源染色体的分离非常相似。"
)
P22 = ANTECEDENT + COMPARISON
P23 = "（4）非等位基因在形成配子时自由组合，非同源染色\n体在减数第一次分裂后期也是自由组合的。"
ADVICE = "可补充萨顿的平行关系依据：等位基因的分离与减数分裂中同源染色体的分离非常相似。"
UNSUPPORTED = "非同源染色体在减数第一次分裂后期自由组合"


def test_actual_he_discovered_has_verbatim_same_hit_context_and_stable_single_ids():
    data = context(evidence=[{"page": 22, "text": P22}, {"page": 54, "text": GENE}])
    originals, sources = _note_span_catalogues(data)
    singles = [s for s in sources if not s.get("context_window")]
    assert [s["text"] for s in singles] == [ANTECEDENT, COMPARISON, GENE]
    assert [s["id"].rsplit("_", 1)[1] for s in singles] == ["0", "1", "2"]
    window = next(s for s in sources if s.get("context_window"))
    assert window["page"] == 22 and window["source_hit"] == 0
    assert window["text"] == P22 == data["evidence"][0]["text"][window["start"]:window["end"]]
    raw = {"summary": "原意保留，补充引文有完整指代上下文。", "polished": TRANSCRIPT,
           "suggestions": [{"original_span_id": originals[4]["id"], "kind": "可以补充",
                            "suggestion": ADVICE, "evidence_span_id": window["id"]}]}
    item = _restore_note_spans(raw, originals, sources)["suggestions"][0]
    assert item["evidence"] == P22 and item["page"] == 22
    assert "萨顿" in item["evidence"] and "他发\n现" in item["evidence"]
    changed = _note_span_catalogues(context(evidence=[{"page": 22, "text": P22 + "变更。"}]))[1]
    assert window["id"] not in {s["id"] for s in changed}


def test_context_windows_keep_offsets_max600_and_linear_size_without_crossing_hits():
    text = "\n  条件一。\n\t他说明条件二；\n第三句。" + "长" * 1250 + "。\n尾句。"
    evidence = [{"page": 22, "text": text}, {"page": 22, "text": "另一个检索命中。"},
                {"page": 23, "text": "旁页不能合并。"}]
    sources = _note_span_catalogues(context(evidence=evidence))[1]
    singles = [s for s in sources if not s.get("context_window")]
    windows = [s for s in sources if s.get("context_window")]
    assert len(sources) <= 2 * len(singles)
    assert all(0 < len(s["text"]) <= 600 for s in sources)
    for source in windows:
        hit = evidence[source["source_hit"]]
        assert source["page"] == hit["page"]
        assert source["text"] == hit["text"][source["start"]:source["end"]]
        assert source["source_hit"] == 0  # Other hits each have only one sentence.
    # The preceding 591-char sentence cannot be joined to the 21-char target.
    over_limit = "甲" * 590 + "。\n" + "乙" * 20 + "。"
    assert not any(s.get("context_window") for s in
                   _note_span_catalogues(context(evidence=[{"page": 22, "text": over_limit}]))[1])
    many = _note_span_catalogues(context(evidence=[{"page": 22, "text": "甲。" * 400}]))[1]
    assert len(many) <= 800 and all(len(s["text"]) <= 600 for s in many)


def review_value():
    return Improvement.model_validate({"summary": "保留原意。", "polished": TRANSCRIPT,
        "suggestions": [{"original": "", "kind": "可以补充", "suggestion": ADVICE,
                         "evidence": COMPARISON, "page": 22}]})


def finding(**changes):
    return {"field": "suggestion", "suggestion_index": 0, "claim": "萨顿的",
            "issue": "本条连续引文只有他发现，没有可确定姓名的先行词。", **changes}


@pytest.mark.parametrize("bad", [
    finding(suggestion_index=-1), finding(suggestion_index=1), finding(suggestion_index=True),
    finding(suggestion_index=0.0), finding(suggestion_index="0"),
    finding(claim="当前建议没有的句子"), finding(claim=""), finding(field="global_rag"),
    finding(field={}), finding(extra="unknown"), finding(issue=True),
    finding(field="polished", suggestion_index=0),
])
def test_pinpoint_review_rejects_unknown_index_fabricated_claim_and_bad_types(bad):
    with pytest.raises(StudioNoteContractError):
        _note_review_feedback({"passed": False, "reason": "待修订", "findings": [bad]}, review_value())


@pytest.mark.parametrize("raw", [
    {"passed": "false", "reason": "不能转为true"},
    {"passed": 1, "reason": "不能把数字当通过"},
    {"passed": True, "reason": "互相矛盾", "findings": [finding()]},
    {"passed": False, "reason": "未知字段", "extra": True},
    {"passed": False, "reason": "非法findings", "findings": {}},
])
def test_pinpoint_feedback_never_overrides_rejected_or_malformed_verdict(raw):
    with pytest.raises(StudioNoteContractError):
        _note_review_feedback(raw, review_value())


def test_legacy_reason_only_rejection_and_global_field_finding_remain_rejections():
    review, feedback = _note_review_feedback({"passed": False, "reason": "全局原意损失不能被空findings消除", "findings": []}, review_value())
    assert review.passed is False and feedback["feedback_scope"] == "legacy_reason_only"
    assert feedback["reason"] == review.reason
    review, feedback = _note_review_feedback({"passed": False, "reason": "限定丢失",
        "findings": [finding(field="polished", suggestion_index=None, claim="通常")]}, review_value())
    assert review.passed is False and feedback["findings"][0]["field"] == "polished"


def coverage_fixture_prompt():
    from adaptive_learning.studio_note_coverage import registry

    packet = {"confirmed_transcript": TRANSCRIPT, **review_value().model_dump()}
    packet["suggestions"] = [{"suggestion_index": i, **s} for i, s in enumerate(packet["suggestions"])]
    expected = registry(packet)
    packet["claim_coverage"] = {key: expected[key] for key in ("candidate_id", "clauses", "global_ids")}
    return "审核笔记整理\n待审核数据：" + json.dumps(packet, ensure_ascii=False), expected


def test_explicit_legacy_fixture_maps_a_cross_clause_negative_without_approving_any_part():
    from adaptive_learning.studio_note_coverage import parse_review

    prompt, expected = coverage_fixture_prompt()
    raw = {"passed": False, "reason": "整条附加断言缺证", "findings": [finding(claim=ADVICE)]}
    response = note_review_fixture(prompt, raw, issue_code="A")
    assert all(ok is False for _, ok in response["clause_checks"])
    assert {key for key, _ in response["findings"]} == {row["id"] for row in expected["clauses"]}
    assert response["passed"] is False and raw["findings"][0]["claim"] == ADVICE
    restored = parse_review(response, expected)
    assert [row["claim"] for row in restored["findings"]] == [row["text"] for row in expected["clauses"]]


@pytest.mark.parametrize("raw", [
    {"passed": 1, "reason": "非法bool"},
    {"passed": False, "reason": "稀疏拒绝"},
    {"passed": False, "reason": "虚构claim", "findings": [finding(claim="没有的事实")]},
    {"passed": False, "reason": "非法index", "findings": [finding(suggestion_index=True)]},
    {"candidate_id": "foreign", "passed": True, "clause_checks": []},
])
def test_test_adapter_never_upgrades_malformed_sparse_or_foreign_fixtures(raw):
    from adaptive_learning.studio_note_coverage import CoverageError, parse_review

    prompt, expected = coverage_fixture_prompt()
    converted = note_review_fixture(prompt, raw)
    assert converted == raw
    with pytest.raises(CoverageError):
        parse_review(converted, expected)


@pytest.mark.parametrize("reason", ["", "   "])
def test_test_adapter_does_not_repair_an_empty_review_reason(reason):
    from adaptive_learning.studio_note_coverage import CoverageError, parse_review

    prompt, expected = coverage_fixture_prompt()
    raw = {"passed": True, "reason": reason}
    assert note_review_fixture(prompt, raw) == raw
    with pytest.raises(CoverageError):
        parse_review(raw, expected)


def test_reason_only_failure_requires_an_explicit_global_fixture_target():
    from adaptive_learning.studio_note_coverage import parse_review

    prompt, expected = coverage_fixture_prompt()
    raw = {"passed": False, "reason": "全局原意未核准"}
    converted = note_review_fixture(prompt, raw, global_failure="fidelity", issue_code="F")
    assert converted["global_checks"] == {"fidelity": False, "summary": True, "polished": True}
    assert converted["findings"] == [["g_fidelity", "F"]]
    assert parse_review(converted, expected)["passed"] is False


@pytest.mark.parametrize("repair_keeps_uncited_clause", [False, True])
def test_actual_antecedent_targeted_repair_gets_complete_quote_but_never_borrows_p23(setup, monkeypatch, repair_keeps_uncited_clause):
    a, _, studio = setup
    key = submit(a, "note_context_repair_111")
    evidence = [{"page": 22, "text": P22}, {"page": 23, "text": P23}, {"page": 54, "text": GENE}]
    calls, packets = [], []

    def make_draft(repaired):
        registered = studio._note_trace.context
        original = next(o for o in registered["original_spans"] if "在减数分裂过程中" in o["text"])
        gene_original = next(o for o in registered["original_spans"] if "通常" in o["text"])
        gene = next(s for s in registered["evidence_spans"] if s["text"] == GENE)
        selected = next(s for s in registered["evidence_spans"] if s["text"] == (P22 if repaired else COMPARISON))
        text = ADVICE[:-1] + "；" + UNSUPPORTED + "。" if repaired and repair_keeps_uncited_clause else ADVICE
        return {"summary": "保留原笔记已有的限定和个人记录，只提供有据的可选补充。", "polished": TRANSCRIPT,
                "suggestions": [{"original_span_id": gene_original["id"], "evidence_span_id": gene["id"],
                    "kind": "可以补充", "suggestion": GENE},
                    {"original_span_id": original["id"], "evidence_span_id": selected["id"],
                     "kind": "可以补充", "suggestion": text}]}

    def llm(prompt, images=None):
        calls.append(prompt)
        if prompt.startswith("审核笔记整理"):
            packet = json.loads(prompt.split("\n待审核数据：", 1)[1])
            packets.append(packet)
            assert set(packet) == {"confirmed_transcript", "summary", "polished", "suggestions", "claim_coverage"}
            assert [s["suggestion_index"] for s in packet["suggestions"]] == [0, 1]
            assert "非等位基因" not in prompt and "不可变教材引文目录" not in prompt
            target = packet["suggestions"][1]
            if target["evidence"] == COMPARISON:
                return note_review_fixture(prompt, {"passed": False, "reason": "第2条人名归属缺少本条引文的先行词。",
                        "findings": [finding(suggestion_index=1)]}, issue_code="A")
            assert target["evidence"] == P22
            if repair_keeps_uncited_clause:
                return note_review_fixture(prompt, {"passed": False, "reason": "本条连续引文不含旁页自由组合阶段事实。",
                        "findings": [finding(suggestion_index=1, claim=UNSUPPORTED, issue="这项事实只在未引用的p23中，不能借证。") ]})
            return note_review_fixture(prompt, {"passed": True, "reason": "归属及分离比较均由本条完整连续引文支持。", "findings": []})
        assert json.dumps(P23, ensure_ascii=False)[1:-1] in prompt  # Writers retain the complete RAG.
        if prompt.startswith("根据审核意见"):
            feedback = json.loads(prompt.split("\n审核意见（仅供校对，编号和claim均已与当前稿核验）:", 1)[1])
            from adaptive_learning.studio_note_coverage import ISSUES

            assert feedback["findings"] == [finding(suggestion_index=1,
                claim="可补充萨顿的平行关系依据：", issue=ISSUES["A"])]
            assert "保留用户原意" in prompt and "未被点名的建议不等于已通过" in prompt
            target = feedback["repair_targets"][0]
            assert target["suggestion_index"] == 1
            assert target["binding_scope"] == "actual_current_wire_id"
            assert target["current_evidence"] == COMPARISON
            assert any(c["text"] == P22 and c["page"] == 22 and c["adds_preceding_context"]
                       for c in target["context_candidates"])
            assert all(P23 not in c["text"] for c in target["context_candidates"])
            return make_draft(True)
        return make_draft(False)

    monkeypatch.setattr(studio, "evidence", lambda *_: evidence)
    monkeypatch.setattr(studio, "llm", llm)
    if repair_keeps_uncited_clause:
        with pytest.raises(ValueError, match="note review failed"):
            run(studio, a, key)
        assert a.get("/api/studio/jobs/" + key).json()["status"] != "succeeded"
    else:
        run(studio, a, key)
        result = a.get("/api/studio/jobs/" + key).json()
        assert result["status"] == "succeeded" and result["result"]["polished"] == TRANSCRIPT
        assert result["result"]["suggestions"][1]["evidence"] == P22
    assert len(calls) == 4 and len(packets) == 2
    assert packets[0]["suggestions"][0] == packets[1]["suggestions"][0]
    assert all(p["confirmed_transcript"] == TRANSCRIPT and p["polished"] == TRANSCRIPT for p in packets)


def test_repair_candidates_bind_exact_selected_hit_not_same_page_duplicate_or_other_page():
    evidence = [{"page": 22, "text": P22}, {"page": 22, "text": "另一段主体。" + COMPARISON},
                {"page": 23, "text": "另一页主体。" + COMPARISON}]
    originals, sources = _note_span_catalogues(context(evidence=evidence))
    selected = next(s for s in sources if s["text"] == COMPARISON and s["source_hit"] == 0)
    wire = {"summary": "保留原意。", "polished": TRANSCRIPT, "suggestions": [
        {"original_span_id": originals[4]["id"], "kind": "可以补充", "suggestion": ADVICE,
         "evidence_span_id": selected["id"]}]}
    value = Improvement.model_validate(_restore_note_spans(wire, originals, sources))
    _, feedback = _note_review_feedback({"passed": False, "reason": "人物先行词缺失，另需复核全局原意。",
        "findings": [finding()]}, value)
    enriched = _note_repair_context_feedback(feedback, value, wire, sources)
    candidates = enriched["repair_targets"][0]["context_candidates"]
    assert candidates and all(c["source_hit"] == 0 and c["page"] == 22 for c in candidates)
    assert all(c["start"] <= selected["start"] and c["end"] >= selected["end"] for c in candidates)
    assert all(c["text"] == P22[c["start"]:c["end"]] for c in candidates)
    assert enriched["reason"] == feedback["reason"]
    assert "unlocalized/global" in enriched["repair_scope"] and "not an approval of untargeted" in enriched["repair_scope"]
    assert not any('passed' in c for c in candidates)  # Context aid is not semantic approval.


def test_legacy_or_global_reason_does_not_guess_source_binding_or_lock_unlocalized_issues():
    value = review_value()
    _, sources = _note_span_catalogues(context(evidence=[{"page": 22, "text": P22}]))
    literal = value.model_dump()
    _, feedback = _note_review_feedback({"passed": False, "reason": "还存在未定位的全局原意损失。"}, value)
    enriched = _note_repair_context_feedback(feedback, value, literal, sources)
    assert enriched["findings"] == [] and enriched["repair_targets"] == []
    assert enriched["reason"] == "还存在未定位的全局原意损失。"
    _, localized = _note_review_feedback({"passed": False, "reason": "局部缺证与未定位问题仍均须处理。",
        "findings": [finding()]}, value)
    target = _note_repair_context_feedback(localized, value, literal, sources)["repair_targets"][0]
    assert target["binding_scope"] == "legacy_literal_no_id_binding"
    assert target["context_candidates"] == [] and target["selected_evidence_span_id"] is None


@pytest.mark.parametrize("bad_id", ["foreign_id", True])
def test_repair_candidate_lookup_never_guesses_an_unknown_current_wire_id(bad_id):
    value = review_value()
    _, feedback = _note_review_feedback({"passed": False, "reason": "缺证", "findings": [finding()]}, value)
    _, sources = _note_span_catalogues(context(evidence=[{"page": 22, "text": P22}]))
    with pytest.raises(StudioNoteContractError):
        _note_repair_context_feedback(feedback, value, {"suggestions": [{"evidence_span_id": bad_id}]}, sources)
