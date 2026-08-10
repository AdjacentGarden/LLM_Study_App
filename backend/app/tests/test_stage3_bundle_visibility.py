from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api import routes_books
from app.api.routes_books import _course_summary, _ensure_artifact
from app.core.auth import Principal
from app.core.config import get_settings
from app.core.errors import AppError
from app.rag.cache import clear_rag_cache
from app.schemas.books import Asset, Chapter, ChapterConfirmRequest, ChapterUpdate, Chunk
from app.services.artifact_store import (
    RagBundleBuild,
    get_rag_bundle_state,
    mark_rag_bundle_building,
    read_assets,
    read_chunks,
    write_assets_and_chunks,
    write_chapters,
)
from app.services.job_store import JobStore
from app.services.storage import artifact_dir, original_file_path


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "false")
    get_settings.cache_clear()
    clear_rag_cache()
    yield
    clear_rag_cache()
    get_settings.cache_clear()


def _chapter() -> Chapter:
    return Chapter(
        chapter_id="chapter",
        level=1,
        source_title="Chapter",
        ai_title="Chapter",
        page_start=1,
        page_end=1,
        confidence=100,
        status="confirmed",
        source="test",
    )


def _chunk(chunk_id: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        book_id="visibility_book",
        chapter_id="chapter",
        page_start=1,
        page_end=1,
        content_type="text",
        text=f"semantic text {chunk_id}",
    )


def _asset(asset_id: str) -> Asset:
    return Asset(
        asset_id=asset_id,
        book_id="visibility_book",
        chapter_id="chapter",
        source_type="mineru",
        page=1,
        type="figure",
        caption=f"caption {asset_id}",
        image_url=f"/assets/{asset_id}",
        thumbnail_url=f"/assets/{asset_id}/thumbnail",
    )


def test_public_bundle_state_is_fail_closed_for_missing_partial_and_building() -> None:
    book_id = "visibility_book"
    assert get_rag_bundle_state(book_id) == "missing"

    artifacts = artifact_dir(book_id)
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "chunks.jsonl").write_text(_chunk("partial").model_dump_json() + "\n", encoding="utf-8")
    assert get_rag_bundle_state(book_id) == "invalid"

    (artifacts / "chunks.jsonl").unlink()
    write_assets_and_chunks(book_id, [_asset("ready")], [_chunk("ready")])
    assert get_rag_bundle_state(book_id) == "ready"
    _ensure_artifact(book_id, "chunks.jsonl")
    _ensure_artifact(book_id, "assets.json")

    mark_rag_bundle_building(book_id)
    assert get_rag_bundle_state(book_id) == "building"
    assert read_chunks(book_id) == []
    assert read_assets(book_id) == []
    for filename in ("chunks.jsonl", "assets.json"):
        with pytest.raises(AppError) as captured:
            _ensure_artifact(book_id, filename)
        assert captured.value.code == "artifact_building"
        assert captured.value.status_code == 409


