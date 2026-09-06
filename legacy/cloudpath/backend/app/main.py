from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from app.api.routes_books import router as books_router
from app.api.routes_assistant import router as assistant_router
from app.api.routes_community import router as community_router
from app.api.routes_health import router as health_router
from app.api.routes_uploads import router as uploads_router
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler, validation_error_handler, unhandled_error_handler
from app.core.logging import configure_logging, get_logger, new_request_id, set_request_id, get_request_id
from app.core.ratelimit import rate_limit_middleware
from app.core.worker import task_queue
from app.document.mineru.exceptions import MinerUProtocolError, MinerUStaleResultError
from app.document.mineru.task_store import mineru_task_store
from app.services.job_store import job_store
from app.services.persistence import state_dir
from app.services.persistence import validate_persisted_state


_logger = get_logger("app.main")


async def request_id_middleware(request: Request, call_next) -> Response:
    rid = request.headers.get("X-Request-Id") or new_request_id()
    set_request_id(rid)
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    return response


async def security_headers_middleware(request: Request, call_next) -> Response:
    response = await call_next(request)
    settings = get_settings()
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-store")
    if settings.environment == "production":
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    _logger.info("startup", extra={"event": "startup"})
    state_dir()
    settings = get_settings()
    corrupt_state = validate_persisted_state()
    if corrupt_state:
        raise RuntimeError(
            "Refusing startup because persisted state is corrupt: " + ", ".join(corrupt_state)
        )
    job_store.reload()
    mineru_task_store.reload()
    interrupted_jobs = job_store.recover_interrupted()
    for job in interrupted_jobs:
        if job.parse_generation is None:
            continue
        try:
            mineru_task_store.finish_worker(
                job.book_id,
                job.parse_generation,
                job.job_id,
                succeeded=False,
            )
        except (MinerUProtocolError, MinerUStaleResultError):
            # A task may have failed before the worker claim or already have
            # a terminal generation. The user-facing job is still recovered.
            continue
    if interrupted_jobs:
        _logger.warning(
            "interrupted_jobs_recovered",
            extra={"event": "interrupted_jobs_recovered", "count": len(interrupted_jobs)},
        )
    if settings.use_worker:
        task_queue.start()
        _logger.info("worker_started", extra={"event": "worker_started"})
    yield
    if settings.use_worker:
        task_queue.stop()
        _logger.info("worker_stopped", extra={"event": "worker_stopped"})


def create_app() -> FastAPI:
    settings = get_settings()
    docs_url = "/docs" if settings.docs_enabled else None
    api = FastAPI(
        title="BookCourse AI Backend",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    api.add_exception_handler(AppError, app_error_handler)
    api.add_exception_handler(RequestValidationError, validation_error_handler)
    api.add_exception_handler(Exception, unhandled_error_handler)
    # Starlette prepends middleware as it is registered. Register CORS last so
    # even responses returned early by request-id/rate-limit middleware carry
    # the browser-visible CORS headers.
    api.middleware("http")(rate_limit_middleware)
    api.middleware("http")(request_id_middleware)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=settings.allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    api.middleware("http")(security_headers_middleware)
    api.include_router(health_router)
    api.include_router(uploads_router)
    api.include_router(assistant_router)
    api.include_router(community_router)
    api.include_router(books_router)
    return api


app = create_app()
