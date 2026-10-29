import json
from datetime import date, timedelta

import pytest
from native_harness import FixtureAnswerModel, api, qa
from test_course_compiler import chapter
from test_native_api import complete_interview, uploaded, workspace

from adaptive_learning.assessment.models import LearnerProfile
from adaptive_learning.learning_preferences import LearningDesignOverrides, resolve_learning_design
from adaptive_learning.native import NativeRepository
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.policy import PersonalizationPolicy
from adaptive_learning.rag.grounded_qa import GroundedAnswerGenerator

pytest_plugins = ["test_native_api"]


def preferences(client, **changes):
    value = {
        "displayName": "个性化学习者",
        "primaryGoal": "exam",
        "dailyTime": "under30",
        "learningBarriers": ["understanding", "memory"],
        "guidanceStyles": ["examples", "stepByStep"],
        "planningSupport": "collaborative",
        "onboardingVersion": 2,
        **changes,
    }
    response = client.put("/api/native/learner-preferences", json=value)
    assert response.status_code == 200, response.text
    return value


def test_resolution_has_explicit_precedence_and_clear_vs_inherit():
    account = {"primaryGoal": "exam", "dailyTime": "under30", "guidanceStyles": ["examples"]}
    config = resolve_learning_design(
        account,
        {"goals": ["interest"]},
        LearningDesignOverrides(guidance_styles=[], minutes_per_day=90),
        {"minutes_per_day": "15"},
    )
    assert config.primary_goal == "interest"
    assert config.guidance_styles == []
    assert config.minutes_per_day == 15
    assert config.sources["minutes_per_day"] == "interview"
    assert resolve_learning_design(
        account, overrides=LearningDesignOverrides(guidance_styles=None)
    ).guidance_styles == ["examples"]
    same = config.model_copy(update={"sources": {}})
    assert same.fingerprint == config.fingerprint


@pytest.mark.parametrize(
    "styles,first",
    [
        (["examples"], "evidence_example"),
        (["principles"], "explanation"),
        (["keyPoints"], "key_points"),
        (["stepByStep"], "steps"),
    ],
)
def test_preferences_change_teaching_units_without_changing_mastery_or_losing_coverage(
    styles, first
):
    material = chapter(23)
    profile = LearnerProfile(user_id="u", book_id="b")
    before = profile.model_copy(deep=True)
    profile.learning_design = resolve_learning_design(
        {
            "primaryGoal": "exam",
            "dailyTime": "under30",
            "guidanceStyles": styles,
            "learningBarriers": ["focus"],
        }
    )
    profile.constraints.minutes_per_day = profile.learning_design.minutes_per_day
    decision = PersonalizationPolicy().decide(profile, material.chapter_id)
    course = ChapterCourseCompiler().compile(
        chapter=material, profile=profile, decision=decision, instance_key="session-a"
    )
    assert decision.depth == PersonalizationPolicy().decide(before, material.chapter_id).depth
    assert profile.chapter_mastery == before.chapter_mastery
    assert len(course.knowledge_points) == 23
    ids = [pid for unit in course.units for pid in unit.knowledge_point_ids]
    assert len(ids) == len(set(ids)) == 23
    assert set(ids) == set(course.coverage_point_ids)
    assert all(
        unit.estimated_minutes <= profile.learning_design.segment_minutes for unit in course.units
    )
    assert course.units[0].sections[0].kind == first
    assert all(section.citations for unit in course.units for section in unit.sections)


