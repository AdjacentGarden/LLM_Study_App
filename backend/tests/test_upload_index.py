from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from adaptive_learning.rag.index import PersistentRAGIndex, RAGIndexError
from adaptive_learning.rag.upload_index import ensure_upload_index


class Encoder:
    dimensions = 2

    def __init__(self) -> None:
        self.calls = 0
        self.invalid = False

    def encode_query(self, text: str) -> np.ndarray:
        return np.array([1, 0], dtype=np.float32)

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        vectors = np.tile(np.array([[1, 0]], dtype=np.float32), (len(texts), 1))
        if self.invalid:
            vectors[:] = np.nan
        return vectors


class Reranker:
    def score(self, query: str, texts: list[str]) -> list[float]:
        return [1.0] * len(texts)


def write_pages(path: Path, text: str) -> None:
    path.write_text(json.dumps({"page_number": 3, "cleaned_text": text}) + "\n", encoding="utf-8")


def test_concurrent_build_publishes_once_and_ocr_revision_uses_a_new_index(tmp_path: Path) -> None:
    source = tmp_path / "pages.jsonl"
    write_pages(source, "孟德尔遗传规律。" * 160)
    encoder, reranker = Encoder(), Reranker()

    def build(_: int) -> Path:
        return ensure_upload_index(source, tmp_path / "rag", encoder=encoder, reranker=reranker)

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(build, range(4)))
    assert len(set(paths)) == 1 and encoder.calls == 1
    first = PersistentRAGIndex.load(paths[0], encoder=encoder, reranker=reranker)
    assert {chunk.page_number for chunk in first.chunks} == {3}
    assert {chunk.granularity for chunk in first.chunks} == {"parent", "child"}

    write_pages(source, "新版本中的遗传概念。" * 80)
    revised = build(0)
    assert revised != paths[0] and encoder.calls == 2
    assert paths[0].is_dir()  # Existing readers retain the previous complete version.
    assert all("新版本" in chunk.text for chunk in PersistentRAGIndex.load(revised, encoder=encoder, reranker=reranker).chunks)


def test_invalid_vectors_never_publish_ready_index_and_retry_repairs_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "pages.jsonl"
    write_pages(source, "用于测试的教材正文。")
    root = tmp_path / "rag"
    encoder, reranker = Encoder(), Reranker()
    encoder.invalid = True
    with pytest.raises(RAGIndexError, match="invalid uploaded-book embeddings"):
        ensure_upload_index(source, root, encoder=encoder, reranker=reranker)
    assert not list(root.glob("*/index_manifest.json"))
    assert not list(root.glob(".building-*"))

    encoder.invalid = False
    target = ensure_upload_index(source, root, encoder=encoder, reranker=reranker)
    (target / "chunks.json").write_text("broken", encoding="utf-8")
    assert ensure_upload_index(source, root, encoder=encoder, reranker=reranker) == target
    assert encoder.calls == 3
    assert PersistentRAGIndex.load(target, encoder=encoder, reranker=reranker).chunks[0].text == "用于测试的教材正文。"


@pytest.mark.parametrize(
    "raw",
    ["", '{"page_number":0,"text":"正文"}', '{"page_number":1,"text":""}',
     '{"page_number":1,"text":"甲"}\n{"page_number":1,"text":"乙"}', "null", "malformed"],
)
def test_bad_ocr_cannot_create_a_ready_index(tmp_path: Path, raw: str) -> None:
    source = tmp_path / "pages.jsonl"
    source.write_text(raw, encoding="utf-8")
    encoder = Encoder()
    with pytest.raises(RAGIndexError):
        ensure_upload_index(source, tmp_path / "rag", encoder=encoder, reranker=Reranker())
    assert encoder.calls == 0
