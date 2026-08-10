from __future__ import annotations

from io import BytesIO
from pathlib import Path
import zipfile

import fitz
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.core.config import get_settings
from app.core.errors import AppError
from app.main import app
from app.services.file_types import ALLOWED_EXTENSIONS
from app.services.file_types import FILE_TYPE_BY_EXTENSION
from app.services.storage import assert_allowed_file
from app.services.upload_validation import validate_declared_content_type, validate_saved_upload


FIXTURES = Path(__file__).resolve().parents[3] / "quality" / "stage0" / "fixtures"


def _image_bytes(fmt: str, size: tuple[int, int] = (10, 10)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color=(70, 130, 190)).save(buffer, format=fmt)
    return buffer.getvalue()


def _pdf_bytes(page_count: int) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page(width=72, height=72)
    payload = document.tobytes()
    document.close()
    return payload


def _rewrite_zip(
    source: Path,
    target: Path,
    *,
    replacements: dict[str, bytes] | None = None,
    additions: dict[str, bytes] | None = None,
) -> None:
    replacements = replacements or {}
    additions = additions or {}
    with zipfile.ZipFile(source, "r") as current, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as updated:
        for info in current.infolist():
            if info.is_dir():
                continue
            updated.writestr(info.filename, replacements.get(info.filename, current.read(info)))
        for name, payload in additions.items():
            updated.writestr(name, payload)


def _expect_code(path: Path, code: str) -> None:
    with pytest.raises(AppError) as exc_info:
        validate_saved_upload(path, path.name)
    assert exc_info.value.code == code


def test_stage5_extension_contract_is_exact() -> None:
    assert ALLOWED_EXTENSIONS == {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".jp2",
        ".webp",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".docx",
        ".pptx",
        ".xlsx",
    }
    for extension in ALLOWED_EXTENSIONS:
        assert_allowed_file(f"source{extension}")
    for extension in (".doc", ".ppt", ".xls", ".exe", ".zip", ""):
        with pytest.raises(AppError) as exc_info:
            assert_allowed_file(f"source{extension}")
        assert exc_info.value.code == "unsupported_file_type"


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("a.pdf", "application/pdf"),
        ("a.png", "image/png"),
        ("a.jpg", "image/jpeg"),
        ("a.jpeg", "image/jpeg"),
        ("a.jp2", "image/jp2"),
        ("a.webp", "image/webp"),
        ("a.gif", "image/gif"),
        ("a.bmp", "image/bmp"),
        ("a.tif", "image/tiff"),
        ("a.tiff", "image/tiff"),
        ("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("a.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
)
def test_declared_mime_contract_accepts_matching_type(filename: str, content_type: str) -> None:
    validate_declared_content_type(filename, content_type)


