"""Personalization must cross the real writer and native content boundaries."""
import json
from types import SimpleNamespace

import pytest
from native_harness import api
from test_learning_design import preferences
from test_native_api import complete_interview, uploaded, workspace
from test_teaching_content import Client, course

from adaptive_learning.api.course_jobs import CourseJobQueue
from adaptive_learning.assessment.models import LearnerProfile
from adaptive_learning.learning_preferences import resolve_learning_design
from adaptive_learning.personalization.policy import PersonalizationPolicy
from adaptive_learning.personalization.teaching_content import (
    GENERATION_PROMPT,
    TeachingContentGenerator,
)
from adaptive_learning.personalization.teaching_repairs import TeachingRepairStore, current_repair
from adaptive_learning.personalization.teaching_unit_engine import UnitEngine

pytest_plugins = ["test_native_api"]


def test_every_course_question_is_resolved_and_private_identity_is_excluded():
    config = resolve_learning_design(
        {"displayName": "PRIVATE_NAME", "learningStage": "middle", "primaryGoal": "interest",
         "learningBarriers": ["memory"], "guidanceStyles": ["stepByStep"]},
        {"goals": ["gaps", "exam", "professional"], "urgency": ["urgent"],
         "timePattern": ["fragmented"], "contentFoci": ["exercises", "notes", "principles"],
         "aids": ["chat", "video", "mistakes", "guidedNotes"],
         "reviews": ["alongside", "notes", "periodic", "mistakes"]},
    )
    context = config.teaching_preferences()
    assert context["learning_stage"] == "middle"
    assert context["course_goals"] == ["gaps", "exam", "growth"]
    assert context["primary_goal"] == "systematic"
    assert context["urgency"] == "urgent"
    assert set(context["content_foci"]) == {"exercises", "notes", "principles"}
    assert len(context["learning_aids"]) == len(context["consolidation"]) == 4
    assert context["segment_minutes"] == 8
    assert context["guidance_styles"] == ["principles"]
    assert "PRIVATE_NAME" not in json.dumps(context)
    assert config.model_copy(update={"learning_stage": "working"}).fingerprint != config.fingerprint


@pytest.mark.parametrize("kind", ["explanation", "flashcard", "practice", "opening", "summary", "example_1", "checkpoint_1"])
@pytest.mark.parametrize("mode", ["write", "repair"])
def test_real_component_writer_receives_preferences_on_initial_and_repair_requests(tmp_path, kind, mode):
    bundle, chapter, evidence = course(1)
    captured = []
    generator = TeachingContentGenerator(object())

    def capture(**kwargs):
        captured.append((kwargs["system"], json.loads(kwargs["user"])))
        return {"component": {}}

    generator._structured = capture
    run = TeachingRepairStore(tmp_path / "repairs.sqlite3").open("owner", chapter.chapter_id, {"test": 1})
    token = current_repair.set(run)
    try:
        for answers in [
            {"learningStage": "middle", "primaryGoal": "exam", "dailyTime": "under30",
             "guidanceStyles": ["examples"], "learningBarriers": ["memory"]},
            {"learningStage": "working", "primaryGoal": "interest", "dailyTime": "1to2h",
             "guidanceStyles": ["principles"], "learningBarriers": ["focus"]},
        ]:
            current = bundle.model_copy(deep=True)
            current.design_config = resolve_learning_design(answers)
            profile = LearnerProfile(user_id="u", book_id="b", learning_design=current.design_config)
            current.decision = PersonalizationPolicy().decide(profile, chapter.chapter_id)
            engine = UnitEngine(generator, current, chapter, evidence)
            for item in engine.inputs:
                item["scope_conditions"] = []
            item = engine.inputs[0]
            unit = {"unit_id": "same-unit", "candidate": {}, "work": {
                "kind": kind, "owner_id": item["point_id"] if kind in {"explanation", "flashcard", "practice"} else None,
                "label": item["label"], "focus_ids": [item["point_id"]],
                "attempt_nonce": "same-nonce", "mode": mode, "revision": 1,
            }}
            engine._write_one(unit)
            payload = captured[-1][1]
            assert payload["teaching_preferences"] == current.design_config.teaching_preferences()
            assert payload["depth"] == current.decision.depth.value
            assert payload["scaffolds"] == current.decision.scaffolds
            assert payload["emphasis"] == current.decision.emphasis
            assert profile.chapter_mastery == {}
        assert captured[0][1] != captured[1][1]
        assert "不能新增无源事实" in captured[0][0]
    finally:
        current_repair.reset(token)


def test_nonpersistent_generation_also_receives_the_effective_preferences():
    bundle, chapter, evidence = course(1)
    bundle.design_config = resolve_learning_design({"guidanceStyles": ["examples"], "learningStage": "high"})
    client = Client()
    TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    payloads = [payload for system, payload in zip(client.systems, client.payloads, strict=True)
                if system == GENERATION_PROMPT]
    assert payloads
    assert all(payload["teaching_preferences"] == bundle.design_config.teaching_preferences() for payload in payloads)


