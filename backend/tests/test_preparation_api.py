from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from importlib import import_module
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_chaptering_api import FakeReconstructor
from test_upload_index import Encoder, Reranker

from adaptive_learning.accounts import Accounts
from adaptive_learning.api import qa_dependency
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import OCRRunResult, SQLiteOCRJobRepository
from adaptive_learning.ingestion.preparation import PreparationQueue
from adaptive_learning.rag.index import RAGIndexError

api_module = import_module("adaptive_learning.api.app")


def setup(tmp_path, monkeypatch):
    jobs = SQLiteOCRJobRepository(tmp_path / "state" / "ocr_jobs.sqlite3")
    accounts = Accounts(CommunityRepository(tmp_path / "state" / "community.sqlite3"))
    monkeypatch.setattr(api_module, "job_repository", jobs)
    monkeypatch.setattr(api_module, "account_repository", accounts)
    monkeypatch.setattr(api_module, "assessment_repository", SQLiteAssessmentRepository(tmp_path / "state/assessments.sqlite3"))
    monkeypatch.setattr(api_module, "settings", replace(
        api_module.settings, data_dir=tmp_path, ocr_worker_enabled=False,
        rag_index_dir=None, rag_embedding_model_path=None, rag_reranker_model_path=None,
    ))
    queue = PreparationQueue(tmp_path / "state" / "preparation.sqlite3", [
        ("indexing", api_module._prepare_index),
        ("structuring", api_module._prepare_structure),
        ("diagnostics", lambda book: api_module.generate_diagnostic_bank(book)),
    ])
    monkeypatch.setattr(queue, "start", lambda: None)
    monkeypatch.setattr(api_module, "preparation_queue", queue)
    owner, token, _ = accounts.repo.visitor(None, [])
    accounts.repo.bind_upload(owner, "private_book")
    return jobs, queue, token


def test_only_explicit_owner_retry_of_failed_preparation_grants_submission_recovery(tmp_path, monkeypatch):
    from test_diagnostic_chapter_cache import identity

    jobs, queue, token = setup(tmp_path, monkeypatch)
    ready_ocr(jobs, tmp_path)
    repository = api_module.assessment_repository
    value = identity(scope="private_book")
    first = repository.claim_diagnostic_chapter(value)
    repository.fail_diagnostic_chapter(value, first["token"], "unknown")
    with TestClient(api_module.app) as client, TestClient(api_module.app) as anonymous:
        client.cookies.set("zhiwo_visitor", token)
        assert anonymous.post("/api/books/private_book/prepare?retry=true").status_code == 403
        assert client.post("/api/books/private_book/prepare?retry=true").status_code == 200
        assert repository.claim_diagnostic_chapter(value)["status"] == "unknown"
        queue.update("private_book", "failed", "diagnostics")
        assert client.post("/api/books/private_book/prepare").json()["status"] == "failed"
        assert repository.claim_diagnostic_chapter(value)["status"] == "unknown"
        assert client.post("/api/books/private_book/prepare?retry=true").json()["status"] == "queued"
        assert repository.claim_diagnostic_chapter(value)["status"] == "claimed"
        assert client.post("/api/books/private_book/prepare?retry=true").json()["status"] == "queued"
        assert repository.claim_diagnostic_chapter(value)["status"] == "running"
    with repository._connect() as db:
        assert db.execute("SELECT epoch FROM diagnostic_recovery_epochs WHERE scope='private_book'").fetchone()[0] == 1


def test_prepare_retry_nonce_does_not_regrant_after_a_new_failure_or_new_user_action(tmp_path, monkeypatch):
    from test_diagnostic_chapter_cache import identity

    jobs, queue, token = setup(tmp_path, monkeypatch)
    ready_ocr(jobs, tmp_path)
    repository = api_module.assessment_repository
    value = identity(scope="private_book")
    queue.enqueue("private_book")
    initial = repository.claim_diagnostic_chapter(value)
    repository.fail_diagnostic_chapter(value, initial["token"], "unknown")
    with TestClient(api_module.app) as client:
        client.cookies.set("zhiwo_visitor", token)
        queue.update("private_book", "failed", "diagnostics")
        first = "/api/books/private_book/prepare?retry=true&retry_request_id=retry_action_one"
        second = "/api/books/private_book/prepare?retry=true&retry_request_id=retry_action_two"
        assert client.post(first).status_code == 200
        current = repository.claim_diagnostic_chapter(value)
        assert current["status"] == "claimed"
        repository.fail_diagnostic_chapter(value, current["token"], "unknown")
        queue.update("private_book", "failed", "diagnostics")
        assert client.post(first).status_code == 200
        assert repository.claim_diagnostic_chapter(value)["status"] == "unknown"
        queue.update("private_book", "failed", "diagnostics")
        assert client.post(second).status_code == 200
        current = repository.claim_diagnostic_chapter(value)
        assert current["status"] == "claimed"
        repository.fail_diagnostic_chapter(value, current["token"], "unknown")
        queue.update("private_book", "failed", "diagnostics")
        assert client.post(first).status_code == 200
        assert repository.claim_diagnostic_chapter(value)["status"] == "unknown"
    with repository._connect() as db:
        assert db.execute("SELECT epoch FROM diagnostic_recovery_epochs WHERE scope='private_book'").fetchone()[0] == 2


