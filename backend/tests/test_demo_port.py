"""The direct UI boundary persists real sources and rejects forged evidence."""

from __future__ import annotations

import hashlib
import time
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaptive_learning.accounts import COOKIE, Accounts
from adaptive_learning.api.demo_port_routes import demo_port_router
from adaptive_learning.community import CommunityRepository
from adaptive_learning.demo_port import DemoPortStore, empty_course_state, extract_source
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository


@pytest.fixture
def api(tmp_path):
    repo = CommunityRepository(tmp_path / "state" / "community.sqlite3")
    accounts = Accounts(repo)
    jobs = SQLiteOCRJobRepository(tmp_path / "state" / "jobs.sqlite3")
    app = FastAPI()
    app.include_router(demo_port_router(tmp_path, accounts, jobs))
    clients = []
    for owner in ("alice", "bob"):
        token = "session-" + owner
        with repo.connect() as db:
            db.execute(
                "INSERT INTO accounts VALUES(?,?,?,?,?,?,?)",
                (owner, owner + "@example.com", "student", "[]", "learn", 0, time.time()),
            )
            db.execute(
                "INSERT INTO account_sessions VALUES(?,?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    owner,
                    time.time(),
                    time.time() + 3600,
                ),
            )
        client = TestClient(app)
        client.cookies.set(COOKIE, token)
        clients.append(client)
    yield tmp_path, clients[0], clients[1]
    for client in clients:
        client.close()


def rpc(client, method, *args):
    return client.post("/api/demo/bookcourse/" + method, json={"args": list(args)})


def upload(
    client, filename="calculus.md", content=b"# Derivative\nDerivative describes rate of change."
):
    init = rpc(client, "initUpload", {"filename": filename, "operation_id": filename}).json()
    book_id = init["book_id"]
    result = client.post(init["upload_url"], files={"file": (filename, content)})
    assert result.status_code == 200, result.text
    job = rpc(client, "startParse", book_id).json()
    assert rpc(client, "getJob", job["job_id"]).json()["status"] == "done"
    return book_id


def test_real_multi_sources_persistence_and_upload_idempotency(api):
    root, alice, bob = api
    first = upload(alice)
    second = upload(alice, "data.csv", b"quantity,value\nspeed,12\n")
    assert first != second
    assert (
        rpc(alice, "initUpload", {"filename": "calculus.md", "operation_id": "calculus.md"}).json()[
            "book_id"
        ]
        == first
    )
    assert len(rpc(alice, "listSources").json()) == 2
    assert rpc(bob, "listSources").json() == []
    chunks = rpc(alice, "getChunks", second).json()
    assert chunks[0]["source_metadata"]["location_type"] == "document"
    assert "speed,12" in chunks[0]["text"]
    lessons = rpc(alice, "getLessons", first).json()
    assert "Derivative describes" in lessons[0]["blocks"][0]["content"]
    assert lessons[0]["blocks"][0]["ai_generated"] is False
    assert DemoPortStore(root).get("alice", "source", first)["pages"]


