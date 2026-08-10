from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Header

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger


_logger = get_logger("app.auth")


@dataclass(frozen=True)
class Principal:
    user_id: str
    is_admin: bool


def _secure_equal(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def require_api_key(
    x_bookcourse_api_key: str | None = Header(default=None, alias="X-BookCourse-Api-Key"),
    x_bookcourse_user_id: str | None = Header(default=None, alias="X-BookCourse-User-Id"),
    x_bookcourse_admin_token: str | None = Header(default=None, alias="X-BookCourse-Admin-Token"),
) -> Principal:
    """Authenticate every business API request.

    Returns a Principal carrying the callers user_id and admin flag.
    Rules:
      - strict mode: API key is mandatory and must match secrets.compare_digest
      - optional mode (legacy/demo): no API key, caller is treated as local_user
      - Admin Token (when configured) bypasses user_id cross-checks; only used
        by server-side orchestration scripts (NginX + backend), never by the web client.
    """
    settings = get_settings()
    if not settings.api_key_required:
        return Principal(user_id=settings.default_user_id, is_admin=False)

    if not settings.api_key:
        raise AppError("api_key_not_configured", "API Key 认证已启用，但服务端未配置 BOOKCOURSE_API_KEY", status_code=503)

    if not x_bookcourse_api_key or not _secure_equal(x_bookcourse_api_key, settings.api_key):
        _logger.warning("invalid_api_key", extra={"event": "app_error", "code": "invalid_api_key"})
        raise AppError("invalid_api_key", "缺少或无效的 API Key", status_code=401)

    user_id = (x_bookcourse_user_id or settings.default_user_id or "local_user").strip() or "local_user"
    is_admin = bool(x_bookcourse_admin_token) and bool(settings.admin_token) and _secure_equal(
        x_bookcourse_admin_token, settings.admin_token
    )
    return Principal(user_id=user_id, is_admin=is_admin)


def is_strict_mode() -> bool:
    return get_settings().auth_mode == "strict"


def require_user_match(path_user_id: str, principal: Principal) -> None:
    if not is_strict_mode():
        return
    if principal.is_admin:
        return
    if not path_user_id:
        return
    if principal.user_id != path_user_id:
        raise AppError("forbidden_user_mismatch", "无权访问该用户的数据", status_code=403, details={"path_user_id": path_user_id})