def test_course_summary_counts_authoritative_pair_not_compatibility_mirrors() -> None:
    book_id = "visibility_book"
    write_chapters(book_id, [_chapter()])
    write_assets_and_chunks(book_id, [_asset("authoritative")], [_chunk("authoritative")])

    # Compatibility mirrors are diagnostic only. Deliberately give them
    # different contents and prove the summary reads the active generation.
    artifacts = artifact_dir(book_id)
    (artifacts / "chunks.jsonl").write_text(
        _chunk("mirror_one").model_dump_json()
        + "\n"
        + _chunk("mirror_two").model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    (artifacts / "assets.json").write_text(
        json.dumps([_asset("mirror_one").model_dump(mode="json"), _asset("mirror_two").model_dump(mode="json")]),
        encoding="utf-8",
    )

    summary = _course_summary(book_id)
    assert summary is not None
    assert summary.status == "ready"
    assert summary.chunk_count == 1
    assert summary.asset_count == 1

    mark_rag_bundle_building(book_id)
    building = _course_summary(book_id)
    assert building is not None
    assert building.status == "processing"
    assert building.chunk_count == 0
    assert building.asset_count == 0


def test_course_summary_exposes_active_parse_job(monkeypatch: pytest.MonkeyPatch) -> None:
    book_id = "processing_book"
    original_file_path(book_id, "source.pdf").write_bytes(b"%PDF-1.4\n")
    isolated_jobs = JobStore()
    monkeypatch.setattr(routes_books, "job_store", isolated_jobs)
    monkeypatch.setattr(routes_books.mineru_task_store, "get_current", lambda _book_id: None)
    job = isolated_jobs.create(book_id, parse_generation=2)
    isolated_jobs.update(
        job.job_id,
        status="processing",
        stage="parser_running",
        progress=68,
        message="OCR is active",
    )

    summary = _course_summary(book_id)

    assert summary is not None
    assert summary.status == "processing"
    assert summary.parse_job_id == job.job_id
    assert summary.parse_job_status == "processing"
    assert summary.parse_job_stage == "parser_running"
    assert summary.parse_job_progress == 68
    assert summary.parse_job_message == "OCR is active"


def test_invalid_bundle_is_never_reported_ready_or_accepted_by_ensure() -> None:
    book_id = "visibility_book"
    write_chapters(book_id, [_chapter()])
    write_assets_and_chunks(book_id, [_asset("source")], [_chunk("source")])
    manifest = artifact_dir(book_id) / ".rag_bundles" / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["state"] = "invalid"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert get_rag_bundle_state(book_id) == "invalid"
    summary = _course_summary(book_id)
    assert summary is not None
    assert summary.status == "error"
    assert summary.chunk_count == 0
    assert summary.asset_count == 0
    for filename in ("chunks.jsonl", "assets.json"):
        with pytest.raises(AppError) as captured:
            _ensure_artifact(book_id, filename)
        assert captured.value.code == "artifact_invalid"
        assert captured.value.status_code == 409


def test_chapter_mutations_reserve_bundle_before_writing_and_reuse_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id = "visibility_book"
    principal = Principal(user_id="test", is_admin=True)
    reservation = RagBundleBuild(book_id=book_id, build_id="build_reserved", assets=[], chunks=[])
    events: list[str] = []

    monkeypatch.setattr(routes_books, "_ensure_artifact", lambda *_: None)
    monkeypatch.setattr(routes_books, "_assert_book_owner", lambda *_: None)
    monkeypatch.setattr(routes_books, "read_chapters", lambda _: [_chapter()])

    def reserve(candidate_book_id: str) -> RagBundleBuild:
        assert candidate_book_id == book_id
        events.append("mark")
        return reservation

    def write(candidate_book_id: str, chapters: list[Chapter], *, build_id: str) -> None:
        assert candidate_book_id == book_id
        assert chapters
        assert build_id == reservation.build_id
        events.append("write")

    def rebuild(
        candidate_book_id: str,
        chapters: list[Chapter],
        *,
        reserved_build: RagBundleBuild | None = None,
    ) -> None:
        assert candidate_book_id == book_id
        assert chapters
        assert reserved_build is reservation
        events.append("rebuild")

    monkeypatch.setattr(routes_books, "mark_rag_bundle_building", reserve)
    monkeypatch.setattr(routes_books, "write_chapters_for_build", write)
    monkeypatch.setattr(routes_books, "_rebuild_current_structure", rebuild)

    updated = routes_books.update_chapter(
        book_id,
        "chapter",
        ChapterUpdate(ai_title="Updated"),
        principal,
    )
    assert updated.ai_title == "Updated"
    assert events == ["mark", "write", "rebuild"]

    events.clear()
    confirmed = routes_books.confirm_chapters(
        book_id,
        ChapterConfirmRequest(chapters=[_chapter()]),
        principal,
    )
    assert confirmed[0].status == "已确认"
    assert events == ["mark", "write", "rebuild"]

    events.clear()
    rebuilt = routes_books.rebuild_chapters(book_id, principal)
    assert rebuilt == [_chapter()]
    assert events == ["mark", "write", "rebuild"]
