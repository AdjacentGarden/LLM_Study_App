"""Offline scheduling/repair contracts; scripted verdicts are not content certification."""
import json
from collections import Counter
from copy import deepcopy

import httpx
import pytest
from test_diagnostic_generation import structure, valid_payload

from adaptive_learning.assessment import item_repairs
from adaptive_learning.assessment.diagnostic_cache import (
    DiagnosticChapterSubmissionBlocked,
    DiagnosticRepairPending,
    RepositoryDiagnosticChapterCache,
)
from adaptive_learning.assessment.item_generation import DiagnosticItemGenerator
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.ingestion.preparation import PreparationDeferred, PreparationQueue
from adaptive_learning.llm.client import (
    LLMCooldownError,
    LLMError,
    LLMRefusalError,
    LLMTimeoutError,
)


class Writer:
    def __init__(self):
        self.calls = []

    def structured(self, *, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        points = request["knowledge_points"]
        assert len(points) == 1
        key = points[0]["knowledge_point_id"]
        return {"items": [deepcopy(next(row for row in valid_payload()["items"]
                                       if row["knowledge_point_id"] == key))]}


class Reviewer:
    def __init__(self, rejects=0):
        self.rejects = rejects
        self.calls = []
        self.counts = Counter()

    def structured(self, *, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        assert len(request["items"]) == 1
        row = request["items"][0]
        key = row["knowledge_point_id"]
        self.counts[key] += 1
        accepted = key != valid_payload()["items"][0]["knowledge_point_id"] or self.counts[key] > self.rejects
        return {"reviews": [{"knowledge_point_id": key, "accepted": accepted,
            "issues": [] if accepted else ["指出的选项需按原文限定修正"],
            "required_edits": None if accepted else {"prompt": False, "option_indices": [1]}}]}


def cache(tmp_path):
    return RepositoryDiagnosticChapterCache(SQLiteAssessmentRepository(tmp_path / "assessments.sqlite3"))


def generator(writer, reviewer):
    return DiagnosticItemGenerator(writer, review_client=reviewer,
        validation_retries=None, allow_grounded_answer_rewrite=True)


def test_unlimited_single_item_repairs_survive_restart_and_never_regenerate_approved_item(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), Reviewer(rejects=9)
    subject = generator(writer, reviewer)
    for attempt in range(9):
        # Recreate both repository and generator to simulate restart after every verdict.
        with pytest.raises(DiagnosticRepairPending) as pending:
            generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
        assert pending.value.details["diagnostics_completed"] == 1
        assert pending.value.details["diagnostics_total"] == 2
        if attempt == 0:
            calls = len(writer.calls)
            with pytest.raises(DiagnosticRepairPending):
                subject.generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
            assert len(writer.calls) == calls  # Polling before the durable retry time does no work.
        clock[0] += 200
    result = subject.generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    assert len(result) == 2
    counts = Counter(call["knowledge_points"][0]["knowledge_point_id"] for call in writer.calls)
    keys = [row["knowledge_point_id"] for row in valid_payload()["items"]]
    assert counts == {keys[0]: 10, keys[1]: 1}
    assert reviewer.counts == counts
    assert all(len(call["knowledge_points"]) == 1 for call in writer.calls)
    assert writer.calls[2]["repair_feedback"]["stage"] == "semantic_review"
    assert writer.calls[2]["required_repairs"]
    assert any(call["generation_strategy"] == "redesign" for call in writer.calls)
    before = len(writer.calls)
    assert subject.generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book") == result
    assert len(writer.calls) == before


def test_reviewer_cooldown_reuses_candidate_without_paying_writer_again(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), Reviewer()
    original = reviewer.structured
    cooling = [True]

    def review(**kwargs):
        if cooling[0]:
            raise LLMCooldownError(5)
        return original(**kwargs)

    reviewer.structured = review
    with pytest.raises(DiagnosticRepairPending) as pending:
        generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    assert pending.value.details["message"].startswith("等待模型服务")
    assert len(writer.calls) == 2
    cooling[0] = False
    clock[0] += 200
    assert len(generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")) == 2
    assert len(writer.calls) == 2


def test_reviewer_content_refusal_redesigns_only_that_item(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), Reviewer()
    original = reviewer.structured
    calls = [0]

    def review(**kwargs):
        calls[0] += 1
        if calls[0] == 1:
            raise LLMRefusalError("controlled content refusal")
        return original(**kwargs)

    reviewer.structured = review
    with pytest.raises(DiagnosticRepairPending):
        generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    clock[0] += 200
    assert len(generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")) == 2
    counts = Counter(call["knowledge_points"][0]["knowledge_point_id"] for call in writer.calls)
    assert sorted(counts.values()) == [1, 2]


def test_unknown_model_outcome_is_fenced_instead_of_blind_resubmission(tmp_path):
    writer, reviewer = Writer(), Reviewer()

    def timeout(**kwargs):
        raise LLMTimeoutError("controlled unknown outcome")

    reviewer.structured = timeout
    subject = generator(writer, reviewer)
    with pytest.raises(LLMTimeoutError):
        subject.generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    before = len(writer.calls)
    reviewer.structured = Reviewer().structured
    with pytest.raises(DiagnosticChapterSubmissionBlocked):
        subject.generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    # The other unattempted item may still advance, but the timed-out item cannot.
    first_key = valid_payload()["items"][0]["knowledge_point_id"]
    assert sum(call["knowledge_points"][0]["knowledge_point_id"] == first_key for call in writer.calls) == 1
    assert len(writer.calls) >= before


@pytest.mark.parametrize("status", [429, 503])
def test_received_service_rejection_waits_and_resumes_without_regenerating_candidate(tmp_path, monkeypatch, status):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), Reviewer()
    original = reviewer.structured

    def unavailable(**kwargs):
        request = httpx.Request("POST", "https://review.invalid/model")
        response = httpx.Response(status, request=request, headers={"Retry-After": "7"})
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise LLMError("controlled service rejection") from error

    reviewer.structured = unavailable
    with pytest.raises(DiagnosticRepairPending) as pending:
        generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    assert pending.value.details["retry_after_seconds"] == 7
    assert "等待模型服务" in pending.value.details["message"]
    reviewer.structured = original
    clock[0] += 8
    assert len(generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")) == 2
    assert len(writer.calls) == 2


def test_preparation_deferral_preserves_checkpoints_and_allows_other_book_to_run(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    calls = []
    attempts = Counter()

    def diagnostics(book):
        attempts[book] += 1
        if book == "first" and attempts[book] <= 8:
            raise PreparationDeferred({"message": "正在根据审核意见优化题目",
                "diagnostics_completed": 1, "diagnostics_total": 2, "retry_after_seconds": 5})

    steps = [("indexing", lambda book: calls.append((book, "index"))),
             ("structuring", lambda book: calls.append((book, "structure"))),
             ("diagnostics", diagnostics)]
    path = tmp_path / "prepare.sqlite3"
    queue = PreparationQueue(path, steps)
    queue.enqueue("first")
    assert queue.process_one()
    assert queue.get("first")["status"] == "queued"
    assert queue.get("first")["next_step"] == 2
    queue.enqueue("second")
    assert queue.process_one()
    assert queue.get("second")["status"] == "succeeded"
    assert not queue.process_one()
    for _ in range(8):
        clock[0] += 6
        queue = PreparationQueue(path, steps)
        assert queue.process_one()
    assert queue.get("first")["status"] == "succeeded"
    assert attempts["first"] == 9
    assert calls.count(("first", "index")) == calls.count(("first", "structure")) == 1


def test_targeted_requests_keep_the_application_role_at_original_index(tmp_path):
    from diagnostic_test_inputs import controlled_application
    from test_diagnostic_chapter_generation_cache import book
    value = book().model_copy(update={"chapters": book().chapters[:1]})
    requests = []

    class RoleWriter:
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            requests.append(request)
            point = request["knowledge_points"][0]
            if point["cognitive_target"]["id"] == "source_application":
                return {"items": [controlled_application(request, index=0)]}
            return {"items": [{"knowledge_point_id": point["knowledge_point_id"],
                "type": "choice", "prompt": "下面哪项符合原文的概念与关系？",
                "options": [point["label"], "不同对象下的一种陈述", "另一条件下的受控陈述", "不同过程下的受控陈述"],
                "correct_index": 0, "expected_answer": point["label"], "rubric": [],
                "difficulty": 0, "estimated_seconds": 40}]}

    result = generator(RoleWriter(), Reviewer()).generate(value, chapter_cache=cache(tmp_path), cache_scope="book")
    assert len(result) == 3
    assert [call["knowledge_points"][0]["cognitive_target"]["id"] for call in requests] == [
        "basic_recognition", "relation_understanding", "source_application"]
    assert len(generator(RoleWriter(), Reviewer()).generate(value, chapter_cache=cache(tmp_path), cache_scope="book")) == 3
    assert len(requests) == 3


def test_existing_approved_chapter_is_reused_by_continuous_policy(tmp_path):
    store, writer, reviewer = cache(tmp_path), Writer(), Reviewer()
    finite = DiagnosticItemGenerator(writer, review_client=reviewer, allow_grounded_answer_rewrite=True)
    value = structure()
    points = finite._select_points(value.chapters[0])
    identity = finite._chapter_identity(value, value.chapters[0], points, "book")
    claim = store.claim(identity)
    # Approve through the normal exact single-item validation/review path, then
    # store the complete chapter as an earlier release would have done.
    items = [finite._generate_chapter(value.title, value.chapters[0], [point],
             point_offset=index, targeted=True)[0] for index, point in enumerate(points)]
    store.approve(identity, claim["token"], items)
    calls = len(writer.calls)
    assert len(generator(writer, reviewer).generate(value, chapter_cache=store, cache_scope="book")) == 2
    assert len(writer.calls) == calls


def test_cancelled_preparation_does_not_retry_or_overwrite_pause_and_can_resume(tmp_path):
    queue = None

    def diagnostics(book):
        queue.cancel(book)
        raise PreparationDeferred({"message": "automatic repair", "retry_after_seconds": 5})

    queue = PreparationQueue(tmp_path / "pause.sqlite3", [("diagnostics", diagnostics)])
    queue.enqueue("book")
    assert queue.process_one()
    assert queue.get("book")["status"] == "cancelled"
    assert not queue.process_one()
    assert queue.enqueue("book", retry=True)["status"] == "queued"


def test_pause_before_preparation_submission_blocks_late_create(tmp_path):
    calls = []
    queue = PreparationQueue(tmp_path / "pause.sqlite3", [("diagnostics", lambda book: calls.append(book))])
    assert queue.cancel("book", create=True)["status"] == "cancelled"
    assert queue.enqueue("book")["status"] == "cancelled"
    assert not queue.process_one() and not calls
    assert queue.enqueue("book", retry=True)["status"] == "queued"
    assert queue.process_one() and calls == ["book"]
