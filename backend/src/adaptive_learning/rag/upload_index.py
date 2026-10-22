"""Immutable, fingerprinted indexes for private uploads; atomic publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from .index import IndexedChunk, RAGIndexError

_build_lock = threading.Lock()


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


def build_upload_index(book_directory: Path, encoder: Any) -> Path:
    with _build_lock:
        source = book_directory / "ocr" / "normalized" / "pages.jsonl"
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        model = str(getattr(encoder, "model_path", "test"))
        version = hashlib.sha256(f"{source_hash}|{model}|native-v1".encode()).hexdigest()
        root = book_directory / "rag"
        root.mkdir(exist_ok=True)
        target = root / version
        if not target.is_dir():
            staging = Path(tempfile.mkdtemp(prefix="building-", dir=root))
            try:
                chunks = upload_chunks(source)
                embeddings = np.asarray(
                    encoder.encode_documents([c.text for c in chunks]), dtype=np.float32
                )
                if (
                    embeddings.shape != (len(chunks), encoder.dimensions)
                    or not np.isfinite(embeddings).all()
                ):
                    raise RAGIndexError("invalid document embeddings")
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                if np.any(norms <= 0):
                    raise RAGIndexError("empty document embeddings")
                np.save(staging / "embeddings.npy", embeddings / norms)
                (staging / "chunks.json").write_text(
                    json.dumps([asdict(c) for c in chunks], ensure_ascii=False)
                )
                (staging / "index_manifest.json").write_text(
                    json.dumps(
                        {
                            "chunk_count": len(chunks),
                            "embedding_dimensions": encoder.dimensions,
                            "source_sha256": source_hash,
                            "embedding_model": model,
                            "chunker_version": "native-v1",
                        }
                    )
                )
                try:
                    staging.rename(target)
                except FileExistsError:
                    pass
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        pointer = root / ("current-" + os.urandom(8).hex() + ".tmp")
        pointer.write_text(json.dumps({"version": version, "source_sha256": source_hash}))
        os.replace(pointer, root / "current.json")
        return target
