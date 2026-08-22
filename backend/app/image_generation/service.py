from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import shutil
import time

import fitz

from app.core.config import get_settings
from app.core.errors import AppError
from app.document.chunk_protocol import is_chunk_indexable
from app.image_generation.adapters import get_image_adapter
from app.image_generation.audit import write_generation_audit
from app.rag.embedding import render_chunk_embedding_text
from app.rag.indexing import fail_index_build, publish_index_build, reserve_index_build
from app.schemas.books import Asset, Chunk, ImageGenerationRequest
from app.services.artifact_store import (
    abort_rag_bundle_build,
    mark_rag_bundle_building,
    read_chunks,
    read_lessons,
    write_assets_and_chunks,
)
from app.services.kv_store import _PersistedKVStore
from app.services.storage import asset_dir


@dataclass
class ImageGenerationJob:
    job_id: str
    book_id: str
    status: str
    stage: str
    progress: int
    asset: Asset | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "book_id": self.book_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "asset": self.asset.model_dump(mode="json") if self.asset else None,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImageGenerationJob":
        asset = Asset.model_validate(data["asset"]) if data.get("asset") else None
        return cls(
            job_id=data["job_id"],
            book_id=data["book_id"],
            status=data["status"],
            stage=data["stage"],
            progress=data["progress"],
            asset=asset,
            error=data.get("error"),
        )


class ImageGenerationJobStore:
    def __init__(self) -> None:
        self._kv = _PersistedKVStore("image_jobs")

    def create(self, book_id: str) -> ImageGenerationJob:
        job = ImageGenerationJob(job_id=f"img_job_{uuid4().hex[:12]}", book_id=book_id, status="pending", stage="queued", progress=0)
        self._kv.upsert(job.job_id, job.to_dict())
        return job

    def get(self, job_id: str) -> ImageGenerationJob | None:
        data = self._kv.get(job_id)
        return ImageGenerationJob.from_dict(data) if data else None

    def update(self, job_id: str, **updates: object) -> ImageGenerationJob:
        def _merge(current: dict | None) -> dict:
            data = dict(current or {})
            for key, value in updates.items():
                if key == "asset" and isinstance(value, Asset):
                    data["asset"] = value.model_dump(mode="json")
                else:
                    data[key] = value
            return data

        updated = self._kv.update_in_place(job_id, _merge)
        return ImageGenerationJob.from_dict(updated)

    def reload(self) -> None:
        self._kv.reload()


image_job_store = ImageGenerationJobStore()


def _validated_source_context(
    payload: ImageGenerationRequest,
    chunks: list[Chunk],
) -> tuple[list[Chunk], str]:
    source_ids = list(payload.source_chunk_ids)
    if len(source_ids) != len(set(source_ids)):
        raise AppError(
            "source_chunks_duplicate",
            "AI 生图的 source chunks 不得重复",
            details={"source_chunk_ids": source_ids},
        )
    chunks_by_id = {
        chunk.chunk_id: chunk
        for chunk in chunks
        if is_chunk_indexable(chunk)
    }
    missing = [chunk_id for chunk_id in source_ids if chunk_id not in chunks_by_id]
    if missing:
        raise AppError("source_chunks_missing", "AI 生图引用的 source chunks 不存在", details={"missing": missing})
    selected = [chunks_by_id[chunk_id] for chunk_id in source_ids]
    chapter_ids = {chunk.chapter_id for chunk in selected}
    if len(chapter_ids) != 1:
        raise AppError(
            "source_chunks_cross_chapter",
            "AI 生图的 source chunks 必须属于同一章节",
            details={"chapter_ids": sorted(chapter_ids)},
        )
    resolved_chapter = next(iter(chapter_ids))
    if payload.chapter_id and payload.chapter_id != resolved_chapter:
        raise AppError(
            "source_chunk_chapter_mismatch",
            "请求章节与 source chunks 所属章节不一致",
            details={"requested_chapter_id": payload.chapter_id, "source_chapter_id": resolved_chapter},
        )

    if payload.lesson_id:
        lesson = next(
            (item for item in read_lessons(payload.book_id) if item.lesson_id == payload.lesson_id),
            None,
        )
        if lesson is not None and lesson.chapter_id != resolved_chapter:
            raise AppError(
                "source_chunk_lesson_mismatch",
                "课程与 source chunks 所属章节不一致",
                details={"lesson_id": payload.lesson_id, "source_chapter_id": resolved_chapter},
            )
    return selected, resolved_chapter


def source_chunks_for_payload(payload: ImageGenerationRequest) -> list[Chunk]:
    selected, _ = _validated_source_context(payload, read_chunks(payload.book_id))
    return selected


def _source_generation_signature(chunks: list[Chunk]) -> list[tuple[str, str]]:
    return [
        (
            chunk.chunk_id,
            sha256(render_chunk_embedding_text(chunk).encode("utf-8")).hexdigest(),
        )
        for chunk in chunks
    ]


def build_generation_prompt(payload: ImageGenerationRequest, source_chunks: list[Chunk]) -> str:
    concepts = "、".join(payload.concepts) if payload.concepts else "本节核心概念"
    source_excerpt = "\n".join(
        f"- p.{chunk.page_start}: {render_chunk_embedding_text(chunk)[:300]}"
        for chunk in source_chunks
    )
    return (
        "Create an original clean educational diagram for a mobile learning app. "
        "Do not copy or recreate any textbook illustration. "
        f"Purpose: {payload.purpose}. Concepts: {concepts}. "
        f"Style: {payload.style}. Keep labels simple and classroom-friendly. "
        "Base the diagram only on these source chunks:\n"
        f"{source_excerpt}"
    )


