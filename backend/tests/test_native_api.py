import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from native_harness import QUOTES, api, configure, textbook_pdf

from adaptive_learning import native_catalog
from adaptive_learning.ingestion.jobs import JobStateError, OCRRunResult
from adaptive_learning.native import NativeRepository
from adaptive_learning.rag.upload_index import published_upload_index


def test_native_capabilities_report_client_support_without_claiming_runtime_ready(native):
    client, _, _ = native
    response = client.get("/api/native/capabilities")
    assert response.status_code == 200
    capabilities = response.json()
    for name in ("voice_transcription", "handwriting", "media_generation"):
        assert capabilities[name]["backend"] is True
        assert capabilities[name]["native"] is True
        assert capabilities[name]["runtime_status_path"] == "/api/studio/capabilities"
        assert "ready" not in capabilities[name]
    for name in ("note_cloud_sync", "server_planning", "teaching_units"):
        assert capabilities[name]["native"] is False


def test_local_default_login_retains_account_and_uploaded_pdf(tmp_path, monkeypatch):
    app = configure(tmp_path, monkeypatch)
    monkeypatch.setenv("NATIVE_LOCAL_LOGIN_ENABLED", "1")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000)) as client:
        response = client.post("/api/native/local-login")
        assert response.status_code == 200, response.text
        assert "HttpOnly" in response.headers["set-cookie"]
        account = client.get("/api/auth/me").json()
        assert account["account"]["email"] == "local@cloudpath.invalid"
        assert not account["email_available"]
        saved = client.post(
            "/api/workspaces", json={"id": "workspace_local_default", "name": "本地课程"}
        )
        assert saved.status_code == 200, saved.text
        upload = client.post(
            "/api/native/uploads", files={"file": ("教材.pdf", textbook_pdf(), "application/pdf")}
        )
        assert upload.status_code == 200, upload.text
        book = upload.json()["book_id"]
        old_cookie = client.cookies.get("zhiwo_account")
        assert client.post("/api/auth/logout").status_code == 200
        assert api.account_repository.session_owner(old_cookie) is None
        assert client.post("/api/native/local-login").status_code == 200
        assert client.get("/api/auth/me").json()["user_id"] == account["user_id"]
        api.native_repository = NativeRepository(tmp_path / "state" / "native.sqlite3")
        state = client.get("/api/native/bootstrap").json()
        assert state["workspaces"][0]["name"] == "本地课程"
        assert client.get(f"/api/books/{book}/source").content.startswith(b"%PDF-")
        with api.account_repository.repo.connect() as db:
            assert db.execute("SELECT count(*) FROM email_codes").fetchone()[0] == 0
        monkeypatch.setenv("APP_ENV", "production")
        assert client.post("/api/native/local-login").status_code == 404
        assert client.get("/api/native/bootstrap").status_code == 401


@pytest.mark.parametrize(
    ("enabled", "host", "peer", "origin", "expected"),
    [
        ("0", "127.0.0.1", "127.0.0.1", None, 404),
        ("1", "127.0.0.1", "192.168.1.10", None, 403),
        ("1", "study.example.com", "127.0.0.1", None, 403),
        ("1", "127.0.0.1", "127.0.0.1", "https://elsewhere.example", 403),
    ],
)
def test_local_login_requires_explicit_local_development_mode(
    tmp_path, monkeypatch, enabled, host, peer, origin, expected
):
    app = configure(tmp_path, monkeypatch)
    monkeypatch.setenv("NATIVE_LOCAL_LOGIN_ENABLED", enabled)
    with TestClient(app, base_url=f"http://{host}", client=(peer, 5000)) as client:
        response = client.post(
            "/api/native/local-login", headers={"Origin": origin} if origin else {}
        )
        assert response.status_code == expected, response.text