def ready_ocr(jobs: SQLiteOCRJobRepository, tmp_path: Path) -> None:
    source = tmp_path / "books" / "private_book" / "source.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-fixture")
    jobs.register_book(book_id="private_book", original_name="教材.pdf", file_path=source, source_sha256="fixture")
    jobs.enqueue(book_id="private_book", output_dir=source.parent / "ocr", max_attempts=1)
    claimed = jobs.claim_next(owner="test", lease_seconds=30)
    assert claimed is not None
    normalized = claimed.output_dir / "normalized"
    normalized.mkdir(parents=True)
    (normalized / "pages.jsonl").write_text('{"page_number":1,"text":"正文","blocks":[]}\n', encoding="utf-8")
    jobs.succeed(job_id=claimed.job_id, owner="test", result=OCRRunResult(
        quality_score=0.95, needs_human_review=False, page_count=1,
    ))


def test_review_rejections_stay_pending_and_owner_can_pause_then_resume_only_unapproved_items(tmp_path, monkeypatch):
    import time

    from test_diagnostic_generation import structure
    from test_diagnostic_item_repairs import Reviewer, Writer, generator

    jobs, queue, token = setup(tmp_path, monkeypatch)
    ready_ocr(jobs, tmp_path)
    jobs.save_structure("private_book", structure())
    writer, reviewer = Writer(), Reviewer(rejects=1)
    monkeypatch.setattr(api_module, "item_generator", generator(writer, reviewer))
    queue.steps = (*queue.steps[:2], ("diagnostics", api_module._prepare_diagnostics))
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    with TestClient(api_module.app) as client, TestClient(api_module.app) as anonymous:
        client.cookies.set("zhiwo_visitor", token)
        assert client.post("/api/books/private_book/prepare").status_code == 200
        assert queue.process_one()
        status = client.get("/api/books/private_book/prepare").json()
        assert status["status"] == "queued" and status["stage"] == "diagnostics"
        assert status["next_step"] == 2 and not status["error"]
        assert (status["diagnostics_completed"], status["diagnostics_total"]) == (1, 2)
        assert "优化" in status["message"]
        for _ in range(3):
            assert client.get("/api/books/private_book/prepare").status_code == 200
        assert len(writer.calls) == 2
        assert anonymous.post("/api/books/private_book/prepare/cancel").status_code == 403
        assert client.post("/api/books/private_book/prepare/cancel").json()["status"] == "cancelled"
        assert not queue.process_one()
        assert client.post("/api/books/private_book/prepare?retry=true").json()["status"] == "queued"
        clock[0] += 200
        assert queue.process_one()
        assert client.get("/api/books/private_book/prepare").json()["status"] == "succeeded"
        assert len(writer.calls) == 3  # The already approved second item was reused.


def test_prepare_requires_upload_owner_valid_session_same_origin_and_ready_ocr(tmp_path, monkeypatch):
    jobs, queue, token = setup(tmp_path, monkeypatch)
    with TestClient(api_module.app) as owner, TestClient(api_module.app) as attacker:
        owner.cookies.set("zhiwo_visitor", token)
        assert owner.get("/api/books/private_book/prepare").status_code == 404
        assert owner.post("/api/books/private_book/prepare").status_code == 409
        ready_ocr(jobs, tmp_path)
        assert attacker.post("/api/books/private_book/prepare").status_code == 403
        assert attacker.get("/api/books/private_book/prepare").status_code == 403
        assert owner.post("/api/books/private_book/prepare", headers={"Origin": "https://attacker.example"}).status_code == 403
        assert queue.get("private_book") is None
        accepted = owner.post("/api/books/private_book/prepare")
        assert accepted.status_code == 200 and accepted.json()["status"] == "queued"
        assert owner.get("/api/books/private_book/prepare").json() == accepted.json()
        owner.cookies.set("zhiwo_account", "expired")
        assert owner.get("/api/books/private_book/prepare").status_code == 401
        assert owner.post("/api/books/private_book/prepare").status_code == 401