def test_all_ownership_boundaries_and_account_epoch(api):
    _, alice, bob = api
    book_id = upload(alice)
    assert rpc(bob, "getChunks", book_id).status_code == 404
    assert bob.get(f"/api/demo/books/{book_id}/pages/1").status_code == 404
    assert bob.get(f"/api/demo/bookcourse/file/{book_id}").status_code == 404
    state = empty_course_state()
    state["resources"] = [{"id": "r", "bookId": book_id}]
    assert bob.put("/api/demo/course-state", json=state).status_code == 404
    state["resources"] = []
    state["courses"] = [{"id": "c", "resourceIds": ["source:" + book_id]}]
    assert bob.put("/api/demo/course-state", json=state).status_code == 404
    note = {
        "id": "note1",
        "kind": "text",
        "title": "Private",
        "body": "text",
        "noteVersion": 1,
        "anchor": {"bookId": book_id},
    }
    assert bob.put("/api/demo/study-notes/note1", json=note).status_code == 404
    assert alice.put("/api/demo/study-notes/note1", json=note).status_code == 200
    assert bob.get("/api/demo/study-notes/note1").status_code == 404
    assert (
        alice.put(
            "/api/demo/course-state",
            json=empty_course_state(),
            headers={"X-Demo-Account-Id": "bob"},
        ).status_code
        == 403
    )
    assert (
        alice.put(
            "/api/demo/course-state",
            json=empty_course_state(),
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )


def test_state_and_chapter_conflicts_manual_evidence(api):
    _, alice, _ = api
    value = alice.put("/api/demo/course-state", json=empty_course_state()).json()
    assert value["revision"] == 1
    assert alice.put("/api/demo/course-state", json=empty_course_state()).status_code == 409
    assert alice.put("/api/demo/course-state", json=value).json()["revision"] == 2
    book_id = upload(alice)
    chapter = rpc(alice, "getChapters", book_id).json()[0]
    updated = rpc(
        alice,
        "updateChapter",
        book_id,
        chapter["chapter_id"],
        {"ai_title": "Rates", "content_version": 1},
    ).json()
    assert updated["confidence"] == 0
    assert updated["source"] == "manual"
    assert (
        rpc(
            alice,
            "updateChapter",
            book_id,
            chapter["chapter_id"],
            {"ai_title": "Stale", "content_version": 1},
        ).status_code
        == 409
    )
    assert (
        rpc(alice, "updateChapter", book_id, chapter["chapter_id"], {"page_end": 99}).status_code
        == 422
    )
    assert (
        rpc(
            alice,
            "updateChapter",
            book_id,
            chapter["chapter_id"],
            {"parent_id": chapter["chapter_id"]},
        ).status_code
        == 422
    )


def test_credits_are_server_priced_idempotent_and_cannot_forge_completion(api):
    root, alice, _ = api
    body = {"id": "request-1", "action": "video", "amount": 0}
    first = alice.post("/api/demo/credits/reserve", json=body).json()
    assert first["state"]["balance"] == 90
    assert alice.post("/api/demo/credits/reserve", json=body).json() == first
    assert alice.post("/api/demo/credits/complete", json={"id": "request-1"}).status_code == 409
    assert alice.post("/api/demo/credits/refund", json={"id": "request-1"}).json()["balance"] == 100
    assert alice.post("/api/demo/credits/refund", json={"id": "request-1"}).json()["balance"] == 100
    assert DemoPortStore(root).credit_state("alice")["balance"] == 100


def test_no_fake_notes_grades_or_scores_and_real_pdf(api, monkeypatch):
    _, alice, bob = api
    book_id = upload(alice)
    note = {
        "id": "voice1",
        "kind": "voice",
        "title": "Voice",
        "noteVersion": 1,
        "durationMs": 0,
        "waveform": [],
        "sizeBytes": 0,
        "anchor": {"bookId": book_id},
        "transcript": "fake demo transcript",
    }
    assert alice.put("/api/demo/study-notes/voice1", json=note).status_code == 422
    note.pop("transcript")
    assert alice.put("/api/demo/study-notes/voice1", json=note).status_code == 200
    monkeypatch.setattr(
        "adaptive_learning.api.demo_port_routes.get_settings", lambda: None, raising=False
    )
    report = alice.get(f"/api/demo/report/{book_id}").json()
    assert report["mastery"] is None
    plan = rpc(alice, "createStudyPlan", book_id, {}).json()
    assert (
        rpc(
            alice, "patchStudyTask", plan["tasks"][0]["task_id"], {"status": "done", "score": 88}
        ).status_code
        == 422
    )
    pytest.importorskip("fitz")
    exported = alice.post(
        "/api/demo/exports", json={"book_id": book_id, "modules": ["lessons", "citations"]}
    )
    assert exported.status_code == 200, exported.text
    data = exported.json()
    pdf = alice.get(data["download_url"])
    assert pdf.content.startswith(b"%PDF-")
    assert bob.get(data["download_url"]).status_code == 404


def test_office_source_units_and_image_failure(tmp_path):
    docx = tmp_path / "source.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="urn:w"><w:p><w:r><w:t>Actual document</w:t></w:r></w:p></w:document>',
        )
    assert extract_source(docx) == (["Actual document"], "document")
    pptx = tmp_path / "source.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml", '<a:sld xmlns:a="urn:a"><a:t>Actual slide</a:t></a:sld>'
        )
    assert extract_source(pptx) == (["Actual slide"], "slide")
    xlsx = tmp_path / "source.xlsx"
    with zipfile.ZipFile(xlsx, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            '<s:sst xmlns:s="urn:s"><s:si><s:t>Actual sheet</s:t></s:si></s:sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<s:worksheet xmlns:s="urn:s"><s:row><s:c t="s"><s:v>0</s:v></s:c></s:row></s:worksheet>',
        )
    assert extract_source(xlsx) == (["Actual sheet"], "sheet")


