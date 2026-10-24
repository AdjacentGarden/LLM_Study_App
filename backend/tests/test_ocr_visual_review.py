from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from adaptive_learning.ingestion.chaptering import ChapterReconstructor, load_normalized_pages
from adaptive_learning.ingestion.models import ChapterDraft, SourceQuote
from adaptive_learning.ingestion.ocr_job import sha256_file
from adaptive_learning.ingestion.ocr_review import VERSION, OCRReviewUnavailable, review_pages
from adaptive_learning.llm.client import LLMError, LLMNotSubmittedError, LLMTimeoutError
from adaptive_learning.personalization.teaching_evidence import build_teaching_evidence
from adaptive_learning.rag.grounded_qa import AnswerStatus, EvidenceChunk, GroundedAnswerGenerator
from adaptive_learning.rag.upload_index import _chunks, upload_chunks


class Model:
    config = SimpleNamespace(model="test-vision", base_url="https://test.invalid")

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def inputs(tmp_path, texts=("", "original one", "original two"), bands=("missing", "rescue", "review")):
    source = tmp_path / "source.pdf"
    doc = pymupdf.open()
    rows = []
    for n, text in enumerate(texts, 1):
        p = doc.new_page()
        if text:
            p.insert_text((50, 80), text)
        rows.append({"page_number": n, "text": text,
                     "blocks": [{"block_index": 0, "type": "text", "text": text}] if text else []})
    doc.save(source)
    doc.close()
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    (normalized / "pages.jsonl").write_text("".join(json.dumps(p) + "\n" for p in rows))
    report = {"source_sha256": sha256_file(source), "backend": "test", "mean_heuristic_score": .7,
              "page_count_normalized": len(rows), "quality_band_counts": {},
              "pages": [{"page_number": i + 1, "band": b} for i, b in enumerate(bands)]}
    (normalized / "quality_report.json").write_text(json.dumps(report))
    return source, normalized


def run(source, normalized, model, heartbeat=lambda *args: True):
    return review_pages(source, normalized, source.parent / "review", heartbeat, client=model)


def test_blank_is_free_unreadable_is_skipped_and_correction_is_verified(tmp_path):
    source, normalized = inputs(tmp_path)
    model = Model([
        {"action": "unreadable", "reason": "blurred original", "blocks": []},
        {"action": "correct", "reason": "digit corrected", "blocks": [{"type": "text", "text": "verified original two"}]},
        {"verified": True, "reason": "matches the image"},
    ])
    output = run(source, normalized, model)
    pages = [json.loads(p) for p in (output / "pages.jsonl").read_text().splitlines()]
    assert [p["processing_status"] for p in pages] == ["blank_confirmed", "skipped", "corrected"]
    assert pages[1]["blocks"] == [] and pages[1]["text"] == ""
    assert pages[2]["text"] == "verified original two"
    assert len(model.calls) == 3 and all(call["images"] for call in model.calls)
    # Immutable original OCR remains available for audit only.
    assert "original one" in (normalized / "pages.jsonl").read_text()
    report = json.loads((output / "quality_report.json").read_text())
    assert report["source_coverage"]["skipped_page_ranges"] == [[2, 2]]
    assert not report["needs_human_review"] and report["review_version"] == VERSION
    assert [p.page_number for p in load_normalized_pages(output / "pages.jsonl")] == [1, 2, 3]
    assert all(c.page_number == 3 for c in upload_chunks(output / "pages.jsonl"))
    assert all(c.page_number == 3 for c in _chunks((output / "pages.jsonl").read_bytes()))
    run(source, normalized, model)
    assert len(model.calls) == 3, "Completed pages must not submit another paid request"


def test_rule_false_positive_is_accepted_without_fabricated_correction(tmp_path):
    source, normalized = inputs(tmp_path, ("parallel vectors",), ("rescue",))
    model = Model([{"action": "accept", "reason": "ordinary English table", "blocks": []}])
    output = run(source, normalized, model)
    assert load_normalized_pages(output / "pages.jsonl")[0].raw_text == "parallel vectors"
    assert json.loads((output / "quality_report.json").read_text())["resolution_counts"] == {"accepted": 1}


def test_failed_correction_verification_skips_instead_of_publishing_candidate(tmp_path):
    source, normalized = inputs(tmp_path, ("original",), ("rescue",))
    model = Model([{"action": "correct", "reason": "uncertain", "blocks": [{"type": "text", "text": "guessed answer"}]},
                   {"verified": False, "reason": "not visible"}])
    output = run(source, normalized, model)
    text = (output / "pages.jsonl").read_text()
    assert "guessed answer" not in text and '"skipped"' in text


