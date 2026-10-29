"""Evidence-adapted target identity tests; scripted verdicts are not quality proof."""
import json
from unittest.mock import patch

import pytest
from diagnostic_test_inputs import controlled_application
from test_diagnostic_chapter_generation_cache import book
from test_diagnostic_item_repairs import cache

from adaptive_learning.assessment import item_generation as generation
from adaptive_learning.assessment import item_repairs
from adaptive_learning.assessment.diagnostic_cache import DiagnosticRepairPending
from adaptive_learning.ingestion.models import SourceQuote


def source_book(*, conditions):
    value = book().model_copy(update={"chapters": book().chapters[:1]}, deep=True)
    chapter = value.chapters[0]
    text = ("原文过程需要条件甲、条件乙。条件甲成立时，过程乙适用。" if conditions else
            "术语甲指一类受控对象。术语乙是另一类受控对象的名称。术语丙指第三类对象。")
    quote = SourceQuote(page_number=1, quote=text)
    chapter.evidence = [quote]
    chapter.knowledge_point_evidence = {label: [quote] for label in chapter.knowledge_points}
    return value


class RoleWriter:
    def __init__(self):
        self.calls = []

    def structured(self, *, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        rows = []
        for index, point in enumerate(request["knowledge_points"]):
            if point["cognitive_target"]["id"] == "source_application":
                rows.append(controlled_application(request, index=index))
            else:
                rows.append({"knowledge_point_id": point["knowledge_point_id"], "type": "choice",
                    "prompt": "以下哪项是原文支持的核心概念或判断？",
                    "options": [point["label"], "受控条件甲下的另一判断", "受控对象乙的另一判断", "受控关系丙的另一判断"],
                    "correct_index": 0, "expected_answer": point["label"], "rubric": [],
                    "difficulty": 0, "estimated_seconds": 40})
        return {"items": rows}


class RoleReviewer:
    def __init__(self, *, reject_once=False):
        self.calls = []
        self.reject_once = reject_once

    def structured(self, *, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        accepted = not self.reject_once or len(self.calls) != 3
        return {"reviews": [{"knowledge_point_id": row["knowledge_point_id"],
            "accepted": accepted, "issues": [] if accepted else ["受控的真实语义否决仍要求修复"],
            "required_edits": None if accepted else {"prompt": False, "option_indices": [1]}}
            for row in request["items"]]}


def subject(writer, reviewer, **kwargs):
    return generation.DiagnosticItemGenerator(writer, review_client=reviewer,
        validation_retries=None, allow_grounded_answer_rewrite=True, **kwargs)


@pytest.mark.parametrize("conditions,expected", [
    (False, ["basic_recognition", "relation_understanding", "basic_recognition"]),
    (True, ["basic_recognition", "relation_understanding", "source_application"]),
])
def test_writer_reviewer_rendering_and_private_identity_share_the_evidence_target(tmp_path, conditions, expected):
    writer, reviewer, events = RoleWriter(), RoleReviewer(), []
    gen = subject(writer, reviewer, audit_callback=events.append)
    value = source_book(conditions=conditions)
    result = gen.generate(value, chapter_cache=cache(tmp_path), cache_scope="book")
    assert len(result) == 3
    assert [request["knowledge_points"][0]["cognitive_target"]["id"] for request in writer.calls] == expected
    assert [request["items"][0]["cognitive_target"]["id"] for request in reviewer.calls] == expected
    assert all(request["cognitive_target_policy"] == generation.DIAGNOSTIC_TARGET_POLICY
               for request in writer.calls + reviewer.calls)
    assert [event["cognitive_target"]["id"] for event in events if event["event"] == "item_attempt_started"] == expected
    third = writer.calls[-1]
    assert third["knowledge_points"][0]["response_shape"] == ("private_application_plan" if conditions else "ordinary_choice")
    assert ("application_task" in reviewer.calls[-1]["items"][0]) is conditions


def test_feedback_never_changes_a_frozen_source_adapted_target(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = RoleWriter(), RoleReviewer(reject_once=True)
    value = source_book(conditions=False)
    with pytest.raises(DiagnosticRepairPending):
        subject(writer, reviewer).generate(value, chapter_cache=cache(tmp_path), cache_scope="book")
    assert len(writer.calls) == 3
    clock[0] += 200
    assert len(subject(writer, reviewer).generate(value, chapter_cache=cache(tmp_path), cache_scope="book")) == 3
    assert len(writer.calls) == 4
    assert writer.calls[-1]["knowledge_points"][0]["cognitive_target"]["id"] == "basic_recognition"
    assert reviewer.calls[-1]["items"][0]["cognitive_target"]["id"] == "basic_recognition"
    assert writer.calls[-1]["repair_feedback"]["stage"] == "semantic_review"


def test_prior_fixed_target_approval_cannot_satisfy_the_new_policy(tmp_path):
    writer, reviewer = RoleWriter(), RoleReviewer()
    value = source_book(conditions=False)
    store = cache(tmp_path)
    def legacy(point, index):
        return dict(generation._COGNITIVE_TARGETS[index % 3])
    with patch.object(generation, "DIAGNOSTIC_TARGET_POLICY", "archived-fixed-role-policy"), \
            patch.object(generation.DiagnosticItemGenerator, "_target_for_point", staticmethod(legacy)):
        assert len(subject(writer, reviewer).generate(value, chapter_cache=store, cache_scope="book")) == 3
    assert len(writer.calls) == 3
    assert writer.calls[-1]["knowledge_points"][0]["cognitive_target"]["id"] == "source_application"
    assert len(subject(writer, reviewer).generate(value, chapter_cache=store, cache_scope="book")) == 3
    assert len(writer.calls) == len(reviewer.calls) == 6
    assert writer.calls[-1]["knowledge_points"][0]["cognitive_target"]["id"] == "basic_recognition"


def test_application_target_with_source_conditions_still_requires_independent_semantic_acceptance(tmp_path):
    writer, reviewer = RoleWriter(), RoleReviewer(reject_once=True)
    with pytest.raises(DiagnosticRepairPending) as pending:
        subject(writer, reviewer).generate(source_book(conditions=True), chapter_cache=cache(tmp_path), cache_scope="book")
    assert pending.value.details["diagnostics_completed"] == 2
    assert pending.value.details["diagnostics_total"] == 3
    assert reviewer.calls[-1]["items"][0]["cognitive_target"]["id"] == "source_application"


@pytest.mark.parametrize("text", [
    "若条件甲成立，则方案乙适用。", "如果记录甲具备该条件就采用方案乙。",
    "当对象满足原文条件时，结果乙成立。", "If condition A holds, outcome B applies.",
    "When condition A holds then outcome B applies.",
])
def test_explicit_condition_with_a_consequent_is_a_weak_application_signal(text):
    point = ("kp_condition", "source condition", [SourceQuote(page_number=1, quote=text)])
    assert generation.DiagnosticItemGenerator._application_signal(point[2]) > 0
    assert generation.DiagnosticItemGenerator._target_for_point(point, 2)["id"] == "source_application"


@pytest.mark.parametrize("text", ["术语甲是一类对象的名称。", "如果条件甲成立。", "若条件甲成立则。", "If condition A holds."])
def test_a_definition_or_unfinished_premise_does_not_manufacture_a_condition(text):
    point = ("kp_definition", "source definition", [SourceQuote(page_number=1, quote=text)])
    assert generation.DiagnosticItemGenerator._application_signal(point[2]) == 0
    assert generation.DiagnosticItemGenerator._target_for_point(point, 2)["id"] == "basic_recognition"