def configured_model(monkeypatch):
    from types import SimpleNamespace

    settings = SimpleNamespace(
        text_api_key="test-configured",
        text_base_url="https://example.invalid/v1",
        text_model="test-model",
        llm_https_proxy=None,
        pucoding_api_key="test-vision",
        pucoding_base_url="https://example.invalid/v1",
        pucoding_vision_model="test-vision",
    )
    monkeypatch.setattr("adaptive_learning.config.get_settings", lambda: settings)
    calls = []

    class Model:
        def __init__(self, config):
            self.config = config

        def structured(self, **kwargs):
            import json

            calls.append(kwargs)
            if "逐字转写提供图片" in kwargs["system"]:
                return {"text": "Derivative describes rate of change.", "uncertain": []}
            data = json.loads(kwargs["user"])
            if "original" in data:
                if "polished" in data:
                    return {"passed": True, "reason": "supported"}
                return {
                    "polished": "Derivative: rate of change.",
                    "evidence": [
                        {
                            "chunk_id": data["sources"][0]["chunk_id"],
                            "quote": "Derivative describes rate of change.",
                        }
                    ],
                }
            if "sources" in data:
                chunk = data["sources"][0]
                return {
                    "items": [
                        {
                            "chunk_id": chunk["chunk_id"],
                            "quote": "Derivative describes rate of change.",
                            "front": "What is derivative?",
                            "back": "Rate of change",
                            "concept": "derivative",
                            "prompt": "What is derivative?",
                            "choices": ["Rate of change", "Fixed quantity"],
                            "answer": "Rate of change",
                            "explanation": "Derivative describes rate of change.",
                        }
                    ]
                }
            if "user_answer" in data:
                return {
                    "score": 0.2,
                    "scoring_confidence": 0.95,
                    "matched_rubric": [],
                    "missing_rubric": ["rate of change"],
                    "misconception_candidates": [],
                    "needs_follow_up": False,
                }
            return {"answer": "Derivative describes rate of change.", "citation_indexes": [1]}

    monkeypatch.setattr("adaptive_learning.llm.client.OpenAICompatibleClient", Model)
    return calls