def test_transport_failure_retains_completed_pages_and_resumes(tmp_path):
    source, normalized = inputs(tmp_path, ("page one", "page two"), ("review", "review"))
    first = Model([{"action": "accept", "reason": "readable", "blocks": []}, LLMNotSubmittedError("connection")])
    with pytest.raises(OCRReviewUnavailable):
        run(source, normalized, first)
    assert not list((tmp_path / "review").glob("published-*"))
    second = Model([{"action": "unreadable", "reason": "image really blurred", "blocks": []}])
    output = run(source, normalized, second)
    assert len(second.calls) == 1
    assert json.loads((output / "quality_report.json").read_text())["source_coverage"]["skipped_page_ranges"] == [[2, 2]]


def test_unknown_submission_cannot_be_replayed_as_a_new_paid_request(tmp_path):
    source, normalized = inputs(tmp_path, ("original",), ("review",))
    model = Model([LLMTimeoutError("read timed out")])
    with pytest.raises(OCRReviewUnavailable):
        run(source, normalized, model)
    with pytest.raises(OCRReviewUnavailable, match="reconciliation"):
        run(source, normalized, model)
    assert len(model.calls) == 1


def test_invalid_model_data_does_not_discard_a_page(tmp_path):
    source, normalized = inputs(tmp_path, ("original",), ("review",))
    model = Model([{"action": "correct", "reason": "oops", "blocks": []}])
    with pytest.raises(OCRReviewUnavailable, match="invalid"):
        run(source, normalized, model)
    assert "original" in (normalized / "pages.jsonl").read_text()
    assert not list((tmp_path / "review").glob("published-*"))


def test_explicit_provider_rejection_can_resume_without_skipping(tmp_path):
    source, normalized = inputs(tmp_path, ("original",), ("review",))
    cause = RuntimeError("authentication rejected")
    cause.status_code = 401
    error = LLMError("provider error")
    error.__cause__ = cause
    with pytest.raises(OCRReviewUnavailable, match="rejected"):
        run(source, normalized, Model([error]))
    model = Model([{"action": "accept", "reason": "readable", "blocks": []}])
    output = run(source, normalized, model)
    assert load_normalized_pages(output / "pages.jsonl")[0].raw_text == "original"
    assert len(model.calls) == 1


def test_windows_copy_publication_rolls_back_on_replace_failure(tmp_path, monkeypatch):
    from adaptive_learning.ingestion import jobs
    source, canonical = tmp_path / "source", tmp_path / "normalized"
    source.mkdir()
    canonical.mkdir()
    (source / "pages.jsonl").write_text("reviewed")
    (canonical / "pages.jsonl").write_text("previous")
    replace = jobs.os.replace
    def fail_publish(src, dst):
        if Path(src).name.startswith(".normalized-"):
            raise OSError("publication interrupted")
        replace(src, dst)
    monkeypatch.setattr(jobs.os, "replace", fail_publish)
    with pytest.raises(OSError):
        jobs._publish_normalized_copy(source, canonical)
    assert (canonical / "pages.jsonl").read_text() == "previous"
    assert not list(tmp_path.glob(".normalized-*"))
    monkeypatch.setattr(jobs.os, "replace", replace)
    jobs._publish_normalized_copy(source, canonical)
    assert (canonical / "pages.jsonl").read_text() == "reviewed"
    assert any((p / "pages.jsonl").read_text() == "previous"
               for p in tmp_path.glob("normalized-legacy-*"))


def test_existing_review_required_job_reuses_ocr_and_publishes_ready(tmp_path, monkeypatch):
    from adaptive_learning.ingestion import jobs, ocr_review
    source, normalized = inputs(tmp_path, ("original",), ("rescue",))
    repo = jobs.SQLiteOCRJobRepository(tmp_path / "jobs.sqlite3")
    repo.register_book(book_id="book", original_name=source.name, file_path=source,
                       source_sha256=sha256_file(source), now=1)
    repo.enqueue(book_id="book", output_dir=tmp_path, max_attempts=3, now=2)
    first = repo.claim_next(owner="first", lease_seconds=30, now=3)
    repo.succeed(job_id=first.job_id, owner="first",
                 result=jobs.OCRRunResult(quality_score=.7, needs_human_review=True,
                                          page_count=1), now=4)
    repo.enqueue(book_id="book", output_dir=tmp_path, max_attempts=3, force_retry=True, now=5)
    job = repo.claim_next(owner="retry", lease_seconds=30, now=6)
    monkeypatch.setattr("adaptive_learning.config.get_settings",
                        lambda: SimpleNamespace(vision_rescue_enabled=True))
    model = Model([{"action": "unreadable", "reason": "blurred image", "blocks": []}])
    model.close = lambda: None
    monkeypatch.setattr(ocr_review, "review_client", lambda: model)
    runner = jobs.SubprocessOCRRunner(backend="test")
    def extract(*args):
        pytest.fail("Existing completed OCR must not be extracted again")
    monkeypatch.setattr(runner, "_extract", extract)
    result = runner.run(job, lambda *args: True)
    repo.succeed(job_id=job.job_id, owner="retry", result=result, now=7)
    assert repo.get_book("book").status == "ocr_ready"
    assert not repo.get_job("book").needs_human_review
    assert load_normalized_pages(normalized / "pages.jsonl")[0].processing_status == "skipped"
    assert (normalized / "SHA256SUMS").is_file()


