from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from adaptive_learning.ingestion.jobs import (
    JobStateError,
    OCRJob,
    OCRRunResult,
    OCRWorker,
    SQLiteOCRJobRepository,
    _terminate_process_group,
    _write_canonical_hashes,
    estimate_ocr_progress,
)
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft


def make_repository(tmp_path: Path) -> SQLiteOCRJobRepository:
    return SQLiteOCRJobRepository(tmp_path / "state" / "jobs.sqlite3")


def register(repo: SQLiteOCRJobRepository, tmp_path: Path, book_id: str = "book_1") -> None:
    source = tmp_path / f"{book_id}.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    repo.register_book(
        book_id=book_id,
        original_name=f"{book_id}.pdf",
        file_path=source,
        source_sha256="fixture-sha",
        now=1,
    )


def enqueue(
    repo: SQLiteOCRJobRepository, tmp_path: Path, book_id: str = "book_1", max_attempts: int = 3
) -> OCRJob:
    return repo.enqueue(
        book_id=book_id,
        output_dir=tmp_path / "output" / book_id,
        max_attempts=max_attempts,
        now=2,
    )


def test_enqueue_is_idempotent_and_persists_across_repository_restart(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    register(repo, tmp_path)

    first = enqueue(repo, tmp_path)
    second = enqueue(repo, tmp_path)
    restarted = make_repository(tmp_path).get_job("book_1")

    assert first.job_id == second.job_id
    assert restarted is not None
    assert restarted.status == "queued"
    assert restarted.source_path == tmp_path / "book_1.pdf"


def test_only_one_concurrent_worker_can_claim_a_job(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    register(repo, tmp_path)
    enqueue(repo, tmp_path)

    def claim(index: int) -> OCRJob | None:
        return repo.claim_next(owner=f"worker-{index}", lease_seconds=60, now=3)

    with ThreadPoolExecutor(max_workers=12) as executor:
        claims = list(executor.map(claim, range(12)))

    owned = [job for job in claims if job is not None]
    assert len(owned) == 1
    assert owned[0].attempts == 1


def test_heartbeat_is_monotonic_and_rejects_stale_owner(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    register(repo, tmp_path)
    enqueue(repo, tmp_path)
    job = repo.claim_next(owner="active", lease_seconds=10, now=3)
    assert job is not None

    assert repo.heartbeat(
        job_id=job.job_id,
        owner="active",
        lease_seconds=10,
        progress=0.7,
        current_step="页面 7/10",
        now=4,
    )
    assert repo.heartbeat(
        job_id=job.job_id,
        owner="active",
        lease_seconds=10,
        progress=0.4,
        current_step="校对中",
        now=5,
    )
    assert not repo.heartbeat(
        job_id=job.job_id,
        owner="stale",
        lease_seconds=10,
        progress=0.9,
        current_step="错误所有者",
        now=5,
    )
    current = repo.get_job("book_1")
    assert current is not None
    assert current.progress == 0.7
    assert current.current_step == "校对中"


def test_expired_lease_is_recovered_after_worker_crash(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    register(repo, tmp_path)
    enqueue(repo, tmp_path)
    crashed = repo.claim_next(owner="crashed", lease_seconds=10, now=3)
    assert crashed is not None

    assert repo.recover_expired(now=14) == 1
    recovered = repo.claim_next(owner="replacement", lease_seconds=10, now=14)

    assert recovered is not None
    assert recovered.job_id == crashed.job_id
    assert recovered.lease_owner == "replacement"
    assert recovered.attempts == 2


def test_expired_lease_stops_after_attempt_budget(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    register(repo, tmp_path)
    enqueue(repo, tmp_path, max_attempts=1)
    assert repo.claim_next(owner="crashed", lease_seconds=10, now=3) is not None

    assert repo.recover_expired(now=14) == 1
    current = repo.get_job("book_1")

    assert current is not None
    assert current.status == "failed"
    assert repo.claim_next(owner="replacement", lease_seconds=10, now=14) is None


def test_failure_retries_then_succeeds_and_clears_internal_error(tmp_path: Path) -> None:
    class FlakyRunner:
        calls = 0

        def run(self, job: OCRJob, heartbeat: object) -> OCRRunResult:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary model error")
            return OCRRunResult(quality_score=0.93, needs_human_review=False, page_count=12)

    repo = make_repository(tmp_path)
    register(repo, tmp_path)
    enqueue(repo, tmp_path)
    worker = OCRWorker(
        repository=repo,
        runner=FlakyRunner(),
        retry_delay_seconds=0,
        owner="worker",
    )

    assert worker.process_once()
    retrying = repo.get_job("book_1")
    assert retrying is not None
    assert retrying.status == "retry_wait"
    assert "temporary model error" in (retrying.last_error or "")

    assert worker.process_once()
    complete = repo.get_job("book_1")
    assert complete is not None
    assert complete.status == "ocr_ready"
    assert complete.attempts == 2
    assert complete.last_error is None
    assert complete.quality_score == 0.93
    assert complete.page_count == 12


def test_review_result_can_be_manually_requeued(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    register(repo, tmp_path)
    enqueue(repo, tmp_path)
    job = repo.claim_next(owner="worker", lease_seconds=10, now=3)
    assert job is not None
    repo.succeed(
        job_id=job.job_id,
        owner="worker",
        result=OCRRunResult(quality_score=0.68, needs_human_review=True, page_count=10),
        now=4,
    )

    retried = repo.enqueue(
        book_id="book_1",
        output_dir=tmp_path / "output" / "book_1",
        max_attempts=3,
        now=5,
        force_retry=True,
    )

    assert retried.job_id == job.job_id
    assert retried.status == "queued"
    assert retried.attempts == 0
    assert retried.quality_score is None


def test_stale_worker_cannot_complete_reassigned_job(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    register(repo, tmp_path)
    enqueue(repo, tmp_path)
    stale = repo.claim_next(owner="old", lease_seconds=1, now=3)
    assert stale is not None
    current = repo.claim_next(owner="new", lease_seconds=10, now=5)
    assert current is not None

    with pytest.raises(JobStateError):
        repo.succeed(
            job_id=stale.job_id,
            owner="old",
            result=OCRRunResult(quality_score=1, needs_human_review=False, page_count=1),
            now=6,
        )

    repo.succeed(
        job_id=current.job_id,
        owner="new",
        result=OCRRunResult(quality_score=1, needs_human_review=False, page_count=1),
        now=6,
    )
    complete = repo.get_job("book_1")
    assert complete is not None
    assert complete.status == "ocr_ready"


def test_unknown_book_cannot_be_enqueued(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    with pytest.raises(KeyError):
        enqueue(repo, tmp_path, book_id="missing")


def test_cooperative_worker_shutdown_requeues_without_spending_attempt(tmp_path: Path) -> None:
    class BlockingRunner:
        def run(self, job: OCRJob, heartbeat: object) -> OCRRunResult:
            while heartbeat(0.2, "运行中"):  # type: ignore[operator]
                time.sleep(0.005)
            raise JobStateError("cancelled")

    repo = make_repository(tmp_path)
    register(repo, tmp_path)
    enqueue(repo, tmp_path)
    worker = OCRWorker(repository=repo, runner=BlockingRunner(), owner="worker", poll_interval=0.01)

    worker.start()
    for _ in range(100):
        current = repo.get_job("book_1")
        if current is not None and current.status == "running":
            break
        time.sleep(0.005)
    worker.stop()
    released = repo.get_job("book_1")

    assert released is not None
    assert released.status == "queued"
    assert released.attempts == 0
    assert released.lease_owner is None


def test_progress_estimate_moves_early_and_never_claims_completion() -> None:
    values = [estimate_ocr_progress(seconds, 3) for seconds in (0, 30, 120, 300, 3600)]

    assert values == sorted(values)
    assert values[0] == 0.08
    assert values[2] > 0.55
    assert 0.939 <= values[-1] <= 0.94


def test_process_tree_termination_includes_child_with_its_own_session(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "start_new_session=True); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", script], start_new_session=True, text=True)
    try:
        for _ in range(200):
            if child_pid_file.is_file():
                break
            time.sleep(0.005)
        child_pid = int(child_pid_file.read_text())

        _terminate_process_group(parent)

        assert parent.poll() is not None
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if parent.poll() is None:
            parent.kill()


def test_canonical_hash_manifest_is_self_contained(tmp_path: Path) -> None:
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    for name in ("quality_report.json", "quality_report.md", "pages.jsonl", "full_text.md"):
        (normalized / name).write_text(f"fixture:{name}", encoding="utf-8")

    _write_canonical_hashes(normalized)

    lines = (normalized / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert all("/" not in line.split("  ", 1)[1] for line in lines)
    assert all((normalized / line.split("  ", 1)[1]).is_file() for line in lines)


def test_book_structure_is_persisted_and_updates_book_state(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    register(repo, tmp_path)
    structure = BookStructure(
        title="测试书",
        summary="整书摘要",
        source_page_count=2,
        chapters=[
            ChapterDraft(
                chapter_id="chapter_one",
                order=1,
                title="全书内容",
                start_page=1,
                end_page=2,
                summary="章节摘要",
                knowledge_points=["要点一"],
                source_block_ids=["b1"],
            )
        ],
        used_fallback_chapter=True,
    )

    repo.save_structure("book_1", structure, now=5)
    restarted = make_repository(tmp_path)

    assert restarted.get_structure("book_1") == structure
    stored_book = restarted.get_book("book_1")
    assert stored_book is not None
    assert stored_book.status == "structured"
