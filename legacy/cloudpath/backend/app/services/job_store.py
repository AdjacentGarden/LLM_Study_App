from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.services.kv_store import _PersistedKVStore


@dataclass
class JobRecord:
    job_id: str
    book_id: str
    status: str
    stage: str
    progress: int
    message: str | None = None
    error: str | None = None
    parse_generation: int | None = None
    mineru_record_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "book_id": self.book_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "parse_generation": self.parse_generation,
            "mineru_record_id": self.mineru_record_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "JobRecord":
        return cls(
            job_id=data["job_id"],
            book_id=data["book_id"],
            status=data["status"],
            stage=data["stage"],
            progress=data["progress"],
            message=data.get("message"),
            error=data.get("error"),
            parse_generation=data.get("parse_generation"),
            mineru_record_id=data.get("mineru_record_id"),
        )


class JobStore:
    def __init__(self) -> None:
        self._kv = _PersistedKVStore("jobs")

    def create(
        self,
        book_id: str,
        stage: str = "pending",
        *,
        parse_generation: int | None = None,
        mineru_record_id: str | None = None,
    ) -> JobRecord:
        record = JobRecord(
            job_id=f"job_{uuid4().hex[:12]}",
            book_id=book_id,
            status="pending",
            stage=stage,
            progress=0,
            parse_generation=parse_generation,
            mineru_record_id=mineru_record_id,
        )
        self._kv.upsert(record.job_id, record.to_dict())
        return record

    def get(self, job_id: str) -> JobRecord | None:
        data = self._kv.get(job_id)
        return JobRecord.from_dict(data) if data else None

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        error: str | None = None,
    ) -> JobRecord:
        def _merge(current: dict | None) -> dict:
            data = dict(current or {})
            if status is not None:
                data["status"] = status
            if stage is not None:
                data["stage"] = stage
            if progress is not None:
                data["progress"] = max(0, min(100, progress))
            if message is not None:
                data["message"] = message
            if error is not None:
                data["error"] = error
            return data

        updated = self._kv.update_in_place(job_id, _merge)
        return JobRecord.from_dict(updated)

    def reload(self) -> None:
        self._kv.reload()

    def all(self) -> list[JobRecord]:
        return [JobRecord.from_dict(item) for item in self._kv.values()]

    def recover_interrupted(self) -> list[JobRecord]:
        """Fail jobs that cannot survive a process restart.

        The local worker queue is in-memory. Persisted pending/processing
        records therefore have no executor after startup and must not remain
        visible as permanently running work.
        """

        interrupted = [record for record in self.all() if record.status in {"pending", "processing"}]
        for record in interrupted:
            self.update(
                record.job_id,
                status="failed",
                stage="failed",
                progress=100,
                message="任务因服务重启而中断，请重新解析",
                error="worker_restarted",
            )
        return interrupted


job_store = JobStore()
