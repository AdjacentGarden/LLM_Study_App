"""Expiring, non-authoritative worker readiness shared by API replicas.

This registry describes live processes, not task ownership. Each worker refreshes
its own rows only while its real queue threads are alive. A crash expires within
15 seconds; orderly exit deletes the rows. Task leases remain the authority for
individual jobs and are independent of this short readiness heartbeat.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .database import connect


class WorkerPresence:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "state/community.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with connect(self.path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS worker_presence (
                    instance_id TEXT NOT NULL, queue TEXT NOT NULL,
                    updated REAL NOT NULL, expires REAL NOT NULL,
                    PRIMARY KEY(instance_id,queue)
                );
                CREATE INDEX IF NOT EXISTS worker_presence_queue ON worker_presence(queue,expires);
            """)

    def refresh(self, instance_id: str, queues: set[str], *, ttl: float = 15,
                now: float | None = None) -> None:
        if not queues <= {"ocr", "studio"} or ttl <= 0:
            raise ValueError("Invalid worker queues or heartbeat lifetime")
        timestamp = time.time() if now is None else now
        with connect(self.path) as db:
            for queue in sorted(queues):
                db.execute("INSERT INTO worker_presence VALUES(?,?,?,?) ON CONFLICT(instance_id,queue) DO UPDATE SET updated=excluded.updated,expires=excluded.expires",
                           (instance_id, queue, timestamp, timestamp + ttl))
            db.execute("DELETE FROM worker_presence WHERE expires<?", (timestamp - 60,))

    def remove(self, instance_id: str) -> None:
        with connect(self.path) as db:
            db.execute("DELETE FROM worker_presence WHERE instance_id=?", (instance_id,))

    def active(self, *, now: float | None = None) -> set[str]:
        timestamp = time.time() if now is None else now
        with connect(self.path) as db:
            return {row[0] for row in db.execute("SELECT DISTINCT queue FROM worker_presence WHERE expires>?", (timestamp,))}


def readiness(*, inline_ocr_enabled: bool, inline_ocr_alive: bool,
              inline_studio_alive: bool, external: set[str]) -> dict[str, Any]:
    ocr_alive = inline_ocr_alive or "ocr" in external
    studio_alive = inline_studio_alive or "studio" in external
    return {"ok": studio_alive and (not inline_ocr_enabled or ocr_alive),
            "ocr_worker_enabled": inline_ocr_enabled or "ocr" in external,
            "ocr_worker_alive": ocr_alive, "studio_worker_alive": studio_alive}
