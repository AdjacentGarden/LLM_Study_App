from __future__ import annotations

from dataclasses import replace
from importlib import import_module
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from adaptive_learning.api.chapter_dependency import require_chapter_reconstructor
from adaptive_learning.ingestion.jobs import OCRRunResult, SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft

api_module = import_module("adaptive_learning.api.app")


class FakeReconstructor:
    def reconstruct_book(self, pages: object, fallback_title: str) -> BookStructure:
        return BookStructure(
            title=fallback_title,
            summary="整书摘要",
            source_page_count=1,
            chapters=[
                ChapterDraft(
                    chapter_id="chapter_fixture",
                    order=1,
                    title="全书内容",
                    start_page=1,
                    end_page=1,
                    summary="章节摘要",
                    knowledge_points=["知识点"],
                    source_block_ids=["block_1_1"],
                )
            ],
            used_fallback_chapter=True,
        )


def test_structure_endpoint_requires_ocr_and_persists_result(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repository = SQLiteOCRJobRepository(tmp_path / "state" / "jobs.sqlite3")
    monkeypatch.setattr(api_module, "job_repository", repository)
    monkeypatch.setattr(
        api_module,
        "settings",
        replace(api_module.settings, data_dir=tmp_path, ocr_worker_enabled=False),
    )
    api_module.app.dependency_overrides[require_chapter_reconstructor] = lambda: FakeReconstructor()
    try:
        with TestClient(api_module.app) as client:
            upload = client.post(
                "/api/books",
                files={"file": ("任意书.pdf", b"%PDF-1.7\nfixture", "application/pdf")},
            )
            book_id = upload.json()["book_id"]
            too_early = client.post(f"/api/books/{book_id}/structure")
            assert too_early.status_code == 409

            client.post(f"/api/books/{book_id}/process")
            job = repository.claim_next(owner="fixture", lease_seconds=30)
            assert job is not None
            normalized = job.output_dir / "normalized"
            normalized.mkdir(parents=True)
            (normalized / "pages.jsonl").write_text(
                '{"page_number":1,"text":"正文","blocks":[]}\n', encoding="utf-8"
            )
            repository.succeed(
                job_id=job.job_id,
                owner="fixture",
                result=OCRRunResult(quality_score=0.95, needs_human_review=False, page_count=1),
            )

            generated = client.post(f"/api/books/{book_id}/structure")
            fetched = client.get(f"/api/books/{book_id}/structure")
            status = client.get(f"/api/books/{book_id}/status")
            repeated_ocr = client.post(f"/api/books/{book_id}/process")

        assert generated.status_code == 200
        assert generated.json()["summary"] == "整书摘要"
        assert fetched.json() == generated.json()
        assert status.json()["status"] == "structured"
        assert repeated_ocr.json()["status"] == "structured"
        stored_book = repository.get_book(book_id)
        assert stored_book is not None
        assert stored_book.status == "structured"
    finally:
        api_module.app.dependency_overrides.clear()
