"""Mechanical recovery contracts, not model-generated content certification."""
import json
from copy import deepcopy

import pytest
from test_diagnostic_generation import structure, valid_payload
from test_diagnostic_item_repairs import Reviewer, Writer, cache, generator

from adaptive_learning.assessment import item_repairs
from adaptive_learning.assessment.diagnostic_cache import (
    DiagnosticChapterSubmissionBlocked,
    DiagnosticRepairPending,
)
from adaptive_learning.assessment.item_generation import (
    DiagnosticGenerationError,
    DiagnosticItemGenerator,
    ItemDraft,
    _negative_answer_instruction,
)
from adaptive_learning.llm.client import LLMTimeoutError


@pytest.mark.parametrize("prompt", [
    "在不包括额外处理的条件下，以下哪项是原文支持的结论？",
    "若甲不属于给定类别时，以下哪项描述符合原文？",
    "已知条件不符合方案甲时，哪项正向判断有原文支持？",
    "原文说‘不正确的是’，这段引用体现哪种表述方式？",
    "甲不属于该类对象，以下哪项是其原文支持的特征？",
])
def test_negative_premise_or_quotation_is_not_a_negative_answer_instruction(prompt):
    assert not _negative_answer_instruction(prompt)
    chapter = structure().chapters[0]
    points = generator(Writer(), Reviewer())._select_points(chapter)
    row = {**valid_payload()["items"][0], "prompt": prompt}
    result = DiagnosticItemGenerator._validate_item(ItemDraft.model_validate(row), chapter,
        {point[0]: point for point in points}, allow_rewrite=True)
    assert result.prompt == prompt  # Only mechanical validity: independent review is still required.


@pytest.mark.parametrize("prompt", [
    "下列哪项不正确？", "以下错误的说法是？", "下列不属于甲类的是？",
    "哪一项不符合原文所列条件？", "下列哪项不包括必要条件？",
    "不正确的是哪一项？", "请选择错误的一项。",
])
def test_explicit_negative_answer_instructions_remain_rejected(prompt):
    assert _negative_answer_instruction(prompt)
    chapter = structure().chapters[0]
    subject = generator(Writer(), Reviewer())
    with pytest.raises(DiagnosticGenerationError, match="negative stem"):
        subject._validate_item(ItemDraft.model_validate({**valid_payload()["items"][0], "prompt": prompt}),
            chapter, {point[0]: point for point in subject._select_points(chapter)}, allow_rewrite=True)


class BadWireOnce(Reviewer):
    def __init__(self, bad_wire):
        super().__init__()
        self.bad_wire = bad_wire

    def structured(self, **kwargs):
        response = super().structured(**kwargs)
        if len(self.calls) == 1:
            return self.bad_wire(deepcopy(response))
        return response


def missing_target(response):
    response["reviews"][0].pop("required_edits")
    return response


def wrong_coverage(response):
    response["reviews"][0]["knowledge_point_id"] = "kp_unrequested"
    return response


def contradictory_acceptance(response):
    response["reviews"][0]["issues"] = ["原文支持仍未确定"]
    return response


def duplicate_coverage(response):
    response["reviews"].append(deepcopy(response["reviews"][0]))
    return response


@pytest.mark.parametrize("bad_wire", [missing_target, wrong_coverage, contradictory_acceptance, duplicate_coverage])
def test_received_review_wire_error_reuses_exact_candidate_across_repository_restart(tmp_path, monkeypatch, bad_wire):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), BadWireOnce(bad_wire)
    with pytest.raises(DiagnosticRepairPending) as pending:
        generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    assert pending.value.details["diagnostics_completed"] == 1  # Invalid review did not approve its item.
    assert len(writer.calls) == 2
    clock[0] += 200
    result = generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    assert len(result) == 2
    assert len(writer.calls) == 2  # Recovery receives only a new independent review, never a new draft.
    assert len(reviewer.calls) == 3
    assert reviewer.calls[0]["items"] == reviewer.calls[-1]["items"]


def test_unknown_review_after_format_recovery_is_fenced_without_another_paid_replay(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), BadWireOnce(missing_target)
    with pytest.raises(DiagnosticRepairPending):
        generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    clock[0] += 200
    target = valid_payload()["items"][0]["knowledge_point_id"]
    original = reviewer.structured
    timeouts = []

    def timeout(**kwargs):
        key = json.loads(kwargs["user"])["items"][0]["knowledge_point_id"]
        assert key == target
        timeouts.append(key)
        raise LLMTimeoutError("controlled ambiguous reviewer result")

    reviewer.structured = timeout
    with pytest.raises(LLMTimeoutError):
        generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    assert len(writer.calls) == 2 and timeouts == [target]
    before = len(reviewer.calls)
    reviewer.structured = original
    clock[0] += 200
    with pytest.raises(DiagnosticChapterSubmissionBlocked):
        generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    assert len(writer.calls) == 2 and len(reviewer.calls) == before


