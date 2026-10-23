from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from adaptive_learning.rag.index import PersistentRAGIndex, RAGIndexError


class FakeEncoder:
    dimensions = 3

    def encode_query(self, text: str) -> np.ndarray:
        if text == "bad-vector":
            return np.array([np.nan, 0, 0], dtype=np.float32)
        return np.array([1, 0, 0], dtype=np.float32)


class FakeReranker:
    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if query == "bad-reranker":
            return [float("nan") for _ in texts]
        return [3.0 if query.replace("?", "") in text else -1.0 for text in texts]


def _write_index(path: Path, *, duplicate: bool = False) -> None:
    path.mkdir()
    chunks = [
        {
            "chunk_id": "p001-c01",
            "page_number": 1,
            "text": "减数分裂时染色体只复制一次，细胞分裂两次。",
            "granularity": "parent",
        },
        {
            "chunk_id": "p001-c01" if duplicate else "p001-c01-s01",
            "page_number": 1,
            "text": "染色体只复制一次。",
            "granularity": "child",
        },
        {
            "chunk_id": "p002-c01",
            "page_number": 2,
            "text": "成熟生殖细胞中的染色体数目减少一半。",
            "granularity": "parent",
        },
        {
            "chunk_id": "p003-c01",
            "page_number": 3,
            "text": "AUG GAA 对应甲硫氨酸和谷氨酸。",
            "granularity": "parent",
        },
    ]
    (path / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    (path / "index_manifest.json").write_text(
        json.dumps({"chunk_count": 4, "embedding_dimensions": 3}),
        encoding="utf-8",
    )
    embeddings = np.array(
        [[1, 0, 0], [0.99, 0.01, 0], [0.9, 0.1, 0], [0, 1, 0]],
        dtype=np.float32,
    )
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    np.save(path / "embeddings.npy", embeddings)


def _load(path: Path) -> PersistentRAGIndex:
    return PersistentRAGIndex.load(path, encoder=FakeEncoder(), reranker=FakeReranker())


def test_index_load_and_search_preserve_page_coverage(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    _write_index(index_dir)
    index = _load(index_dir)

    result = index.search("减数分裂?", top_pages=3, max_evidence=3)

    assert result.score == 3.0
    assert len(result.pages) == 3
    assert len({item.page_number for item in result.evidence}) == 3
    assert result.evidence[0].page_number == result.pages[0]


def test_exact_identifiers_keep_table_evidence_available(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    _write_index(index_dir)
    index = _load(index_dir)

    result = index.search("AUG GAA", top_pages=3, max_evidence=3)

    assert any(item.page_number == 3 for item in result.evidence)


def test_index_rejects_duplicate_chunk_ids(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    _write_index(index_dir, duplicate=True)

    with pytest.raises(RAGIndexError, match="duplicate"):
        _load(index_dir)


def test_index_rejects_missing_files(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    index_dir.mkdir()

    with pytest.raises(RAGIndexError, match="missing"):
        _load(index_dir)


def test_search_rejects_invalid_encoder_vector(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    _write_index(index_dir)
    index = _load(index_dir)

    with pytest.raises(RAGIndexError, match="invalid vector"):
        index.search("bad-vector")


def test_search_rejects_invalid_reranker_output(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    _write_index(index_dir)
    index = _load(index_dir)

    with pytest.raises(RAGIndexError, match="reranker"):
        index.search("bad-reranker")


@pytest.mark.parametrize("question", ["", "  "])
def test_search_rejects_empty_question(tmp_path: Path, question: str) -> None:
    index_dir = tmp_path / "index"
    _write_index(index_dir)
    index = _load(index_dir)

    with pytest.raises(ValueError, match="empty"):
        index.search(question)
