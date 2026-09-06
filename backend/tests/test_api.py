from fastapi.testclient import TestClient

from adaptive_learning.api.app import app


def test_demo_interview_starts_with_user_goal_question() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        response = client.post(
            "/api/interviews/start",
            json={"user_id": "test_user", "book_id": "demo_book"},
        )

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert response.status_code == 200
    turn = response.json()["turn"]
    assert "主要目标" in turn["question"]
    assert turn["response_type"] == "single_choice"
    assert len(turn["options"]) == 4


def test_demo_interview_uses_structured_profile_and_never_leaks_answer_key() -> None:
    with TestClient(app) as client:
        started = client.post(
            "/api/interviews/start",
            json={"user_id": "privacy_user", "book_id": "demo_book"},
        )
        assert started.status_code == 200
        session_id = started.json()["session_id"]
        steps = [
            {"answer": "从基础开始系统掌握", "selected_option_ids": ["foundation"]},
            {"answer": "选择重点", "selected_option_ids": ["ch_2", "ch_3"]},
            {"answer": "系统学过", "selected_option_ids": ["systematic"]},
            {"answer": "45 分钟", "selected_option_ids": ["45"]},
        ]
        responses = [
            client.post(f"/api/interviews/{session_id}/profile", json=payload) for payload in steps
        ]
        diagnostic = client.post(f"/api/interviews/{session_id}/next")
        resumed = client.get(f"/api/interviews/{session_id}")

    assert all(response.status_code == 200 for response in responses)
    assert diagnostic.status_code == 200
    assert resumed.status_code == 200
    payload = diagnostic.json()
    serialized = diagnostic.text
    assert payload["phase"] == "adaptive_diagnosis"
    assert payload["turn"]["response_type"] == "single_choice"
    assert len(payload["turn"]["options"]) >= 2
    assert payload["profile"]["focus_chapter_ids"] == ["ch_2", "ch_3"]
    assert payload["profile"]["background_level"] == "systematic"
    assert payload["profile"]["constraints"]["minutes_per_day"] == 45
    assert "correct_option_ids" not in serialized
    assert "expected_answer" not in serialized
    assert "rubric" not in serialized
    assert resumed.json()["turn"]["turn_id"] == payload["turn"]["turn_id"]


def test_next_diagnostic_is_idempotent_while_an_answer_is_pending() -> None:
    with TestClient(app) as client:
        started = client.post(
            "/api/interviews/start",
            json={"user_id": "idempotent_user", "book_id": "demo_book"},
        ).json()
        session_id = started["session_id"]
        for payload in [
            {"answer": "快速理解全书重点", "selected_option_ids": ["overview"]},
            {"answer": "自动", "selected_option_ids": ["auto"]},
            {"answer": "没学过", "selected_option_ids": ["new"]},
            {"answer": "30", "selected_option_ids": ["30"]},
        ]:
            assert (
                client.post(f"/api/interviews/{session_id}/profile", json=payload).status_code
                == 200
            )
        first = client.post(f"/api/interviews/{session_id}/next")
        second = client.post(f"/api/interviews/{session_id}/next")

    assert first.status_code == second.status_code == 200
    assert first.json()["turn"]["turn_id"] == second.json()["turn"]["turn_id"]
    assert first.json()["turn"]["item"]["item_id"] == second.json()["turn"]["item"]["item_id"]


def test_course_generation_requires_a_confirmed_profile() -> None:
    with TestClient(app) as client:
        started = client.post(
            "/api/interviews/start",
            json={"user_id": "unconfirmed_user", "book_id": "demo_book"},
        ).json()
        response = client.post(
            f"/api/interviews/{started['session_id']}/courses/ch_1"
        )

    assert response.status_code == 409
    assert "确认学习画像" in response.json()["detail"]
