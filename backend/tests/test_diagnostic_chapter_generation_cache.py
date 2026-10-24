"""Product-level checkpoint hooks; scripted model responses do not certify content quality."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from adaptive_learning.assessment import item_generation as generation
from adaptive_learning.assessment.diagnostic_cache import DiagnosticChapterSubmissionBlocked
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote
from adaptive_learning.llm.client import LLMCooldownError, LLMError, LLMTimeoutError


class MemoryApprovals:
    """An explicit test adapter implementing the private repository contract."""

    def __init__(self):
        self.records = {}
        self.outcomes = []
        self.claims = []

    def claim(self, identity):
        key = json.dumps(identity, sort_keys=True)
        self.claims.append(deepcopy(identity))
        if key in self.records:
            return deepcopy(self.records[key])
        value = {"status": "claimed", "token": str(len(self.claims))}
        self.records[key] = {**value, "status": "running"}
        return value

    def approve(self, identity, token, items):
        key = json.dumps(identity, sort_keys=True)
        assert self.records[key]["token"] == token
        self.records[key] = {"status": "approved", "items": deepcopy(items)}

    def fail(self, identity, token, outcome):
        key = json.dumps(identity, sort_keys=True)
        assert self.records[key]["token"] == token
        self.outcomes.append(outcome)
        if outcome in {"rejected", "not_admitted"}:
            del self.records[key]
        else:
            self.records[key]["status"] = "unknown"


def book():
    chapters = []
    for index in range(6):
        labels = [f"第{index}章概念{point}的定义" for point in range(3)]
        quote = SourceQuote(page_number=index + 1, quote=f"第{index}章原文证据，包含三个受控概念。")
        chapters.append(ChapterDraft(chapter_id=f"chapter_{index}", order=index + 1,
            title=f"第{index}章", start_page=index + 1, end_page=index + 1,
            summary="Controlled offline source", knowledge_points=labels,
            source_block_ids=[f"block_{index}"], evidence=[quote],
            knowledge_point_evidence={label: [quote] for label in labels}))
    return BookStructure(title="Controlled checkpoint book", summary="Offline contract fixture",
                         source_page_count=6, chapters=chapters)


class Writer:
    def __init__(self):
        self.calls = []

    def structured(self, *, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        return {"items": [{"knowledge_point_id": point["knowledge_point_id"],
            "type": "choice", "prompt": "下面哪项符合这个原文概念的定义？",
            "options": [point["label"], "第一种受控测试干扰陈述", "第二种受控测试干扰陈述", "第三种受控测试干扰陈述"],
            "correct_index": 0, "expected_answer": point["label"], "rubric": [],
            "difficulty": 0, "estimated_seconds": 40} for point in request["knowledge_points"]]}


class Reviewer:
    def __init__(self, reject=None):
        self.calls = []
        self.reject = reject

    def structured(self, *, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        accepted = request["source"]["chapter_id"] != self.reject
        return {"reviews": [{"knowledge_point_id": item["knowledge_point_id"],
            "accepted": accepted, "issues": [] if accepted else ["Controlled semantic veto"]}
            for item in request["items"]]}


@pytest.fixture(autouse=True)
def ordered_offline(monkeypatch):
    # Deterministically finish five siblings before the last rejected chapter.
    # Repository concurrency/token CAS has separate integration coverage.
    monkeypatch.setattr(generation, "ordered_book_map", lambda fn, values: [fn(value) for value in values])


def generator(writer=None, reviewer=None, **kwargs):
    return generation.DiagnosticItemGenerator(writer or Writer(), review_client=reviewer or Reviewer(),
        allow_grounded_answer_rewrite=True, validation_retries=0, **kwargs)


def test_complete_approved_chapters_survive_another_failure_but_bank_requires_all_eighteen():
    writer, reviewer, cache, events = Writer(), Reviewer("chapter_5"), MemoryApprovals(), []
    subject = generator(writer, reviewer, audit_callback=events.append)
    with pytest.raises(generation.DiagnosticGenerationError, match="semantic veto"):
        subject.generate(book(), chapter_cache=cache, cache_scope="actual_book_scope")
    assert len(writer.calls) == len(reviewer.calls) == 6
    assert sum(value["status"] == "approved" for value in cache.records.values()) == 5
    assert not any(event["event"] == "bank_accepted" for event in events)
    reviewer.reject = None
    result = subject.generate(book(), chapter_cache=cache, cache_scope="actual_book_scope")
    assert len(result) == 18
    assert len(writer.calls) == len(reviewer.calls) == 7
    assert [row["chapter_id"] for row in writer.calls[-1:]] == ["chapter_5"]
    assert sum(event["event"] == "chapter_approval_reused" for event in events) == 5
    # Persisted rows are exactly pre-bank balancing, and no model call is needed
    # to reapply the deterministic final bank order on a later full read.
    replay = subject.generate(book(), chapter_cache=cache, cache_scope="actual_book_scope")
    assert replay == result
    assert len(writer.calls) == len(reviewer.calls) == 7
    assert all(len(row["items"]) == 3 for row in cache.records.values())


@pytest.mark.parametrize("change", ["source", "anchor", "target", "protocol", "reviewer", "writer", "scope"])
def test_exact_identity_invalidates_only_affected_chapters_or_changed_protocol(monkeypatch, change):
    writer, reviewer, cache = Writer(), Reviewer(), MemoryApprovals()
    subject, value, scope = generator(writer, reviewer), book(), "book-one"
    subject.generate(value, chapter_cache=cache, cache_scope=scope)
    if change == "source":
        value.chapters[2].knowledge_point_evidence[value.chapters[2].knowledge_points[0]][0].quote += "新增限定。"
    elif change == "anchor":
        value.chapters[2].knowledge_point_evidence[value.chapters[2].knowledge_points[0]][0].anchor_quote = "原文证据"
    elif change == "target":
        monkeypatch.setattr(generation, "_COGNITIVE_TARGETS", tuple(
            {**row, "goal": row["goal"] + "明确比较条件。"} for row in generation._COGNITIVE_TARGETS))
    elif change == "protocol":
        monkeypatch.setattr(generation, "DIAGNOSTIC_BANK_VERSION", "changed-protocol")
    elif change == "reviewer":
        subject.review_identity["revision"] = "new-review-protocol"
    elif change == "writer":
        writer.config = SimpleNamespace(base_url="https://writer.invalid/v1", model="new-writer", reasoning_effort="low")
    else:
        scope = "book-two"
    subject.generate(value, chapter_cache=cache, cache_scope=scope)
    assert len(writer.calls) == (7 if change in {"source", "anchor"} else 12)
    assert len(reviewer.calls) == len(writer.calls)


@pytest.mark.parametrize("stage", ["writer", "reviewer"])
@pytest.mark.parametrize("error", [LLMTimeoutError("unknown timeout"), LLMError("unknown connection state")])
def test_unknown_submission_is_not_repaired_or_automatically_resent(stage, error):
    cache, writer, reviewer = MemoryApprovals(), Writer(), Reviewer()
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise error
    (writer if stage == "writer" else reviewer).structured = fail
    subject = generator(writer, reviewer)
    subject.validation_retries = 2
    value = book().model_copy(update={"chapters": book().chapters[:1]})
    with pytest.raises(LLMError):
        subject.generate(value, chapter_cache=cache, cache_scope="book")
    assert len(calls) == 1 and cache.outcomes == ["unknown"]
    with pytest.raises(DiagnosticChapterSubmissionBlocked):
        subject.generate(value, chapter_cache=cache, cache_scope="book")
    assert len(calls) == 1


def test_known_not_admitted_can_release_without_claiming_approval():
    cache, writer, reviewer = MemoryApprovals(), Writer(), Reviewer()
    original = writer.structured
    calls = []
    def admission(**kwargs):
        calls.append(kwargs)
        raise LLMCooldownError(5)
    writer.structured = admission
    subject = generator(writer, reviewer)
    value = book().model_copy(update={"chapters": book().chapters[:1]})
    with pytest.raises(LLMCooldownError):
        subject.generate(value, chapter_cache=cache, cache_scope="book")
    assert cache.outcomes == ["not_admitted"] and not cache.records
    writer.structured = original
    assert len(subject.generate(value, chapter_cache=cache, cache_scope="book")) == 3
    assert len(calls) == 1


@pytest.mark.parametrize("corruption", ["points", "source", "answer", "item_id", "payload_type"])
def test_corrupt_approved_result_never_becomes_a_hit_or_a_new_paid_submission(corruption):
    cache, writer, reviewer = MemoryApprovals(), Writer(), Reviewer()
    subject, value = generator(writer, reviewer), book()
    subject.generate(value, chapter_cache=cache, cache_scope="book")
    record = next(iter(cache.records.values()))
    item = record["items"][0]
    if corruption == "points":
        record["items"] = record["items"][:2]
    elif corruption == "source":
        item.source_pages = [999]
    elif corruption == "answer":
        item.correct_option_ids = ["3"]
    elif corruption == "item_id":
        item.item_id = "forged_item"
    else:
        record["items"] = [item.model_dump() for item in record["items"]]
    with pytest.raises((DiagnosticChapterSubmissionBlocked, RuntimeError)):
        subject.generate(value, chapter_cache=cache, cache_scope="book")
    assert len(writer.calls) == len(reviewer.calls) == 6


def test_cache_requires_scope_and_independent_review_before_any_claim():
    cache = MemoryApprovals()
    with pytest.raises(ValueError, match="book scope"):
        generator().generate(book(), chapter_cache=cache)
    subject = generation.DiagnosticItemGenerator(Writer(), verify_items=False)
    with pytest.raises(ValueError, match="independent review"):
        subject.generate(book(), chapter_cache=cache, cache_scope="book")
    assert not cache.claims


def test_failed_approval_persistence_remains_unknown_and_does_not_return_a_bank():
    cache = MemoryApprovals()
    def storage_failure(*args):
        raise OSError("Controlled private storage failure")
    cache.approve = storage_failure
    subject = generator()
    value = book().model_copy(update={"chapters": book().chapters[:1]})
    with pytest.raises(OSError):
        subject.generate(value, chapter_cache=cache, cache_scope="book")
    assert cache.outcomes == ["unknown"]
    with pytest.raises(DiagnosticChapterSubmissionBlocked):
        subject.generate(value, chapter_cache=cache, cache_scope="book")


def test_identity_change_during_review_cannot_checkpoint_old_protocol_approval():
    cache, writer, reviewer = MemoryApprovals(), Writer(), Reviewer()
    subject = generator(writer, reviewer)
    original = reviewer.structured
    def change_identity(**kwargs):
        result = original(**kwargs)
        subject.review_identity["revision"] = "changed-mid-submission"
        return result
    reviewer.structured = change_identity
    value = book().model_copy(update={"chapters": book().chapters[:1]})
    with pytest.raises(generation.DiagnosticGenerationError, match="identity changed"):
        subject.generate(value, chapter_cache=cache, cache_scope="book")
    assert cache.outcomes == ["rejected"]
    assert not cache.records


def test_real_repository_adapter_preserves_five_complete_chapters_across_restart(tmp_path):
    from adaptive_learning.assessment.diagnostic_cache import RepositoryDiagnosticChapterCache
    from adaptive_learning.assessment.repository import SQLiteAssessmentRepository

    database_path = tmp_path / "assessments.sqlite3"
    first_repo = SQLiteAssessmentRepository(database_path)
    writer, reviewer = Writer(), Reviewer("chapter_5")
    subject = generator(writer, reviewer)
    with pytest.raises(generation.DiagnosticGenerationError, match="semantic veto"):
        subject.generate(book(), chapter_cache=RepositoryDiagnosticChapterCache(first_repo), cache_scope="actual_book")
    with first_repo._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM diagnostic_chapter_cache WHERE state='approved'").fetchone()[0] == 5
        assert db.execute("SELECT COUNT(*) FROM diagnostic_banks").fetchone()[0] == 0
    # A fresh repository/generator process boundary reads only the five exact
    # private approvals; neither audits nor an incomplete whole bank are input.
    restarted_repo = SQLiteAssessmentRepository(database_path)
    fresh_writer, fresh_reviewer = Writer(), Reviewer()
    restarted = generator(fresh_writer, fresh_reviewer)
    result = restarted.generate(book(), chapter_cache=RepositoryDiagnosticChapterCache(restarted_repo), cache_scope="actual_book")
    assert len(result) == 18
    assert len(fresh_writer.calls) == len(fresh_reviewer.calls) == 1
    assert fresh_writer.calls[0]["chapter_id"] == "chapter_5"
    assert restarted_repo.get_bank("diagnostic:actual_book") is None
