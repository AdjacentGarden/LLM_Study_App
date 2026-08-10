from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.assignments import service as assignments_service
from app.assignments.service import clear_for_test
from app.core.config import get_settings
from app.main import app
from app.schemas.books import AssignmentSubmitRequest, Asset, Chapter, Chunk, Citation, Lesson, RagResponse
from app.services.artifact_store import write_assets, write_chapters, write_chunks, write_lessons
from app.study_plan.service import clear_for_test as clear_plans


BOOK_ID = "book_phase4"
CHAPTER_ID = "c2s1"
MEIOSIS = "\u51cf\u6570\u5206\u88c2"
HOMOLOGOUS = "\u540c\u6e90\u67d3\u8272\u4f53"
SISTER_CHROMATID = "\u59d0\u59b9\u67d3\u8272\u5355\u4f53"


@pytest.fixture(autouse=True)
def reset_phase4_state():
    yield
    clear_for_test()
    clear_plans()
    get_settings.cache_clear()


def _seed_artifacts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    clear_for_test()
    clear_plans()
    write_chapters(
        BOOK_ID,
        [
            Chapter(
                chapter_id=CHAPTER_ID,
                level=2,
                source_title="\u7b2c1\u8282 \u51cf\u6570\u5206\u88c2\u548c\u53d7\u7cbe\u4f5c\u7528",
                ai_title="\u8bfe\u7a0b2.1 \u540c\u6e90\u67d3\u8272\u4f53\u5982\u4f55\u5206\u79bb",
                page_start=28,
                page_end=36,
                confidence=90,
                status="confirmed",
                source="test",
            ),
            Chapter(
                chapter_id="c2s2",
                level=2,
                source_title="\u7b2c2\u8282 \u6709\u4e1d\u5206\u88c2",
                ai_title="\u8bfe\u7a0b2.2 \u6709\u4e1d\u5206\u88c2\u5bf9\u6bd4",
                page_start=37,
                page_end=42,
                confidence=90,
                status="confirmed",
                source="test",
            )
        ],
    )
    write_chunks(
        BOOK_ID,
        [
            Chunk(
                chunk_id="chunk_meiosis_001",
                book_id=BOOK_ID,
                chapter_id=CHAPTER_ID,
                page_start=30,
                page_end=30,
                content_type="text",
                text=f"{HOMOLOGOUS}\u5728{MEIOSIS}\u7b2c\u4e00\u6b21\u5206\u88c2\u540e\u671f\u5206\u79bb\uff0c{SISTER_CHROMATID}\u5728\u7b2c\u4e8c\u6b21\u5206\u88c2\u540e\u671f\u5206\u79bb\u3002",
                asset_ids=["fig_phase4_001"],
                key_concepts=[MEIOSIS, HOMOLOGOUS, SISTER_CHROMATID],
            ),
            Chunk(
                chunk_id="chunk_mitosis_001",
                book_id=BOOK_ID,
                chapter_id="c2s2",
                page_start=38,
                page_end=38,
                content_type="text",
                text="\u6709\u4e1d\u5206\u88c2\u4fdd\u6301\u4eb2\u4ee3\u7ec6\u80de\u548c\u5b50\u4ee3\u7ec6\u80de\u67d3\u8272\u4f53\u6570\u76ee\u76f8\u540c\u3002",
                asset_ids=[],
                key_concepts=["\u6709\u4e1d\u5206\u88c2", "\u67d3\u8272\u4f53"],
            )
        ],
    )
    write_assets(
        BOOK_ID,
        [
            Asset(
                asset_id="fig_phase4_001",
                book_id=BOOK_ID,
                chapter_id=CHAPTER_ID,
                source_type="extracted",
                page=30,
                type="figure",
                caption="\u51cf\u6570\u5206\u88c2\u793a\u610f\u56fe",
                bbox=[10, 20, 300, 220],
                image_url="/api/books/book_phase4/assets/fig_phase4_001/file",
                thumbnail_url="/api/books/book_phase4/assets/fig_phase4_001/thumbnail",
                source_page_image_url="/api/books/book_phase4/assets/fig_phase4_001/source-page",
                source_chunk_ids=["chunk_meiosis_001"],
                concepts=[MEIOSIS, HOMOLOGOUS],
            )
        ],
    )


