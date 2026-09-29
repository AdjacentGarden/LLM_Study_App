import importlib.util
import json
from pathlib import Path

import pytest

from adaptive_learning.config import get_settings

spec = importlib.util.spec_from_file_location(
    "import_demo_biology", Path(__file__).parents[1] / "scripts/import_demo_biology.py"
)
assert spec and spec.loader
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def test_import_keeps_missing_source_separate_and_excludes_unverified_diagnostics():
    pages = importer.normalize_pages([
        {"page_idx": 10, "type": "text", "text": "染色体只复制一次，而细胞分裂两次。"},
    ])
    source = {
        "chapters": [
            {"chapter_id": "c1", "page_start": 0, "page_end": 0,
             "parent_id": None, "level": 1, "source_title": "第一章"},
            {"chapter_id": "c1s1", "page_start": 0, "page_end": 0,
             "parent_id": "c1", "level": 2, "source_title": "第一节"},
            {"chapter_id": "c2", "page_start": 10, "page_end": 35,
             "parent_id": None, "level": 1, "source_title": "第二章"},
            {"chapter_id": "c2s1", "page_start": 11, "page_end": 21,
             "parent_id": "c2", "level": 2, "source_title": "减数分裂"},
        ], "lessons": [], "flashcards": [], "quiz": [],
    }
    base = {"prompt": "复制几次？", "choices": ["一次", "两次"], "answer": "一次",
            "concept": "复制次数", "source_quote": "染色体只复制一次，而细胞分裂两次。"}
    source["quiz"] = [
        {**base, "question_id": "verified", "chapter_id": "c2s1", "page_start": 11},
        {**base, "question_id": "supplement", "chapter_id": "c1s1", "page_start": 0},
        {**base, "question_id": "wrong-page", "chapter_id": "c2s1", "page_start": 20},
    ]
    structure = importer.build_structure(source, pages)
    assert structure.chapters[0].knowledge_points == []
    assert structure.chapters[1].start_page == 0
    assert "原书正文缺失" not in structure.chapters[1].title
    assert structure.chapters[2].knowledge_points == ["复制次数"]
    assert structure.chapters[3].parent_id == "c2"
    bank = importer.diagnostic_bank(source, structure, pages)
    assert [x.item_id for x in bank] == ["demo-diagnostic-verified"]
    assert bank[0].chapter_id == "c2"
    assert bank[0].correct_option_ids == ["0"]


def test_normalized_pages_do_not_turn_image_paths_into_source_evidence():
    pages = importer.normalize_pages([
        {"page_idx": 1, "type": "image", "img_path": "secret/source.jpg", "image_caption": ["图 1 染色体"]}
    ])
    assert len(pages) == 125
    assert pages[1]["raw_text"] == "图 1 染色体"
    assert "secret" not in json.dumps(pages)
    assert pages[9]["printed_page_number"] == "15"
    with pytest.raises(ValueError):
        importer.normalize_pages([{"page_idx": 125, "type": "text", "text": "wrong"}])


def test_import_registry_preserves_configured_books_and_rejects_path_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLISHED_BOOK_IDS", "original-book")
    registry = tmp_path / "imported_books.json"
    registry.write_text('["book_biology_2", "original-book"]')
    get_settings.cache_clear()
    try:
        assert get_settings().published_book_ids == ("original-book", "book_biology_2")
        registry.write_text('["../other-book"]')
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="valid book IDs"):
            get_settings()
    finally:
        get_settings.cache_clear()