def test_cancellation_during_model_call_does_not_publish(tmp_path):
    source, normalized = inputs(tmp_path, ("original",), ("review",))
    active = [True]
    model = Model([])
    def submit(**kwargs):
        active[0] = False
        return {"action": "accept", "reason": "readable", "blocks": []}
    model.structured = submit
    with pytest.raises(InterruptedError):
        run(source, normalized, model, lambda *args: active[0])
    assert not list((tmp_path / "review").glob("published-*"))


def test_skip_marker_blocks_every_legacy_text_fallback_and_preserves_evidence_pages(tmp_path):
    path = tmp_path / "pages.jsonl"
    rows = [{"page_number": 1, "processing_status": "skipped", "text": "bad text", "cleaned_text": "bad cleaned",
             "raw_text": "bad raw", "blocks": [{"text": "bad block"}]},
            {"page_number": 2, "text": "A readable definition.", "blocks": [{"type": "text", "text": "A readable definition."}]}]
    path.write_text("\n".join(json.dumps(p) for p in rows))
    pages = load_normalized_pages(path)
    assert pages[0].raw_text == "" and pages[0].blocks == []
    assert all(c.page_number == 2 for c in upload_chunks(path))
    assert all(c.page_number == 2 for c in _chunks(path.read_bytes()))
    chapter = ChapterDraft(chapter_id="c", order=1, title="Chapter", start_page=1, end_page=2,
                           summary="", knowledge_points=["definition"], source_block_ids=[],
                           knowledge_point_evidence={"definition": [SourceQuote(page_number=2, quote="A readable definition.")]})
    evidence = build_teaching_evidence(chapter, pages)
    assert evidence.source_coverage["skipped_page_ranges"] == [[1, 1]]
    assert all(s.page_number == 2 for s in evidence.sources.values())
    assert not any("第 1 页" in warning for warning in evidence.warnings)
    structure = ChapterReconstructor().reconstruct_book(pages, "test")
    assert structure.source_coverage["skipped_page_ranges"] == [[1, 1]]
    assert structure.source_page_count == 2


def test_general_answer_after_skipped_pages_has_no_fabricated_citations_or_warning():
    model = Model([{"answer": "向量具有大小和方向。"}])
    generator = GroundedAnswerGenerator(model)
    coverage = {"total_pages": 3, "skipped_page_ranges": [[2, 2]]}
    answer = generator.answer(question="解释向量", evidence=[EvidenceChunk(source_id="E1", page_number=2, text="bad")],
                              retrieval_score=1, source_coverage=coverage)
    assert answer.status == AnswerStatus.GENERAL
    assert answer.claims == [] and "不足" not in answer.answer and "缺页" not in answer.answer
    assert json.loads(model.calls[0]["user"])["source_coverage"] == coverage


def test_supported_mode_can_fall_back_after_internal_insufficiency():
    model = Model([{"status": "insufficient", "claims": [], "confidence": 0}, {"answer": "直接解释。"}])
    generator = GroundedAnswerGenerator(model)
    result = generator.answer(question="为什么", evidence=[EvidenceChunk(source_id="E1", page_number=1, text="some text")],
                              retrieval_score=1, source_coverage={"skipped_page_ranges": [[2, 2]]})
    assert result.status == AnswerStatus.GENERAL and result.answer == "直接解释。"


def test_legacy_books_keep_existing_evidence_checks():
    model = Model([])
    result = GroundedAnswerGenerator(model).answer(question="q", evidence=[], retrieval_score=0)
    assert result.status == AnswerStatus.INSUFFICIENT and not model.calls
