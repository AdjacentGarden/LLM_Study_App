"""Independent durable OCR/Studio workers: python -m adaptive_learning.worker."""
from __future__ import annotations

import argparse
import logging
import signal
import threading
import uuid

from dotenv import load_dotenv

from .config import get_settings
from .database import DATABASE_ERRORS, close_pools, healthcheck
from .ingestion.jobs import OCRWorker, SQLiteOCRJobRepository, SubprocessOCRRunner
from .studio import Studio
from .worker_presence import WorkerPresence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", choices=("ocr", "studio", "all"), default="all")
    args = parser.parse_args()
    load_dotenv()
    settings = get_settings()
    healthcheck()
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    presence = WorkerPresence(settings.data_dir)
    instance_id = uuid.uuid4().hex
    workers: list[OCRWorker | Studio] = []
    queues: set[str] = set()
    if args.queue in {"ocr", "all"}:
        queues.add("ocr")
        workers.append(OCRWorker(repository=SQLiteOCRJobRepository(settings.data_dir / "state/ocr_jobs.sqlite3"),
                                 runner=SubprocessOCRRunner(backend=settings.ocr_backend),
                                 lease_seconds=settings.ocr_worker_lease_seconds,
                                 retry_delay_seconds=settings.ocr_retry_delay_seconds,
                                 poll_interval=settings.ocr_worker_poll_seconds))
    if args.queue in {"studio", "all"}:
        queues.add("studio")
        workers.append(Studio(settings.data_dir))
    try:
        for worker in workers:
            worker.start()
        while True:
            if any(not worker.is_alive() for worker in workers):
                raise RuntimeError("A worker stopped unexpectedly")
            try:
                presence.refresh(instance_id, queues)
            except DATABASE_ERRORS:
                logging.getLogger(__name__).warning("Worker readiness heartbeat unavailable; retrying")
            if stopped.wait(5):
                break
    finally:
        for worker in workers:
            worker.stop()
        try:
            presence.remove(instance_id)
        except DATABASE_ERRORS:
            logging.getLogger(__name__).warning("Worker readiness will expire after shutdown")
        close_pools()


if __name__ == "__main__":
    main()
