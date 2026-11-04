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


def test_eight_round_limit_survives_restart_and_completes_with_approved_items(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), Reviewer(rejects=100)
    subject = generator(writer, reviewer)
    for attempt in range(7):
        with pytest.raises(DiagnosticRepairPending) as pending:
            generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
        assert pending.value.details["diagnostics_completed"] == 1
        assert pending.value.details["diagnostics_total"] is None
        assert pending.value.details["diagnostics_finalized"] is False
        if attempt == 0:
            calls = len(writer.calls)
            with pytest.raises(DiagnosticRepairPending):
                subject.generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
            assert len(writer.calls) == calls
        clock[0] += 200
    result = subject.generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    assert len(result) == 1
    keys = [row["knowledge_point_id"] for row in valid_payload()["items"]]
    counts = Counter(call["knowledge_points"][0]["knowledge_point_id"] for call in writer.calls)
    assert counts == {keys[0]: 8, keys[1]: 1}
    assert reviewer.counts == counts
    assert writer.calls[2]["repair_feedback"]["stage"] == "semantic_review"
    redesigns = [call for call in writer.calls if call["generation_strategy"] == "redesign"]
    assert len(redesigns) == 2
    for call in redesigns:
        assert len(call["knowledge_points"]) == 1
        assert not {"previous_candidate", "repair_feedback", "required_repairs", "preserve_accepted_items"} & call.keys()
    before = len(writer.calls)
    assert generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book") == result
    points = subject._select_points(structure().chapters[0])
    identity = item_repairs._identity(subject, structure(), structure().chapters[0], points, 0, "book")
    saved = cache(tmp_path)
    assert saved.repair_state(identity)["terminal_status"] == "skipped"
    saved.repository.grant_diagnostic_recovery("book", "user-retry")
    assert saved.claim(identity)["status"] == "skipped"
    assert len(writer.calls) == before
    progress = item_repairs.diagnostic_item_progress(subject, structure(), saved, "book")
    assert progress == {"diagnostics_completed": 1, "diagnostics_skipped": 1,
                        "diagnostics_total": None, "diagnostics_finalized": False}


def test_eighth_round_may_still_succeed(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), Reviewer(rejects=7)
    for _ in range(7):
        with pytest.raises(DiagnosticRepairPending):
            generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
        clock[0] += 200
    assert len(generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")) == 2


def test_legacy_exhausted_point_is_skipped_without_waiting_or_regenerating(tmp_path):
    writer, reviewer = Writer(), Reviewer()
    subject = generator(writer, reviewer)
    value = structure()
    points = subject._select_points(value.chapters[0])
    identity = item_repairs._identity(subject, value, value.chapters[0], points, 0, "book")
    saved = cache(tmp_path)
    claim = saved.claim(identity)
    saved.defer(identity, claim["token"], {"attempt": 15, "next_retry_at": 10**12})
    result = subject.generate(value, chapter_cache=cache(tmp_path), cache_scope="book")
    assert len(result) == 1
    assert len(writer.calls) == 1
    assert cache(tmp_path).repair_state(identity)["terminal_status"] == "skipped"


def test_invalid_review_format_counts_towards_eight_rounds(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), Reviewer()
    review = reviewer.structured
    broken = []

    def invalid_format(**kwargs):
        request = json.loads(kwargs["user"])
        if request["items"][0]["knowledge_point_id"] == valid_payload()["items"][0]["knowledge_point_id"]:
            broken.append(request)
            return {"reviews": []}
        return review(**kwargs)

    reviewer.structured = invalid_format
    for _ in range(7):
        with pytest.raises(DiagnosticRepairPending):
            generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
        clock[0] += 200
    assert len(generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")) == 1
    assert len(broken) == 8
    generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    assert len(broken) == 8


def test_all_exhausted_points_fail_clearly_without_ninth_round(tmp_path, monkeypatch):
    from adaptive_learning.assessment.item_generation import DiagnosticNoUsableItemsError
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), Reviewer()
    def reject(**kwargs):
        point = json.loads(kwargs["user"])["items"][0]["knowledge_point_id"]
        return {"reviews": [{"knowledge_point_id": point, "accepted": False,
            "issues": ["题目未能唯一回答本问"], "required_edits": {"prompt": True, "option_indices": []}}]}
    reviewer.structured = reject
    for _ in range(7):
        with pytest.raises(DiagnosticRepairPending):
            generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
        clock[0] += 200
    for _ in range(2):
        with pytest.raises(DiagnosticNoUsableItemsError):
            generator(writer, reviewer).generate(structure(), chapter_cache=cache(tmp_path), cache_scope="book")
    counts = Counter(call["knowledge_points"][0]["knowledge_point_id"] for call in writer.calls)
    assert list(counts.values()) == [8, 8]


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


def test_redesign_discards_earlier_candidate_but_service_retry_reuses_fresh_one(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(item_repairs.time, "time", lambda: clock[0])
    writer, reviewer = Writer(), Reviewer()
    value = structure()
    subject = generator(writer, reviewer)
    saved = cache(tmp_path)
    points = subject._select_points(value.chapters[0])
    identity = item_repairs._identity(subject, value, value.chapters[0], points, 0, "book")
    claim = saved.claim(identity)
    saved.defer(identity, claim["token"], {"rounds_completed": 3, "context": {
        "_pending_candidate": [valid_payload()["items"][0]], "_pending_candidate_round": 3,
        "previous_candidate": {"private": "old question"}, "repair_feedback": {"private": "old feedback"}}})
    original = reviewer.structured
    cooling = [True]
    def review(**kwargs):
        if cooling[0]:
            raise LLMCooldownError(5)
        return original(**kwargs)
    reviewer.structured = review
    with pytest.raises(DiagnosticRepairPending):
        subject.generate(value, chapter_cache=saved, cache_scope="book")
    redesign = writer.calls[0]
    assert redesign["generation_strategy"] == "redesign"
    assert not {"previous_candidate", "repair_feedback"} & redesign.keys()
    assert saved.repair_state(identity)["context"]["_pending_candidate_round"] == 4
    before = len(writer.calls)
    cooling[0] = False
    clock[0] += 200
    assert len(subject.generate(value, chapter_cache=cache(tmp_path), cache_scope="book")) == 2
    assert len(writer.calls) == before


def test_recovery_fences_old_running_submission_when_legacy_budget_is_exhausted(tmp_path):
    from adaptive_learning.assessment.diagnostic_cache import chapter_identity
    saved = cache(tmp_path)
    subject = generator(Writer(), Reviewer())
    value = structure()
    points = subject._select_points(value.chapters[0])
    identity = item_repairs._identity(subject, value, value.chapters[0], points, 0, "book")
    claim = saved.claim(identity)
    key, _ = chapter_identity(identity)
    with saved.repository._connect() as db:
        db.execute("INSERT INTO diagnostic_item_repairs(cache_key,state_json,updated_at) VALUES (?,?,?)",
                   (key, json.dumps({"attempt": 15}), 1))
    assert saved.claim(identity)["status"] == "running"
    saved.repository.grant_diagnostic_recovery("book", "explicit-recovery")
    assert saved.claim(identity)["status"] == "skipped"
    assert saved.repository.has_pending_diagnostic_submission("book") is False
    with pytest.raises(Exception, match="stale"):
        saved.fail(identity, claim["token"], "rejected")
