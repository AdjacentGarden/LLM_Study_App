"""A claimed private upload remains usable after its router is rebuilt."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaptive_learning.api.community_routes import community_router
from adaptive_learning.assessment.models import InterviewSession, LearnerProfile
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft


def test_private_claim_survives_router_restart_without_exposing_other_accounts(tmp_path) -> None:
    jobs = SQLiteOCRJobRepository(tmp_path / "state" / "ocr_jobs.sqlite3")
    assessments = SQLiteAssessmentRepository(tmp_path / "state" / "assessments.sqlite3")
    repo = CommunityRepository(tmp_path / "state" / "community.sqlite3")
    source = tmp_path / "books" / "private-upload" / "source.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"isolated upload fixture")
    jobs.register_book(
        book_id="private-upload", original_name="自编教材.pdf", file_path=source,
        source_sha256="private-upload-source-hash",
    )
    jobs.save_structure(
        "private-upload",
        BookStructure(
            title="自编教材", summary="真实保存的结构", source_page_count=1,
            chapters=[
                ChapterDraft(
                    chapter_id="chapter-1", order=1, title="第一章", start_page=1,
                    end_page=1, summary="学习内容", knowledge_points=[], source_block_ids=[],
                )
            ],
        ),
    )

    def build_app() -> FastAPI:
        app = FastAPI()
        # A new factory call gets a fresh empty in-memory ``published`` set,
        # exactly as the real process does after restart.
        app.include_router(
            community_router(tmp_path, lambda: [], lambda: jobs, lambda: assessments)
        )
        return app

    with TestClient(build_app()) as original, TestClient(build_app()) as outsider:
        assert original.get("/api/library").status_code == 200
        assert outsider.get("/api/library").status_code == 200
        owner_token = original.cookies.get("zhiwo_visitor")
        other_token = outsider.cookies.get("zhiwo_visitor")
        assert owner_token and other_token and owner_token != other_token
        owner, _, _ = repo.visitor(owner_token, [])

        assert original.post("/api/library/books/private-upload/bind-upload").status_code == 200
        claimed = original.post("/api/library/books/private-upload/claim")
        assert claimed.status_code == 200, claimed.text
        canonical = claimed.json()["book_id"]
        assert repo.asset(canonical) is not None
        assert repo.owns(owner, canonical)
        now = datetime.now(UTC)
        assessments.create_session(
            InterviewSession(
                session_id="private-study-session",
                profile=LearnerProfile(user_id=owner, book_id=canonical),
                chapter_options=[{"id": "chapter-1", "label": "第一章"}],
                created_at=now, updated_at=now,
            )
        )
        assert repo.claim_session(owner, "private-study-session")
        saved = original.post(
            "/api/learning/doubts",
            json={
                "book_id": canonical, "question": "这一章的主要论点是什么？",
                "chapter_id": "chapter-1", "chapter_title": "第一章", "pages": [1],
            },
        )
        assert saved.status_code == 200, saved.text

    with TestClient(build_app()) as resumed, TestClient(build_app()) as other:
        resumed.cookies.set("zhiwo_visitor", owner_token)
        other.cookies.set("zhiwo_visitor", other_token)
        doubt_url = f"/api/learning/doubts?book_id={canonical}"
        memory_url = "/api/learning/memory/private-study-session"
        doubts = resumed.get(doubt_url)
        memory = resumed.get(memory_url)
        assert doubts.status_code == 200, doubts.text
        assert [item["question"] for item in doubts.json()["items"]] == ["这一章的主要论点是什么？"]
        assert memory.status_code == 200, memory.text
        assert memory.json()["items"] == []

        assert other.get(doubt_url).status_code == 403
        assert other.get(memory_url).status_code == 403
        assert resumed.get("/api/learning/doubts?book_id=unknown-upload").status_code == 403

        assert resumed.post(f"/api/library/books/{canonical}/remove").status_code == 200
        assert resumed.get(doubt_url).status_code == 403
        assert resumed.get(memory_url).status_code == 403