@pytest.fixture
def native(tmp_path, monkeypatch):
    app = configure(tmp_path, monkeypatch)
    with TestClient(app) as a, TestClient(app) as b:
        for client, email in [(a, "reader@zhiwo.test"), (b, "xiaolin@zhiwo.test")]:
            challenge = client.post(
                "/api/auth/code", json={"email": email, "purpose": "register"}
            ).json()
            response = client.post(
                "/api/auth/verify",
                json={
                    "challenge_id": challenge["challenge_id"],
                    "code": challenge["demo_code"],
                    "registration": {
                        "nickname": "测试读者",
                        "stage": "university",
                        "interests": [],
                        "goal": "interest",
                    },
                },
            )
            assert response.status_code == 200, response.text
        yield a, b, tmp_path


def test_learner_questions_completion_is_explicit_persistent_and_private(native):
    a, b, root = native
    assert a.get("/api/native/bootstrap").json()["learner_preferences"] is None
    value = {"displayName": "小新", "primaryGoal": "exam", "dailyTime": "30to60"}
    legacy_defaults = {
        "learningStage": None,
        "learningBarriers": [],
        "guidanceStyles": [],
        "planningSupport": None,
        "onboardingVersion": 1,
    }
    assert a.put("/api/native/learner-preferences", json=value).json() == {
        **value,
        **legacy_defaults,
    }
    api.native_repository = NativeRepository(root / "state" / "native.sqlite3")
    assert a.get("/api/native/bootstrap").json()["learner_preferences"] == {
        **value,
        **legacy_defaults,
    }
    assert a.get("/api/user/profile").json()["nickname"] == "小新"
    assert b.get("/api/native/bootstrap").json()["learner_preferences"] is None
    assert (
        a.put("/api/native/learner-preferences", json={**value, "displayName": "  "}).status_code
        == 422
    )
    assert (
        a.put(
            "/api/native/learner-preferences", json={**value, "primaryGoal": "unknown"}
        ).status_code
        == 422
    )
    skipped = {"displayName": "小新", "primaryGoal": None, "dailyTime": None}
    assert a.put("/api/native/learner-preferences", json=skipped).json() == {
        **skipped,
        **legacy_defaults,
    }


def test_six_step_preferences_restore_and_legacy_edits_preserve_new_answers(native):
    a, b, root = native
    value = {
        "learningStage": "university",
        "displayName": "完整偏好",
        "primaryGoal": "exam",
        "dailyTime": "under30",
        "learningBarriers": ["understanding", "memory"],
        "guidanceStyles": ["examples", "stepByStep"],
        "planningSupport": "collaborative",
        "onboardingVersion": 2,
    }
    response = a.put("/api/native/learner-preferences", json=value)
    assert response.status_code == 200, response.text
    assert response.json() == value
    api.native_repository = NativeRepository(root / "state" / "native.sqlite3")
    assert a.get("/api/native/bootstrap").json()["learner_preferences"] == value
    assert b.get("/api/native/bootstrap").json()["learner_preferences"] is None
    # A client with the old three-field contract must not erase completed six-step answers.
    value["displayName"] = "更新称呼"
    edited = a.put("/api/native/learner-preferences", json={"displayName": "更新称呼"})
    assert edited.json() == value
    assert a.get("/api/user/profile").json()["nickname"] == "更新称呼"
    # All optional questions may be skipped while the flow is still explicitly complete.
    value.update(
        primaryGoal=None,
        dailyTime=None,
        learningBarriers=[],
        guidanceStyles=[],
        planningSupport=None,
    )
    assert a.put("/api/native/learner-preferences", json=value).json() == value


def test_personalization_stage_updates_account_without_erasing_it_for_older_clients(native):
    a, b, _ = native
    initial = a.get("/api/auth/me").json()["account"]["stage"]
    response = a.put("/api/native/learner-preferences", json={
        "displayName": "新同学", "learningStage": "working", "onboardingVersion": 2,
    })
    assert response.status_code == 200, response.text
    assert response.json()["learningStage"] == "working"
    assert a.get("/api/auth/me").json()["account"]["stage"] == "working"
    assert b.get("/api/auth/me").json()["account"]["stage"] == initial
    response = a.put("/api/native/learner-preferences", json={"displayName": "更新昵称"})
    assert response.json()["learningStage"] == "working"
    assert a.get("/api/auth/me").json()["account"]["stage"] == "working"
    response = a.put("/api/native/learner-preferences", json={
        "displayName": "跳过阶段", "learningStage": None,
    })
    assert response.json()["learningStage"] is None
    assert a.get("/api/auth/me").json()["account"]["stage"] == "other"


