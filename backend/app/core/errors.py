from __future__ import annotations

import logging
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger, get_request_id


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: dict | None = None) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


_logger = get_logger("app.errors")


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    _logger.warning(
        "app_error",
        extra={
            "event": "app_error",
            "code": exc.code,
            "status_code": exc.status_code,
            "error_message": exc.message,
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "details": exc.details, "request_id": get_request_id()},
    )


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    _logger.warning(
        "validation_error",
        extra={"event": "validation_error", "status_code": 422, "errors": str(exc.errors())},
    )
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "请求参数校验失败",
            "details": {"errors": exc.errors()},
            "request_id": get_request_id(),
        },
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    _logger.exception(
        "unhandled_exception",
        extra={"event": "unhandled_exception", "error": repr(exc)},
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_server_error",
            "message": "服务器内部错误",
            "details": {"type": type(exc).__name__, "request_id": get_request_id()},
            "request_id": get_request_id(),
        },
    )