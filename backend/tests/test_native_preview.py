"""Preview is a separate purpose, never an invented diagnostic transcript."""

import json
from types import SimpleNamespace

from native_harness import api, textbook_pdf
from test_native_api import complete_interview, uploaded, workspace
from test_native_api import native as native

from adaptive_learning.assessment.models import InterviewPhase
from adaptive_learning.personalization.teaching_content import TeachingContentError


def preview_path(resource):
    return f"/api/workspaces/{resource['workspace_id']}/resources/{resource['id']}"


def test_preview_generates_real_course_without_diagnostic_or_preference_pollution(
    native, monkeypatch
):
    client, other, _ = native
    preferences = {"displayName": "原有读者", "primaryGoal": "exam", "dailyTime": "30to60"}
    saved = client.put("/api/native/learner-preferences", json=preferences).json()
    book = uploaded(client)
    original = workspace(client, book, "workspace_original_learning")
    original_sid = complete_interview(client, original)
    original_session = api.assessment_repository.get_session(original_sid).model_dump(mode="json")
    resource = workspace(client, book, "workspace_preview_test")
    with api.assessment_repository._connect() as db:
        bank_count = db.execute("SELECT count(*) FROM diagnostic_banks").fetchone()[0]

    def must_not_generate(*args, **kwargs):
        raise AssertionError("preview must not generate or submit diagnostic items")

    monkeypatch.setattr(api, "item_generator", SimpleNamespace(generate=must_not_generate))
    response = client.post(preview_path(resource) + "/preview-session")
    assert response.status_code == 200, response.text
    value = response.json()
    sid = value["session_id"]
    assert value["purpose"] == "preview" and value["phase"] == "preview_ready"
    assert value["turn"]["phase"] == "preview_ready"
    assert "未做诊断" in value["turn"]["message"]
    assert "掌握度未知" in value["turn"]["message"]
    assert value["turn"]["item"] is None
    assert value["profile"]["background_level"] == "unknown"
    assert value["profile"]["evidence"] == value["profile"]["diagnostic_observations"] == []
    assert value["profile"]["profile_confidence"] == 0
    session = api.assessment_repository.get_session(sid)
    assert session.responses == session.asked_item_ids == session.follow_up_item_ids == []
    assert all(
        point.evidence_count == 0 and point.tracked_mastery is None
        for point in [
            *session.profile.chapter_mastery.values(),
            *session.profile.knowledge_mastery.values(),
        ]
    )
    with api.assessment_repository._connect() as db:
        assert db.execute("SELECT count(*) FROM diagnostic_banks").fetchone()[0] == bank_count
    assert json.loads(api.native_repository.session_context(sid)["items_json"]) == []
    assert client.post(preview_path(resource) + "/preview-session").json()["session_id"] == sid
    assert client.post(preview_path(resource) + "/session").json()["purpose"] == "preview"
    assert other.get(f"/api/interviews/{sid}").status_code == 403
    assert other.post(preview_path(resource) + "/preview-session").status_code == 404

    course = client.post(f"/api/interviews/{sid}/courses/chapter_meiosis")
    assert course.status_code == 200, course.text
    bundle = course.json()
    assert bundle["original_reading"] and bundle["flashcards"] and bundle["practice_items"]
    assert all(point["state"] == "尚未验证" for point in bundle["knowledge_points"])
    assert bundle["decision"]["evidence_confidence"] == 0
    assert "未做诊断" in bundle["decision"]["explanation"]
    assert "%" not in bundle["decision"]["explanation"]
    assert all(
        "correct_option_ids" not in item and "expected_answer" not in item
        for item in bundle["practice_items"]
    )
    published = api.assessment_repository.get_latest_course(sid, "chapter_meiosis")
    assert published.teaching_review["accepted"] and published.teaching_fact_review["accepted"]
    full = client.get("/api/native/bootstrap").json()
    assert full["learner_preferences"] == saved
    result = next(w for w in full["workspaces"] if w["id"] == resource["workspace_id"])[
        "resources"
    ][0]
    assert result["session_purpose"] == "preview" and len(result["courses"]) == 1
    assert result["source_content"] is None
    metadata = client.get("/api/native/bootstrap?mode=metadata").json()
    assert (
        next(w for w in metadata["workspaces"] if w["id"] == resource["workspace_id"])["resources"][
            0
        ]["session_purpose"]
        == "preview"
    )
    assert client.get(preview_path(resource)).json()["session_purpose"] == "preview"
    records = client.get(f"/api/interviews/{sid}/learning-records").json()
    assert records["knowledge"] == records["evidence"] == records["flashcards"] == []
    stored = api.assessment_repository.get_session(sid)
    assert stored.purpose == "preview" and stored.phase == InterviewPhase.PREVIEW_READY
    assert stored.responses == stored.profile.evidence == []
    assert (
        api.assessment_repository.get_session(original_sid).model_dump(mode="json")
        == original_session
    )


def test_preview_cannot_replace_existing_learning_session(native):
    client, _, _ = native
    resource = workspace(client, uploaded(client))
    sid = complete_interview(client, resource)
    before = api.assessment_repository.get_session(sid).model_dump(mode="json")
    response = client.post(preview_path(resource) + "/preview-session")
    assert response.status_code == 409
    assert "不能改为课程预览" in response.text
    assert api.assessment_repository.get_session(sid).model_dump(mode="json") == before
    assert client.get(preview_path(resource)).json()["session_purpose"] == "learning"
    assert client.get(f"/api/interviews/{sid}").json()["purpose"] == "learning"


def test_preview_requires_source_and_rejects_diagnostic_mutations(native):
    client, _, _ = native
    upload = client.post(
        "/api/native/uploads", files={"file": ("pending.pdf", textbook_pdf(), "application/pdf")}
    )
    resource = workspace(client, upload.json()["book_id"])
    assert client.post(preview_path(resource) + "/preview-session").status_code == 409
    ready = workspace(client, uploaded(client), "workspace_ready_preview")
    sid = client.post(preview_path(ready) + "/preview-session").json()["session_id"]
    for suffix, body in [
        ("/profile", {"answer": "new", "selected_option_ids": ["new"]}),
        ("/next", None),
        ("/confirm", {"confirmed": True}),
    ]:
        assert client.post(f"/api/interviews/{sid}" + suffix, json=body).status_code == 409
    session = api.assessment_repository.get_session(sid)
    assert session.phase == InterviewPhase.PREVIEW_READY and session.responses == []


def test_preview_preserves_publication_rejection_and_no_course_is_saved(native, monkeypatch):
    client, _, _ = native
    resource = workspace(client, uploaded(client))
    sid = client.post(preview_path(resource) + "/preview-session").json()["session_id"]

    def reject(*args, **kwargs):
        raise TeachingContentError("fixture: independent source review rejected content")

    monkeypatch.setattr(api, "teaching_generator", SimpleNamespace(generate=reject))
    response = client.post(f"/api/interviews/{sid}/courses/chapter_meiosis")
    assert response.status_code == 503
    assert api.assessment_repository.get_latest_course(sid, "chapter_meiosis") is None
    assert client.get(preview_path(resource)).json()["courses"] == []
    session = api.assessment_repository.get_session(sid)
    assert session.responses == session.profile.evidence == []