def test_configured_generation_scoring_fsrs_and_citation_tasks(api, monkeypatch):
    root, alice, _ = api
    calls = configured_model(monkeypatch)
    book_id = upload(alice)
    chapters = rpc(alice, "getChapters", book_id).json()
    assert rpc(alice, "confirmChapters", book_id, chapters).status_code == 200
    for method in ("buildFlashcards", "buildQuizzes"):
        result = rpc(alice, method, book_id, {})
        assert result.status_code == 202
        job = rpc(alice, "getJob", result.json()["job_id"]).json()
        assert job["status"] == "done", job
        assert job["items"][0]["source_quote"] == "Derivative describes rate of change."
    cards = rpc(alice, "getFlashcards", book_id).json()
    reviewed = rpc(
        alice,
        "reviewFlashcard",
        book_id,
        cards[0]["card_id"],
        {"event_id": "review-1", "rating": "good"},
    ).json()
    assert reviewed["review"]["algorithm"] == "fsrs-6"
    assert reviewed["review"]["repetitions"] == 1
    assert reviewed["due"]
    duplicate = rpc(
        alice,
        "reviewFlashcard",
        book_id,
        cards[0]["card_id"],
        {"event_id": "review-1", "rating": "good"},
    ).json()
    assert duplicate == reviewed
    submission = rpc(
        alice,
        "submitAssignment",
        "a1",
        {
            "book_id": book_id,
            "chapter_id": chapters[0]["chapter_id"],
            "question": "What is derivative?",
            "answer": "Fixed quantity",
        },
    ).json()
    diagnosis = rpc(alice, "diagnoseAssignment", "a1", submission["submission_id"])
    assert diagnosis.status_code == 200, diagnosis.text
    assert diagnosis.json()["score"] == 0.2
    assert diagnosis.json()["mistake_recorded"] is True
    assert len(rpc(alice, "getMistakes", "ignored-user", book_id).json()) == 1
    assert len(calls) == 3
    reserve = alice.post("/api/demo/credits/reserve", json={"id": "qa1", "action": "chat"})
    assert reserve.status_code == 200
    question = {
        "book_id": book_id,
        "chapter_id": chapters[0]["chapter_id"],
        "question": "Derivative rate",
        "reservation_id": "qa1",
    }
    response = rpc(alice, "queryRag", question)
    assert response.status_code == 200, response.text
    assert response.json()["citations"][0]["book_id"] == book_id
    assert alice.post("/api/demo/credits/complete", json={"id": "qa1"}).json()["balance"] == 99
    assert rpc(alice, "queryRag", question).json() == response.json()
    assert len(calls) == 4
    alice.post("/api/demo/credits/reserve", json={"id": "qa2", "action": "chat"})
    missing = rpc(
        alice, "queryRag", {"book_id": book_id, "question": "unrelatedxyz", "reservation_id": "qa2"}
    )
    assert missing.json()["retrieval"]["status"] == "no_match"
    assert alice.get("/api/demo/credits").json()["balance"] == 99
    assert DemoPortStore(root).get("alice", "diagnosis", submission["submission_id"])


