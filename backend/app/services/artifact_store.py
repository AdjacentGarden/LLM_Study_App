from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import tempfile
import threading
from uuid import uuid4

from app.rag.cache import (
    get_chunks,
    invalidate_artifact_embeddings,
    invalidate_bm25,
    invalidate_chunks,
    invalidate_rag_answers,
)
from app.schemas.books import Asset, Chapter, Chunk, Flashcard, Lesson, QuizQuestion
from app.services.storage import artifact_dir


_BUNDLE_SCHEMA_VERSION = 1
_BUNDLE_DIRECTORY = ".rag_bundles"
_MANIFEST_NAME = "manifest.json"
_BOOK_LOCKS_GUARD = threading.Lock()
_BOOK_LOCKS: dict[str, threading.RLock] = {}
_CHUNK_CACHE_TOKENS: dict[str, str] = {}


class RagBundleBuildingError(RuntimeError):
    pass


class RagBundleStaleBuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class RagBundleBuild:
    book_id: str
    build_id: str
    assets: list[Asset]
    chunks: list[Chunk]


@dataclass(frozen=True)
class RagBundleIdentity:
    """Identity of the currently visible or in-progress artifact generation."""

    book_id: str
    state: str
    build_id: str | None
    generation: str | None


def get_rag_bundle_state(book_id: str) -> str:
    """Return the externally visible state of the active RAG artifact pair.

    ``ready`` means both Assets and Chunks belong to one complete, readable
    generation.  A build in progress is deliberately not treated as ready,
    even when compatibility mirrors from an older generation still exist.
    Pre-manifest books remain supported only when both legacy files exist.
    """

    with _book_lock(book_id):
        manifest = _read_manifest_unlocked(book_id)
        if manifest is None:
            artifacts = artifact_dir(book_id)
            assets_exists = (artifacts / "assets.json").is_file()
            chunks_exists = (artifacts / "chunks.jsonl").is_file()
            if assets_exists and chunks_exists:
                return "ready"
            if not assets_exists and not chunks_exists:
                return "missing"
            return "invalid"

        if manifest.get("schema_version") != _BUNDLE_SCHEMA_VERSION:
            return "invalid"
        state = str(manifest.get("state") or "invalid")
        if state == "building":
            return "building"
        if state != "ready":
            return "invalid"
        return "ready" if _active_artifact_paths_unlocked(book_id) is not None else "invalid"


def get_rag_bundle_identity(book_id: str) -> RagBundleIdentity:
    """Return the build/generation identity used by the Stage-4 index CAS."""

    with _book_lock(book_id):
        manifest = _read_manifest_unlocked(book_id)
        if manifest is None:
            state = get_rag_bundle_state(book_id)
            return RagBundleIdentity(
                book_id=book_id,
                state=state,
                build_id=None,
                generation="legacy" if state == "ready" else None,
            )
        state = str(manifest.get("state") or "invalid")
        return RagBundleIdentity(
            book_id=book_id,
            state=state,
            build_id=str(manifest.get("build_id") or "") or None,
            generation=str(manifest.get("generation") or "") or None,
        )


# Read-oriented alias kept for callers that name artifact-store operations by
# intent. Both names expose the same fail-closed state contract.
read_rag_bundle_state = get_rag_bundle_state


def _book_lock(book_id: str) -> threading.RLock:
    with _BOOK_LOCKS_GUARD:
        return _BOOK_LOCKS.setdefault(book_id, threading.RLock())


@contextmanager
def _cross_process_book_lock(book_id: str):
    """Serialize short artifact manifest mutations across API workers.

    The in-memory RLock protects threads in one worker.  This one-byte file
    lock closes the corresponding multi-process TOCTOU window while a build is
    reserved or published.  Long parsing/embedding work intentionally happens
    outside this lock.
    """

    lock_path = _bundle_root(book_id) / ".write.lock"
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
def rag_book_mutation_lock(book_id: str):
    """Public short-lived lock for parse reservation and delete fencing."""

    with _cross_process_book_lock(book_id), _book_lock(book_id):
        yield