@pytest.mark.parametrize(
    "invalid",
    [
        {"learningStage": "unknown"},
        {"learningBarriers": ["starting", "focus", "memory"]},
        {"learningBarriers": ["memory", "memory"]},
        {"learningBarriers": ["unknown"]},
        {"guidanceStyles": ["keyPoints", "examples", "principles"]},
        {"guidanceStyles": ["examples", "examples"]},
        {"guidanceStyles": ["unknown"]},
        {"planningSupport": "unknown"},
        {"onboardingVersion": 3},
    ],
)
def test_six_step_preferences_reject_invalid_answers_without_saving(native, invalid):
    a, _, _ = native
    response = a.put("/api/native/learner-preferences", json={"displayName": "学习者", **invalid})
    assert response.status_code == 422
    assert a.get("/api/native/bootstrap").json()["learner_preferences"] is None


def test_existing_biology_catalog_import_preserves_content_and_isolates_progress(native):
    a, b, root = native
    entries = a.get("/api/native/catalog").json()
    assert entries[0]["id"] == native_catalog.CATALOG_ID
    assert entries[0]["flashcardCount"] == 90
    body = {
        "id": "workspace_biology_existing",
        "name": "生物复习",
        "preferences": {"goal": ["exam"]},
    }
    response = a.post(f"/api/native/catalog/{native_catalog.CATALOG_ID}/import", json=body)
    assert response.status_code == 200, response.text
    row = response.json()["resources"][0]
    rid = row["id"]
    path = f"/api/workspaces/{body['id']}/resources/{rid}"
    again = a.post(f"/api/native/catalog/{native_catalog.CATALOG_ID}/import", json=body)
    assert again.json()["resources"][0]["id"] == rid
    content = row["source_content"]
    assert len(content["lessons"]) == 15 and len(content["flashcards"]) == 90
    assert len(content["quizzes"]) == 45
    assert all(not q["answer"] and not q["explanation"] for q in content["quizzes"])
    assert all(t["status"] == "pending" for t in content["plan"]["tasks"])
    assert all(card["mastery"] == 0 for card in content["flashcards"])
    assert content["lessons"][0]["status"] == "supplemental_draft"
    assert b.get(path).status_code == 404
    assert (
        b.post(f"/api/native/catalog/{native_catalog.CATALOG_ID}/import", json=body).status_code
        == 404
    )
    independent = b.post(
        f"/api/native/catalog/{native_catalog.CATALOG_ID}/import",
        json={**body, "id": "workspace_biology_other"},
    ).json()["resources"][0]
    progress = {
        "revision": 0,
        "mutation_id": "catalog-progress-test",
        "page": 11,
        "chapter_id": "c2s1",
        "lesson_id": "lesson_meiosis",
        "completed_chapters": ["c2s1"],
        "task_status": {
            t["task_id"]: "done" for t in content["plan"]["tasks"] if t.get("chapter_id") == "c2s1"
        },
    }
    result = a.put(path + "/progress", json=progress)
    assert result.status_code == 200, result.text
    api.native_repository = NativeRepository(root / "state" / "native.sqlite3")
    saved = a.get(path).json()
    assert saved["progress"]["page"] == 11
    assert any(t["status"] == "done" for t in saved["source_content"]["plan"]["tasks"])
    assert all(t["status"] == "pending" for t in independent["source_content"]["plan"]["tasks"])
    assert (
        a.put(
            path + "/progress",
            json={**progress, "mutation_id": "invalid-lesson-id", "lesson_id": "other"},
        ).status_code
        == 422
    )
    question = native_catalog.snapshot()["source"]["quizzes"][0]
    practice_path = path + "/catalog-practice/" + question["question_id"]
    answer = {
        "event_id": "catalog-practice-test",
        "answer": question["answer"],
        "response_seconds": 10,
        "confidence": 0.6,
    }
    assert a.post(practice_path, json=answer).json()["evidence"]["score"] == 1
    assert a.post(practice_path, json=answer).json()["duplicate"]
    assert a.post(practice_path, json={**answer, "answer": "different"}).status_code == 409
    assert len(a.get(path + "/catalog-records").json()["evidence"]) == 1
    assert b.get(path + "/catalog-records").status_code == 404
    open_question = next(q for q in content["quizzes"] if not q["choices"])
    assert (
        a.post(
            path + "/catalog-practice/" + open_question["question_id"],
            json={**answer, "event_id": "new-open-question"},
        ).status_code
        == 503
    )


