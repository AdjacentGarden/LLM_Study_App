"""Immutable, fingerprinted indexes for private uploads; atomic publication."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from ..ingestion.coverage import page_is_skipped
from .index import IndexedChunk, PairReranker, PersistentRAGIndex, QueryEncoder, RAGIndexError

_INDEX_VERSION = b"upload-index-v2"


def _windows_extended_path(absolute: str) -> str:
    """Apply Windows extended-length syntax without changing path identity."""
    normalized = ntpath.normpath(absolute)
    if normalized.startswith("\\\\?\\"):
        return normalized
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized[2:]
    return "\\\\?\\" + normalized


def _artifact_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    # Do not resolve through the filesystem before adding the prefix: resolving
    # an existing >MAX_PATH filename can itself fail on unconfigured Windows.
    return Path(_windows_extended_path(os.path.abspath(path)))


class DocumentEncoder(QueryEncoder, Protocol):
    def encode_documents(self, texts: Sequence[str]) -> NDArray[np.float32]: ...


def _split(text: str, limit: int, overlap: int = 60) -> list[str]:
    result = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundary = max(text.rfind(mark, start + limit // 2, end) for mark in "。！？；\n")
            if boundary > start:
                end = boundary + 1
        if text[start:end].strip():
            result.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return result


def upload_chunks(path: Path) -> list[IndexedChunk]:
    chunks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        page = json.loads(line)
        if page_is_skipped(page):
            continue
        number = int(page["page_number"])
        # The normalized page text retains markup and exact quotation characters.
        text = str(page.get("cleaned_text") or page.get("raw_text") or "")
        if not text.strip():
            text = "\n".join(str(b.get("text", "")) for b in page.get("blocks", []))
        for index, parent in enumerate(_split(text, 900), 1):
            key = f"p{number:05d}-c{index:04d}"
            chunks.append(IndexedChunk(key, number, parent, "parent"))
            if len(parent) > 360:
                for child, part in enumerate(_split(parent, 360), 1):
                    chunks.append(IndexedChunk(f"{key}-s{child:03d}", number, part, "child"))
    if not chunks:
        raise RAGIndexError("normalized PDF contains no searchable text")
    return chunks


def published_upload_index(book_directory: Path) -> Path | None:
    book_directory = _artifact_path(book_directory)
    source = book_directory / "ocr" / "normalized" / "pages.jsonl"
    try:
        pointer = json.loads((book_directory / "rag" / "current.json").read_text())
        version = str(pointer["version"])
        if len(version) != 64 or any(c not in "0123456789abcdef" for c in version):
            return None
        if pointer["source_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
            return None
        target = book_directory / "rag" / version
        return target if (target / "index_manifest.json").is_file() else None
    except (OSError, KeyError, ValueError, TypeError):
        return None


def _remove_index_artifact(path: Path) -> None:
    # An interrupted build can leave a file or dangling link at the fingerprint
    # path. Never follow a link while repairing a private index artifact.
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _clean_abandoned_builds(root: Path) -> None:
    # Both builders hold the same cross-process lock before removing staging
    # directories. Published versions have hash names and are never removed here.
    for pattern in ("building-*", ".building-*"):
        for abandoned in root.glob(pattern):
            if abandoned.is_dir() and not abandoned.is_symlink():
                shutil.rmtree(abandoned)


def _valid_vectors(vectors: NDArray[np.float32]) -> bool:
    if not np.isfinite(vectors).all():
        return False
    with np.errstate(over="ignore", invalid="ignore"):
        norms = np.linalg.norm(vectors, axis=1)
    return bool(np.isfinite(norms).all() and np.all(norms > 0))


def _valid_native_index(target: Path, encoder: Any) -> bool:
    try:
        index = PersistentRAGIndex.load(target, encoder=encoder, reranker=None)
        return _valid_vectors(index.embeddings)
    except (RAGIndexError, OSError, ValueError, TypeError):
        return False


def _upload_fingerprint(raw: bytes, encoder: Any) -> str:
    model_identity = str(getattr(encoder, "model_path", "")).encode("utf-8")
    return hashlib.sha256(
        raw + model_identity + str(encoder.dimensions).encode("ascii") + _INDEX_VERSION
    ).hexdigest()


def _reuse_legacy_upload_index(book_directory: Path, raw: bytes, encoder: Any) -> Path | None:
    """Adopt an existing whole-book index under the canonical publication lock."""
    version = _upload_fingerprint(raw, encoder)
    target = book_directory / "rag" / version
    if target.is_dir() and not target.is_symlink() and _valid_native_index(target, encoder):
        return target
    legacy_root = book_directory / "rag-index"
    if not legacy_root.is_dir():
        return None
    with closing(sqlite3.connect(legacy_root / ".build.sqlite3", timeout=3600)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        legacy = legacy_root / version
        if not legacy.is_dir() or legacy.is_symlink() or not _valid_native_index(legacy, encoder):
            return None
        if target.exists() or target.is_symlink():
            _remove_index_artifact(target)
        staging = Path(tempfile.mkdtemp(prefix="building-", dir=target.parent))
        try:
            for name in ("chunks.json", "embeddings.npy", "index_manifest.json"):
                shutil.copy2(legacy / name, staging / name)
            manifest = staging / "index_manifest.json"
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
            metadata.update(
                source_sha256=hashlib.sha256(raw).hexdigest(),
                embedding_model=str(getattr(encoder, "model_path", "")),
                chunker_version=_INDEX_VERSION.decode("ascii"),
            )
            manifest.write_text(json.dumps(metadata), encoding="utf-8")
            if not _valid_native_index(staging, encoder):
                raise RAGIndexError("legacy uploaded-book index cannot be published")
            staging.rename(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return target


def build_upload_index(book_directory: Path, encoder: Any) -> Path:
    book_directory = _artifact_path(book_directory)
    root = book_directory / "rag"
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / ".build.sqlite3", timeout=3600)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        _clean_abandoned_builds(root)
        source = book_directory / "ocr" / "normalized" / "pages.jsonl"
        raw = source.read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        model = str(getattr(encoder, "model_path", "test"))
        version = hashlib.sha256(f"{source_hash}|{model}|{encoder.dimensions}|native-v2".encode()).hexdigest()
        root = book_directory / "rag"
        root.mkdir(parents=True, exist_ok=True)
        target = root / version
        if (target.exists() or target.is_symlink()) and (
            target.is_symlink() or not _valid_native_index(target, encoder)
        ):
            _remove_index_artifact(target)
        if not target.is_dir():
            reused = _reuse_legacy_upload_index(book_directory, raw, encoder)
            if reused is not None:
                target = reused
        if not target.is_dir():
            staging = Path(tempfile.mkdtemp(prefix="building-", dir=root))
            try:
                chunks = upload_chunks(source)
                embeddings = np.asarray(
                    encoder.encode_documents([c.text for c in chunks]), dtype=np.float32
                )
                if (
                    embeddings.shape != (len(chunks), encoder.dimensions)
                    or not _valid_vectors(embeddings)
                ):
                    raise RAGIndexError("invalid document embeddings")
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                np.save(staging / "embeddings.npy", embeddings / norms)
                (staging / "chunks.json").write_text(
                    json.dumps([asdict(c) for c in chunks], ensure_ascii=False), encoding="utf-8"
                )
                (staging / "index_manifest.json").write_text(
                    json.dumps(
                        {
                            "chunk_count": len(chunks),
                            "embedding_dimensions": encoder.dimensions,
                            "source_sha256": source_hash,
                            "embedding_model": model,
                            "chunker_version": "native-v2",
                        }
                    )
                )
                PersistentRAGIndex.load(staging, encoder=encoder, reranker=None)
                staging.rename(target)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        pointer = root / ("current-" + os.urandom(8).hex() + ".tmp")
        pointer.write_text(json.dumps({"version": target.name, "source_sha256": source_hash}))
        os.replace(pointer, root / "current.json")
        return target


def split_text(text: str, limit: int) -> list[str]:
    if limit <= 60:
        raise ValueError("chunk limit must exceed the overlap")
    result: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundary = max(text.rfind(mark, start + limit // 2, end) for mark in "。！？\n")
            if boundary >= 0:
                end = boundary + 1
        piece = text[start:end].strip()
        if piece:
            result.append(piece)
        if end == len(text):
            break
        start = end - 60
    return result


def _page_text(page: dict[str, Any]) -> str:
    if page_is_skipped(page):
        return ""
    for key in ("cleaned_text", "text", "raw_text"):
        value = page.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "\n".join(
        str(block.get("text", ""))
        for block in page.get("blocks", [])
        if isinstance(block, dict)
        and block.get("type", block.get("block_type")) not in {"footer", "page_number"}
    )


def _chunks(raw: bytes) -> list[IndexedChunk]:
    chunks: list[IndexedChunk] = []
    seen_pages: set[int] = set()
    try:
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            page = json.loads(line)
            number = int(page["page_number"])
            if number < 1 or number in seen_pages:
                raise ValueError("invalid or repeated page number")
            seen_pages.add(number)
            for index, text in enumerate(split_text(_page_text(page), 900), 1):
                key = f"p{number:03d}-c{index:02d}"
                chunks.append(IndexedChunk(key, number, text, "parent"))
                if len(text) > 360:
                    chunks.extend(
                        IndexedChunk(f"{key}-s{child:02d}", number, value, "child")
                        for child, value in enumerate(split_text(text, 360), 1)
                    )
    except (UnicodeError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise RAGIndexError("uploaded OCR pages are invalid") from error
    if not chunks:
        raise RAGIndexError("no usable text in uploaded book")
    return chunks


def ensure_upload_index(
    pages_path: Path,
    root: Path,
    *,
    encoder: DocumentEncoder,
    reranker: PairReranker,
) -> Path:
    pages_path = _artifact_path(pages_path)
    root = _artifact_path(root)
    raw = pages_path.read_bytes()
    fingerprint = _upload_fingerprint(raw, encoder)
    root.mkdir(parents=True, exist_ok=True)
    target = root / fingerprint
    # A separate SQLite file provides a cross-process lock on Linux, macOS and
    # Windows without holding the application's job database during GPU work.
    with closing(sqlite3.connect(root / ".build.sqlite3", timeout=3600)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        _clean_abandoned_builds(root)
        if target.exists() or target.is_symlink():
            try:
                if target.is_symlink():
                    raise RAGIndexError("uploaded-book index must be a local immutable directory")
                index = PersistentRAGIndex.load(target, encoder=encoder, reranker=reranker)
                if not _valid_vectors(index.embeddings):
                    raise RAGIndexError("invalid uploaded-book embeddings")
                return target
            except (RAGIndexError, OSError, ValueError):
                # Failed/corrupt artifacts must be repairable by the retry path.
                _remove_index_artifact(target)
        chunks = _chunks(raw)
        staging = Path(tempfile.mkdtemp(prefix=".building-", dir=root))
        try:
            vectors = np.asarray(encoder.encode_documents([chunk.text for chunk in chunks]), dtype=np.float32)
            if (
                vectors.shape != (len(chunks), encoder.dimensions)
                or not _valid_vectors(vectors)
            ):
                raise RAGIndexError("invalid uploaded-book embeddings")
            np.save(staging / "embeddings.npy", vectors)
            (staging / "chunks.json").write_text(
                json.dumps([asdict(chunk) for chunk in chunks], ensure_ascii=False), encoding="utf-8"
            )
            (staging / "index_manifest.json").write_text(
                json.dumps(
                    {
                        "chunk_count": len(chunks),
                        "embedding_dimensions": encoder.dimensions,
                        "source_fingerprint": fingerprint,
                    }
                ),
                encoding="utf-8",
            )
            PersistentRAGIndex.load(staging, encoder=encoder, reranker=reranker)
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return target
