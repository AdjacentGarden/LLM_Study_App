import importlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adaptive_learning.assessment.models import InterviewPhase, InterviewSession, LearnerProfile
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.ingestion.models import ChapterDraft, SourceQuote
from adaptive_learning.llm.client import LLMError
from adaptive_learning.personalization.flashcard_quality import (
    CorrectedCard,
    FlashcardQualityError,
    FlashcardQualityGate,
)
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.policy import PersonalizationPolicy


def course():
    label = "摩尔根通过果蝇实验将白眼基因与X染色体联系起来。"
    chapter = ChapterDraft(chapter_id="morgan", order=1, title="基因与染色体",
        start_page=1, end_page=2, summary=label, knowledge_points=[label],
        source_block_ids=["b1"], evidence=[SourceQuote(page_number=1, quote=label)],
        knowledge_point_evidence={label: [SourceQuote(page_number=1, quote=label)]})
    profile = LearnerProfile(user_id="u", book_id="book")
    bundle = ChapterCourseCompiler().compile(chapter=chapter, profile=profile,
        decision=PersonalizationPolicy().decide(profile, chapter.chapter_id))
    bundle.flashcards[0].front = "请用自己的话解释摩尔根。"
    bundle.flashcards[0].back = "摩尔。根通过果。蝇实验……"
    return bundle


class FakeClient:
    def __init__(self, mode="ok"):
        self.calls = 0
        self.mode = mode

    def structured(self, *, system: str, user: str, temperature: float = 0,
                   max_tokens: int = 4096) -> dict[str, Any]:
        self.calls += 1
        data = json.loads(user)["cards"]
        if self.mode == "outage":
            raise LLMError("unavailable")
        if "candidate" in data[0]:
            reject = self.mode == "reject" or (self.mode == "retry" and self.calls == 2)
            return {"reviews": [{"id": item["id"],
                "accepted": "true" if self.mode == "string_bool" else not reject,
                "issues": ["缺少完整实验名"] if reject else []} for item in data]}
        cards = [{"id": item["id"], "front": "摩尔根的果蝇实验把白眼基因与哪条染色体联系起来？",
                  "back": "摩尔根通过果蝇实验，将白眼基因与X染色体联系起来。"} for item in data]
        if self.mode == "missing":
            cards = []
        if self.mode == "duplicate":
            cards *= 2
        if self.mode == "control":
            cards[0]["back"] += "\u200b"
        if self.mode == "formula":
            cards[0]["back"] = "公式示例：P(A)=1/2，基因型为 XᴬXᵃ；不能删除合法符号。"
        return {"cards": cards}


def test_repair_and_separate_review_preserve_sources_ids_and_personalization(tmp_path):
    original = course()
    client = FakeClient()
    gate = FlashcardQualityGate(client, tmp_path / "q.db", "test-model")
    reviewed = gate.ensure(original)
    assert client.calls == 2
    assert "果蝇实验" in reviewed.flashcards[0].front
    assert "摩尔。根" not in reviewed.flashcards[0].back
    assert original.flashcards[0].front == "请用自己的话解释摩尔根。"
    for name in ("card_id", "point_id", "citations", "source", "reason_for_user"):
        assert getattr(reviewed.flashcards[0], name) == getattr(original.flashcards[0], name)
    assert reviewed.course_id == original.course_id
    assert gate.ensure(reviewed) == reviewed
    assert client.calls == 2
    # Shared wording cache must never copy another user's card IDs or recommendation.
    other = original.model_copy(deep=True)
    other.flashcards[0].card_id = "other-user-card"
    other.flashcards[0].reason_for_user = "针对你的薄弱点"
    assert gate.ensure(other).flashcards[0].card_id == "other-user-card"
    assert client.calls == 2


@pytest.mark.parametrize("mode", ["missing", "duplicate", "control", "reject", "outage", "string_bool"])
def test_fail_closed_and_do_not_cache_unreviewed_cards(tmp_path, mode):
    gate = FlashcardQualityGate(FakeClient(mode), tmp_path / "q.db", "test")
    with pytest.raises(FlashcardQualityError):
        gate.ensure(course())
    with gate._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reviewed_flashcards").fetchone()[0] == 0


def test_rejected_review_is_repaired_and_reviewed_again(tmp_path):
    client = FakeClient("retry")
    gate = FlashcardQualityGate(client, tmp_path / "q.db", "test")
    gate.ensure(course())
    assert client.calls == 4


def test_retry_only_regenerates_rejected_cards(tmp_path):
    bundle = course()
    bundle.knowledge_points.append(bundle.knowledge_points[0].model_copy(update={
        'point_id':'another', 'explanation':'第二个完整知识点'}))
    bundle.flashcards.append(bundle.flashcards[0].model_copy(update={'point_id':'another', 'card_id':'two'}))

    class PartialClient(FakeClient):
        sizes = []
        reviews = 0

        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)['cards']
            result = super().structured(system=system, user=user, **kwargs)
            if 'candidate' in data[0]:
                self.reviews += 1
                if self.reviews == 1:
                    result['reviews'][-1].update(accepted=False, issues=['请缩小问题范围'])
            else:
                self.sizes.append(len(data))
                if len(data) == 1:
                    assert data[0]['previous_candidate'] is not None
            return result

    client = PartialClient()
    output = FlashcardQualityGate(client, tmp_path/'partial.db', 'test').ensure(bundle)
    assert len(output.flashcards) == 2
    assert client.sizes == [2, 1]
    assert client.calls == 4