def test_declared_mime_contract_rejects_mismatch() -> None:
    with pytest.raises(AppError) as exc_info:
        validate_declared_content_type("lesson.pptx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert exc_info.value.code == "file_content_type_mismatch"


@pytest.mark.parametrize(
    "filename",
    [
        "native_text.pdf",
        "source_diagram.png",
        "sample.jpg",
        "sample.jpeg",
        "sample.jp2",
        "sample.webp",
        "sample.bmp",
        "sample.tif",
        "sample.docx",
        "sample.pptx",
        "sample.xlsx",
    ],
)
def test_frozen_valid_formats_pass_content_validation(filename: str) -> None:
    validate_saved_upload(FIXTURES / filename, filename)


def test_upload_api_accepts_every_target_format(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    generated_gif = tmp_path / "single.gif"
    generated_gif.write_bytes(_image_bytes("GIF"))
    generated_tiff = tmp_path / "single.tiff"
    generated_tiff.write_bytes(_image_bytes("TIFF"))
    sources = {
        ".pdf": FIXTURES / "native_text.pdf",
        ".png": FIXTURES / "source_diagram.png",
        ".jpg": FIXTURES / "sample.jpg",
        ".jpeg": FIXTURES / "sample.jpeg",
        ".jp2": FIXTURES / "sample.jp2",
        ".webp": FIXTURES / "sample.webp",
        ".gif": generated_gif,
        ".bmp": FIXTURES / "sample.bmp",
        ".tif": FIXTURES / "sample.tif",
        ".tiff": generated_tiff,
        ".docx": FIXTURES / "sample.docx",
        ".pptx": FIXTURES / "sample.pptx",
        ".xlsx": FIXTURES / "sample.xlsx",
    }
    client = TestClient(app)
    for extension, source in sources.items():
        content_type = sorted(FILE_TYPE_BY_EXTENSION[extension].content_types)[0]
        filename = f"target{extension}"
        payload = source.read_bytes()
        initialized = client.post(
            "/api/uploads/init",
            json={"filename": filename, "content_type": content_type, "size_bytes": len(payload)},
        )
        assert initialized.status_code == 200, (extension, initialized.text)
        uploaded = client.post(
            f"/api/books/{initialized.json()['book_id']}/files",
            files={"file": (filename, payload, content_type)},
        )
        assert uploaded.status_code == 200, (extension, uploaded.text)


@pytest.mark.parametrize(("extension", "fmt"), [(".gif", "GIF"), (".tiff", "TIFF")])
def test_single_frame_gif_and_single_page_tiff_are_allowed(tmp_path: Path, extension: str, fmt: str) -> None:
    path = tmp_path / f"single{extension}"
    path.write_bytes(_image_bytes(fmt))
    validate_saved_upload(path, path.name)


@pytest.mark.parametrize(
    ("filename", "code"),
    [
        ("animated.gif", "animated_gif_unsupported"),
        ("multipage.tiff", "multipage_tiff_unsupported"),
        ("path_traversal.docx", "ooxml_unsafe_path"),
        ("high_compression_ratio.docx", "zip_compression_ratio_exceeded"),
        ("spoofed.docx", "file_content_mismatch"),
        ("encrypted.pdf", "encrypted_pdf_unsupported"),
        ("corrupt.pdf", "invalid_pdf"),
        ("corrupt.png", "invalid_image"),
    ],
)
def test_frozen_rejection_matrix(filename: str, code: str) -> None:
    _expect_code(FIXTURES / filename, code)


def test_image_extension_spoofing_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "actually_png.jpg"
    path.write_bytes(_image_bytes("PNG"))
    _expect_code(path, "file_content_mismatch")


def test_office_internal_type_spoofing_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "renamed.pptx"
    path.write_bytes((FIXTURES / "sample.docx").read_bytes())
    _expect_code(path, "ooxml_type_mismatch")


def test_external_relationship_is_rejected(tmp_path: Path) -> None:
    source = FIXTURES / "sample.docx"
    with zipfile.ZipFile(source) as archive:
        rels = archive.read("_rels/.rels")
    injected = rels.replace(
        b"</Relationships>",
        b'<Relationship Id="evil" Type="urn:evil" Target="https://example.invalid/a" TargetMode="External"/></Relationships>',
    )
    path = tmp_path / "external.docx"
    _rewrite_zip(source, path, replacements={"_rels/.rels": injected})
    _expect_code(path, "ooxml_external_relationship")


def test_xxe_declaration_is_rejected_before_xml_parsing(tmp_path: Path) -> None:
    source = FIXTURES / "sample.docx"
    with zipfile.ZipFile(source) as archive:
        document = archive.read("word/document.xml")
    injected = b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>' + document
    path = tmp_path / "xxe.docx"
    _rewrite_zip(source, path, replacements={"word/document.xml": injected})
    _expect_code(path, "ooxml_unsafe_xml")


def test_nested_archive_and_active_content_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "nested.docx"
    _rewrite_zip(FIXTURES / "sample.docx", path, additions={"word/media/payload.zip": b"PK\x03\x04payload"})
    _expect_code(path, "ooxml_nested_or_active_content")


def test_traversal_directory_entry_and_symlink_are_rejected(tmp_path: Path) -> None:
    directory_path = tmp_path / "directory_traversal.docx"
    _rewrite_zip(FIXTURES / "sample.docx", directory_path, additions={"../escape/": b""})
    _expect_code(directory_path, "ooxml_unsafe_path")

    symlink_path = tmp_path / "symlink.docx"
    with zipfile.ZipFile(FIXTURES / "sample.docx", "r") as source, zipfile.ZipFile(
        symlink_path, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for info in source.infolist():
            if not info.is_dir():
                target.writestr(info.filename, source.read(info))
        link = zipfile.ZipInfo("word/media/link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        target.writestr(link, b"../../outside")
    _expect_code(symlink_path, "ooxml_unsafe_link")


def test_compound_file_password_container_is_reported_as_encrypted_office(tmp_path: Path) -> None:
    path = tmp_path / "protected.docx"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    _expect_code(path, "encrypted_office_unsupported")


def test_upload_byte_limit_accepts_boundary_and_rejects_overflow(monkeypatch, tmp_path: Path) -> None:
    payload = _image_bytes("PNG")
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("BOOKCOURSE_MAX_UPLOAD_BYTES", str(len(payload)))
    get_settings.cache_clear()
    client = TestClient(app)

    boundary_init = client.post(
        "/api/uploads/init",
        json={"filename": "boundary.png", "content_type": "image/png", "size_bytes": len(payload)},
    )
    assert boundary_init.status_code == 200
    boundary_upload = client.post(
        f"/api/books/{boundary_init.json()['book_id']}/files",
        files={"file": ("boundary.png", payload, "image/png")},
    )
    assert boundary_upload.status_code == 200

    over_init = client.post(
        "/api/uploads/init",
        json={"filename": "over.png", "content_type": "image/png", "size_bytes": len(payload) + 1},
    )
    assert over_init.status_code == 400
    assert over_init.json()["code"] == "file_too_large"

    streaming_init = client.post("/api/uploads/init", json={"filename": "stream.png"})
    streaming_over = client.post(
        f"/api/books/{streaming_init.json()['book_id']}/files",
        files={"file": ("stream.png", payload + b"x", "image/png")},
    )
    assert streaming_over.status_code == 400
    assert streaming_over.json()["code"] == "file_too_large"


def test_image_pixel_limit_boundary_and_overflow(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_MAX_IMAGE_PIXELS", "100")
    get_settings.cache_clear()
    boundary = tmp_path / "boundary.png"
    boundary.write_bytes(_image_bytes("PNG", (10, 10)))
    validate_saved_upload(boundary, boundary.name)

    overflow = tmp_path / "overflow.png"
    overflow.write_bytes(_image_bytes("PNG", (101, 1)))
    _expect_code(overflow, "image_pixel_limit_exceeded")


def test_pdf_page_limit_boundary_and_overflow(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_MAX_PDF_PAGES", "2")
    get_settings.cache_clear()
    boundary = tmp_path / "boundary.pdf"
    boundary.write_bytes(_pdf_bytes(2))
    validate_saved_upload(boundary, boundary.name)

    overflow = tmp_path / "overflow.pdf"
    overflow.write_bytes(_pdf_bytes(3))
    _expect_code(overflow, "pdf_page_limit_exceeded")


def test_pptx_xlsx_and_docx_resource_limits(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_MAX_PPTX_SLIDES", "1")
    get_settings.cache_clear()
    _expect_code(FIXTURES / "sample.pptx", "pptx_slide_limit_exceeded")

    monkeypatch.setenv("BOOKCOURSE_MAX_PPTX_SLIDES", "2")
    monkeypatch.setenv("BOOKCOURSE_MAX_XLSX_SHEETS", "1")
    get_settings.cache_clear()
    validate_saved_upload(FIXTURES / "sample.pptx", "sample.pptx")
    _expect_code(FIXTURES / "sample.xlsx", "xlsx_sheet_limit_exceeded")

    resource_heavy = tmp_path / "resource_heavy.docx"
    _rewrite_zip(FIXTURES / "sample.docx", resource_heavy, additions={"word/media/extra.png": _image_bytes("PNG")})
    monkeypatch.setenv("BOOKCOURSE_MAX_XLSX_SHEETS", "2")
    monkeypatch.setenv("BOOKCOURSE_MAX_DOCX_RESOURCES", "1")
    get_settings.cache_clear()
    _expect_code(resource_heavy, "docx_resource_limit_exceeded")


def test_zip_entry_single_total_and_ratio_limits(monkeypatch) -> None:
    sample = FIXTURES / "sample.docx"
    with zipfile.ZipFile(sample) as archive:
        infos = [item for item in archive.infolist() if not item.is_dir()]
    max_entry = max(item.file_size for item in infos)
    total = sum(item.file_size for item in infos)
    max_ratio = max(item.file_size / max(item.compress_size, 1) for item in infos)

    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_ENTRIES", str(len(infos)))
    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES", str(max_entry))
    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES", str(total))
    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_COMPRESSION_RATIO", str(max_ratio + 0.001))
    get_settings.cache_clear()
    validate_saved_upload(sample, sample.name)

    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_ENTRIES", str(len(infos) - 1))
    get_settings.cache_clear()
    _expect_code(sample, "zip_entry_limit_exceeded")

    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_ENTRIES", str(len(infos)))
    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES", str(max_entry - 1))
    get_settings.cache_clear()
    _expect_code(sample, "zip_entry_size_limit_exceeded")

    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES", str(max_entry))
    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES", str(total - 1))
    get_settings.cache_clear()
    _expect_code(sample, "zip_total_size_limit_exceeded")

    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES", str(total))
    monkeypatch.setenv("BOOKCOURSE_MAX_ZIP_COMPRESSION_RATIO", str(max(1.0, max_ratio - 0.001)))
    get_settings.cache_clear()
    _expect_code(sample, "zip_compression_ratio_exceeded")


def test_office_validation_timeout_is_a_typed_failure(monkeypatch) -> None:
    import app.services.ooxml_validation as validation

    class ExpiredDeadline:
        def __init__(self, timeout_seconds: float) -> None:
            assert timeout_seconds > 0

        def check(self) -> None:
            raise AppError("office_validation_timeout", "Office 文件安全校验超时")

    monkeypatch.setattr(validation, "_Deadline", ExpiredDeadline)
    _expect_code(FIXTURES / "sample.docx", "office_validation_timeout")
