"""API publication, persisted identity and privacy around whole-course review."""
import importlib
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from adaptive_learning.assessment.models import (
    InterviewPhase,
    InterviewSession,
    LearnerProfile,
)
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.ingestion.models import (
    ChapterDraft,
    ExtractionMethod,
    PageExtraction,
    PageKind,
    QualityBand,
    QualitySignals,
    SourceQuote,
)
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.models import LessonSection
from adaptive_learning.personalization.teaching_content import TeachingContentError
from adaptive_learning.personalization.teaching_scope import TEACHING_SCOPE_VERSION
from fastapi.testclient import TestClient


class FakeTeachingGenerator:
    """API tests isolate the separately tested content/review implementation."""
    def __init__(self, module):
        self.module = module
        self.calls = 0
        self.reject = False

    def generate(self, bundle, chapter, evidence, *, run_scope: str | None = None):
        self.calls += 1
        if self.reject:
            raise TeachingContentError("独立审核拒绝了有问题的课程")
        output = bundle.model_copy(deep=True)
        for point in output.knowledge_points:
            point.title = "果蝇实验与染色体"
            point.explanation = "摩尔根研究果蝇的白眼性状，并将白眼基因与X染色体联系起来。理解实验时，应区分观察到的性状和用来解释观察的基因关系；只在原文支持的范围内陈述实验结论。"
        for card in output.flashcards:
            card.front = "摩尔根的果蝇实验把白眼基因与哪条染色体联系起来？"
            card.back = "果蝇白眼基因与X染色体联系起来。"
        for item in output.practice_items:
            item.prompt = "说明果蝇白眼实验所支持的基因与染色体关系。"
            item.expected_answer = "PRIVATE_REFERENCE_ANSWER_WHITE_EYE_X_CHROMOSOME"
            item.rubric = ["PRIVATE_RUBRIC_EXPERIMENT_OBJECT", "PRIVATE_RUBRIC_CHROMOSOME_RELATION"]
        output.worked_examples = [LessonSection(
            title="根据实验判断联系", content="题目：果蝇实验支持什么联系？\n1. 识别白眼性状的研究对象。\n2. 将原文结论对应到染色体。\n结论：白眼基因与X染色体联系。",
            purpose="示范有来源的推理。", citations=list(evidence.sources.values())[:1])]
        output.teaching_quality_version = self.module.TEACHING_CONTENT_VERSION
        output.teaching_fact_review = {"accepted": True, "issues": []}
        output.teaching_scope_version = TEACHING_SCOPE_VERSION
        output.teaching_scope_plan = {point.point_id: [] for point in output.knowledge_points}
        output.teaching_review = {"accepted": True, "issues": [], "points": [
            {"point_id": point.point_id, "explanation_ok": True, "flashcard_ok": True,
             "practice_ok": True, "evidence_ok": True, "issues": []}
            for point in output.knowledge_points], "lesson": {"reading_ok": True,
                "examples_ok": True, "checkpoints_ok": True, "evidence_ok": True, "issues": []}}
        return output


