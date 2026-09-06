from __future__ import annotations

import logging
import logging.config
import sys
import uuid
from contextvars import ContextVar
from typing import Any

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

LOGGING_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "app.core.logging.JsonFormatter",
        },
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "stream": sys.stderr,
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["stderr"],
        "level": "INFO",
    },
    "loggers": {
        "app": {
            "handlers": ["stderr"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn": {
            "handlers": ["stderr"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": ["stderr"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": int(record.created * 1000),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event": getattr(record, "event", record.name),
        }
        rid = request_id_ctx.get("")
        if rid:
            payload["request_id"] = rid
        for field in ("code", "status_code", "path", "method", "user_id", "task", "provider", "error"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        import json

        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    logging.config.dictConfig(LOGGING_CONFIG)


def get_logger(name: str = "app") -> logging.Logger:
    return logging.getLogger(name)


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_request_id(request_id: str) -> None:
    request_id_ctx.set(request_id)


def get_request_id() -> str:
    return request_id_ctx.get("")