def test_semantic_rejection_after_format_recovery_still_requires_real_item_repair(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), BadWireOnce(missing_target)
    with pytest.raises(DiagnosticRepairPending):
        generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    clock[0] += 200
    target = valid_payload()["items"][0]["knowledge_point_id"]
    original = reviewer.structured

    def reject_received_candidate(**kwargs):
        response = original(**kwargs)
        row = response["reviews"][0]
        if row["knowledge_point_id"] == target:
            row.update(accepted=False, issues=["存在第二个同样正确的选项"],
                       required_edits={"prompt": False, "option_indices": [1]})
        return response

    reviewer.structured = reject_received_candidate
    with pytest.raises(DiagnosticRepairPending) as pending:
        generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    assert pending.value.details["diagnostics_completed"] == 1
    assert len(writer.calls) == 2
    clock[0] += 200
    reviewer.structured = original
    assert len(generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")) == 2
    assert len(writer.calls) == 3  # Received semantic refusal was not treated as a formatting fault.
    assert writer.calls[-1]["repair_feedback"]["stage"] == "semantic_review"


def test_repeated_review_format_errors_back_off_without_consuming_semantic_redesign_attempts(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer = Writer()
    target = valid_payload()["items"][0]["knowledge_point_id"]

    class FormatThenSemantic(Reviewer):
        def structured(self, **kwargs):
            response = super().structured(**kwargs)
            row = response["reviews"][0]
            if row["knowledge_point_id"] == target:
                count = self.counts[target]
                if count <= 3:
                    row.pop("required_edits")
                elif count == 4:
                    row.update(accepted=False, issues=["实际缺少来源必要条件"],
                               required_edits={"prompt": False, "option_indices": [1]})
            return response

    reviewer = FormatThenSemantic()
    value = structure()
    for number, delay in ((1, 5), (2, 10), (3, 20)):
        subject = generator(writer, reviewer)
        store = cache(tmp_path)
        with pytest.raises(DiagnosticRepairPending):
            subject.generate(value, chapter_cache=store, cache_scope="book")
        points = subject._select_points(value.chapters[0])
        identity = item_repairs._identity(subject, value, value.chapters[0], points, 0, "book")
        state = store.repair_state(identity)
        assert state["attempt"] == 0
        assert state["review_format_failures"] == number
        assert state["next_retry_at"] == clock[0] + delay
        assert state["context"]["repair_feedback"]["stage"] == "review_format"
        assert "_pending_candidate" in state["context"]
        assert len(writer.calls) == 2
        clock[0] += 200
    with pytest.raises(DiagnosticRepairPending):
        subject.generate(value, chapter_cache=cache(tmp_path), cache_scope="book")
    state = cache(tmp_path).repair_state(identity)
    assert state["attempt"] == 1 and state["review_format_failures"] == 0
    assert "_pending_candidate" not in state["context"]
    clock[0] += 200
    assert len(generator(writer, reviewer).generate(value, chapter_cache=cache(tmp_path), cache_scope="book")) == 2
    assert len(writer.calls) == 3
    assert writer.calls[-1]["generation_strategy"] == "repair"


def test_finite_format_retry_uses_existing_pass_budget_without_regenerating_writer():
    class BatchWriter:
        def __init__(self):
            self.calls = 0
        def structured(self, **kwargs):
            self.calls += 1
            return valid_payload()

    class BatchReviewer:
        def __init__(self):
            self.calls = 0
        def structured(self, *, user, **kwargs):
            self.calls += 1
            rows = json.loads(user)["items"]
            response = {"reviews": [{"knowledge_point_id": row["knowledge_point_id"],
                "accepted": True, "issues": [], "required_edits": None} for row in rows]}
            if self.calls == 1:
                response["reviews"][0].pop("required_edits")
            return response

    writer, reviewer = BatchWriter(), BatchReviewer()
    subject = DiagnosticItemGenerator(writer, review_client=reviewer,
        validation_retries=1, allow_grounded_answer_rewrite=True)
    assert len(subject.generate(structure())) == 2
    assert writer.calls == 1
    assert reviewer.calls == 2
