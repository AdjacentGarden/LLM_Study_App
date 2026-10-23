"""New uploads must become usable without restarting the app or redoing OCR."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaptive_learning.api.community_routes import community_router
from adaptive_learning.assessment.item_generation import structure_fingerprint
from adaptive_learning.assessment.models import DiagnosticItem, ResponseType
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft


@pytest.fixture
def uploads(tmp_path):
    jobs = SQLiteOCRJobRepository(tmp_path / "jobs.sqlite3")
    assessments = SQLiteAssessmentRepository(tmp_path / "assessments.sqlite3")
    app = FastAPI()
    app.include_router(community_router(tmp_path, lambda: [], lambda: jobs, lambda: assessments))
    repo = CommunityRepository(tmp_path / "state/community.sqlite3")

    def register(book_id, source_hash=None, page=None, custom_output=False):
        source = tmp_path / book_id / "source.pdf"
        source.parent.mkdir()
        source.write_bytes(b"pdf fixture")
        jobs.register_book(book_id=book_id, original_name=f"{book_id}.pdf", file_path=source,
                           source_sha256=source_hash or book_id)
        structure = BookStructure(title=f"{book_id}.pdf", summary="教材摘要", source_page_count=1,
            chapters=[ChapterDraft(chapter_id="chapter", order=1, title="第一章", start_page=1,
                end_page=1, summary="章节摘要", knowledge_points=["知识点"], source_block_ids=[])])
        if custom_output:
            output = tmp_path / f"custom-{book_id}"
            jobs.enqueue(book_id=book_id, output_dir=output, max_attempts=2)
        else:
            output = source.parent / "ocr"
        jobs.save_structure(book_id, structure)
        if page is not None:
            normalized = output / "normalized"
            normalized.mkdir(parents=True)
            (normalized / "pages.jsonl").write_text(json.dumps(page, ensure_ascii=False) + "\n")
        return structure

    def bank(book_id, structure, response_type=ResponseType.SINGLE_CHOICE):
        assessments.save_bank(book_id=book_id, structure_fingerprint=structure_fingerprint(structure),
            items=[DiagnosticItem(item_id=f"{book_id}-q", chapter_id="chapter",
                knowledge_point_ids=["point"], prompt="选择正确陈述", response_type=response_type,
                options=["正确", "错误"], correct_option_ids=["0"])])

    with TestClient(app) as owner, TestClient(app) as stranger:
        yield owner, stranger, jobs, repo, register, bank


def claim(client, book_id):
    assert client.post(f"/api/library/books/{book_id}/bind-upload").status_code == 200
    result = client.post(f"/api/library/books/{book_id}/claim")
    assert result.status_code == 200, result.text
    return result.json()


def test_claim_before_generation_refreshes_without_second_claim(uploads):
    owner, _, _, _, register, bank = uploads
    structure = register("new")
    assert claim(owner, "new")["diagnostics_ready"] is False
    bank("new", structure)
    # The original bug kept serving the false snapshot captured during claim.
    assert owner.get("/api/library").json()[0]["diagnostics_ready"] is True


def test_stale_fingerprint_and_old_open_questions_never_announce_ready(uploads):
    owner, _, jobs, _, register, bank = uploads
    original = register("new")
    bank("new", original)
    assert claim(owner, "new")["diagnostics_ready"] is True
    changed = original.model_copy(deep=True)
    changed.chapters[0].knowledge_points.append("新增知识点")
    jobs.save_structure("new", changed)
    assert owner.get("/api/library").json()[0]["diagnostics_ready"] is False
    assert owner.post("/api/library/books/new/claim").json()["diagnostics_ready"] is False
    bank("new", changed, ResponseType.EXPLANATION)
    assert owner.get("/api/library").json()[0]["diagnostics_ready"] is False
    bank("new", changed)
    assert owner.get("/api/library").json()[0]["diagnostics_ready"] is True


def test_invalid_historical_bank_does_not_block_library_or_announce_ready(uploads, tmp_path):
    owner, _, _, _, register, bank = uploads
    structure = register("new")
    bank("new", structure)
    assert claim(owner, "new")["diagnostics_ready"] is True
    # Simulate an older persisted schema, rather than mocking readiness itself.
    assessments = SQLiteAssessmentRepository(tmp_path / "assessments.sqlite3")
    with assessments._connect() as connection:
        connection.execute("UPDATE diagnostic_banks SET items_json='[{\"old\":true}]' WHERE book_id='new'")
    response = owner.get("/api/library")
    assert response.status_code == 200
    assert response.json()[0]["diagnostics_ready"] is False


def test_repeated_claim_dedup_keeps_canonical_readiness_and_owner_checks(uploads):
    owner, stranger, _, _, register, bank = uploads
    first = register("first", source_hash="same-file")
    bank("first", first)
    assert claim(owner, "first")["book_id"] == "first"
    register("alias", source_hash="same-file")
    for _ in range(2):
        result = claim(owner, "alias")
        assert result["book_id"] == "first" and result["diagnostics_ready"] is True
    assert len(owner.get("/api/library").json()) == 1
    assert stranger.get("/api/library").json() == []
    assert stranger.post("/api/library/books/alias/claim").status_code == 403
    assert stranger.post("/api/library/books/alias/bind-upload").status_code == 403


@pytest.mark.parametrize("field", ["text", "cleaned_text", "raw_text", "blocks"])
def test_text_dedup_reads_actual_normalized_schema_and_job_output_dir(uploads, field):
    owner, _, _, _, register, _ = uploads
    text = "这是一段足够长的教材原文，不能只依据书名来合并教材。" * 20
    register("first", page={"page_number": 1, "text": text})
    assert claim(owner, "first")["book_id"] == "first"
    content = [{"text": text, "block_type": "text"}] if field == "blocks" else text
    register("repacked", page={"page_number": 1, field: content}, custom_output=True)
    assert claim(owner, "repacked")["book_id"] == "first"
    assert len(owner.get("/api/library").json()) == 1


def test_catalog_without_current_structure_cannot_keep_historical_ready_flag(uploads):
    owner, _, _, repo, _, _ = uploads
    owner.get("/api/library")
    identity, _, _ = repo.visitor(owner.cookies.get("zhiwo_visitor"), [])
    repo.register_asset({"book_id": "missing", "title": "旧书", "status": "structured",
        "page_count": 1, "chapter_count": 1, "summary": "旧目录", "diagnostics_ready": True},
        "old-file", None)
    repo.add_book(identity, "missing")
    assert owner.get("/api/library").json()[0]["diagnostics_ready"] is False