def test_builtin_biology_remains_in_discovery_after_personal_course_deletion_and_restart(native):
    a, b, root = native
    original = a.get("/api/native/catalog").json()
    body = {"id": "workspace_biology_temporary", "name": "个人生物课"}
    imported = a.post(f"/api/native/catalog/{native_catalog.CATALOG_ID}/import", json=body)
    assert imported.status_code == 200
    assert a.delete(f"/api/workspaces/{body['id']}").status_code == 200
    api.native_repository = NativeRepository(root / "state" / "native.sqlite3")
    native_catalog.snapshot.cache_clear()
    for client in (a, b):
        assert client.get("/api/native/catalog").json() == original
        state = client.get("/api/native/bootstrap").json()
        assert state["catalog"] == original
        assert state["workspaces"] == []
    assert (
        a.post(f"/api/native/catalog/{native_catalog.CATALOG_ID}/import", json=body).status_code
        == 200
    )


def uploaded(client):
    response = client.post(
        "/api/native/uploads", files={"file": ("生物教材.pdf", textbook_pdf(), "application/pdf")}
    )
    assert response.status_code == 200, response.text
    book = response.json()["book_id"]
    assert client.post(f"/api/books/{book}/process").status_code == 200
    prepared = client.post(f"/api/native/books/{book}/prepare")
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["rag_ready"]
    return book


def workspace(client, book, key="workspace_first"):
    response = client.post(
        "/api/workspaces", json={"id": key, "name": "生物复习", "preferences": {"goals": ["exam"]}}
    )
    assert response.status_code == 200, response.text
    resource = client.post(f"/api/workspaces/{key}/resources", json={"book_id": book})
    assert resource.status_code == 200, resource.text
    return resource.json()


