"""Exercise real long artifact paths as well as Windows drive/UNC spelling."""
import json
import os

import pytest
from test_upload_index import Reranker
from test_upload_index_native import Encoder

from adaptive_learning.rag.index import PersistentRAGIndex
from adaptive_learning.rag.upload_index import (
    _artifact_path,
    _windows_extended_path,
    build_upload_index,
    ensure_upload_index,
    published_upload_index,
)


@pytest.mark.parametrize("source,expected", [
    (r"C:\books\old\..\current", r"\\?\C:\books\current"),
    (r"\\server\share\books", r"\\?\UNC\server\share\books"),
    (r"\\?\C:\books", r"\\?\C:\books"),
    (r"\\?\UNC\server\share\books", r"\\?\UNC\server\share\books"),
])
def test_windows_extended_path_preserves_drive_unc_and_existing_prefix(source, expected):
    assert _windows_extended_path(source) == expected
    assert _windows_extended_path(expected) == expected


def test_artifact_path_keeps_host_path_identity_off_windows(tmp_path):
    if os.name == "nt":
        assert str(_artifact_path(tmp_path)).startswith("\\\\?\\")
    else:
        assert _artifact_path(tmp_path) is tmp_path


@pytest.mark.parametrize("builder", ["native", "whole_book"])
def test_real_long_path_build_cache_and_corruption_repair(tmp_path, builder):
    directory = tmp_path / ("corpus-" + "a" * 64)
    while len(str(directory)) < 320:
        directory /= "nested-" + "b" * 48
    # Set up through the same prefix helper so the test does not depend on a
    # machine-wide LongPathsEnabled policy before it reaches the actual builder.
    source = directory / "ocr" / "normalized" / "pages.jsonl"
    io_source = _artifact_path(source)
    io_source.parent.mkdir(parents=True)
    io_source.write_text(json.dumps({
        "page_number": 1, "cleaned_text": "Complete textbook evidence. " * 45,
    }), encoding="utf-8")
    encoder = Encoder()
    reranker = Reranker()

    def build():
        if builder == "native":
            return build_upload_index(directory, encoder)
        return ensure_upload_index(
            source, directory / "rag-index", encoder=encoder, reranker=reranker,
        )

    target = build()
    assert len(str(target)) > 260
    if os.name == "nt":
        assert str(target).startswith("\\\\?\\")
    assert PersistentRAGIndex.load(target, encoder=encoder, reranker=reranker).chunks
    assert build() == target and encoder.calls == 1
    (target / "chunks.json").write_text("broken", encoding="utf-8")
    assert build() == target and encoder.calls == 2
    assert PersistentRAGIndex.load(target, encoder=encoder, reranker=reranker).chunks
    assert not list(target.parent.glob("*building-*"))
    if builder == "native":
        assert published_upload_index(directory) == target
        assert published_upload_index(_artifact_path(directory)) == target