def test_preparation_failure_stays_failed_then_retries_only_diagnostics(tmp_path, monkeypatch):
    jobs, queue, token = setup(tmp_path, monkeypatch)
    ready_ocr(jobs, tmp_path)
    calls: list[str] = []
    failing = True

    class Reconstructor(FakeReconstructor):
        def reconstruct_book(self, pages, fallback_title):
            calls.append("structure")
            return super().reconstruct_book(pages, fallback_title)

    def diagnostics(book):
        calls.append("diagnostics")
        if failing:
            raise HTTPException(502, "diagnostic evidence review rejected the answer")
        return {"ready": True}

    monkeypatch.setattr(api_module, "require_chapter_reconstructor", lambda: Reconstructor())
    monkeypatch.setattr(api_module, "generate_diagnostic_bank", diagnostics)
    monkeypatch.setattr(api_module, "prepare_uploaded_book_index", lambda _: (_ for _ in ()).throw(AssertionError("unconfigured RAG must not load models")))
    with TestClient(api_module.app) as client:
        client.cookies.set("zhiwo_visitor", token)
        assert client.post("/api/books/private_book/prepare").status_code == 200
        assert queue.process_one()
        failed = client.get("/api/books/private_book/prepare").json()
        assert failed["status"] == "failed" and failed["stage"] == "diagnostics"
        assert failed["next_step"] == 2 and jobs.get_structure("private_book") is not None
        assert client.post("/api/books/private_book/prepare").json()["status"] == "failed"
        failing = False
        assert client.post("/api/books/private_book/prepare?retry=true").json()["status"] == "queued"
        assert queue.process_one()
        result = client.get("/api/books/private_book/prepare").json()
        assert result["status"] == "succeeded" and result["stage"] == "ready"
    assert calls == ["structure", "diagnostics", "diagnostics"]


def test_configured_but_broken_rag_blocks_preparation_before_structure(tmp_path, monkeypatch):
    jobs, queue, token = setup(tmp_path, monkeypatch)
    ready_ocr(jobs, tmp_path)
    monkeypatch.setattr(api_module, "settings", replace(api_module.settings, rag_index_dir=tmp_path / "index"))

    def broken_index(book):
        raise ValueError("invalid embeddings")

    monkeypatch.setattr(api_module, "prepare_uploaded_book_index", broken_index)
    with TestClient(api_module.app) as client:
        client.cookies.set("zhiwo_visitor", token)
        client.post("/api/books/private_book/prepare")
        assert queue.process_one()
        failed = client.get("/api/books/private_book/prepare").json()
    assert failed["status"] == "failed" and failed["stage"] == "indexing"
    assert failed["next_step"] == 0 and jobs.get_structure("private_book") is None


def test_upload_only_reranker_warmup_failure_blocks_ready_and_retry_reuses_embeddings(tmp_path, monkeypatch):
    jobs, queue, token = setup(tmp_path, monkeypatch)
    ready_ocr(jobs, tmp_path)
    configured = replace(
        api_module.settings, rag_index_dir=None,
        rag_embedding_model_path=tmp_path / "embedding",
        rag_reranker_model_path=tmp_path / "reranker",
    )
    monkeypatch.setattr(api_module, "settings", configured)
    monkeypatch.setattr(qa_dependency, "get_settings", lambda: configured)
    monkeypatch.setattr(qa_dependency, "_upload_indexes", OrderedDict())

    class RetryableReranker(Reranker):
        failing = True
        calls = 0

        def score(self, query, texts):
            self.calls += 1
            if self.failing:
                raise RAGIndexError("reranker model could not be loaded")
            return super().score(query, texts)

    encoder, reranker = Encoder(), RetryableReranker()
    monkeypatch.setattr(qa_dependency, "_shared_components", lambda: (encoder, reranker, object()))
    monkeypatch.setattr(api_module, "require_chapter_reconstructor", lambda: FakeReconstructor())
    monkeypatch.setattr(api_module, "generate_diagnostic_bank", lambda _: {"ready": True})

    with TestClient(api_module.app) as client:
        client.cookies.set("zhiwo_visitor", token)
        client.post("/api/books/private_book/prepare")
        assert queue.process_one()
        failed = client.get("/api/books/private_book/prepare").json()
        assert failed["status"] == "failed" and failed["stage"] == "indexing"
        assert failed["next_step"] == 0 and jobs.get_structure("private_book") is None
        assert "private_book" not in qa_dependency._upload_indexes
        assert encoder.calls == reranker.calls == 1

        reranker.failing = False
        client.post("/api/books/private_book/prepare?retry=true")
        assert queue.process_one()
        ready = client.get("/api/books/private_book/prepare").json()
        assert ready["status"] == "succeeded" and ready["stage"] == "ready"
        assert jobs.get_structure("private_book") is not None
        # The complete document index survives warmup failure; only retrieval retries.
        assert encoder.calls == 1 and reranker.calls == 2
        assert qa_dependency.prepare_uploaded_book_index("private_book").is_dir()
        assert encoder.calls == 1 and reranker.calls == 2
