"""Build complete private-upload indexes with the application's resident encoder."""

from __future__ import annotations

import hashlib
import json
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

from .index import IndexedChunk, PairReranker, PersistentRAGIndex, QueryEncoder, RAGIndexError

_INDEX_VERSION = b"upload-index-v2"


class DocumentEncoder(QueryEncoder, Protocol):
    def encode_documents(self, texts: Sequence[str]) -> NDArray[np.float32]: ...


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
    raw = pages_path.read_bytes()
    model_identity = str(getattr(encoder, "model_path", "")).encode("utf-8")
    fingerprint = hashlib.sha256(
        raw + model_identity + str(encoder.dimensions).encode("ascii") + _INDEX_VERSION
    ).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    target = root / fingerprint
    # A separate SQLite file provides a cross-process lock on Linux, macOS and
    # Windows without holding the application's job database during GPU work.
    with closing(sqlite3.connect(root / ".build.sqlite3", timeout=3600)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        for abandoned in root.glob(".building-*"):
            if abandoned.is_dir() and not abandoned.is_symlink():
                shutil.rmtree(abandoned)
        if target.exists():
            try:
                index = PersistentRAGIndex.load(target, encoder=encoder, reranker=reranker)
                if not np.isfinite(index.embeddings).all() or np.any(
                    np.linalg.norm(index.embeddings, axis=1) <= 0
                ):
                    raise RAGIndexError("invalid uploaded-book embeddings")
                return target
            except (RAGIndexError, OSError, ValueError):
                # Failed/corrupt artifacts must be repairable by the retry path.
                shutil.rmtree(target)
        chunks = _chunks(raw)
        staging = Path(tempfile.mkdtemp(prefix=".building-", dir=root))
        try:
            vectors = np.asarray(encoder.encode_documents([chunk.text for chunk in chunks]), dtype=np.float32)
            if (
                vectors.shape != (len(chunks), encoder.dimensions)
                or not np.isfinite(vectors).all()
                or np.any(np.linalg.norm(vectors, axis=1) <= 0)
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