def publication_environment(tmp_path, monkeypatch, legacy=None):
    module = importlib.import_module("adaptive_learning.api.app")
    repo = SQLiteAssessmentRepository(tmp_path / "publication.db")
    label = "摩尔根通过果蝇实验将白眼基因与X染色体联系起来。"
    chapter = ChapterDraft(chapter_id="morgan", order=1, title="基因与染色体", start_page=1,
        end_page=2, summary=label, knowledge_points=[label], source_block_ids=["b1"],
        evidence=[SourceQuote(page_number=1, quote=label)],
        knowledge_point_evidence={label: [SourceQuote(page_number=1, quote=label)]})
    quality = QualitySignals(character_score=1, language_score=1, layout_score=1,
        coverage_score=1, agreement_score=1, overall_score=1)
    pages = [PageExtraction(page_number=number, page_kind=PageKind.SCANNED,
        method=ExtractionMethod.PADDLE_GPU, raw_text=text, quality=quality,
        band=QualityBand.ACCEPTED) for number, text in [
            (1, "原文上下文。" + label), (2, "只在实验支持的范围内陈述结论，不扩大到所有性状。")]]
    session = InterviewSession(session_id="session", profile=LearnerProfile(user_id="u", book_id="book"),
        phase=InterviewPhase.COMPLETE, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    generator = FakeTeachingGenerator(module)
    monkeypatch.setattr(module, "_session_or_404", lambda _: session)
    monkeypatch.setattr(module, "assessment_repository", repo)
    monkeypatch.setattr(module, "job_repository", SimpleNamespace(
        get_structure=lambda _: SimpleNamespace(chapters=[chapter]),
        get_job=lambda _: SimpleNamespace(output_dir=tmp_path / "ocr")))
    monkeypatch.setattr(module, "load_normalized_pages", lambda _: pages)
    monkeypatch.setattr(module, "teaching_generator", generator)
    if legacy is not None:
        repo.save_course("session", legacy)
    return SimpleNamespace(module=module, repo=repo, generator=generator, session=session,
                           chapter=chapter, pages=pages, client=TestClient(module.app))


def test_current_reviewed_course_is_persisted_and_reused_on_post_and_get(tmp_path, monkeypatch):
    env = publication_environment(tmp_path, monkeypatch)
    first = env.client.post("/api/interviews/session/courses/morgan")
    assert first.status_code == 200
    second = env.client.post("/api/interviews/session/courses/morgan")
    third = env.client.get("/api/interviews/session/courses/morgan")
    assert second.status_code == third.status_code == 200
    assert first.json()["course_id"] == second.json()["course_id"] == third.json()["course_id"]
    assert env.generator.calls == 1
    saved = env.repo.get_latest_course("session", "morgan")
    assert saved.teaching_evidence_fingerprint
    assert saved.teaching_review["accepted"] is True


@pytest.mark.parametrize("change", ["source", "model", "revision"])
def test_get_invalidates_changed_source_model_or_generation_revision(tmp_path, monkeypatch, change):
    env = publication_environment(tmp_path, monkeypatch)
    first = env.client.post("/api/interviews/session/courses/morgan").json()
    before = env.repo.get_course(first["course_id"]).model_dump_json()
    if change == "source":
        env.pages[1].raw_text += "新增原文条件，也必须参与证据指纹。"
    elif change == "model":
        monkeypatch.setattr(env.module, "settings", replace(env.module.settings,
            deepseek_model="new-test-model", pucoding_text_model="new-test-model"))
    else:
        monkeypatch.setattr(env.module, "TEACHING_CONTENT_VERSION", "next-teaching-version")
    response = env.client.get("/api/interviews/session/courses/morgan")
    assert response.status_code == 409
    assert env.generator.calls == 1  # Merely opening a lesson cannot spend model calls.
    response = env.client.post("/api/interviews/session/courses/morgan")
    assert response.status_code == 200
    assert response.json()["course_id"] != first["course_id"]
    assert response.json()["version"] == first["version"] + 1
    assert env.generator.calls == 2
    assert env.repo.get_course(first["course_id"]).model_dump_json() == before


def test_returning_to_previous_model_reuses_approved_course_without_rewriting_history(tmp_path, monkeypatch):
    env = publication_environment(tmp_path, monkeypatch)
    original_settings = env.module.settings
    first = env.client.post("/api/interviews/session/courses/morgan").json()
    first_saved = env.repo.get_course(first["course_id"]).model_dump_json()
    monkeypatch.setattr(env.module, "settings", replace(original_settings,
        deepseek_model="temporary-test-model", pucoding_text_model="temporary-test-model"))
    second_response = env.client.post("/api/interviews/session/courses/morgan")
    assert second_response.status_code == 200
    second = second_response.json()
    assert second["course_id"] != first["course_id"]
    second_saved = env.repo.get_course(second["course_id"]).model_dump_json()
    monkeypatch.setattr(env.module, "settings", original_settings)
    restored = env.client.post("/api/interviews/session/courses/morgan")
    reread = env.client.get("/api/interviews/session/courses/morgan")
    assert restored.status_code == reread.status_code == 200
    assert restored.json()["course_id"] == reread.json()["course_id"] == first["course_id"]
    assert env.generator.calls == 2
    assert len(env.repo.courses_for_session("session")) == 2
    assert env.repo.get_course(first["course_id"]).model_dump_json() == first_saved
    assert env.repo.get_course(second["course_id"]).model_dump_json() == second_saved


@pytest.mark.parametrize("missing_review", ["teaching_review", "teaching_fact_review"])
def test_return_to_historical_model_never_publishes_unapproved_content(tmp_path, monkeypatch, missing_review):
    env = publication_environment(tmp_path, monkeypatch)
    original_settings = env.module.settings
    first = env.client.post("/api/interviews/session/courses/morgan").json()
    historical = env.repo.get_course(first["course_id"])
    setattr(historical, missing_review, {"accepted": False})
    env.repo.update_reviewed_course(historical)
    monkeypatch.setattr(env.module, "settings", replace(original_settings,
        deepseek_model="temporary-test-model", pucoding_text_model="temporary-test-model"))
    assert env.client.post("/api/interviews/session/courses/morgan").status_code == 200
    monkeypatch.setattr(env.module, "settings", original_settings)
    response = env.client.post("/api/interviews/session/courses/morgan")
    assert response.status_code == 409
    assert env.generator.calls == 2
    assert getattr(env.repo.get_course(first["course_id"]), missing_review)["accepted"] is False


def test_unreviewed_generation_fails_closed_without_persisting_new_course(tmp_path, monkeypatch):
    env = publication_environment(tmp_path, monkeypatch)
    env.generator.reject = True
    response = env.client.post("/api/interviews/session/courses/morgan")
    assert response.status_code == 503
    assert env.repo.get_latest_course("session", "morgan") is None


def test_public_course_excludes_answers_rubrics_and_private_audit_data(tmp_path, monkeypatch):
    env = publication_environment(tmp_path, monkeypatch)
    response = env.client.post("/api/interviews/session/courses/morgan")
    assert response.status_code == 200
    for secret in ("PRIVATE_REFERENCE", "PRIVATE_RUBRIC", "expected_answer", "correct_option_ids",
                   "teaching_review", "teaching_fact_review", "teaching_evidence_fingerprint", "source_fingerprint",
                   "teaching_scope_plan", "teaching_scope_version"):
        assert secret not in response.text
    assert env.repo.get_latest_course("session", "morgan").practice_items[0].expected_answer.startswith("PRIVATE_REFERENCE")


def test_missing_raw_context_cannot_publish_or_return_old_course(tmp_path, monkeypatch):
    env = publication_environment(tmp_path, monkeypatch)
    env.client.post("/api/interviews/session/courses/morgan")
    env.pages.pop()
    assert env.client.get("/api/interviews/session/courses/morgan").status_code == 409
    assert env.generator.calls == 1


def test_course_without_fact_review_gets_new_version_and_preserves_historical_items(tmp_path, monkeypatch):
    env = publication_environment(tmp_path, monkeypatch)
    old = ChapterCourseCompiler().compile(chapter=env.chapter, profile=env.session.profile,
        decision=env.module.personalization_policy.decide(env.session.profile, env.chapter.chapter_id),
        instance_key="session", source_fingerprint="historical-v1-source-hash")
    old.teaching_quality_version = "whole-course-evidence-review-v1"
    old.teaching_review = {"accepted": True}
    old.teaching_fact_review = {}
    env.repo.save_course("session", old)
    before = env.repo.get_course(old.course_id).model_dump_json()
    response = env.client.get("/api/interviews/session/courses/morgan")
    assert response.status_code == 409
    assert env.generator.calls == 0
    response = env.client.post("/api/interviews/session/courses/morgan")
    assert response.status_code == 200
    assert response.json()["course_id"] != old.course_id
    assert response.json()["version"] == old.version + 1
    assert env.generator.calls == 1
    assert env.repo.get_course(old.course_id).model_dump_json() == before


def test_read_keeps_current_course_after_profile_change_until_explicit_refresh(tmp_path, monkeypatch):
    env = publication_environment(tmp_path, monkeypatch)
    first = env.client.post("/api/interviews/session/courses/morgan").json()
    env.session.profile.goal = "新的考试目标"
    read = env.client.get("/api/interviews/session/courses/morgan")
    assert read.status_code == 200 and read.json()["course_id"] == first["course_id"]
    assert env.generator.calls == 1
    refresh = env.client.post("/api/interviews/session/courses/morgan")
    assert refresh.status_code == 200 and refresh.json()["course_id"] != first["course_id"]
    assert env.generator.calls == 2


def test_real_generator_planner_outage_is_503_and_does_not_save_course(tmp_path, monkeypatch):
    from adaptive_learning.llm.client import LLMError
    from adaptive_learning.personalization.teaching_content import TeachingContentGenerator
    from adaptive_learning.personalization.teaching_scope import SCOPE_PLANNING_PROMPT

    class OutageClient:
        calls = 0

        def structured(self, **kwargs):
            self.calls += 1
            assert kwargs["system"] == SCOPE_PLANNING_PROMPT
            raise LLMError("planning unavailable")

    env = publication_environment(tmp_path, monkeypatch)
    client = OutageClient()
    monkeypatch.setattr(env.module, "teaching_generator", TeachingContentGenerator(client))
    response = env.client.post("/api/interviews/session/courses/morgan")
    assert response.status_code == 503
    assert client.calls == 1
    assert env.repo.get_latest_course("session", "morgan") is None


def test_background_course_submit_is_idempotent_and_returns_before_generation(tmp_path, monkeypatch):
    from adaptive_learning.api.course_jobs import CourseJobQueue
    env = publication_environment(tmp_path, monkeypatch)
    queue = CourseJobQueue(tmp_path / 'course-jobs.db', env.module._generate_course_task)
    monkeypatch.setattr(env.module, 'course_job_queue', queue)
    monkeypatch.setattr(env.module, '_require_course_actor', lambda *_: None)
    first = env.client.post('/api/interviews/session/course-jobs', json={'chapter_id': 'morgan'})
    repeated = env.client.post('/api/interviews/session/course-jobs', json={'chapter_id': 'morgan'})
    assert first.status_code == repeated.status_code == 202
    assert first.json()['job_id'] == repeated.json()['job_id']
    assert first.json()['status'] == 'queued' and first.json()['course'] is None
    assert env.generator.calls == 0
    assert queue.process_one()
    polled = env.client.get('/api/interviews/session/course-jobs/' + first.json()['job_id'])
    assert polled.status_code == 200 and polled.json()['status'] == 'succeeded'
    assert env.generator.calls == 1
    assert 'PRIVATE_REFERENCE' not in polled.text and 'teaching_review' not in polled.text
    assert env.client.get('/api/interviews/other/course-jobs/' + first.json()['job_id']).status_code == 404
    cached = env.client.post('/api/interviews/session/course-jobs', json={'chapter_id': 'morgan'})
    assert cached.status_code == 200 and cached.json()['course']['course_id'] == polled.json()['course']['course_id']
    monkeypatch.setattr(env.module, 'TEACHING_CONTENT_VERSION', 'changed-review-version')
    assert env.client.get('/api/interviews/session/course-jobs/' + first.json()['job_id']).status_code == 409
    assert env.generator.calls == 1


def test_background_task_is_bound_to_learning_identity(tmp_path, monkeypatch):
    from adaptive_learning.accounts import Accounts
    from adaptive_learning.community import CommunityRepository
    from adaptive_learning.api.course_jobs import CourseJobQueue
    env = publication_environment(tmp_path, monkeypatch)
    actor = Accounts(CommunityRepository(tmp_path / 'identities.db'))
    monkeypatch.setattr(actor, 'request_owner', lambda request: request.headers.get('x-test-actor'))
    monkeypatch.setattr(env.module, 'account_repository', actor)
    monkeypatch.setattr(env.module, 'course_job_queue', CourseJobQueue(tmp_path / 'tasks.db', env.module._generate_course_task))
    assert env.client.post('/api/interviews/session/course-jobs', json={'chapter_id': 'morgan'}).status_code == 403
    first = env.client.post('/api/interviews/session/course-jobs', json={'chapter_id': 'morgan'}, headers={'x-test-actor': 'owner-A'})
    assert first.status_code == 202
    assert env.client.get('/api/interviews/session/course-jobs/' + first.json()['job_id'], headers={'x-test-actor': 'owner-B'}).status_code == 403
    assert env.client.post('/api/interviews/session/course-jobs', json={'chapter_id': 'morgan'}, headers={'x-test-actor': 'owner-B'}).status_code == 403


def test_cached_current_version_without_plan_is_never_published(tmp_path, monkeypatch):
    env = publication_environment(tmp_path, monkeypatch)
    first = env.client.post("/api/interviews/session/courses/morgan").json()
    saved = env.repo.get_course(first["course_id"])
    saved.teaching_scope_plan = {}
    env.repo.update_reviewed_course(saved)
    assert env.client.get("/api/interviews/session/courses/morgan").status_code == 409
    assert env.generator.calls == 1