def test_actual_region_image_is_sent_to_configured_model(api, monkeypatch):
    import base64
    import io

    from PIL import Image

    _, alice, _ = api
    calls = configured_model(monkeypatch)
    book_id = upload(alice)
    output = io.BytesIO()
    Image.new("RGB", (20, 10), "red").save(output, format="PNG")
    content = output.getvalue()
    result = rpc(
        alice,
        "queryRag",
        {
            "book_id": book_id,
            "question": "Derivative",
            "reference_image": {
                "data_url": "data:image/png;base64," + base64.b64encode(content).decode(),
                "region": {"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.2},
            },
        },
    )
    assert result.status_code == 200, result.text
    assert calls[0]["images"] == [("image/png", content)]


def test_note_audio_studio_uses_original_recording_and_real_job_result(api, monkeypatch):
    import io
    import json
    import wave

    from adaptive_learning.studio import get_studio

    root, alice, _ = api
    book_id = upload(alice)
    studio = get_studio(root)
    monkeypatch.setattr(
        studio,
        "capabilities",
        lambda: {"voice_notes": True, "notes_ai": True, "video": True, "image": True},
    )
    calls = []

    def submit(owner, data, kind):
        calls.append((owner, data, kind))
        assert studio.voice_path(owner, data["note_id"]).read_bytes() == audio
        with studio.repo.connect() as db:
            # Use Studio's real persisted job schema and polling contract.
            db.execute(
                "INSERT INTO studio_jobs(id,owner,book_id,kind,request_id,fingerprint,status,data,result,error,created,updated,reserved) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "actual-task-" + kind,
                    owner,
                    book_id,
                    kind,
                    data["request_id"],
                    "fp-" + kind,
                    "succeeded",
                    json.dumps(data),
                    json.dumps(
                        {
                            "transcript": data["transcript"],
                            "polished": "organized actual confirmed transcript",
                        }
                        if kind == "improve"
                        else {
                            "transcript": "actual speech from configured recognizer",
                            "uncertain": [],
                        }
                    ),
                    "",
                    time.time(),
                    time.time(),
                    0,
                ),
            )
        return studio.public_job(studio.job(owner, "actual-task-" + kind))

    monkeypatch.setattr(studio, "submit", submit)
    recording = io.BytesIO()
    with wave.open(recording, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x01" * 800)
    audio = recording.getvalue()
    note = {
        "id": "voice1",
        "kind": "voice",
        "title": "Voice",
        "noteVersion": 1,
        "durationMs": 100,
        "waveform": [],
        "sizeBytes": len(audio),
        "audioId": "audio1",
        "anchor": {"bookId": book_id},
    }
    assert alice.put("/api/demo/study-notes/voice1", json=note).status_code == 200
    assert (
        alice.put(
            "/api/demo/study-notes/audio1/audio",
            files={"file": ("recording.wav", audio, "audio/wav")},
        ).status_code
        == 200
    )
    task = alice.post("/api/demo/study-notes/voice1/transcribe", json={})
    assert task.status_code == 200, task.text
    result = alice.get("/api/demo/study-notes/voice1").json()
    assert result["transcript"] == "actual speech from configured recognizer"
    assert result["pipelinePhase"] == "needs_confirmation"
    assert calls[0][2] == "recognize"
    assert alice.get("/api/demo/study-notes/audio1/audio").content == audio
    confirmed = {**result, "confirmedTranscript": "corrected words from the learner"}
    assert alice.put("/api/demo/study-notes/voice1", json=confirmed).status_code == 200
    task = alice.post(
        "/api/demo/study-notes/voice1/organize",
        json={"transcript": confirmed["confirmedTranscript"], "consent": True},
    )
    assert task.status_code == 200, task.text
    result = alice.get("/api/demo/study-notes/voice1").json()
    assert result["recognizedText"] == "actual speech from configured recognizer"
    assert result["confirmedTranscript"] == "corrected words from the learner"
    assert result["organizedText"] == "organized actual confirmed transcript"
    assert result["organizedVersions"][0]["noteVersion"] == 1
    assert result["pipelinePhase"] == "complete"
    assert calls[1][1]["transcript"] == confirmed["confirmedTranscript"]


def test_chapter_export_does_not_include_other_chapter(api):
    import fitz

    from adaptive_learning.demo_port import source_content

    root, alice, _ = api
    book_id = upload(alice)
    store = DemoPortStore(root)
    original = store.get("alice", "source", book_id)
    value = source_content(
        book_id, "chapters.md", ["FIRST visible text", "SECOND forbidden text"], "document"
    )
    store.put("alice", "source", book_id, {**original, **value})
    chapter_id = value["chapters"][0]["chapter_id"]
    response = alice.post(
        "/api/demo/export",
        json={
            "bookId": book_id,
            "chapterId": chapter_id,
            "modules": ["AI 导学笔记", "原文引用页码"],
        },
    )
    assert response.status_code == 200
    with fitz.open(stream=response.content, filetype="pdf") as doc:
        text = "\n".join(p.get_text() for p in doc).replace("\u00a0", " ")
        assert "FIRST visible text" in text
        assert "SECOND forbidden text" not in text
        assert len(doc) == 1


def test_interrupted_native_jobs_fail_and_are_retryable(api):
    root, alice, _ = api
    book_id = upload(alice)
    store = DemoPortStore(root)
    value = store.get("alice", "source", book_id)
    store.put(
        "alice",
        "job",
        "interrupted",
        {
            "book_id": book_id,
            "job_id": "interrupted",
            "status": "processing",
            "stage": "extract",
            "progress": 20,
        },
    )
    store.put("alice", "source", book_id, {**value, "parse_job_id": "interrupted"})
    alice.post(
        "/api/demo/credits/reserve",
        json={"id": "interrupted-chat", "action": "chat", "task_id": "browser-forged"},
    )
    recovered = DemoPortStore(root, recover_interrupted=True)
    assert recovered.get("alice", "job", "interrupted")["status"] == "failed"
    assert recovered.credit_state("alice")["balance"] == 100
    assert DemoPortStore(root, recover_interrupted=True).credit_state("alice")["balance"] == 100
    retry = rpc(alice, "startParse", book_id).json()
    assert retry["job_id"] != "interrupted"
    assert rpc(alice, "getJob", retry["job_id"]).json()["status"] == "done"


def test_public_account_header_matches_real_account(api):
    from adaptive_learning.social import SocialRepository

    root, alice, _ = api
    social = SocialRepository(CommunityRepository(root / "state" / "community.sqlite3"))
    alice_id = social.me("alice")["user_id"]
    bob_id = social.me("bob")["user_id"]
    assert (
        alice.get("/api/demo/course-state", headers={"X-Demo-Account-Id": alice_id}).status_code
        == 200
    )
    assert (
        alice.get("/api/demo/course-state", headers={"X-Demo-Account-Id": bob_id}).status_code
        == 403
    )


def test_pdf_page_render_uses_actual_uploaded_file(api):
    import fitz

    _, alice, bob = api
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 80), "actual PDF source")
        content = doc.tobytes()
    book_id = upload(alice, "source.pdf", content)
    page = alice.get(f"/api/demo/books/{book_id}/pages/1").json()
    assert "actual PDF source" in page["source_text"]
    assert alice.get(page["image_url"]).content.startswith(b"\x89PNG")
    assert bob.get(page["image_url"]).status_code == 404


