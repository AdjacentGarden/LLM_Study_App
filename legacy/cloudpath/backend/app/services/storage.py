from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import json
import re
import shutil

from app.core.config import get_settings
from app.core.errors import AppError
from app.services.file_types import ALLOWED_EXTENSIONS, supported_file_type_message


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


def create_book_id() -> str:
    return f"book_{uuid4().hex[:12]}"


def sanitize_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = SAFE_NAME_RE.sub("_", name)
    if not name:
        raise AppError("invalid_filename", "文件名无效")
    return name


def assert_allowed_file(filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AppError("unsupported_file_type", supported_file_type_message(), details={"extension": ext})


def storage_root() -> Path:
    root = get_settings().storage_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_under_root(*parts: str) -> Path:
    root = storage_root()
    target = root.joinpath(*parts).resolve()
    if root != target and root not in target.parents:
        raise AppError("invalid_path", "目标路径超出存储目录")
    return target


def book_dir(book_id: str) -> Path:
    if not book_id:
        raise AppError("invalid_book_id", "book_id 不能为空")
    target = resolve_under_root("books", book_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def books_root() -> Path:
    target = resolve_under_root("books")
    target.mkdir(parents=True, exist_ok=True)
    return target


def list_book_ids() -> list[str]:
    root = books_root()
    return [item.name for item in root.iterdir() if item.is_dir()]


def original_file_path(book_id: str, filename: str) -> Path:
    safe = sanitize_filename(filename)
    assert_allowed_file(safe)
    target = book_dir(book_id) / "original" / safe
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def write_book_owner(book_id: str, user_id: str | None) -> None:
    path = book_dir(book_id) / "_owner.json"
    path.write_text(json.dumps({"user_id": user_id or "anonymous"}, ensure_ascii=False), encoding="utf-8")


def read_book_owner(book_id: str) -> str | None:
    path = resolve_under_root("books", book_id, "_owner.json")
    if not path.exists():
        return None
    try:
        owner = json.loads(path.read_text(encoding="utf-8")).get("user_id")
        return owner.strip() if isinstance(owner, str) and owner.strip() else None
    except Exception:
        return None


def artifact_dir(book_id: str) -> Path:
    target = book_dir(book_id) / "artifacts"
    target.mkdir(parents=True, exist_ok=True)
    return target


def asset_dir(book_id: str) -> Path:
    target = book_dir(book_id) / "assets"
    target.mkdir(parents=True, exist_ok=True)
    return target


def find_original_file(book_id: str) -> Path | None:
    original = book_dir(book_id) / "original"
    if not original.exists():
        return None
    files = [item for item in original.iterdir() if item.is_file()]
    return files[0] if files else None


def remove_book(book_id: str) -> None:
    target = book_dir(book_id)
    root = storage_root()
    resolved = target.resolve()
    if root == resolved or root not in resolved.parents:
        raise AppError("invalid_path", "拒绝删除存储根目录之外的内容")
    shutil.rmtree(resolved)
    if resolved.exists():
        raise AppError("book_delete_failed", "课程目录删除后仍然存在", status_code=500)
