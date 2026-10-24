"""Native publication and whole-book index builders share safe artifact handling."""
import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from test_upload_index import Reranker

from adaptive_learning.rag.index import PersistentRAGIndex, RAGIndexError
from adaptive_learning.rag.upload_index import (
    build_upload_index,
    ensure_upload_index,
    published_upload_index,
)


class Encoder:
    model_path = "fixture-encoder"

    def __init__(self, dimensions=2, fault=None):
        self.dimensions = dimensions
        self.fault = fault
        self.calls = 0

    def encode_documents(self, texts):
        self.calls += 1
        vectors = np.ones((len(texts), self.dimensions), dtype=np.float32)
        if self.fault == "nan":
            vectors[:] = np.nan
        elif self.fault == "zero":
            vectors[:] = 0
        elif self.fault == "dimensions":
            vectors = vectors[:, :0]
        elif self.fault == "overflow":
            vectors[:] = np.finfo(np.float32).max
        return vectors

    def encode_query(self, text):
        return np.ones(self.dimensions, dtype=np.float32)


@pytest.fixture
def book(tmp_path):
    pages = tmp_path / "ocr" / "normalized" / "pages.jsonl"
    pages.parent.mkdir(parents=True)
    pages.write_text(json.dumps({"page_number": 1, "cleaned_text": "完整教材的原文内容。" * 120}))
    return tmp_path, pages


def test_native_concurrent_build_is_serialized_and_cached(book):
    directory, _ = book
    encoder = Encoder()
    with ThreadPoolExecutor(max_workers=4) as pool:
        targets = list(pool.map(lambda _: build_upload_index(directory, encoder), range(4)))
    assert len(set(targets)) == 1 and encoder.calls == 1
    assert published_upload_index(directory) == targets[0]
    assert not list((directory / "rag").glob("building-*"))


@pytest.mark.parametrize("corruption", ["chunks", "nan", "dimensions", "npy", "file", "link"])
def test_native_existing_corruption_is_rebuilt_without_following_links(book, corruption):
    directory, _ = book
    encoder = Encoder()
    target = build_upload_index(directory, encoder)
    outside = directory / "outside"
    if corruption == "chunks":
        (target / "chunks.json").write_text("broken")
    elif corruption == "nan":
        data = np.load(target / "embeddings.npy")
        data[:] = np.nan
        np.save(target / "embeddings.npy", data)
    elif corruption == "dimensions":
        data = np.load(target / "embeddings.npy")
        np.save(target / "embeddings.npy", data[:, :1])
    elif corruption == "npy":
        (target / "embeddings.npy").write_bytes(b"broken")
    else:
        shutil.rmtree(target)
        if corruption == "file":
            target.write_bytes(b"interrupted artifact")
        else:
            outside.mkdir()
            (outside / "keep.txt").write_text("keep")
            target.symlink_to(outside, target_is_directory=True)
    assert build_upload_index(directory, encoder) == target
    assert not target.is_symlink()
    assert encoder.calls == 2
    assert published_upload_index(directory) == target
    assert PersistentRAGIndex.load(target, encoder=encoder, reranker=None).chunks
    if corruption == "link":
        assert (outside / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize("fault", ["nan", "zero", "dimensions", "overflow"])
def test_native_invalid_encoder_cannot_publish_pointer(book, fault):
    directory, _ = book
    with pytest.raises(RAGIndexError):
        build_upload_index(directory, Encoder(fault=fault))
    assert published_upload_index(directory) is None
    assert not (directory / "rag" / "current.json").exists()
    assert not list((directory / "rag").glob("building-*"))


def test_dimension_change_creates_separate_native_version_without_deleting_previous(book):
    directory, _ = book
    first_encoder, second_encoder = Encoder(2), Encoder(3)
    first = build_upload_index(directory, first_encoder)
    second = build_upload_index(directory, second_encoder)
    assert first != second
    assert published_upload_index(directory) == second
    assert PersistentRAGIndex.load(first, encoder=first_encoder, reranker=None).embeddings.shape[1] == 2
    assert PersistentRAGIndex.load(second, encoder=second_encoder, reranker=None).embeddings.shape[1] == 3


def test_whole_book_builder_preserves_legacy_native_pointer_until_source_changes(book):
    directory, pages = book
    encoder = Encoder()
    target = build_upload_index(directory, encoder)
    pointer = directory / "rag" / "current.json"
    original_pointer = pointer.read_bytes()
    # Existing native-v1 pointers remain valid; merely adding another builder
    # must not invalidate a published version with unchanged OCR content.
    manifest = target / "index_manifest.json"
    metadata = json.loads(manifest.read_text())
    metadata["chunker_version"] = "native-v1"
    manifest.write_text(json.dumps(metadata))
    assert published_upload_index(directory) == target
    whole = ensure_upload_index(pages, directory / "rag", encoder=encoder, reranker=Reranker())
    assert whole != target and target.is_dir()
    assert pointer.read_bytes() == original_pointer
    assert published_upload_index(directory) == target
    pages.write_text(pages.read_text() + "\n")
    assert published_upload_index(directory) is None


def test_both_builders_clean_only_abandoned_staging_under_shared_lock(book):
    directory, pages = book
    root = directory / "rag"
    root.mkdir()
    for name in ("building-old", ".building-old"):
        (root / name).mkdir()
    encoder = Encoder()
    target = build_upload_index(directory, encoder)
    assert not (root / "building-old").exists()
    assert not (root / ".building-old").exists()
    (root / "building-second").mkdir()
    (root / ".building-second").mkdir()
    ensure_upload_index(pages, root, encoder=encoder, reranker=Reranker())
    assert not (root / "building-second").exists()
    assert not (root / ".building-second").exists()
    assert target.is_dir()
    saved = json.loads((root / "current.json").read_text())
    assert saved["source_sha256"] == hashlib.sha256(pages.read_bytes()).hexdigest()
