from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
import json
import os
import shutil
import tempfile

from app.core.auth import Principal
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.community import CommunityBookSummary, CommunityImportResponse
from app.services.storage import (
    assert_allowed_file,
    books_root,
    create_book_id,
    list_book_ids,
    read_book_owner,
    sanitize_filename,
    storage_root,
)
from app.services.upload_validation import validate_saved_upload


@dataclass(frozen=True, slots=True)
class CommunityCatalogEntry:
    id: str
    title: str
    catalog_title: str
    author: str
    filename: str
    cover_filename: str
    subject: str
    level: str
    language: str
    edition: str
    page_count: int
    file_size_bytes: int
    source_url: str
    source_page_url: str
    sha256: str
    license_name: str
    license_url: str
    rights_notice: str
    description: str
    chapters: tuple[str, ...]
    tags: tuple[str, ...]


CATALOG: tuple[CommunityCatalogEntry, ...] = (
    CommunityCatalogEntry(
        id="gutenberg_calculus_made_easy",
        title="Calculus Made Easy",
        catalog_title="Calculus Made Easy",
        author="Silvanus P. Thompson",
        filename="calculus-made-easy.pdf",
        cover_filename="calculus-made-easy.webp",
        subject="数学",
        level="大学",
        language="English",
        edition="Second Edition, 1914",
        page_count=292,
        file_size_bytes=1_298_365,
        source_url="https://www.gutenberg.org/files/33283/33283-pdf.pdf",
        source_page_url="https://www.gutenberg.org/ebooks/33283",
        sha256="0a6957ae86625bbabcb71e070ef32e5dc7197cebb8bf90f0275caab45e71baf6",
        license_name="Project Gutenberg License / U.S. public-domain text",
        license_url="https://www.gutenberg.org/policy/license",
        rights_notice="Project Gutenberg 标注该作品在美国不受版权限制；其他地区使用者需自行确认当地法律。",
        description="Silvanus P. Thompson 以直观方式讲解微分与积分的经典入门教材；导入的是完整 292 页 PDF，而不是演示卡片。",
        chapters=(
            "To Deliver You from the Preliminary Terrors",
            "On Different Degrees of Smallness",
            "On Relative Growings",
            "Geometrical Meaning of Differentiation",
            "Integration",
            "Finding Areas by Integrating",
        ),
        tags=("真实 PDF", "微积分", "公共领域"),
    ),
    CommunityCatalogEntry(
        id="gutenberg_euclid_elements",
        title="The First Six Books of the Elements of Euclid",
        catalog_title="Euclid's Elements",
        author="Euclid · John Casey",
        filename="euclid-elements-first-six-books.pdf",
        cover_filename="euclid-elements.webp",
        subject="数学",
        level="大学",
        language="English",
        edition="Casey edition, 1885",
        page_count=228,
        file_size_bytes=1_860_807,
        source_url="https://www.gutenberg.org/files/21076/21076-pdf.pdf",
        source_page_url="https://www.gutenberg.org/ebooks/21076",
        sha256="fa77c91ea6b1e31fe09dea4d9a4310e7f8345dba5be0563603f62e7742ffce5c",
        license_name="Project Gutenberg License / U.S. public-domain text",
        license_url="https://www.gutenberg.org/policy/license",
        rights_notice="Project Gutenberg 标注该作品在美国不受版权限制；其他地区使用者需自行确认当地法律。",
        description="John Casey 编订的欧几里得《几何原本》前六卷，含命题、证明和图示；导入后使用真实原书页面解析。",
        chapters=("Book I", "Book II", "Book III", "Book IV", "Book V", "Book VI"),
        tags=("真实 PDF", "几何", "公共领域"),
    ),
    CommunityCatalogEntry(
        id="gutenberg_quaternions_physics",
        title="Utility of Quaternions in Physics",
        catalog_title="Quaternions in Physics",
        author="Alexander McAulay",
        filename="utility-of-quaternions-in-physics.pdf",
        cover_filename="quaternions-physics.webp",
        subject="物理",
        level="大学",
        language="English",
        edition="1893 edition",
        page_count=134,
        file_size_bytes=732_335,
        source_url="https://www.gutenberg.org/files/26262/26262-pdf.pdf",
        source_page_url="https://www.gutenberg.org/ebooks/26262",
        sha256="8f7f1a9444f85912687c14bd346e90e5d9049291cf21bd307dae815315d0df05",
        license_name="Project Gutenberg License / U.S. public-domain text",
        license_url="https://www.gutenberg.org/policy/license",
        rights_notice="Project Gutenberg 标注该作品在美国不受版权限制；其他地区使用者需自行确认当地法律。",
        description="Alexander McAulay 关于四元数在数学物理中应用的经典专著，提供完整 134 页可验证 PDF。",
        chapters=(
            "Fundamental Principles",
            "Differentiation of Quaternions",
            "Kinematics",
            "Dynamics",
            "Physical Applications",
        ),
        tags=("真实 PDF", "数学物理", "公共领域"),
    ),
)