def test_cache_invalidates_for_source_model_or_depth_changes(tmp_path):
    client = FakeClient()
    gate = FlashcardQualityGate(client, tmp_path / "q.db", "test")
    original = course()
    gate.ensure(original)
    original.flashcards[0].citations[0].quote += "补充原文证据。"
    gate.ensure(original)
    assert client.calls == 4
    FlashcardQualityGate(client, tmp_path / "q.db", "new-model").ensure(original)
    assert client.calls == 6


def test_missing_model_or_evidence_cannot_bypass_gate(tmp_path):
    with pytest.raises(FlashcardQualityError):
        FlashcardQualityGate(None, tmp_path / "q.db", "none").ensure(course())
    bundle = course()
    bundle.flashcards[0].citations = []
    with pytest.raises(FlashcardQualityError):
        FlashcardQualityGate(FakeClient(), tmp_path / "q.db", "test").ensure(bundle)


def test_legal_scientific_symbols_are_not_stripped(tmp_path):
    reviewed = FlashcardQualityGate(FakeClient("formula"), tmp_path / "q.db", "test").ensure(course())
    assert "P(A)=1/2" in reviewed.flashcards[0].back
    assert "XᴬXᵃ" in reviewed.flashcards[0].back


def test_ocr_lost_chromosome_notation_requires_model_repair_not_guessing():
    data = {"evidence": [{"quote": "性染色体上的基因。原文存在残缺记号。"}]}
    card = CorrectedCard(id="one", front="如何根据羽毛区分雏鸡雌雄？",
                         back="用芦花雌鸡（B）与非芦花雄鸡（bb）杂交。")
    with pytest.raises(FlashcardQualityError, match="记号残缺"):
        FlashcardQualityGate._validate_notation(card, data)
    card.back = "用芦花雌鸡与非芦花雄鸡杂交，后代雄鸡为芦花，雌鸡为非芦花。"
    FlashcardQualityGate._validate_notation(card, data)


def test_existing_course_get_is_reviewed_and_persisted_without_new_version(tmp_path, monkeypatch):
    module = importlib.import_module("adaptive_learning.api.app")
    repo = SQLiteAssessmentRepository(tmp_path / "assessments.db")
    bundle = course()
    session = InterviewSession(session_id="session", profile=LearnerProfile(user_id="u", book_id="book"),
                               phase=InterviewPhase.COMPLETE,
                               created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    monkeypatch.setattr(module, "_session_or_404", lambda _: session)
    monkeypatch.setattr(module, "assessment_repository", repo)
    monkeypatch.setattr(module, "flashcard_quality",
                        FlashcardQualityGate(FakeClient(), tmp_path / "q.db", "test"))
    repo.save_course("session", bundle)
    client = TestClient(module.app)
    response = client.get("/api/interviews/session/courses/morgan")
    assert response.status_code == 200
    assert "果蝇实验" in response.json()["flashcards"][0]["front"]
    saved = repo.get_course(bundle.course_id)
    assert saved.version == bundle.version
    assert saved.flashcards[0].card_id == bundle.flashcards[0].card_id
    assert "果蝇实验" in saved.flashcards[0].front
    monkeypatch.setattr(module, "flashcard_quality", FlashcardQualityGate(None, tmp_path / "empty.db", "test"))
    assert client.get("/api/interviews/session/courses/morgan").status_code == 503


@pytest.mark.parametrize("cached", [False, True])
def test_post_new_and_cached_courses_require_review_before_success(tmp_path, monkeypatch, cached):
    module = importlib.import_module("adaptive_learning.api.app")
    repo = SQLiteAssessmentRepository(tmp_path / "assessments.db")
    bundle = course()
    profile = LearnerProfile(user_id="u", book_id="book")
    session = SimpleNamespace(session_id="s", profile=profile, phase=InterviewPhase.COMPLETE)
    chapter = SimpleNamespace(chapter_id="morgan")
    monkeypatch.setattr(module, "_session_or_404", lambda _: session)
    monkeypatch.setattr(module, "assessment_repository", repo)
    monkeypatch.setattr(module, "job_repository", SimpleNamespace(
        get_structure=lambda _: SimpleNamespace(chapters=[chapter])))
    monkeypatch.setattr(module, "chapter_fingerprint", lambda _: bundle.source_fingerprint)
    monkeypatch.setattr(module, "profile_fingerprint", lambda _: bundle.profile_fingerprint)
    monkeypatch.setattr(module, "course_compiler", SimpleNamespace(compile=lambda **kwargs: bundle))
    monkeypatch.setattr(module, "flashcard_quality",
                        FlashcardQualityGate(None, tmp_path / "q.db", "test"))
    if cached:
        repo.save_course("s", bundle)
    client = TestClient(module.app)
    assert client.post("/api/interviews/s/courses/morgan").status_code == 503
    saved = repo.get_latest_course("s", "morgan")
    assert (saved is not None) == cached
    if saved:
        assert saved.flashcards[0].front == bundle.flashcards[0].front
    monkeypatch.setattr(module, "flashcard_quality",
                        FlashcardQualityGate(FakeClient(), tmp_path / "q.db", "test"))
    response = client.post("/api/interviews/s/courses/morgan")
    assert response.status_code == 200
    assert "果蝇实验" in response.json()["flashcards"][0]["front"]
