"""Explicit, loopback-only fixture adapter server for XCTest. Never production."""

import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from native_harness import api, configure, native_api, qa, textbook_pdf
from pytest import MonkeyPatch

from adaptive_learning.api.course_jobs import CourseJobQueue
from adaptive_learning.ingestion.preparation import PreparationQueue
from adaptive_learning.rag.upload_index import build_upload_index

if os.getenv("NATIVE_E2E_TEST_SERVER") != "1":
    raise SystemExit("Set NATIVE_E2E_TEST_SERVER=1 to run the isolated test adapter server")

root = Path(tempfile.mkdtemp(prefix="cloudpath-native-e2e-"))
patch = MonkeyPatch()
app = configure(root, patch)
patch.setattr(api, "require_chapter_reconstructor", native_api.require_chapter_reconstructor)


def prepare_index(book_id: str) -> None:
    # Exercise real index publication with the explicitly selected fixture encoder.
    build_upload_index(root / "books" / book_id, qa.upload_components()[0])


preparation = PreparationQueue(root / "state/preparation.sqlite3", [
    ("indexing", prepare_index),
    ("structuring", api._prepare_structure),
    ("diagnostics", api._prepare_diagnostics),
])
courses = CourseJobQueue(root / "state/course_jobs.sqlite3", api._generate_course_task)
patch.setattr(api, "preparation_queue", preparation)
patch.setattr(api, "course_job_queue", courses)


@asynccontextmanager
async def lifespan(_):
    preparation.start()
    courses.start()
    try:
        yield
    finally:
        courses.stop()
        preparation.stop()


app.router.lifespan_context = lifespan
for path, method, endpoint in [
    ("/api/books/{book_id}/prepare", "POST", api.prepare_book),
    ("/api/books/{book_id}/prepare", "GET", api.preparation_status),
    ("/api/books/{book_id}/structure", "GET", api.get_book_structure),
    ("/api/books/{book_id}/diagnostics", "GET", api.get_diagnostic_bank),
    ("/api/interviews/{session_id}/course-jobs", "POST", api.start_course_task),
    ("/api/interviews/{session_id}/course-jobs/{job_id}", "GET", api.course_task_status),
]:
    app.add_api_route(path, endpoint, methods=[method])
if os.getenv("NATIVE_E2E_LOCAL_LOGIN") == "1":
    patch.setenv("NATIVE_LOCAL_LOGIN_ENABLED", "1")


@app.get("/api/native-test/health")
def health():
    return {
        "ok": True, "fixture_adapters": True,
        "local_default": os.getenv("NATIVE_E2E_LOCAL_LOGIN") == "1",
    }


@app.get("/api/native-test/pdf")
def pdf():
    from fastapi import Response

    return Response(textbook_pdf(), media_type="application/pdf")


uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("NATIVE_E2E_PORT", "18101")), log_level="warning")
