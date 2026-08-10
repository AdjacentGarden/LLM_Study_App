from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.services.storage import sanitize_filename, assert_allowed_file, resolve_under_root


def test_sanitize_filename_removes_path_segments() -> None:
    assert sanitize_filename("..\\unsafe.pdf") == "unsafe.pdf"


def test_allowed_file_rejects_unknown_extension() -> None:
    with pytest.raises(AppError):
        assert_allowed_file("malware.exe")


def test_resolve_under_root_rejects_escape() -> None:
    with pytest.raises(AppError):
        resolve_under_root("..", "outside.pdf")
