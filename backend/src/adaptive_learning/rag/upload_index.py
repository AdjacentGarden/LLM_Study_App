"""Durable indexes for private uploads, using the application's resident models.

Publish only a completely validated index. Content-addressed directories make
OCR retries safe and leave readers of the previous version undisturbed.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .index import IndexedChunk, PersistentRAGIndex, RAGIndexError


def split_text(text: str, limit: int) -> list[str]:
    result = []
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
        start = max(start + 1, end - 60)
    return result


def page_text(page: dict) -> str:
    return str(page.get("cleaned_text") or page.get("text") or page.get("raw_text") or
               "\n".join(str(b.get("text", "")) for b in page.get("blocks", [])
                         if b.get("type", b.get("block_type")) not in {"footer", "page_number"}))


def ensure_upload_index(pages_path: Path, root: Path, *, encoder, reranker) -> Path:
    raw = pages_path.read_bytes()
    fingerprint = hashlib.sha256(raw + str(getattr(encoder, "model_path", "")).encode()
                                 + b"upload-index-v1").hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    target = root / fingerprint
    # flock also protects multiple workers/processes; a crash releases the lock.
    with (root / ".build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists():
            PersistentRAGIndex.load(target, encoder=encoder, reranker=reranker)
            return target
        chunks = []
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            page = json.loads(line)
            number = int(page["page_number"])
            for i, text in enumerate(split_text(page_text(page), 900), 1):
                key = f"p{number:03d}-c{i:02d}"
                chunks.append(IndexedChunk(key, number, text, "parent"))
                if len(text) > 360:
                    chunks.extend(IndexedChunk(f"{key}-s{j:02d}", number, child, "child")
                                  for j, child in enumerate(split_text(text, 360), 1))
        if not chunks:
            raise RAGIndexError("no usable text in uploaded book")
        staging = Path(tempfile.mkdtemp(prefix=".building-", dir=root))
        try:
            vectors = encoder.encode_documents([c.text for c in chunks])
            if vectors.shape != (len(chunks), encoder.dimensions) or not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors, axis=1) <= 0):
                raise RAGIndexError("invalid uploaded-book embeddings")
            np.save(staging / "embeddings.npy", vectors)
            (staging / "chunks.json").write_text(json.dumps([asdict(c) for c in chunks], ensure_ascii=False))
            (staging / "index_manifest.json").write_text(json.dumps({
                "chunk_count": len(chunks), "embedding_dimensions": encoder.dimensions,
                "source_fingerprint": fingerprint,
            }))
            PersistentRAGIndex.load(staging, encoder=encoder, reranker=reranker)
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return target