@pytest.mark.parametrize(
    ("question", "chapter_id", "expected_confidence"),
    [
        (f"{HOMOLOGOUS}\u4ec0\u4e48\u65f6\u5019\u5206\u79bb", CHAPTER_ID, "medium"),
        (f"{SISTER_CHROMATID}\u4ec0\u4e48\u65f6\u5019\u5206\u79bb", CHAPTER_ID, "medium"),
        (f"\u8bf7\u89e3\u91ca{MEIOSIS}", None, "high"),
        (f"{MEIOSIS}\u548c\u6709\u4e1d\u5206\u88c2\u5982\u4f55\u5bf9\u6bd4", None, "medium"),
        (f"{HOMOLOGOUS}\u4ec0\u4e48\u65f6\u5019\u5206\u79bb", "missing_chapter", "low"),
        ("Newton law outside this biology chapter", None, "low"),
    ],
)
def test_rag_query_fixed_eval_questions(monkeypatch, tmp_path, question: str, chapter_id: str | None, expected_confidence: str) -> None:
    _seed_artifacts(monkeypatch, tmp_path)
    client = TestClient(app)

    body = {"book_id": BOOK_ID, "question": question}
    if chapter_id:
        body["chapter_id"] = chapter_id
    response = client.post("/api/rag/query", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["confidence"] == expected_confidence
    if expected_confidence in {"medium", "high"}:
        assert payload["citations"][0]["score"] > 0
        assert payload["citations"][0]["retrieval_method"] == "rrf"
        assert "bm25_score" in payload["citations"][0]["source_metadata"]
    else:
        assert payload["citations"] == []
        assert payload["related_assets"] == []


def test_assignment_diagnosis_uses_rag_and_records_mistake(monkeypatch, tmp_path) -> None:
    _seed_artifacts(monkeypatch, tmp_path)
    client = TestClient(app)

    submit = client.post(
        "/api/assignments/assign_001/submit",
        json={
            "user_id": "demo_user",
            "book_id": BOOK_ID,
            "chapter_id": CHAPTER_ID,
            "question": f"{HOMOLOGOUS}\u5206\u79bb\u53d1\u751f\u5728\u54ea\u4e2a\u9636\u6bb5\uff1f",
            "answer": "\u51cf\u6570\u7b2c\u4e8c\u6b21\u5206\u88c2\u540e\u671f",
        },
    )
    assert submit.status_code == 200
    submission_id = submit.json()["submission_id"]

    diagnosis = client.post(f"/api/assignments/assign_001/diagnose?submission_id={submission_id}")
    assert diagnosis.status_code == 200
    payload = diagnosis.json()
    assert payload["review_citations"][0]["chunk_id"] == "chunk_meiosis_001"
    assert payload["related_assets"][0]["asset_id"] == "fig_phase4_001"
    assert HOMOLOGOUS in payload["knowledge_points"]
    assert payload["mistake_recorded"] is True
    assert payload["needs_followup"] is False

    mistakes_response = client.get(f"/api/users/demo_user/mistakes?book_id={BOOK_ID}")
    assert mistakes_response.status_code == 200
    assert mistakes_response.json()[0]["assignment_id"] == "assign_001"
    assert mistakes_response.json()[0]["user_id"] == "demo_user"


def test_study_plan_is_generated_from_chapters(monkeypatch, tmp_path) -> None:
    _seed_artifacts(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post("/api/books/book_phase4/plan", json={"user_id": "demo_user", "days": 7, "daily_minutes": 40})

    assert response.status_code == 200
    payload = response.json()
    assert payload["book_id"] == BOOK_ID
    assert payload["user_id"] == "demo_user"
    assert payload["days"] == 7
    assert payload["daily_minutes"] == 40
    assert [task["task_type"] for task in payload["tasks"]][:2] == ["lesson_source_qa", "mistake_review"]
    assert all(task["user_id"] == "demo_user" for task in payload["tasks"])


def test_study_plan_prefers_generated_lessons(monkeypatch, tmp_path) -> None:
    _seed_artifacts(monkeypatch, tmp_path)
    write_lessons(
        BOOK_ID,
        [
            Lesson(
                book_id=BOOK_ID,
                lesson_id="lesson_structured_c2s1",
                chapter_id=CHAPTER_ID,
                title="Structured meiosis lesson",
                source_title="Chapter source",
                page_start=28,
                page_end=36,
                confidence=92,
                source_chunk_ids=["chunk_meiosis_001"],
            )
        ],
    )
    client = TestClient(app)

    response = client.post("/api/books/book_phase4/plan", json={"user_id": "demo_user", "days": 7, "daily_minutes": 40})

    assert response.status_code == 200
    first_task = response.json()["tasks"][0]
    assert first_task["lesson_id"] == "lesson_structured_c2s1"
    assert first_task["title"] == "Structured meiosis lesson"


def test_study_task_patch_is_idempotent_and_adds_explained_adjustment(monkeypatch, tmp_path) -> None:
    _seed_artifacts(monkeypatch, tmp_path)
    client = TestClient(app)
    plan = client.post("/api/books/book_phase4/plan", json={"user_id": "demo_user", "days": 7, "daily_minutes": 40}).json()
    task_id = plan["tasks"][0]["task_id"]

    payload = {"status": "done", "score": 45, "weak_points": [HOMOLOGOUS]}
    first = client.patch(f"/api/study-tasks/{task_id}", json=payload)
    second = client.patch(f"/api/study-tasks/{task_id}", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    updated_plan = client.get("/api/books/book_phase4/plan?user_id=demo_user").json()
    adjustments = [task for task in updated_plan["tasks"] if task["adjustment_reason"] == "quiz_score_below_60"]
    assert len(adjustments) == 1

    state = client.get("/api/users/demo_user/learning-state")
    assert state.status_code == 200
    assert state.json()["completed_tasks"] == 1
    assert HOMOLOGOUS in state.json()["weak_points"]


def test_correct_assignment_is_not_added_to_mistakes(monkeypatch, tmp_path) -> None:
    _seed_artifacts(monkeypatch, tmp_path)
    client = TestClient(app)

    submit = client.post(
        "/api/assignments/assign_correct/submit",
        json={
            "user_id": "demo_user",
            "book_id": BOOK_ID,
            "chapter_id": CHAPTER_ID,
            "question": f"{HOMOLOGOUS}\u5206\u79bb\u53d1\u751f\u5728\u54ea\u4e2a\u9636\u6bb5\uff1f",
            "answer": "\u6211\u4f1a\u5bf9\u7167\u6559\u6750\u533a\u5206\u5206\u79bb\u5bf9\u8c61\uff0c\u4e0d\u628a\u59d0\u59b9\u67d3\u8272\u5355\u4f53\u5f53\u6210\u540c\u6e90\u67d3\u8272\u4f53\u3002",
        },
    )
    submission_id = submit.json()["submission_id"]
    diagnosis = client.post(f"/api/assignments/assign_correct/diagnose?submission_id={submission_id}")

    assert diagnosis.status_code == 200
    assert diagnosis.json()["mistake_recorded"] is False
    mistakes_response = client.get(f"/api/users/demo_user/mistakes?book_id={BOOK_ID}")
    assert mistakes_response.json() == []


def test_generic_diagnosis_hint_is_grounded_in_current_course(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    clear_for_test()
    monkeypatch.setattr(
        assignments_service,
        "answer_query",
        lambda _: RagResponse(
            answer="The plasma membrane regulates transport and communication.",
            citations=[
                Citation(
                    chapter_id="cell_structure",
                    chapter_title="Cell structure",
                    page=1,
                    chunk_id="cell_chunk_1",
                    quote="The plasma membrane regulates transport and communication.",
                )
            ],
            confidence="high",
        ),
    )
    submit = assignments_service.submit_assignment(
        "assign_cells",
        AssignmentSubmitRequest(
            user_id="demo_user",
            book_id="cell_book",
            chapter_id="cell_structure",
            question="What does the plasma membrane regulate?",
            answer="It regulates transport and communication, but I am unsure about the mechanism.",
        ),
    )
    payload = assignments_service.diagnose_assignment("assign_cells", submit.submission_id)

    assert "Cell structure" in payload.hint
    assert "第 1 页" in payload.hint
    assert "染色体" not in payload.hint
    assert payload.mistake_recorded is False