def _write_thumbnail(image_path: Path, thumb_path: Path) -> None:
    try:
        pix = fitz.Pixmap(image_path)
        if pix.alpha:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        for _ in range(6):
            if pix.width <= 320 and pix.height <= 320:
                break
            pix.shrink(2)
        pix.save(thumb_path)
    except Exception:
        shutil.copyfile(image_path, thumb_path)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_generation_job(job_id: str, payload: ImageGenerationRequest) -> None:
    started = time.perf_counter()
    provider = get_settings().image_provider
    image_path: Path | None = None
    thumb_path: Path | None = None
    bundle_committed = False
    bundle_build = None
    index_build = None
    try:
        image_job_store.update(job_id, status="processing", stage="prompting", progress=15)
        source_chunks, chapter_id = _validated_source_context(payload, read_chunks(payload.book_id))
        source_signature = _source_generation_signature(source_chunks)
        prompt = build_generation_prompt(payload, source_chunks)
        asset_id = f"ai_fig_{payload.book_id}_{chapter_id}_{uuid4().hex[:8]}"
        output_dir = asset_dir(payload.book_id)
        image_path = output_dir / f"{asset_id}.png"
        thumb_path = output_dir / f"thumb_{asset_id}.png"

        image_job_store.update(job_id, stage="calling_provider", progress=35)
        adapter = get_image_adapter()
        adapter.generate(prompt, image_path)
        _write_thumbnail(image_path, thumb_path)
        content_hash = _file_sha256(image_path)

        settings = get_settings()
        asset = Asset(
            asset_id=asset_id,
            book_id=payload.book_id,
            chapter_id=chapter_id,
            source_type="ai_generated",
            page=None,
            type="diagram",
            caption=f"AI 生成示意图：{payload.purpose}",
            bbox=None,
            image_url=f"/api/books/{payload.book_id}/assets/{asset_id}/file",
            thumbnail_url=f"/api/books/{payload.book_id}/assets/{asset_id}/thumbnail",
            source_page_image_url=None,
            source_chunk_ids=list(payload.source_chunk_ids),
            concepts=payload.concepts,
            generation_provider=settings.image_provider,
            generation_prompt=prompt,
            review_status="pending",
            content_hash=content_hash,
        )

        committed_asset: Asset | None = None

        def bind_generated_asset(
            current_assets: list[Asset],
            current_chunks: list[Chunk],
        ) -> tuple[list[Asset], list[Chunk]]:
            nonlocal committed_asset
            selected, current_chapter = _validated_source_context(payload, current_chunks)
            if (
                current_chapter != chapter_id
                or _source_generation_signature(selected) != source_signature
            ):
                raise AppError(
                    "source_chunk_generation_changed",
                    "AI 生图期间 source chunks 的内容代际已变化",
                )
            selected_ids = {chunk.chunk_id for chunk in selected}
            committed_asset = asset.model_copy(
                update={
                    "chapter_id": current_chapter,
                    "source_chunk_ids": list(payload.source_chunk_ids),
                }
            )
            updated_chunks = [
                chunk.model_copy(
                    update={"asset_ids": list(dict.fromkeys([*chunk.asset_ids, asset_id]))}
                )
                if chunk.chunk_id in selected_ids
                else chunk
                for chunk in current_chunks
            ]
            updated_assets = [item for item in current_assets if item.asset_id != asset_id]
            updated_assets.append(committed_asset)
            return updated_assets, updated_chunks

        bundle_build = mark_rag_bundle_building(payload.book_id)
        try:
            updated_assets, updated_chunks = bind_generated_asset(
                bundle_build.assets,
                bundle_build.chunks,
            )
            index_build = reserve_index_build(
                payload.book_id,
                bundle_build.build_id,
                "asset_update",
            )
            write_assets_and_chunks(
                payload.book_id,
                updated_assets,
                updated_chunks,
                build_id=bundle_build.build_id,
            )
            bundle_committed = True
            image_job_store.update(job_id, stage="rag_indexing", progress=90)
            publish_index_build(index_build, updated_chunks)
        except Exception as exc:
            if index_build is not None:
                fail_index_build(index_build, exc)
            if not bundle_committed:
                abort_rag_bundle_build(payload.book_id, bundle_build.build_id)
            raise
        if committed_asset is None:
            raise RuntimeError("Generated asset bundle update did not commit")
        image_job_store.update(job_id, status="done", stage="done", progress=100, asset=committed_asset)
        write_generation_audit(payload, provider=provider, status="done", duration_ms=int((time.perf_counter() - started) * 1000))
    except Exception as exc:
        # Provider output is written before the bundle transaction so the
        # committed Asset never points at a missing file. If validation or the
        # pair commit fails, remove those unreferenced files instead of
        # accumulating semantic-orphan images on disk. Never remove files
        # after a successful bundle commit, even if a later audit/job update
        # fails.
        if not bundle_committed:
            for candidate in (thumb_path, image_path):
                if candidate is None:
                    continue
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass
        error_code = exc.code if isinstance(exc, AppError) else exc.__class__.__name__
        write_generation_audit(payload, provider=provider, status="failed", duration_ms=int((time.perf_counter() - started) * 1000), error_code=error_code)
        image_job_store.update(job_id, status="failed", stage="failed", progress=100, error=str(exc))