def test_submission_event_id_records_once_and_preserves_unknown_measurement(api, monkeypatch):
    _, alice, _ = api
    configured_model(monkeypatch)
    book_id = upload(alice)
    chapter_id = rpc(alice, "getChapters", book_id).json()[0]["chapter_id"]
    payload = {
        "book_id": book_id,
        "chapter_id": chapter_id,
        "question": "Derivative?",
        "answer": "Fixed",
        "event_id": "event-a",
        "confidence": None,
        "response_seconds": 120,
    }
    first = rpc(alice, "submitAssignment", "a", payload).json()
    assert rpc(alice, "submitAssignment", "a", payload).json() == first
    assert rpc(alice, "submitAssignment", "a", {**payload, "answer": "Changed"}).status_code == 409
    result = rpc(alice, "diagnoseAssignment", "a", first["submission_id"]).json()
    assert result["review_citations"][0]["book_id"] == book_id
    report = alice.get(f"/api/demo/report/{book_id}?chapter_id={chapter_id}").json()
    assert report["minutes"] == 2
    assert report["mastery"] == 20
    assert report["mistakes"] == 1


def test_studio_media_submission_settles_only_real_completed_task(api, monkeypatch):
    import json

    from adaptive_learning.studio import get_studio

    root, alice, _ = api
    book_id = upload(alice)
    chapter_id = rpc(alice, "getChapters", book_id).json()[0]["chapter_id"]
    studio = get_studio(root)
    monkeypatch.setattr(studio, "capabilities", lambda: {"video": True, "image": True})
    calls = []

    def submit(owner, data, kind):
        calls.append((owner, data, kind))
        with studio.repo.connect() as db:
            db.execute(
                "INSERT INTO studio_jobs(id,owner,book_id,kind,request_id,fingerprint,status,data,result,error,created,updated,reserved) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "video-task",
                    owner,
                    book_id,
                    kind,
                    data["request_id"],
                    "fp",
                    "queued",
                    json.dumps(data),
                    "{}",
                    "",
                    time.time(),
                    time.time(),
                    0,
                ),
            )
        return studio.public_job(studio.job(owner, "video-task"))

    monkeypatch.setattr(studio, "submit", submit)
    alice.post("/api/demo/credits/reserve", json={"id": "video-credit", "action": "video"})
    result = rpc(
        alice,
        "generateLessonVideo",
        book_id,
        chapter_id + ":lesson",
        {"chapter_id": chapter_id, "reservation_id": "video-credit", "goal": "过程"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["asset_url"] is None
    assert "Derivative describes" in calls[0][1]["excerpt"]
    assert calls[0][2] == "video"
    assert alice.post("/api/demo/credits/complete", json={"id": "video-credit"}).status_code == 409
    studio.update(
        "video-task", status="succeeded", result=json.dumps({"title": "actual service result"})
    )
    result = rpc(alice, "getVideoJob", "video-task").json()
    assert result["status"] == "succeeded"
    assert result["asset_url"] == "/api/studio/jobs/video-task/asset"
    assert (
        alice.post("/api/demo/credits/complete", json={"id": "video-credit"}).json()["balance"]
        == 90
    )


def test_text_organization_keeps_original_and_vetted_edition(api, monkeypatch):
    _, alice, _ = api
    calls = configured_model(monkeypatch)
    book_id = upload(alice)
    note = {
        "id": "text1",
        "kind": "text",
        "title": "Original",
        "body": "my original wording",
        "noteVersion": 1,
        "anchor": {"bookId": book_id},
    }
    assert alice.put("/api/demo/study-notes/text1", json=note).status_code == 200
    task = alice.post(
        "/api/demo/study-notes/text1/organize", json={"transcript": note["body"], "consent": True}
    )
    assert task.status_code == 200, task.text
    result = alice.get("/api/demo/study-notes/text1").json()
    assert result["body"] == "my original wording"
    assert result["organizedText"] == "Derivative: rate of change."
    assert result["evidence"][0]["excerpt"] == "Derivative describes rate of change."
    assert result["pipelinePhase"] == "complete"
    assert len(calls) == 2


def test_uploaded_image_is_sent_to_ocr_and_original_remains_readable(api, monkeypatch):
    import io

    from PIL import Image

    _, alice, bob = api
    calls = configured_model(monkeypatch)
    output = io.BytesIO()
    Image.new("RGB", (30, 20), "white").save(output, format="PNG")
    content = output.getvalue()
    book_id = upload(alice, "photo.png", content)
    assert calls[0]["images"][0][1] == content
    scan = rpc(alice, "getScanResult", book_id).json()
    assert scan["source_unit"] == "image"
    assert scan["has_text_layer"] is False
    assert scan["needs_ocr"] is True
    page = alice.get(f"/api/demo/books/{book_id}/pages/1").json()
    assert alice.get(page["image_url"]).content == content
    assert bob.get(page["image_url"]).status_code == 404


def test_async_diagnostics_reuses_existing_generator_and_persists_bank(api, monkeypatch):
    from adaptive_learning.assessment.models import DiagnosticItem, ResponseType
    from adaptive_learning.assessment.repository import SQLiteAssessmentRepository

    root, alice, _ = api
    configured_model(monkeypatch)
    book_id = upload(alice)
    chapters = rpc(alice, "getChapters", book_id).json()
    assert rpc(alice, "confirmChapters", book_id, chapters).status_code == 200
    calls = []

    def generate(self, structure):
        calls.append(structure)
        assert structure.chapters[0].evidence[0].quote
        return [
            DiagnosticItem(
                item_id="real-bank-item",
                chapter_id=chapters[0]["chapter_id"],
                knowledge_point_ids=["rate"],
                prompt="Derivative?",
                response_type=ResponseType.SINGLE_CHOICE,
                options=["Rate", "Fixed"],
                correct_option_ids=["0"],
            )
        ]

    monkeypatch.setattr(
        "adaptive_learning.assessment.item_generation.DiagnosticItemGenerator.generate", generate
    )
    task = rpc(alice, "buildDiagnostics", book_id)
    assert task.status_code == 202
    result = rpc(alice, "getJob", task.json()["job_id"]).json()
    assert result["status"] == "done", result
    assert len(calls) == 1
    bank = SQLiteAssessmentRepository(root / "state" / "assessments.sqlite3").get_bank(book_id)
    assert bank[0].item_id == "real-bank-item"