def test_course_config_is_private_validated_and_independent_between_workspaces(native):
    a, b, _ = native
    preferences(a)
    book = uploaded(a)
    workspace(a, book)
    workspace(a, book, "workspace_second")
    first = a.get("/api/workspaces/workspace_first/learning-design").json()
    assert first["effective"]["minutes_per_day"] == 20
    changed = a.put(
        "/api/workspaces/workspace_second/learning-design",
        json={
            "revision": 0,
            "overrides": {
                "primary_goal": "interest",
                "minutes_per_day": 90,
                "guidance_styles": ["principles"],
            },
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["effective"]["primary_goal"] == "interest"
    assert a.get("/api/workspaces/workspace_first/learning-design").json() == first
    assert b.get("/api/workspaces/workspace_second/learning-design").status_code == 404
    assert (
        a.put(
            "/api/workspaces/workspace_second/learning-design",
            json={"revision": 0, "overrides": {}},
        ).status_code
        == 409
    )
    assert (
        a.put(
            "/api/workspaces/workspace_second/learning-design",
            json={"revision": 1, "overrides": {"guidance_styles": ["examples"] * 3}},
        ).status_code
        == 422
    )


def test_new_preferences_generate_a_new_version_keep_stable_items_and_saved_progress(native):
    a, _, root = native
    preferences(a)
    book = uploaded(a)
    resource = workspace(a, book)
    path = f"/api/workspaces/workspace_first/resources/{resource['id']}"
    initial = a.post(path + "/session").json()
    assert initial["phase"] == "goal_discovery"
    assert initial["profile"]["constraints"]["minutes_per_day"] == 20
    sid = complete_interview(a, resource)
    endpoint = f"/api/interviews/{sid}/courses/chapter_meiosis"
    first = a.post(endpoint)
    assert first.status_code == 200, first.text
    first = first.json()
    assert first["design_config"]["guidance_styles"] == ["examples", "stepByStep"]
    assert first["units"][0]["sections"][0]["kind"] == "evidence_example"
    saved = a.put(
        path + "/progress",
        json={
            "revision": 0,
            "mutation_id": "before-design-change",
            "page": 2,
            "chapter_id": "chapter_meiosis",
            "completed_chapters": ["chapter_meiosis"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert a.post(endpoint).json()["version"] == first["version"]
    preferences(a, guidanceStyles=["principles"])
    second = a.post(endpoint).json()
    assert second["version"] == first["version"] + 1
    assert second["units"][0]["sections"][0]["kind"] == "explanation"
    assert [c["card_id"] for c in second["flashcards"]] == [
        c["card_id"] for c in first["flashcards"]
    ]
    assert [q["item_id"] for q in second["practice_items"]] == [
        q["item_id"] for q in first["practice_items"]
    ]
    api.native_repository = NativeRepository(root / "state" / "native.sqlite3")
    assert a.get(path).json()["progress"] == saved.json()
    assert a.post(endpoint).json()["course_id"] == second["course_id"]
    assert "expected_answer" not in json.dumps(second)


def import_catalog(client):
    response = client.post(
        "/api/native/catalog/community_genetics/import",
        json={"id": "workspace_catalog_plan", "name": "已有生物课"},
    )
    assert response.status_code == 200, response.text
    return response.json()["resources"][0]


def test_confirmed_plan_completion_and_future_preferences_survive_restart(native):
    a, b, root = native
    preferences(a)
    import_catalog(a)
    base = "/api/workspaces/workspace_catalog_plan/study-plan"
    day = date.today().isoformat()
    plan = a.get(base, params={"day": day}).json()
    assert plan["estimated_minutes"] <= plan["budget_minutes"] == 20
    assert plan["needs_confirmation"]
    assert len(plan["queue"]) > len(plan["tasks"])
    confirmed = a.post(
        base + "/confirm",
        json={
            "day": day,
            "plan_id": plan["plan_id"],
            "task_ids": [t["task_id"] for t in plan["tasks"]],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed"]
    confirmation = {
        "day": day,
        "plan_id": plan["plan_id"],
        "task_ids": [t["task_id"] for t in plan["tasks"]],
    }
    assert a.post(base + "/confirm", json=confirmation).status_code == 200
    assert a.post(base + "/confirm", json={**confirmation, "task_ids": []}).status_code == 409
    task = plan["tasks"][0]["task_id"]
    completion = {"event_id": "completed-first-plan-task", "day": day}
    assert a.post(base + f"/tasks/{task}/complete", json=completion).json()["duplicate"] is False
    assert a.post(base + f"/tasks/{task}/complete", json=completion).json()["duplicate"] is True
    assert b.get(base).status_code == 404
    preferences(a, dailyTime="1to2h")
    api.native_repository = NativeRepository(root / "state" / "native.sqlite3")
    today = a.get(base, params={"day": day}).json()
    assert today["plan_id"] == plan["plan_id"]
    assert today["tasks"][0]["status"] == "done"
    assert today["preferences_changed"]
    tomorrow = a.get(base, params={"day": (date.today() + timedelta(days=1)).isoformat()}).json()
    assert tomorrow["budget_minutes"] == 90
    assert all(t["task_id"] != task for t in tomorrow["tasks"])


def test_saved_plan_can_complete_task_after_live_queue_changes(native, monkeypatch):
    from adaptive_learning.learning_design import NativeLearningDesign

    a, b, _ = native
    preferences(a)
    import_catalog(a)
    base = "/api/workspaces/workspace_catalog_plan/study-plan"
    day = date.today().isoformat()
    plan = a.get(base, params={"day": day}).json()
    task_id = plan["tasks"][0]["task_id"]
    assert a.post(base + "/confirm", json={
        "day": day, "plan_id": plan["plan_id"],
        "task_ids": [t["task_id"] for t in plan["tasks"]], "revision": plan["revision"],
    }).status_code == 200
    monkeypatch.setattr(NativeLearningDesign, "queue", lambda *args: [])
    path = base + f"/tasks/{task_id}/complete"
    body = {"day": day, "event_id": "review-left-live-queue"}
    assert a.post(path, json=body).status_code == 200
    assert a.post(path, json=body).json()["duplicate"] is True
    assert b.post(path, json=body).status_code == 404
    assert a.post(base + "/tasks/unknown/complete", json={**body, "event_id": "unknown-task"}).status_code == 404
    reloaded = a.get(base, params={"day": day}).json()
    assert reloaded["tasks"][0]["status"] == "done"


def test_generated_course_plan_uses_time_budget_and_completion_survives_reload(native):
    a, _, root = native
    preferences(a, dailyTime="under30", planningSupport="structured")
    book = uploaded(a)
    resource = workspace(a, book)
    sid = complete_interview(a, resource)
    generated = a.post(f"/api/interviews/{sid}/courses/chapter_meiosis")
    assert generated.status_code == 200, generated.text
    base = "/api/workspaces/workspace_first/study-plan"
    day = date.today().isoformat()
    response = a.get(base, params={"day": day})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["budget_minutes"] == 20
    assert 0 < plan["estimated_minutes"] <= 20
    assert all(task["resource_id"] == resource["id"] for task in plan["tasks"])
    assert any(task["kind"] == "learn" for task in plan["tasks"])
    task = plan["tasks"][0]
    completed = a.post(base + f"/tasks/{task['task_id']}/complete", json={
        "day": day, "event_id": "native-generated-task-completed",
    })
    assert completed.status_code == 200, completed.text
    api.native_repository = NativeRepository(root / "state" / "native.sqlite3")
    reloaded = a.get(base, params={"day": day}).json()
    assert reloaded["plan_id"] == plan["plan_id"]
    assert next(t for t in reloaded["tasks"] if t["task_id"] == task["task_id"])["status"] == "done"


def test_feedback_requires_repeated_evidence_and_adjustment_can_be_reverted(native):
    a, _, _ = native
    preferences(a, guidanceStyles=["keyPoints"])
    resource = import_catalog(a)
    base = "/api/workspaces/workspace_catalog_plan"
    from adaptive_learning import native_catalog

    question = next(q for q in native_catalog.snapshot()["source"]["quizzes"] if q["choices"])
    wrong = next(choice for choice in question["choices"] if choice != question["answer"])
    path = base + f"/resources/{resource['id']}/catalog-practice/{question['question_id']}"
    for i in range(3):
        answer = {
            "event_id": f"repeated-error-{i}",
            "answer": wrong,
            "response_seconds": 12,
            "confidence": 0.5,
        }
        assert a.post(path, json=answer).status_code == 200
        assert a.post(path, json=answer).json()["duplicate"]
        suggestions = a.get(base + "/learning-design/feedback").json()
        if i < 2:
            assert suggestions == []
    assert len(suggestions) == 1
    suggestion = suggestions[0]
    accepted = a.post(
        base + "/learning-design/adjustments",
        json={"suggestion_id": suggestion["suggestion_id"], "revision": 0},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["design"]["effective"]["guidance_styles"] == ["examples", "stepByStep"]
    reverted = a.post(base + f"/learning-design/adjustments/{suggestion['suggestion_id']}/revert")
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["effective"]["guidance_styles"] == ["keyPoints"]
    assert len(api.native_repository.catalog_practice(resource["id"])) == 3


def test_qa_uses_course_preferences_and_isolates_account_and_design_cache(native):
    a, b, _ = native
    preferences(a)
    book = uploaded(a)
    first = workspace(a, book)
    second = workspace(a, book, "workspace_other_qa")
    changed = a.put(
        "/api/workspaces/workspace_other_qa/learning-design",
        json={"revision": 0, "overrides": {"guidance_styles": ["principles"]}},
    )
    assert changed.status_code == 200
    payloads = []

    class CaptureModel(FixtureAnswerModel):
        def structured(self, **kwargs):
            payloads.append(json.loads(kwargs["user"]))
            return super().structured(**kwargs)

    qa.build_book_qa_service(book).generator = GroundedAnswerGenerator(CaptureModel())
    endpoint = f"/api/books/{book}/qa"
    body = {
        "question": "减数分裂有什么特点？",
        "workspace_id": first["workspace_id"],
        "resource_id": first["id"],
    }
    answer = a.post(endpoint, json=body)
    assert answer.status_code == 200, answer.text
    assert payloads[-1]["teaching_preferences"]["guidance_styles"] == ["examples", "stepByStep"]
    assert a.post(endpoint, json=body).json()["cache_hit"]
    assert len(payloads) == 1
    body.update(workspace_id=second["workspace_id"], resource_id=second["id"])
    assert a.post(endpoint, json=body).status_code == 200
    assert payloads[-1]["teaching_preferences"]["guidance_styles"] == ["principles"]
    assert len(payloads) == 2
    assert b.post(endpoint, json=body).status_code == 403
    assert len(payloads) == 2
    preferences(a, guidanceStyles=["keyPoints"])
    body.update(workspace_id=first["workspace_id"], resource_id=first["id"])
    assert a.post(endpoint, json=body).status_code == 200
    assert payloads[-1]["teaching_preferences"]["guidance_styles"] == ["keyPoints"]
    assert len(payloads) == 3
    # The old book-only API must not accidentally inherit a different course's
    # settings when the same book belongs to multiple workspaces.
    legacy = a.post(endpoint, json={"question": body["question"]})
    assert legacy.status_code == 200
    assert payloads[-1]["teaching_preferences"]["guidance_styles"] == ["keyPoints"]
    assert len(payloads) == 4


def test_catalog_recall_completion_is_idempotent_and_not_repeated_next_day(native):
    a, _, _ = native
    preferences(a)
    resource = import_catalog(a)
    from adaptive_learning import native_catalog

    card = next(c for c in native_catalog.snapshot()["source"]["flashcards"] if c["page_start"] > 0)
    resource_path = f"/api/workspaces/workspace_catalog_plan/resources/{resource['id']}"
    response = a.put(
        resource_path + "/progress",
        json={
            "revision": 0,
            "mutation_id": "learned-for-recall",
            "page": card["page_start"],
            "completed_chapters": [card["chapter_id"]],
        },
    )
    assert response.status_code == 200, response.text
    today = date.today()
    endpoint = "/api/workspaces/workspace_catalog_plan/study-plan"
    plan = a.get(endpoint, params={"day": today.isoformat()}).json()
    recall = next(t for t in plan["queue"] if t.get("card_id") == card["card_id"])
    body = {"event_id": "completed-catalog-recall", "day": today.isoformat()}
    completion_path = endpoint + f"/tasks/{recall['task_id']}/complete"
    assert a.post(completion_path, json=body).status_code == 200
    assert a.post(completion_path, json=body).json()["duplicate"]
    tomorrow = a.get(endpoint, params={"day": (today + timedelta(days=1)).isoformat()}).json()
    assert not any(t.get("card_id") == card["card_id"] for t in tomorrow["queue"])
    later = a.get(endpoint, params={"day": (today + timedelta(days=3)).isoformat()}).json()
    assert any(t.get("card_id") == card["card_id"] for t in later["queue"])


def test_old_card_review_is_reused_when_a_course_changes_its_card_ids():
    from adaptive_learning.personalization.review import review_state_for_point, schedule_review

    profile = LearnerProfile(user_id="u", book_id="b")
    value = chapter(2)
    compiler = ChapterCourseCompiler()
    original = compiler.compile(
        chapter=value,
        profile=profile,
        decision=PersonalizationPolicy().decide(profile, value.chapter_id),
    )
    original.flashcards[0].card_id = "legacy_versioned_card"
    previous = schedule_review(None, "good")
    profile.flashcard_reviews["legacy_versioned_card"] = previous
    profile.learning_design = resolve_learning_design({"guidanceStyles": ["examples"]})
    changed = compiler.compile(
        chapter=value,
        profile=profile,
        decision=PersonalizationPolicy().decide(profile, value.chapter_id),
    )
    point = original.flashcards[0].point_id
    restored = review_state_for_point(profile, point, [original, changed])
    assert restored == previous
    assert restored.card_id == previous.card_id


def test_new_course_goal_override_supersedes_an_older_interview_answer(native):
    a, _, _ = native
    preferences(a, primaryGoal=None)
    book = uploaded(a)
    resource = workspace(a, book)
    assert (
        a.post(
            "/api/workspaces", json={"id": "workspace_first", "name": "我的课程", "preferences": {}}
        ).status_code
        == 200
    )
    sid = complete_interview(a, resource)
    before = a.post(f"/api/interviews/{sid}/courses/chapter_meiosis").json()
    assert before["design_config"]["primary_goal"] == "exam"
    response = a.put(
        "/api/workspaces/workspace_first/learning-design",
        json={"revision": 0, "overrides": {"primary_goal": "interest"}},
    )
    assert response.status_code == 200
    after = a.post(f"/api/interviews/{sid}/courses/chapter_meiosis").json()
    assert after["design_config"]["primary_goal"] == "interest"
    assert after["version"] == before["version"] + 1