def _invalidate_rag_caches(book_id: str) -> None:
    _CHUNK_CACHE_TOKENS.pop(book_id, None)
    invalidate_chunks(book_id)
    invalidate_bm25(book_id)
    invalidate_artifact_embeddings(book_id)
    invalidate_rag_answers(book_id)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_json(path: Path, payload: object) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _bundle_root(book_id: str) -> Path:
    path = artifact_dir(book_id) / _BUNDLE_DIRECTORY
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path(book_id: str) -> Path:
    return _bundle_root(book_id) / _MANIFEST_NAME


def _read_manifest_unlocked(book_id: str) -> dict[str, object] | None:
    path = _manifest_path(book_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "invalid"}
    return payload if isinstance(payload, dict) else {"state": "invalid"}


def _generation_directory(book_id: str, generation: object) -> Path | None:
    value = str(generation or "")
    if not value.startswith("rag_") or not all(character.isalnum() or character in {"_", "-"} for character in value):
        return None
    root = _bundle_root(book_id).resolve()
    candidate = (root / value).resolve()
    if candidate.parent != root:
        return None
    return candidate


def _read_assets_file(path: Path) -> list[Asset]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("assets artifact must contain a JSON array")
    return [Asset.model_validate(item) for item in payload]


def _read_chunks_file(path: Path) -> list[Chunk]:
    if not path.exists():
        return []
    chunks: list[Chunk] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            chunks.append(Chunk.model_validate_json(line))
    return chunks


def _write_assets_file(path: Path, assets: list[Asset]) -> None:
    _atomic_write_json(path, [item.model_dump(mode="json") for item in assets])


def _write_chunks_file(path: Path, chunks: list[Chunk]) -> None:
    _atomic_write_text(path, "".join(chunk.model_dump_json() + "\n" for chunk in chunks))


def _active_artifact_paths_unlocked(
    book_id: str,
    *,
    allow_previous_build: bool = False,
) -> tuple[Path, Path, str] | None:
    root = artifact_dir(book_id)
    manifest = _read_manifest_unlocked(book_id)
    if manifest is None:
        return root / "assets.json", root / "chunks.jsonl", f"{root.resolve()}::legacy"
    if manifest.get("schema_version") != _BUNDLE_SCHEMA_VERSION:
        return None

    state = str(manifest.get("state") or "")
    generation: object = manifest.get("generation")
    if state == "building" and allow_previous_build:
        generation = manifest.get("previous_generation")
    elif state != "ready":
        return None

    directory = _generation_directory(book_id, generation)
    if directory is None:
        return None
    assets_path = directory / "assets.json"
    chunks_path = directory / "chunks.jsonl"
    # The mirrors are part of the committed compatibility contract. Missing
    # either the authoritative bundle files or a mirror is treated as a
    # corrupt generation and fails closed.
    artifacts = artifact_dir(book_id)
    if (
        not assets_path.exists()
        or not chunks_path.exists()
        or not (artifacts / "assets.json").exists()
        or not (artifacts / "chunks.jsonl").exists()
    ):
        return None
    return assets_path, chunks_path, f"{root.resolve()}::{directory.name}"


def _active_generation_token_unlocked(book_id: str) -> str | None:
    """Return the manifest identity without reopening already cached files."""

    root = artifact_dir(book_id)
    manifest = _read_manifest_unlocked(book_id)
    if manifest is None:
        return f"{root.resolve()}::legacy"
    if manifest.get("schema_version") != _BUNDLE_SCHEMA_VERSION or manifest.get("state") != "ready":
        return None
    directory = _generation_directory(book_id, manifest.get("generation"))
    return f"{root.resolve()}::{directory.name}" if directory is not None else None


