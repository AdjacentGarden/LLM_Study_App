import json

import pytest
from studio_note_review_fixtures import note_review_fixture
from test_community import community as community
from test_studio import note, run
from test_studio import setup as setup
from test_studio_note_span_wire import TRANSCRIPT

P22 = "在\n配子中成对的基因只有一个，同样，成对的染色体也只有\n一条。"
P23 = "（4）非等位基因在形成配子时自由组合，非同源染色\n体在减数第一次分裂后期也是自由组合的。"
COUNT = "可补充萨顿的平行关系依据：配子中成对的基因只有一个，成对的染色体也只有一条。"
COMPOUND = COUNT[:-1] + "；非同源染色体在减数第一次分裂后期自由组合。"


@pytest.mark.parametrize("repair_removes_uncited_clause", [False, True])
def test_actual_p22_p23_compound_advice_cannot_borrow_global_evidence_during_review(setup, monkeypatch, repair_removes_uncited_clause):
    a, _, studio = setup
    saved = a.post("/api/studio/notes", json=note()).json()
    key = a.post("/api/studio/notes/note_123456/analyze", json={
        "revision": saved["revision"], "action": "improve", "transcript": TRANSCRIPT,
        "consent": True, "request_id": "own_quote_p22_p23_111"}).json()["id"]
    evidence = [{"page": 22, "text": P22}, {"page": 23, "text": P23}]
    prompts, review_inputs = [], []

    def llm(prompt, images=None):
        prompts.append(prompt)
        if prompt.startswith("审核笔记整理"):
            packet = json.loads(prompt.split("\n待审核数据：", 1)[1])
            review_inputs.append(packet)
            assert set(packet) == {"confirmed_transcript", "summary", "polished", "suggestions", "claim_coverage"}
            assert packet["confirmed_transcript"] == TRANSCRIPT
            item = packet["suggestions"][0]
            assert item["evidence"] == P22 and item["page"] == 22
            assert "非等位基因" not in prompt  # The distinctive p23 source is absent.
            assert "不可变教材引文目录" not in prompt and "完整检索原文" not in prompt
            assert "首半句有据不能替后半句背书" in prompt
            raw = {"passed": item["suggestion"] == COUNT,
                   "reason": "p22只支持配子数量，未引用的p23不能支持本条新增阶段/自由组合子句。"}
            if not raw["passed"]:
                raw["findings"] = [{"field": "suggestion", "suggestion_index": 0,
                    "claim": "非同源染色体在减数第一次分裂后期自由组合。", "issue": "本条未引p23。"}]
            return note_review_fixture(prompt, raw)
        # Generation and its one semantic repair retain both complete RAG sources.
        assert json.dumps(P23, ensure_ascii=False)[1:-1] in prompt
        assert "跨来源内容应拆成" in prompt and "全部新增事实和子句" in prompt
        registered = studio._note_trace.context
        original = next(o for o in registered["original_spans"] if "在减数分裂过程中" in o["text"])
        source = next(s for s in registered["evidence_spans"] if s["page"] == 22)
        fixed = len(prompts) > 1 and repair_removes_uncited_clause
        return {"summary": "保留遗传学要点和个人操作记录，未展示展开理解。",
                "polished": TRANSCRIPT,
                "suggestions": [{"original_span_id": original["id"], "evidence_span_id": source["id"],
                                 "kind": "可以补充", "suggestion": COUNT if fixed else COMPOUND}]}

    monkeypatch.setattr(studio, "evidence", lambda *_: evidence)
    monkeypatch.setattr(studio, "llm", llm)
    if repair_removes_uncited_clause:
        run(studio, a, key)
        result = a.get("/api/studio/jobs/" + key).json()
        assert result["status"] == "succeeded"
        assert result["result"]["suggestions"][0]["suggestion"] == COUNT
    else:
        with pytest.raises(ValueError, match="note review failed"):
            run(studio, a, key)
        assert a.get("/api/studio/jobs/" + key).json()["status"] != "succeeded"
    assert len(prompts) == 4 and len(review_inputs) == 2
    traces = [json.loads(p.read_text()) for p in (studio.assets / "private-note-traces").glob(key + "-*.json")]
    reviews = sorted((t for t in traces if t["stage"] == "final_review"), key=lambda t: t["created"])
    assert [t["review_input"] for t in reviews] == review_inputs
    assert reviews[0]["response"]["passed"] is False
    assert all("Only review_input" in t["input_scope"] for t in reviews)
