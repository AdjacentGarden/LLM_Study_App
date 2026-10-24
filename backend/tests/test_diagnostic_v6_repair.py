"""Replay observed wire failures, without pretending scripted verdicts prove quality."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from diagnostic_test_inputs import ArchivedFreeformDiagnosticGenerator
from test_diagnostic_generation import FakeClient

from adaptive_learning.assessment import item_generation as generation
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote


@pytest.fixture
def observed():
    return json.loads((Path(__file__).parent / "fixtures" /
                       "diagnostic_v5_repair_regression.json").read_text())


def structure_for(source):
    rows = source["knowledge_points"]
    evidence = [SourceQuote(**quote) for row in rows for quote in row["evidence"]]
    return BookStructure(
        title="Recorded diagnostic contract regression", summary="Controlled offline wire replay",
        source_page_count=125,
        chapters=[ChapterDraft(
            chapter_id=source["chapter_id"], order=1, title=source["chapter_title"],
            start_page=min(quote.page_number for quote in evidence),
            end_page=max(quote.page_number for quote in evidence),
            summary="Source excerpts are preserved exactly; no fixture verdict certifies quality.",
            knowledge_points=[row["label"] for row in rows], source_block_ids=[], evidence=evidence,
            knowledge_point_evidence={row["label"]: [SourceQuote(**quote) for quote in row["evidence"]]
                                      for row in rows},
        )],
    )


@pytest.mark.parametrize("accepted", [True, False])
def test_real_compact_tool_names_reach_semantic_review_without_padding_or_fact_approval(observed, accepted):
    case = observed["short_tool_options"]
    writer = FakeClient(deepcopy(case["generation_response"]))
    target = case["target_knowledge_point_id"]
    requests = []
    class Reviewer:
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            requests.append(request)
            tool = next(row for row in request["items"] if row["knowledge_point_id"] == target)
            assert set(tool["options"]) == {"DNA连接酶", "限制性核酸内切酶", "质粒", "噬菌体"}
            assert sorted(tool["choice_length_metrics"]["option_characters"]) == [2, 3, 6, 8]
            assert tool["choice_length_metrics"]["correct_characters"] == 6
            assert not tool["choice_length_metrics"]["correct_is_uniquely_longest"]
            return {"reviews": [{"knowledge_point_id": row["knowledge_point_id"],
                "accepted": accepted, "issues": [] if accepted else [
                    "p98只说明缝合骨架缺口，当前额外重组场景需要来源支持，不能由标签补足。"]}
                for row in request["items"]]}
    generator = ArchivedFreeformDiagnosticGenerator(
        writer, review_client=Reviewer(), allow_grounded_answer_rewrite=True, validation_retries=0,
    )
    if accepted:
        items = generator.generate(structure_for(case["source"]))
        assert set(items[-1].options) == set(case["generation_response"]["items"][-1]["options"])
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match="review rejected"):
            generator.generate(structure_for(case["source"]))
    assert writer.calls == len(requests) == 1


@pytest.mark.parametrize("accept_pending", [True, False])
def test_real_unchanged_approved_items_are_not_sent_back_for_an_opposite_verdict(observed, accept_pending):
    case = observed["unchanged_review_reversals"]
    first, second = case["attempts"]
    first_response = first["generation_response"]
    second_response = second["generation_response"]
    first_reviews = first["review_verdict"]["reviews"]
    accepted_ids = {row["knowledge_point_id"] for row in first_reviews if row["accepted"]}
    pending_ids = [row["knowledge_point_id"] for row in first_reviews if not row["accepted"]]
    assert len(accepted_ids) == 2 and len(pending_ids) == 1
    writer_requests, reviewer_requests, events = [], [], []
    class Writer:
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            writer_requests.append(request)
            if len(writer_requests) == 2:
                assert {row["knowledge_point_id"] for row in request["preserve_accepted_items"]} == accepted_ids
            return deepcopy(first_response if len(writer_requests) == 1 else second_response)
    class Reviewer:
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            reviewer_requests.append(request)
            if len(reviewer_requests) == 1:
                return {"reviews": deepcopy(first_reviews)}
            # Previously these exact approved inputs received opposite verdicts.
            # They must be reused internally, never re-sent to this fake judge.
            assert [row["knowledge_point_id"] for row in request["items"]] == pending_ids
            return {"reviews": [{"knowledge_point_id": key, "accepted": accept_pending,
                "issues": [] if accept_pending else ["未通过题仍有事实或干扰项问题。"]}
                for key in pending_ids]}
    generator = ArchivedFreeformDiagnosticGenerator(
        Writer(), review_client=Reviewer(), allow_grounded_answer_rewrite=True,
        validation_retries=1, audit_callback=events.append,
    )
    if accept_pending:
        result = generator.generate(structure_for(case["source"]))
        assert len(result) == 3
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match="未通过题"):
            generator.generate(structure_for(case["source"]))
    assert len(writer_requests) == len(reviewer_requests) == 2
    reused = next(event for event in events if event["event"] == "review_items_reused")
    assert {row["knowledge_point_id"] for row in reused["items"]} == accepted_ids
    assert all(len(row["approval_key"]) == 64 for row in reused["items"])


def test_three_targets_are_program_assigned_and_identical_for_writer_and_reviewer(observed):
    case = observed["short_tool_options"]
    requests = []
    class Writer(FakeClient):
        def structured(self, *, user, **kwargs):
            requests.append(json.loads(user))
            response = deepcopy(self.payload)
            for row in response["items"]:
                row["cognitive_target"] = {"id": "writer_invented_target"}
            return response
    class Reviewer:
        def structured(self, *, system, user, **kwargs):
            request = json.loads(user)
            expected = ["basic_recognition", "relation_understanding", "source_application"]
            assert [row["cognitive_target"]["id"] for row in request["items"]] == expected
            assert [row["cognitive_target"]["id"] for row in requests[0]["knowledge_points"]] == expected
            assert request["question_contract"] == requests[0]["question_contract"]
            assert "正确结论可从原文找到不是泄题" in system
            assert "自然术语最长本身不是拒绝依据" in system
            assert "基础辨认" in request["question_contract"]
            return {"reviews": [{"knowledge_point_id": row["knowledge_point_id"],
                                  "accepted": True, "issues": []} for row in request["items"]]}
    result = ArchivedFreeformDiagnosticGenerator(
        Writer(case["generation_response"]), review_client=Reviewer(),
        allow_grounded_answer_rewrite=True, validation_retries=0,
    ).generate(structure_for(case["source"]))
    assert all("cognitive_target" not in item.model_dump() for item in result)


def test_changed_review_identity_forces_recheck_and_can_reject_old_approved_items(observed):
    case = observed["unchanged_review_reversals"]
    first, second = case["attempts"]
    requests = []
    generator = None
    class Writer:
        calls = 0
        def structured(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                generator.review_identity["model"] = "changed-independent-reviewer"
            return deepcopy((first if self.calls == 1 else second)["generation_response"])
    class Reviewer:
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            requests.append(request)
            assert len(request["items"]) == 3
            return deepcopy((first if len(requests) == 1 else second)["review_verdict"])
    generator = ArchivedFreeformDiagnosticGenerator(
        Writer(), review_client=Reviewer(), allow_grounded_answer_rewrite=True,
        validation_retries=1,
    )
    with pytest.raises(generation.DiagnosticGenerationError, match="review rejected"):
        generator.generate(structure_for(case["source"]))
    assert len(requests) == 2


@pytest.mark.parametrize("fault", ["omitted", "approved_sibling", "duplicate", "string_true"])
def test_pending_review_still_requires_exact_complete_strict_verdicts(observed, fault):
    case = observed["unchanged_review_reversals"]
    first, second = case["attempts"]
    class Writer:
        calls = 0
        def structured(self, **kwargs):
            self.calls += 1
            return deepcopy((first if self.calls == 1 else second)["generation_response"])
    class Reviewer:
        calls = 0
        def structured(self, *, user, **kwargs):
            self.calls += 1
            request = json.loads(user)
            if self.calls == 1:
                return deepcopy(first["review_verdict"])
            assert len(request["items"]) == 1
            verdict = {"knowledge_point_id": request["items"][0]["knowledge_point_id"],
                       "accepted": True, "issues": []}
            if fault == "omitted":
                return {"reviews": []}
            if fault == "approved_sibling":
                verdict["knowledge_point_id"] = next(row["knowledge_point_id"]
                    for row in first["review_verdict"]["reviews"] if row["accepted"])
            elif fault == "duplicate":
                return {"reviews": [verdict, deepcopy(verdict)]}
            else:
                verdict["accepted"] = "true"
            return {"reviews": [verdict]}
    with pytest.raises(generation.DiagnosticGenerationError):
        ArchivedFreeformDiagnosticGenerator(
            Writer(), review_client=Reviewer(), allow_grounded_answer_rewrite=True,
            validation_retries=1,
        ).generate(structure_for(case["source"]))


@pytest.mark.parametrize("change", ["source", "target", "prompt", "options", "answer", "id", "reviewer", "protocol"])
def test_cached_approval_identity_changes_with_every_reviewed_input(observed, monkeypatch, change):
    case = observed["short_tool_options"]
    source = {"book_title": "Book", **deepcopy(case["source"])}
    for index, row in enumerate(source["knowledge_points"]):
        row["cognitive_target"] = dict(generation._COGNITIVE_TARGETS[index])
    structure = structure_for(case["source"])
    chapter = structure.chapters[0]
    by_id = {row["knowledge_point_id"]: (row["knowledge_point_id"], row["label"],
             [SourceQuote(**quote) for quote in row["evidence"]]) for row in source["knowledge_points"]}
    item = generation.DiagnosticItemGenerator._validate_item(
        generation.ItemDraft.model_validate(case["generation_response"]["items"][0]),
        chapter, by_id, allow_rewrite=True,
    )
    generator = ArchivedFreeformDiagnosticGenerator(FakeClient({}), review_client=FakeClient({}))
    key = generator._approval_key(source, item)
    assert generator._approval_key(deepcopy(source), item.model_copy(deep=True)) == key
    if change == "source":
        source["knowledge_points"][0]["evidence"][0]["quote"] += "新的限定条件。"
    elif change == "target":
        source["knowledge_points"][0]["cognitive_target"] = {"id": "different_goal"}
    elif change == "prompt":
        item.prompt += "改变了题设。"
    elif change == "options":
        item.options.reverse()
    elif change == "answer":
        item.expected_answer += "新结论。"
    elif change == "id":
        item.item_id += "_different"
    elif change == "reviewer":
        generator.review_identity["model"] = "different_review_model"
    else:
        monkeypatch.setattr(generation, "_ITEM_REVIEW_SYSTEM", generation._ITEM_REVIEW_SYSTEM + "新协议")
    assert generator._approval_key(source, item) != key
