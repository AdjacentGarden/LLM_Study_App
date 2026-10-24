"""Read-only runtime configuration/worker checks; no inference or warmup."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, StrictBool


class RuntimeSnapshot(BaseModel):
    """Only booleans cross this boundary, never Settings/URLs/paths/errors."""
    model_config = ConfigDict(extra="forbid")

    text_configured: StrictBool = False
    review_configured: StrictBool = False
    diagnostics_configured: StrictBool = False
    ocr_configured: StrictBool = False
    ocr_worker_alive: StrictBool = False
    preparation_worker_alive: StrictBool = False
    course_worker_alive: StrictBool = False
    rag_configured: StrictBool = False
    rag_ready: StrictBool = False
    note_worker_alive: StrictBool = False
    media_worker_alive: StrictBool = False
    image_configured: StrictBool = False
    video_configured: StrictBool = False
    notes_configured: StrictBool = False
    voice_configured: StrictBool = False


def runtime_router(snapshot: Callable[[], RuntimeSnapshot]) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["runtime"])

    @router.get("/readiness")
    def readiness(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        try:
            value = RuntimeSnapshot.model_validate(snapshot())
        except Exception as error:
            # Exceptions can contain connection URLs, local filenames or keys.
            # Never expose their text through this public diagnostic endpoint.
            raise HTTPException(503, "暂时无法确认服务状态，请稍后重试",
                                headers={"Cache-Control": "no-store"}) from error
        features = {
            "prepare": all((value.ocr_configured, value.ocr_worker_alive, value.text_configured,
                            value.review_configured, value.diagnostics_configured, value.preparation_worker_alive, value.rag_configured)),
            "courses": value.text_configured and value.review_configured and value.course_worker_alive,
            "qa": value.text_configured and value.rag_configured and value.rag_ready,
            "notes_ai": value.notes_configured and value.note_worker_alive,
            "voice_notes": value.voice_configured and value.note_worker_alive,
            "image": value.image_configured and value.media_worker_alive,
            "video": value.video_configured and value.media_worker_alive,
        }
        core = ("prepare", "courses", "qa")
        return {"ok": all(features[name] for name in core),
            "check_scope": "configuration_and_workers", "external_inference_verified": False,
            "checks": value.model_dump(), "features": features,
            "blocked_features": [name for name, available in features.items() if not available]}

    return router
