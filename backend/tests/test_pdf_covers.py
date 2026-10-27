import hashlib
import io
import json
import threading
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image

from adaptive_learning.ingestion import covers
from adaptive_learning.ingestion.covers import CoverService, cover_url
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository


def source(tmp_path, count=3):
    jobs = SQLiteOCRJobRepository(tmp_path / "jobs.sqlite3")
    path = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        for index in range(count):
            page = document.new_page(width=800 if index == 1 else 600, height=400 if index == 1 else 800)
            color = [(1, 0, 0), (0, 1, 0), (0, 0, 1)][index % 3]
            page.draw_rect(page.rect, color=color, fill=color)
            page.insert_text((30, 40), f"Original page {index + 1}")
        document.save(path)
    jobs.register_book(book_id="book-cover", original_name="textbook.pdf", file_path=path,
                       source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return jobs


class Model:
    def __init__(self, page=2, error=None):
        self.page, self.error = page, error
        self.calls = []
        self.closed = 0

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"page": self.page, "reason": "该页有完整书名，适合作为封面"}

    def close(self):
        self.closed += 1


def test_three_original_pages_choose_full_source_page_and_cache_without_more_calls(tmp_path):
    jobs, model = source(tmp_path), Model()
    service = CoverService(tmp_path, jobs, client_factory=lambda: model)
    result = service.process("book-cover")
    assert result["status"] == "ready" and result["selected_page"] == 2
    assert len(model.calls) == model.closed == 1
    images = model.calls[0]["images"]
    assert len(images) == 3 and all(mime == "image/jpeg" for mime, _ in images)
    for _, raw in images:
        with Image.open(io.BytesIO(raw)) as image:
            assert image.format == "JPEG"
    with Image.open(tmp_path / "covers/book-cover.jpg") as image:
        assert image.size == (1600, 800)  # Full original landscape page, never a cover-shaped crop.
        red, green, blue = image.getpixel((800, 400))
        assert green > 230 and red < 20 and blue < 20
    url = cover_url(tmp_path, "book-cover")
    assert url and url.startswith("/api/books/book-cover/cover?v=")
    assert service.process("book-cover") == result
    assert len(model.calls) == 1 and cover_url(tmp_path, "book-cover") == url
    # Restore a missing derivative from the cached original-page decision without another call.
    (tmp_path / "covers/book-cover.jpg").unlink()
    assert service.process("book-cover")["selected_page"] == 2
    assert len(model.calls) == 1 and cover_url(tmp_path, "book-cover")


@pytest.mark.parametrize("count", [1, 2])
def test_short_pdf_only_sends_existing_first_pages(tmp_path, count):
    model = Model(page=count)
    service = CoverService(tmp_path, source(tmp_path, count), client_factory=lambda: model)
    assert service.process("book-cover")["selected_page"] == count
    assert len(model.calls[0]["images"]) == count


@pytest.mark.parametrize("page,error", [(4, None), (True, None), (1, TimeoutError("private failure details"))])
def test_bad_model_result_or_timeout_keeps_first_page_preview_and_never_retries(tmp_path, page, error):
    model = Model(page, error)
    service = CoverService(tmp_path, source(tmp_path), client_factory=lambda: model)
    result = service.process("book-cover")
    assert result["status"] == "fallback" and result["selected_page"] == 1
    assert "private failure details" not in json.dumps(result)
    with Image.open(tmp_path / "covers/book-cover.jpg") as image:
        assert image.size == (1200, 1600)
        assert image.getpixel((600, 800))[0] > 230
    assert service.process("book-cover") == result
    assert len(model.calls) == 1


def test_interrupted_submission_does_not_replay_after_restart(tmp_path):
    model = Model(error=KeyboardInterrupt())
    jobs = source(tmp_path)
    service = CoverService(tmp_path, jobs, client_factory=lambda: model)
    with pytest.raises(KeyboardInterrupt):
        service.process("book-cover")
    assert json.loads((tmp_path / "covers/book-cover.selection.json").read_text())["status"] == "submitted"
    restarted = CoverService(tmp_path, jobs, client_factory=lambda: pytest.fail("never replay paid request"))
    assert restarted.process("book-cover")["status"] == "unknown"
    assert cover_url(tmp_path, "book-cover") and len(model.calls) == 1


def test_concurrent_processes_and_background_enqueue_submit_only_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_COVER_ENABLED", "true")
    model = Model()
    entered, release = threading.Event(), threading.Event()
    original = model.structured

    def blocked(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    model.structured = blocked
    jobs = source(tmp_path)
    first = CoverService(tmp_path, jobs, client_factory=lambda: model)
    second = CoverService(tmp_path, jobs, client_factory=lambda: pytest.fail("duplicate submission"))
    try:
        assert first.enqueue("book-cover")
        assert entered.wait(5)
        assert not first.enqueue("book-cover")
        assert second.process("book-cover")["status"] == "submitted"
    finally:
        release.set()
        first.close(wait=True)


@pytest.mark.parametrize("registered_hash", [None, "legacy-inaccurate-hash"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_enqueue_preserves_actual_pdf_decision_with_missing_or_stale_registered_hash(
    tmp_path, monkeypatch, registered_hash, interrupted,
):
    monkeypatch.setenv("PDF_COVER_ENABLED", "true")
    jobs = source(tmp_path)
    model = Model(error=KeyboardInterrupt() if interrupted else None)
    service = CoverService(tmp_path, jobs, client_factory=lambda: model)
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            service.process("book-cover")
    else:
        service.process("book-cover")
    monkeypatch.setattr(jobs, "source_fingerprint", lambda book_id: registered_hash)
    assert service.enqueue("book-cover")
    service.close(wait=True)
    saved = json.loads((tmp_path / "covers/book-cover.selection.json").read_text())
    assert saved["status"] == ("unknown" if interrupted else "ready")
    assert len(model.calls) == 1


def test_cover_client_uses_flash_images_without_paid_retries(monkeypatch):
    configs = []
    monkeypatch.setattr(covers, "get_settings", lambda: SimpleNamespace(
        deepseek_api_key="offline-key", deepseek_base_url="https://api.deepseek.com/v1", llm_https_proxy=None))
    monkeypatch.setattr(covers, "OpenAICompatibleClient", lambda config: configs.append(config) or object())
    covers._client()
    assert configs[0].model == "deepseek-flash" and configs[0].max_retries == 0
    assert configs[0].thinking_enabled is False


def test_cover_url_is_read_only_and_rejects_untrusted_paths(tmp_path):
    assert cover_url(tmp_path, "missing") is None
    assert cover_url(tmp_path, "../source") is None
    assert not (tmp_path / "covers").exists()