def _read_pair_unlocked(
    book_id: str,
    *,
    allow_previous_build: bool = False,
) -> tuple[list[Asset], list[Chunk]]:
    paths = _active_artifact_paths_unlocked(book_id, allow_previous_build=allow_previous_build)
    if paths is None:
        return [], []
    assets_path, chunks_path, _ = paths
    try:
        return _read_assets_file(assets_path), _read_chunks_file(chunks_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return [], []


def _mark_rag_bundle_building_unlocked(book_id: str) -> RagBundleBuild:
    manifest = _read_manifest_unlocked(book_id)
    assets, chunks = _read_pair_unlocked(book_id, allow_previous_build=True)
    previous_generation: str | None = None
    previous_build_id: str | None = None
    if manifest:
        if manifest.get("state") == "ready":
            previous_generation = str(manifest.get("generation") or "") or None
            previous_build_id = str(manifest.get("build_id") or "") or None
        elif manifest.get("state") == "building":
            previous_generation = str(manifest.get("previous_generation") or "") or None
            previous_build_id = str(manifest.get("previous_build_id") or "") or None

    build_id = f"build_{uuid4().hex}"
    _atomic_write_json(
        _manifest_path(book_id),
        {
            "schema_version": _BUNDLE_SCHEMA_VERSION,
            "state": "building",
            "build_id": build_id,
            "previous_generation": previous_generation,
            "previous_build_id": previous_build_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    _invalidate_rag_caches(book_id)
    return RagBundleBuild(
        book_id=book_id,
        build_id=build_id,
        assets=[asset.model_copy(deep=True) for asset in assets],
        chunks=[chunk.model_copy(deep=True) for chunk in chunks],
    )


def mark_rag_bundle_building(book_id: str) -> RagBundleBuild:
    with _cross_process_book_lock(book_id), _book_lock(book_id):
        return _mark_rag_bundle_building_unlocked(book_id)


def abort_rag_bundle_build(book_id: str, build_id: str) -> bool:
    """Abort an uncommitted optional mutation and restore its prior bundle.

    Parsing and chapter rebuild failures intentionally stay fail-closed.  This
    helper is for mutations such as AI-image attachment that validate their
    source generation after reserving the artifact build but before publishing
    any new pair.
    """

    with _cross_process_book_lock(book_id), _book_lock(book_id):
        manifest = _read_manifest_unlocked(book_id) or {}
        if manifest.get("state") != "building" or manifest.get("build_id") != build_id:
            return False
        previous_generation = str(manifest.get("previous_generation") or "") or None
        previous_build_id = str(manifest.get("previous_build_id") or "") or None
        if previous_generation is None:
            _manifest_path(book_id).unlink(missing_ok=True)
        else:
            directory = _generation_directory(book_id, previous_generation)
            if directory is None or not (directory / "assets.json").is_file() or not (directory / "chunks.jsonl").is_file():
                return False
            _atomic_write_json(
                _manifest_path(book_id),
                {
                    "schema_version": _BUNDLE_SCHEMA_VERSION,
                    "state": "ready",
                    "build_id": previous_build_id or f"restored_{uuid4().hex}",
                    "generation": previous_generation,
                    "previous_generation": None,
                    "restored_from_build_id": build_id,
                    "committed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        _invalidate_rag_caches(book_id)
        return True


def _assert_current_build_unlocked(book_id: str, build_id: str) -> dict[str, object]:
    manifest = _read_manifest_unlocked(book_id) or {}
    if manifest.get("state") != "building" or manifest.get("build_id") != build_id:
        raise RagBundleStaleBuildError(f"RAG bundle build is no longer current for {book_id}")
    return manifest


def _cleanup_staging_directory(staging: Path) -> None:
    for filename in ("assets.json", "chunks.jsonl"):
        candidate = staging / filename
        if candidate.exists():
            candidate.unlink()
    if staging.exists():
        staging.rmdir()


def _publish_pair_unlocked(
    book_id: str,
    assets: list[Asset],
    chunks: list[Chunk],
    *,
    build_id: str,
) -> None:
    manifest = _assert_current_build_unlocked(book_id, build_id)
    generation = f"rag_{uuid4().hex}"
    root = _bundle_root(book_id)
    staging = root / f".{generation}.building"
    final = root / generation
    staging.mkdir(parents=False, exist_ok=False)
    try:
        _write_assets_file(staging / "assets.json", assets)
        _write_chunks_file(staging / "chunks.jsonl", chunks)
        os.replace(staging, final)

        # Compatibility mirrors remain for diagnostics and older tooling. The
        # manifest is still building while they are replaced, so application
        # readers cannot observe a mixed pair.
        artifacts = artifact_dir(book_id)
        _write_assets_file(artifacts / "assets.json", assets)
        _write_chunks_file(artifacts / "chunks.jsonl", chunks)

        _atomic_write_json(
            _manifest_path(book_id),
            {
                "schema_version": _BUNDLE_SCHEMA_VERSION,
                "state": "ready",
                "build_id": build_id,
                "generation": generation,
                "previous_generation": manifest.get("previous_generation"),
                "committed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        if staging.exists():
            _cleanup_staging_directory(staging)
        raise
    finally:
        _invalidate_rag_caches(book_id)


def write_assets_and_chunks(
    book_id: str,
    assets: list[Asset],
    chunks: list[Chunk],
    *,
    build_id: str | None = None,
) -> None:
    with _cross_process_book_lock(book_id), _book_lock(book_id):
        resolved_build_id = build_id
        if resolved_build_id is None:
            manifest = _read_manifest_unlocked(book_id)
            if manifest and manifest.get("state") == "building":
                raise RagBundleBuildingError(f"RAG bundle is already building for {book_id}")
            resolved_build_id = _mark_rag_bundle_building_unlocked(book_id).build_id
        _publish_pair_unlocked(book_id, assets, chunks, build_id=resolved_build_id)


def update_assets_and_chunks(
    book_id: str,
    updater: Callable[[list[Asset], list[Chunk]], tuple[list[Asset], list[Chunk]]],
) -> tuple[list[Asset], list[Chunk]]:
    """Apply a short read-modify-write transaction to the current pair."""

    with _cross_process_book_lock(book_id), _book_lock(book_id):
        manifest = _read_manifest_unlocked(book_id)
        if manifest and manifest.get("state") == "building":
            raise RagBundleBuildingError(f"RAG bundle is already building for {book_id}")
        assets, chunks = _read_pair_unlocked(book_id)
        updated_assets, updated_chunks = updater(
            [asset.model_copy(deep=True) for asset in assets],
            [chunk.model_copy(deep=True) for chunk in chunks],
        )
        build = _mark_rag_bundle_building_unlocked(book_id)
        _publish_pair_unlocked(
            book_id,
            updated_assets,
            updated_chunks,
            build_id=build.build_id,
        )
        return updated_assets, updated_chunks


def _read_json(path: Path, default: list[dict]) -> list[dict]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_chapters(book_id: str) -> list[Chapter]:
    with _book_lock(book_id):
        path = artifact_dir(book_id) / "chapters.json"
        return [Chapter.model_validate(item) for item in _read_json(path, [])]


def write_chapters(
    book_id: str,
    chapters: list[Chapter],
    *,
    build_id: str | None = None,
) -> None:
    """Atomically persist chapters, optionally guarded by the active build."""

    with _cross_process_book_lock(book_id), _book_lock(book_id):
        if build_id is not None:
            _assert_current_build_unlocked(book_id, build_id)
        _write_chapters_unlocked(book_id, chapters)


def write_chapters_for_build(
    book_id: str,
    chapters: list[Chapter],
    *,
    build_id: str,
    preserve_original: bool = False,
) -> None:
    """Publish chapter files under the same build CAS as chunks/assets."""

    with _cross_process_book_lock(book_id), _book_lock(book_id):
        _assert_current_build_unlocked(book_id, build_id)
        if preserve_original:
            original = artifact_dir(book_id) / "chapters_original.json"
            if not original.exists():
                _atomic_write_json(
                    original,
                    [item.model_dump(mode="json") for item in chapters],
                )
        _write_chapters_unlocked(book_id, chapters)


def _write_chapters_unlocked(book_id: str, chapters: list[Chapter]) -> None:
    _atomic_write_json(
        artifact_dir(book_id) / "chapters.json",
        [item.model_dump(mode="json") for item in chapters],
    )


def write_original_chapters_once(book_id: str, chapters: list[Chapter]) -> None:
    with _cross_process_book_lock(book_id), _book_lock(book_id):
        path = artifact_dir(book_id) / "chapters_original.json"
        if not path.exists():
            _atomic_write_json(path, [item.model_dump(mode="json") for item in chapters])


def read_assets(book_id: str) -> list[Asset]:
    with _book_lock(book_id):
        assets, _ = _read_pair_unlocked(book_id)
        return assets


def write_assets(book_id: str, assets: list[Asset]) -> None:
    with _book_lock(book_id):
        _, chunks = _read_pair_unlocked(book_id)
        write_assets_and_chunks(book_id, assets, chunks)


def read_chunks(book_id: str) -> list[Chunk]:
    with _book_lock(book_id):
        manifest = _read_manifest_unlocked(book_id)
        if manifest and manifest.get("state") != "ready":
            _invalidate_rag_caches(book_id)
            return []

        known_token = _CHUNK_CACHE_TOKENS.get(book_id)

        def _load_active(_: str) -> list[Chunk]:
            _, active_chunks = _read_pair_unlocked(book_id)
            return active_chunks

        # A cached generation remains valid until its manifest identity
        # changes or this process is explicitly invalidated.  This preserves
        # the established cache contract while still detecting generations
        # activated by another API worker.
        current_token = _active_generation_token_unlocked(book_id)
        if known_token is not None and current_token == known_token:
            return get_chunks(book_id, _load_active)

        paths = _active_artifact_paths_unlocked(book_id)
        if paths is None:
            _invalidate_rag_caches(book_id)
            return []
        _, _, token = paths
        # Revalidate the manifest token on every read.  Another API worker may
        # have activated a generation and cannot invalidate this process's
        # in-memory cache directly.
        if known_token != token:
            invalidate_chunks(book_id)
            _CHUNK_CACHE_TOKENS[book_id] = token
        return get_chunks(book_id, _load_active)


def write_chunks(book_id: str, chunks: list[Chunk]) -> None:
    with _book_lock(book_id):
        assets, _ = _read_pair_unlocked(book_id)
        write_assets_and_chunks(book_id, assets, chunks)


def read_lessons(book_id: str) -> list[Lesson]:
    with _book_lock(book_id):
        path = artifact_dir(book_id) / "lessons.json"
        return [Lesson.model_validate(item) for item in _read_json(path, [])]


def write_lessons(book_id: str, lessons: list[Lesson]) -> None:
    with _cross_process_book_lock(book_id), _book_lock(book_id):
        _atomic_write_json(
            artifact_dir(book_id) / "lessons.json",
            [item.model_dump(mode="json") for item in lessons],
        )


def read_flashcards(book_id: str) -> list[Flashcard]:
    with _book_lock(book_id):
        path = artifact_dir(book_id) / "flashcards.json"
        return [Flashcard.model_validate(item) for item in _read_json(path, [])]


def write_flashcards(book_id: str, cards: list[Flashcard]) -> None:
    with _cross_process_book_lock(book_id), _book_lock(book_id):
        _atomic_write_json(
            artifact_dir(book_id) / "flashcards.json",
            [item.model_dump(mode="json") for item in cards],
        )


def read_quizzes(book_id: str) -> list[QuizQuestion]:
    with _book_lock(book_id):
        path = artifact_dir(book_id) / "quizzes.json"
        return [QuizQuestion.model_validate(item) for item in _read_json(path, [])]


def write_quizzes(book_id: str, questions: list[QuizQuestion]) -> None:
    with _cross_process_book_lock(book_id), _book_lock(book_id):
        _atomic_write_json(
            artifact_dir(book_id) / "quizzes.json",
            [item.model_dump(mode="json") for item in questions],
        )