CATALOG_BY_ID = {entry.id: entry for entry in CATALOG}
_IMPORT_LOCK = Lock()
_SOURCE_METADATA_FILENAME = "_community_source.json"
_OWNER_METADATA_FILENAME = "_owner.json"
_STAGING_DIRECTORY_NAME = ".community-imports"
_STALE_STAGING_AGE = timedelta(hours=6)
_IMPORT_LOCK_DIRECTORY_NAME = ".community-import-locks"
_LIBRARY_DIRECTORY_NAME = "community-library"
_CACHE_LOCK_DIRECTORY_NAME = ".community-cache-locks"
_CACHE_LOCK = Lock()


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def get_catalog_entry(catalog_id: str) -> CommunityCatalogEntry:
    entry = CATALOG_BY_ID.get(catalog_id)
    if entry is None:
        raise AppError("community_book_not_found", "社区教材不存在", status_code=404)
    return entry


def community_cover_path(entry: CommunityCatalogEntry) -> Path:
    return Path(__file__).resolve().parent / "covers" / entry.cover_filename


def _metadata_path(book_id: str) -> Path:
    return books_root() / book_id / _SOURCE_METADATA_FILENAME


def read_community_source_metadata(book_id: str) -> dict[str, object] | None:
    path = _metadata_path(book_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _write_community_source_metadata(path: Path, entry: CommunityCatalogEntry) -> None:
    _write_json_atomic(
        path,
        {
            **asdict(entry),
            "imported_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _staging_root() -> Path:
    root = storage_root() / _STAGING_DIRECTORY_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def community_library_root() -> Path:
    """Return the server-owned community source cache.

    This directory is deliberately outside ``books/`` so a cached source can
    never appear as a user course before the atomic import publish step.
    """

    root = storage_root() / _LIBRARY_DIRECTORY_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def cached_community_source(entry: CommunityCatalogEntry) -> Path:
    return community_library_root() / entry.id / sanitize_filename(entry.filename)


def _cached_source_matches(entry: CommunityCatalogEntry, path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size != entry.file_size_bytes:
            return False
        digest = sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest() == entry.sha256
    except OSError:
        return False


def is_community_book_cached(entry: CommunityCatalogEntry) -> bool:
    return _cached_source_matches(entry, cached_community_source(entry))


def _cleanup_abandoned_staging(now: datetime | None = None) -> None:
    """Remove old invisible imports without racing a healthy download.

    Staging lives outside ``books/``, so even a killed process can never expose a
    half-course. A later import removes directories that are old enough that no
    configured community download could still be healthy.
    """

    cutoff = (now or datetime.now(timezone.utc)) - _STALE_STAGING_AGE
    for candidate in _staging_root().iterdir():
        try:
            modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
            if modified > cutoff:
                continue
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


@contextmanager
def _cross_process_import_lock(user_id: str, catalog_id: str):
    """Serialize one tenant/catalog import across API worker processes."""

    lock_key = sha256(f"{user_id}\0{catalog_id}".encode("utf-8")).hexdigest()
    lock_root = storage_root() / _IMPORT_LOCK_DIRECTORY_NAME
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{lock_key}.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _cross_process_cache_lock(catalog_id: str):
    lock_key = sha256(catalog_id.encode("utf-8")).hexdigest()
    lock_root = storage_root() / _CACHE_LOCK_DIRECTORY_NAME
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{lock_key}.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _imported_book_id(entry: CommunityCatalogEntry, principal: Principal) -> str | None:
    for book_id in list_book_ids():
        if read_book_owner(book_id) != principal.user_id:
            continue
        metadata = read_community_source_metadata(book_id)
        if metadata and metadata.get("id") == entry.id:
            return book_id
    return None


def community_book_summary(entry: CommunityCatalogEntry, principal: Principal) -> CommunityBookSummary:
    return CommunityBookSummary(
        id=entry.id,
        title=entry.title,
        catalog_title=entry.catalog_title,
        author=entry.author,
        cover=f"/api/community/books/{entry.id}/cover",
        subject=entry.subject,
        level=entry.level,
        language=entry.language,
        edition=entry.edition,
        page_count=entry.page_count,
        file_size_bytes=entry.file_size_bytes,
        source_page_url=entry.source_page_url,
        license_name=entry.license_name,
        license_url=entry.license_url,
        rights_notice=entry.rights_notice,
        description=entry.description,
        chapters=list(entry.chapters),
        tags=list(entry.tags),
        server_cached=is_community_book_cached(entry),
        imported_book_id=_imported_book_id(entry, principal),
    )


def list_community_books(principal: Principal) -> list[CommunityBookSummary]:
    return [community_book_summary(entry, principal) for entry in CATALOG]


def _stream_verified_pdf(entry: CommunityCatalogEntry, destination: Path) -> None:
    settings = get_settings()
    if entry.file_size_bytes > settings.max_upload_bytes:
        raise AppError("community_source_too_large", "社区教材超过当前上传大小限制", status_code=409)

    request = Request(
        entry.source_url,
        headers={
            "Accept": "application/pdf",
            "Accept-Encoding": "identity",
            "User-Agent": "BookCourse/0.1 community-library importer",
        },
        method="GET",
    )
    digest = sha256()
    total = 0
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=settings.community_download_timeout_seconds) as response:
            if response.status != 200:
                raise AppError("community_source_unavailable", "社区教材源暂时不可用", status_code=502)
            content_type = response.headers.get_content_type().lower()
            if content_type != "application/pdf":
                raise AppError(
                    "community_source_type_mismatch",
                    "社区教材源没有返回 PDF",
                    status_code=502,
                    details={"content_type": content_type},
                )
            declared_length = response.headers.get("Content-Length")
            if declared_length and int(declared_length) != entry.file_size_bytes:
                raise AppError("community_source_size_changed", "社区教材源文件已变化，已拒绝导入", status_code=409)
            with destination.open("xb") as handle:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > entry.file_size_bytes or total > settings.max_upload_bytes:
                        raise AppError("community_source_size_changed", "社区教材源文件已变化，已拒绝导入", status_code=409)
                    digest.update(block)
                    handle.write(block)
    except HTTPError as exc:
        raise AppError(
            "community_source_redirected" if 300 <= exc.code < 400 else "community_source_unavailable",
            "社区教材源重定向已被拒绝" if 300 <= exc.code < 400 else "社区教材源暂时不可用",
            status_code=502,
        ) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise AppError(
            "community_source_unavailable",
            "社区教材源暂时不可用",
            status_code=502,
            details={"type": exc.__class__.__name__},
        ) from None

    if total != entry.file_size_bytes or digest.hexdigest() != entry.sha256:
        raise AppError("community_source_integrity_failed", "社区教材完整性校验失败，已拒绝导入", status_code=409)


def ensure_community_source_cached(entry: CommunityCatalogEntry) -> Path:
    """Download and validate a catalog PDF once into server-owned storage."""

    with _CACHE_LOCK, _cross_process_cache_lock(entry.id):
        target = cached_community_source(entry)
        if _cached_source_matches(entry, target):
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{entry.id}.",
            suffix=".pdf.downloading",
            dir=target.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        temporary.unlink(missing_ok=True)
        try:
            _stream_verified_pdf(entry, temporary)
            validate_saved_upload(temporary, entry.filename)
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return target


def prefetch_community_library() -> list[Path]:
    return [ensure_community_source_cached(entry) for entry in CATALOG]


def import_community_book(catalog_id: str, principal: Principal) -> CommunityImportResponse:
    entry = get_catalog_entry(catalog_id)
    with _IMPORT_LOCK, _cross_process_import_lock(principal.user_id, entry.id):
        # This check must happen after both locks are held. Another worker may
        # have published the same real book while this request was waiting.
        existing = _imported_book_id(entry, principal)
        if existing:
            return CommunityImportResponse(
                catalog_id=entry.id,
                book_id=existing,
                filename=entry.filename,
                size_bytes=entry.file_size_bytes,
                status="saved",
                already_imported=True,
            )

        book_id = create_book_id()
        _cleanup_abandoned_staging()
        staging = _staging_root() / f"{book_id}.{os.getpid()}"
        staging.mkdir(parents=False, exist_ok=False)
        safe_filename = sanitize_filename(entry.filename)
        assert_allowed_file(safe_filename)
        target = staging / "original" / safe_filename
        target.parent.mkdir(parents=True, exist_ok=False)
        temporary = target.with_name(f".{target.name}.community-importing")
        published = False
        try:
            cached_source = ensure_community_source_cached(entry)
            shutil.copyfile(cached_source, temporary)
            validate_saved_upload(temporary, entry.filename)
            temporary.replace(target)
            _write_json_atomic(
                staging / _OWNER_METADATA_FILENAME,
                {"user_id": principal.user_id},
            )
            _write_community_source_metadata(staging / _SOURCE_METADATA_FILENAME, entry)
            final_directory = books_root() / book_id
            staging.replace(final_directory)
            published = True
        except Exception:
            temporary.unlink(missing_ok=True)
            if not published:
                shutil.rmtree(staging, ignore_errors=True)
            raise

        return CommunityImportResponse(
            catalog_id=entry.id,
            book_id=book_id,
            filename=entry.filename,
            size_bytes=entry.file_size_bytes,
            status="saved",
        )