def complete_interview(client, resource):
    base = f"/api/workspaces/{resource['workspace_id']}/resources/{resource['id']}"
    response = client.post(base + "/session")
    assert response.status_code == 200, response.text
    value = response.json()
    sid = value["session_id"]
    for _ in range(12):
        phase, turn = value["phase"], value["turn"]
        if phase == "complete":
            return sid
        if phase == "profile_confirmation":
            response = client.post(f"/api/interviews/{sid}/confirm", json={"confirmed": True})
        elif turn.get("item"):
            response = client.post(
                f"/api/interviews/{sid}/respond",
                json={
                    "item_id": turn["item"]["item_id"],
                    "answer": "0",
                    "selected_option_ids": ["0"],
                    "confidence": 0.6,
                    "response_seconds": 12,
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            value = {
                "session_id": sid,
                "phase": body["next_turn"]["phase"],
                "turn": body["next_turn"],
            }
            continue
        elif phase == "adaptive_diagnosis":
            response = client.post(f"/api/interviews/{sid}/next")
        else:
            selected = [turn["options"][0]["id"]]
            response = client.post(
                f"/api/interviews/{sid}/profile",
                json={"answer": ",".join(selected), "selected_option_ids": selected},
            )
        assert response.status_code == 200, response.text
        value = response.json()
    raise AssertionError("diagnosis did not complete")


def test_login_pdf_course_qa_and_restart_progress(native):
    client, _, root = native
    book = uploaded(client)
    resource = workspace(client, book)
    base = f"/api/workspaces/workspace_first/resources/{resource['id']}"
    sid = complete_interview(client, resource)
    bundle = client.post(f"/api/interviews/{sid}/courses/chapter_meiosis")
    assert bundle.status_code == 200, bundle.text
    course = bundle.json()
    assert course["original_reading"] and course["flashcards"]
    assert all(
        "expected_answer" not in item and "correct_option_ids" not in item
        for item in course["practice_items"]
    )
    answer = client.post(f"/api/books/{book}/qa", json={"question": "减数分裂有什么特点？"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "supported"
    for claim in answer.json()["claims"]:
        for citation in claim["citations"]:
            assert citation["quote"] in QUOTES[citation["page_number"] - 1]
    pdf = client.get(f"/api/books/{book}/source")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert client.get(f"/api/books/{book}/pages/2").json()["text"] == QUOTES[1]
    progress = {
        "revision": 0,
        "mutation_id": "progress_mutation_1",
        "page": 2,
        "chapter_id": "chapter_meiosis",
        "lesson_id": course["course_id"],
        "completed_chapters": ["chapter_meiosis"],
        "task_status": {"lesson": "done"},
    }
    saved = client.put(base + "/progress", json=progress)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert client.put(base + "/progress", json=progress).json()["revision"] == 1
    assert (
        client.put(
            "/api/native/selection",
            json={"workspace_id": "workspace_first", "resource_id": resource["id"]},
        ).status_code
        == 200
    )
    api.native_repository = NativeRepository(root / "state" / "native.sqlite3")
    with TestClient(client.app) as reopened:
        reopened.cookies.update(client.cookies)
        bootstrap = reopened.get("/api/native/bootstrap").json()
        restored = bootstrap["workspaces"][0]["resources"][0]
        assert restored["session_id"] == sid
        assert restored["progress"]["page"] == 2
        assert restored["progress"]["completed_chapters"] == ["chapter_meiosis"]
        assert restored["courses"][0]["course_id"] == course["course_id"]
        assert bootstrap["selection"]["resource_id"] == resource["id"]
    assert published_upload_index(root / "books" / book)


def test_private_resources_cannot_be_accessed_or_claimed_by_another_account(native):
    a, b, _ = native
    book = uploaded(a)
    resource = workspace(a, book)
    sid = complete_interview(a, resource)
    assert b.get("/api/native/bootstrap").json()["workspaces"] == []
    assert b.get("/api/workspaces/workspace_first").status_code == 404
    for path, method in [
        (f"/api/books/{book}/source", "GET"),
        (f"/api/books/{book}/status", "GET"),
        (f"/api/books/{book}/qa", "POST"),
        (f"/api/native/books/{book}/prepare", "POST"),
        (f"/api/native/books/{book}/cancel", "POST"),
        (f"/api/interviews/{sid}", "GET"),
        (f"/api/library/books/{book}/bind-upload", "POST"),
    ]:
        assert b.request(
            method, path, json={"question": "教材内容"} if path.endswith("/qa") else None
        ).status_code in {403, 404}
    assert (
        b.post("/api/workspaces", json={"id": "workspace_first", "name": "抢占"}).status_code == 404
    )
    assert a.post("/api/auth/logout").status_code == 200
    assert a.get("/api/native/bootstrap").status_code == 401


def test_same_book_workspaces_have_independent_sessions_and_personal_directories(native):
    a, _, _ = native
    book = uploaded(a)
    first = workspace(a, book)
    second = workspace(a, book, "workspace_second")
    base = f"/api/workspaces/workspace_second/resources/{second['id']}"
    chapter = second["structure"]["chapters"][0]
    changed = a.put(
        base + "/chapters",
        json={
            "revision": 0,
            "chapters": [
                {
                    "chapter_id": chapter["chapter_id"],
                    "title": "我的复习章节",
                    "start_page": 1,
                    "end_page": 3,
                }
            ],
        },
    )
    assert changed.status_code == 200, changed.text
    assert api.job_repository.get_structure(book).chapters[0].title == "第一章 减数分裂"
    sid1, sid2 = complete_interview(a, first), complete_interview(a, changed.json())
    assert sid1 != sid2
    course = a.post(f"/api/interviews/{sid2}/courses/chapter_meiosis")
    assert course.status_code == 200, course.text
    assert course.json()["chapter_title"] == "我的复习章节"
    assert (
        a.get("/api/workspaces/workspace_first").json()["resources"][0]["progress"]["revision"] == 0
    )


def test_progress_conflicts_and_validation_do_not_overwrite_saved_state(native):
    a, _, _ = native
    resource = workspace(a, uploaded(a))
    path = f"/api/workspaces/workspace_first/resources/{resource['id']}/progress"
    body = {"revision": 0, "mutation_id": "progress_mutation_1", "page": 2}
    assert a.put(path, json=body).status_code == 200
    assert (
        a.put(path, json={**body, "mutation_id": "progress_mutation_2", "page": 3}).status_code
        == 409
    )
    assert a.put(path, json={**body, "revision": 1, "page": 4}).status_code == 422
    assert (
        a.put(path, json={**body, "revision": 1, "lesson_id": "another-users-course"}).status_code
        == 422
    )
    assert a.get("/api/workspaces/workspace_first").json()["resources"][0]["progress"]["page"] == 2


def test_invalid_pdf_does_not_create_jobs_and_source_change_invalidates_index(native):
    a, _, root = native
    assert (
        a.post(
            "/api/native/uploads", files={"file": ("fake.pdf", b"not PDF", "application/pdf")}
        ).status_code
        == 415
    )
    assert (
        a.post(
            "/api/native/uploads", files={"file": ("empty.pdf", b"", "application/pdf")}
        ).status_code
        == 422
    )
    book = uploaded(a)
    directory = root / "books" / book
    assert published_upload_index(directory)
    path = directory / "ocr" / "normalized" / "pages.jsonl"
    page = json.loads(path.read_text().splitlines()[0])
    page["cleaned_text"] += "新版本原文。"
    path.write_text(json.dumps(page, ensure_ascii=False))
    assert published_upload_index(directory) is None


def test_concurrent_progress_accepts_one_revision(native):
    a, _, _ = native
    resource = workspace(a, uploaded(a))
    path = f"/api/workspaces/workspace_first/resources/{resource['id']}/progress"

    def send(index):
        with TestClient(a.app) as client:
            client.cookies.update(a.cookies)
            return client.put(
                path, json={"revision": 0, "mutation_id": f"concurrent-{index}", "page": index + 1}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send, range(2))) == [200, 409]


def test_cancel_invalidates_worker_lease_and_can_retry_without_reupload(native):
    a, _, _ = native
    response = a.post(
        "/api/native/uploads", files={"file": ("教材.pdf", textbook_pdf(), "application/pdf")}
    )
    book_id = response.json()["book_id"]
    api.process_book(book_id)
    job = api.job_repository.claim_next(owner="slow-worker", lease_seconds=60)
    assert job is not None
    assert a.post(f"/api/native/books/{book_id}/cancel").json()["ok"]
    assert not api.job_repository.heartbeat(
        job_id=job.job_id,
        owner="slow-worker",
        lease_seconds=60,
        progress=0.5,
        current_step="late callback",
    )
    with pytest.raises(JobStateError):
        api.job_repository.succeed(
            job_id=job.job_id, owner="slow-worker", result=OCRRunResult(1, False, 3)
        )
    assert a.get(f"/api/books/{book_id}/status").json()["status"] == "cancelled"
    retried = a.post(f"/api/books/{book_id}/process/retry")
    assert retried.status_code == 200 and retried.json()["status"] == "queued"


def test_last_selected_course_is_independent_of_progress_revision(native):
    a, b, _ = native
    book = uploaded(a)
    first = workspace(a, book)
    second = workspace(a, book, "workspace_second")
    for row in (first, second, first):
        body = {"workspace_id": row["workspace_id"], "resource_id": row["id"]}
        assert a.put("/api/native/selection", json=body).status_code == 200
        assert a.get("/api/native/bootstrap").json()["selection"] == body
        assert b.put("/api/native/selection", json=body).status_code == 404
    value = a.get("/api/workspaces/workspace_first").json()["resources"][0]
    assert value["progress"]["revision"] == 0


def test_builtin_qa_uses_shipped_pages_with_course_chapter_and_history(native, monkeypatch):
    from adaptive_learning.api import qa_dependency

    a, b, root = native
    workspace = a.post('/api/native/catalog/community_genetics/import', json={
        'id': 'workspace_qa_scope', 'name': '内置生物课',
    }).json()
    row = workspace['resources'][0]
    path = f"/api/workspaces/{workspace['id']}/resources/{row['id']}"
    service = qa_dependency.build_book_qa_service(native_catalog.BOOK_ID)
    assert service.book_id == 'book_biology_2'
    assert service is not qa_dependency._service
    pages = {page['page_number']: page['raw_text'] for page in map(json.loads, native_catalog.qa_pages().splitlines())}
    answer = a.post(path + '/qa', json={'question': '减数分裂有什么特点？'})
    assert answer.status_code == 200, answer.text
    assert answer.json()['claims']
    for claim in answer.json()['claims']:
        assert claim['book_id'] == 'book_biology_2'
        for citation in claim['citations']:
            assert citation['quote'] in pages[citation['page_number']]
    captured = []
    original = service.answer

    def record(question, **kwargs):
        captured.append(kwargs)
        return original(question, **kwargs)

    monkeypatch.setattr(service, 'answer', record)
    history = [{'role': 'user', 'text': '减数分裂有什么特点？'}, {'role': 'assistant', 'text': '前一轮回答'}]
    followup = a.post(path + '/qa', json={'question': '为什么会这样？', 'chapter_id': 'c2s1', 'history': history})
    assert followup.status_code == 200, followup.text
    assert captured[-1]['cache_scope'].endswith(f":{workspace['id']}:{row['id']}")
    assert captured[-1]['conversation_context']['history'] == history
    assert captured[-1]['conversation_context']['chapter']['id'] == 'c2s1'
    assert a.post(path + '/qa', json={'question': '问题', 'chapter_id': 'missing'}).status_code == 422
    assert a.post(path + '/qa', json={'question': '问题', 'book_ids': ['not-in-course']}).status_code == 422
    assert a.post(path + '/qa', json={'question': '问题', 'history': history * 5}).status_code == 422
    assert b.post(path + '/qa', json={'question': '问题'}).status_code == 404


def test_catalog_flashcard_reviews_are_durable_idempotent_and_resource_scoped(native):
    a, b, root = native
    workspaces = [a.post('/api/native/catalog/community_genetics/import', json={
        'id': f'workspace_review_{i}', 'name': f'生物课 {i}',
    }).json() for i in range(2)]
    first, second = [w['resources'][0] for w in workspaces]
    card = native_catalog.snapshot()['source']['flashcards'][0]
    path = f"/api/workspaces/{workspaces[0]['id']}/resources/{first['id']}/catalog-flashcards/{card['card_id']}"
    payload = {'event_id': 'review-event-0001', 'rating': 'again', 'response_seconds': 12}
    initial = a.post(path, json=payload)
    assert initial.status_code == 200, initial.text
    state = initial.json()['review_state']
    assert state['algorithm'] == 'fsrs-6'
    assert state['repetitions'] == 1 and state['due_at'] and state['last_rating'] == 'again'
    retry = a.post(path, json=payload).json()
    assert retry['duplicate'] and retry['review_state'] == state
    assert a.post(path, json={**payload, 'rating': 'good'}).status_code == 409
    assert b.post(path, json=payload).status_code == 404
    assert a.post(path, json={**payload, 'event_id': 'review-event-invalid', 'rating': 'unknown'}).status_code == 422
    api.native_repository = NativeRepository(root / 'state' / 'native.sqlite3')
    restored = a.get('/api/native/bootstrap').json()['workspaces']
    resources = {r['id']: r for w in restored for r in w['resources']}
    assert resources[first['id']]['flashcard_reviews'][card['card_id']] == state
    assert resources[second['id']]['flashcard_reviews'] == {}
    next_review = a.post(path, json={**payload, 'event_id': 'review-event-0002', 'rating': 'good'}).json()
    assert next_review['review_state']['repetitions'] == 2
    assert next_review['review_state']['last_rating'] == 'good'
    assert len(api.native_repository.catalog_practice(first['id'])) == 2


def test_resource_qa_keeps_multi_book_citations_and_course_scope(native):
    a, _, _ = native
    workspace = a.post('/api/native/catalog/community_genetics/import', json={
        'id': 'workspace_multi_qa', 'name': '多资料生物课',
    }).json()
    row = workspace['resources'][0]
    uploaded_book = uploaded(a)
    assert a.post(f"/api/workspaces/{workspace['id']}/resources", json={'book_id': uploaded_book}).status_code == 200
    response = a.post(f"/api/workspaces/{workspace['id']}/resources/{row['id']}/qa", json={
        'question': '减数分裂有什么特点？', 'chapter_id': 'c2s1',
        'book_ids': [uploaded_book, row['book_id'], uploaded_book],
    })
    assert response.status_code == 200, response.text
    assert {claim['book_id'] for claim in response.json()['claims']} == {row['book_id'], uploaded_book}
    for claim in response.json()['claims']:
        if claim['book_id'] == uploaded_book:
            assert all(c['quote'] in QUOTES[c['page_number'] - 1] for c in claim['citations'])


def test_packaged_builtin_qa_is_exact_client_pdf_page_map():
    import hashlib
    from pathlib import Path

    corpus_path = Path(native_catalog.__file__).parent / 'data/native_biology_qa.json'
    corpus = json.loads(corpus_path.read_text())
    client = Path(__file__).resolve().parents[2] / 'frontend/Resources/Content/rag/biology-required-2-rag-v1'
    original = client / 'page-map.json'
    manifest = json.loads((client / 'manifest.json').read_text())
    assert corpus['page_map_sha256'] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert corpus['source_sha256'] == manifest['source']['sha256']
    assert corpus['book_id'] == manifest['book_id'] == native_catalog.BOOK_ID
    assert corpus['page_count'] == 125
    assert [(p['page_number'], p['raw_text']) for p in corpus['pages']] == [
        (p['pdf_page'], p['text']) for p in json.loads(original.read_text())['pages']
    ]


def test_metadata_bootstrap_deduplicates_bodies_with_constant_query_budget(native, monkeypatch):
    from adaptive_learning.database import Connection

    client, _, _ = native
    original = Connection.execute
    selects = []

    def counted(self, sql, params=()):
        if sql.lstrip().upper().startswith("SELECT"):
            selects.append(sql)
        return original(self, sql, params)

    monkeypatch.setattr(Connection, "execute", counted)
    measurements = []
    for count in (1, 5, 20):
        for index in range(len(measurements) and measurements[-1][0] or 0, count):
            result = client.post(f"/api/native/catalog/{native_catalog.CATALOG_ID}/import", json={"id": f"workspace_batch_{index}", "name": f"课程 {index}"})
            assert result.status_code == 200
        selects.clear()
        light = client.get("/api/native/bootstrap?mode=metadata")
        queries = len(selects)
        full = client.get("/api/native/bootstrap")
        assert light.status_code == full.status_code == 200
        rows = [r for w in light.json()["workspaces"] for r in w["resources"]]
        assert len({r["content_key"] for r in rows}) == 1
        assert all(r["source_content"] is None and r["structure"] is None for r in rows)
        assert len(light.content) < len(full.content) / 20
        assert queries <= 12
        measurements.append((count, queries))
    assert len({value[1] for value in measurements}) == 1
    path = rows[0]["content_path"]
    body = client.get(path).json()
    assert body["source_content"]["lessons"]
    assert body["source_content"]["plan"]["tasks"][0]["task_id"].startswith("catalog:__shared__:")
    assert client.get("/api/native/builtin-content/outdated").status_code == 404
    assert client.get("/api/native/capabilities").json()["bootstrap_metadata"]