def test_native_metadata_exposes_current_design_without_replacing_old_content_or_progress(native, monkeypatch):
    a, _, _ = native
    preferences(a)
    resource = workspace(a, uploaded(a))
    sid = complete_interview(a, resource)
    path = f"/api/workspaces/workspace_first/resources/{resource['id']}"
    endpoint = f"/api/interviews/{sid}/courses/chapter_meiosis"
    first = a.post(endpoint).json()
    saved = a.put(path + "/progress", json={"revision": 0, "mutation_id": "personalization-progress",
        "page": 2, "chapter_id": "chapter_meiosis", "completed_chapters": ["chapter_meiosis"]}).json()
    before = a.get("/api/native/bootstrap?mode=metadata").json()["workspaces"][0]["resources"][0]
    assert before["learning_design_fingerprint"] == first["design_fingerprint"]
    preferences(a, guidanceStyles=["principles"], learningStage="working")
    metadata = a.get("/api/native/bootstrap?mode=metadata").json()["workspaces"][0]["resources"][0]
    detail = a.get(path).json()
    desired = detail["learning_design_fingerprint"]
    assert desired == metadata["learning_design_fingerprint"] != first["design_fingerprint"]
    interview = a.get(f"/api/interviews/{sid}").json()
    assert interview["design_fingerprint"] == desired
    assert interview["profile"]["learning_design"]["learning_stage"] == "working"
    assert metadata["content_key"] == before["content_key"]
    assert detail["courses"][0]["course_id"] == first["course_id"]
    assert detail["progress"] == saved
    submitted = []
    monkeypatch.setattr(api, "course_job_queue", SimpleNamespace(enqueue=lambda *args, **kwargs:
        submitted.append((args, kwargs)) or {"status": "queued"}))
    a.app.add_api_route("/api/interviews/{session_id}/course-jobs", api.start_course_task, methods=["POST"])
    job_path = f"/api/interviews/{sid}/course-jobs"
    stale = a.post(job_path, json={"chapter_id": "chapter_meiosis", "design_fingerprint": first["design_fingerprint"]})
    assert stale.status_code == 409
    assert not submitted
    response = a.post(job_path, json={"chapter_id": "chapter_meiosis", "design_fingerprint": desired})
    assert response.status_code == 202, response.text
    assert len(submitted) == 1
    updated = a.post(endpoint).json()
    assert updated["design_fingerprint"] == desired
    assert updated["version"] == first["version"] + 1
    assert updated["design_config"]["learning_stage"] == "working"
    assert a.get(path).json()["progress"] == saved


def test_returning_to_previous_preferences_reuses_reviewed_content_as_latest_publication(native, monkeypatch):
    a, _, root = native
    preferences(a, guidanceStyles=["examples"])
    resource = workspace(a, uploaded(a))
    sid = complete_interview(a, resource)
    path = f"/api/workspaces/workspace_first/resources/{resource['id']}"
    queue = CourseJobQueue(root / "cycle-jobs.sqlite3", api._generate_course_task)
    monkeypatch.setattr(api, "course_job_queue", queue)
    a.app.add_api_route("/api/interviews/{session_id}/course-jobs", api.start_course_task, methods=["POST"])
    a.app.add_api_route("/api/interviews/{session_id}/course-jobs/{job_id}", api.course_task_status, methods=["GET"])
    job_path = f"/api/interviews/{sid}/course-jobs"

    def generate(request_id):
        desired = a.get(path).json()["learning_design_fingerprint"]
        response = a.post(job_path, json={"chapter_id": "chapter_meiosis",
            "design_fingerprint": desired, "request_id": request_id})
        assert response.status_code == 202, response.text
        job = response.json()
        assert queue.process_one()
        result = a.get(job_path + "/" + job["job_id"]).json()
        assert result["status"] == "succeeded", result
        return job["job_id"], result["course"]

    first_job, first = generate("cycle-first-a")
    original = api.assessment_repository.get_course(first["course_id"]).model_dump_json()
    preferences(a, guidanceStyles=["principles"])
    _, second = generate("cycle-second-b")
    preferences(a, guidanceStyles=["examples"])

    def must_reuse(*args, **kwargs):
        pytest.fail("Previously reviewed teaching must not request another model rewrite")

    monkeypatch.setattr(api.teaching_generator, "generate", must_reuse)
    restored_job, restored = generate("cycle-restored-a")
    assert restored_job != first_job
    assert restored["version"] == second["version"] + 1
    assert restored["design_fingerprint"] == first["design_fingerprint"]
    assert restored["knowledge_points"] == first["knowledge_points"]
    detail = a.get(path).json()
    assert detail["courses"][0]["course_id"] == restored["course_id"]
    assert detail["learning_design_fingerprint"] == restored["design_fingerprint"]
    assert api.assessment_repository.get_course(first["course_id"]).model_dump_json() == original
    # A lost POST reply recovers the new publication through the same request ID.
    recovered = a.post(job_path, json={"chapter_id": "chapter_meiosis",
        "design_fingerprint": restored["design_fingerprint"], "request_id": "cycle-restored-a"}).json()
    assert recovered["job_id"] == restored_job
    assert recovered["course"]["course_id"] == restored["course_id"]
