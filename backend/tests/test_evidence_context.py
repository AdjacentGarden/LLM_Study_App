from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from adaptive_learning.assessment.item_generation import structure_fingerprint
from adaptive_learning.ingestion.chaptering import (
    ChapterReconstructor,
    _split_evidence,
    load_normalized_pages,
)
from adaptive_learning.ingestion.evidence_context import (
    contextualize_structure,
    expand_source_quote,
)
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote


def pages_from(tmp_path: Path, rows: list[dict]):
    path = tmp_path / "pages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return load_normalized_pages(path)


def text_page(*lines: str, layout: bool = True) -> dict:
    return {"page_number": 1, "text": "\n".join(lines), "blocks": [
        {"type": "text", "text": line, **({"bbox": [10, i * 25, 510, i * 25 + 20]} if layout else {})}
        for i, line in enumerate(lines)
    ]}


def structure(quote: SourceQuote) -> BookStructure:
    return BookStructure(title="教材", summary="摘要不作为原文", source_page_count=1,
        chapters=[ChapterDraft(chapter_id="chapter", order=1, title="章节", start_page=1, end_page=1,
            summary="不从标签猜补原文", knowledge_points=["标签不能提供缺失事实"], source_block_ids=[],
            evidence=[quote], knowledge_point_evidence={"标签不能提供缺失事实": [quote]})])


def real_pages(tmp_path: Path):
    fixture = json.loads((Path(__file__).parent / "fixtures/ocr_line_context.json").read_text())
    return pages_from(tmp_path, fixture["pages"]), [SourceQuote.model_validate(x) for x in fixture["anchors"]]


def test_recorded_ocr_context_restores_split_negation_and_next_line_sentence_tail(tmp_path: Path):
    pages, anchors = real_pages(tmp_path)
    page = next(page for page in pages if page.page_number == 95)
    negative = expand_source_quote(anchors[0], page)
    ending = expand_source_quote(anchors[1], page)
    assert "并不\n能创造新的基因" in negative.quote
    assert "育种进程\n缓慢，过程复杂。" in negative.quote
    assert "优良变异\n类型。" in ending.quote
    for old, expanded in [(anchors[0], negative), (anchors[1], ending)]:
        assert expanded.anchor_quote == old.quote
        assert old.quote in expanded.quote in page.raw_text
        assert expanded.page_number == old.page_number
        assert len(expanded.quote) <= 500
        assert expand_source_quote(expanded, page) == expanded


def test_recorded_figure_interleaving_is_not_reordered_or_joined_across_columns(tmp_path: Path):
    pages, anchors = real_pages(tmp_path)
    page = next(page for page in pages if page.page_number == 98)
    expanded = expand_source_quote(anchors[2], page)
    assert expanded.quote in page.raw_text
    assert "脱氧核糖和磷酸" in expanded.quote
    assert "图6-4DNA连接酶的连接作用" not in expanded.quote
    assert "CTTAA" not in expanded.quote
    assert "基因的运载体" not in expanded.quote


def test_repeated_anchor_never_selects_a_convenient_occurrence(tmp_path: Path):
    page = pages_from(tmp_path, [text_page("只在条件甲成立。", "相同短引", "条件乙不成立。", "相同短引")])[0]
    original = SourceQuote(page_number=1, quote="相同短引")
    assert expand_source_quote(original, page) == original


def test_column_change_is_a_context_barrier(tmp_path: Path):
    raw = text_page("左栏错误条件", "右栏真条件", "右栏结论。")
    raw["blocks"][0]["bbox"] = [10, 0, 210, 20]
    raw["blocks"][1]["bbox"] = [310, 0, 510, 20]
    raw["blocks"][2]["bbox"] = [310, 25, 510, 45]
    page = pages_from(tmp_path, [raw])[0]
    expanded = expand_source_quote(SourceQuote(page_number=1, quote="右栏真条件"), page)
    assert expanded.quote == "右栏真条件\n右栏结论。"
    assert "左栏" not in expanded.quote


def test_no_layout_does_not_guess_adjacency_between_blocks(tmp_path: Path):
    page = pages_from(tmp_path, [text_page("不应被拼接的前提", "短引", "不应被拼接的后文", layout=False)])[0]
    original = SourceQuote(page_number=1, quote="短引")
    assert expand_source_quote(original, page) == original


@pytest.mark.parametrize("fault", ["missing", "wrong-page", "empty", "duplicate-page"])
def test_missing_ambiguous_or_wrong_page_context_preserves_original_without_label_fill(tmp_path: Path, fault: str):
    page = pages_from(tmp_path, [text_page("真实前提", "真实结论。")])[0]
    original = SourceQuote(page_number=2 if fault == "wrong-page" else 1,
        quote="标签中的额外事实" if fault == "missing" else "真实结论。")
    book = structure(original)
    pages = [] if fault == "empty" else [page, page.model_copy(deep=True)] if fault == "duplicate-page" else [page]
    adapted = contextualize_structure(book, pages)
    assert adapted == book
    assert book.chapters[0].knowledge_points == ["标签不能提供缺失事实"]


def test_unbounded_sentence_cannot_create_oversized_or_truncated_context(tmp_path: Path):
    text = "前" * 300 + "唯一锚点" + "后" * 300 + "。"
    page = pages_from(tmp_path, [{"page_number": 1, "text": text, "blocks": [{"type": "text", "text": text}]}])[0]
    source = SourceQuote(page_number=1, quote="唯一锚点")
    assert expand_source_quote(source, page) == source
    with pytest.raises(ValidationError):
        SourceQuote(page_number=1, quote=text)
    with pytest.raises(ValidationError, match="literal part"):
        SourceQuote(page_number=1, quote="真实原文", anchor_quote="猜补事实")


def test_literal_spacing_and_line_breaks_are_preserved_by_source_units(tmp_path: Path):
    text = "前置  条件\n并不\n产生新的事实。"
    page = pages_from(tmp_path, [{"page_number": 1, "text": text, "blocks": [{"type": "text", "text": text}]}])[0]
    assert _split_evidence(text) == [text]
    payload, sources = ChapterReconstructor()._evidence_units([page])
    assert [row["text"] for row in payload] == [text]
    assert [quote.quote for quote in sources.values()] == [text]


def test_new_structure_units_and_read_projection_are_idempotent_and_do_not_mutate_inputs(tmp_path: Path):
    page = pages_from(tmp_path, [text_page("原始前提并不", "产生新的事实，后续", "结果仍需要验证。")])[0]
    payload, sources = ChapterReconstructor()._evidence_units([page])
    assert len(payload) == len({row["text"] for row in payload})
    assert all(row["text"] in page.raw_text for row in payload)
    book = structure(next(iter(sources.values())))
    before = book.model_dump_json()
    assert contextualize_structure(book, [page]) == book
    assert book.model_dump_json() == before


def test_old_stored_json_is_unchanged_while_current_evidence_and_fingerprint_refresh(tmp_path: Path):
    repository = SQLiteOCRJobRepository(tmp_path / "state/ocr_jobs.sqlite3")
    original_pdf = tmp_path / "original.pdf"
    original_pdf.write_bytes(b"%PDF-original")
    repository.register_book(book_id="book", original_name="教材.pdf", file_path=original_pdf,
        source_sha256="original-source-hash")
    output = tmp_path / "ocr"
    repository.enqueue(book_id="book", output_dir=output, max_attempts=1)
    book = structure(SourceQuote(page_number=1, quote="能创造新的基因，后续"))
    repository.save_structure("book", book)
    legacy_json = book.model_dump_json(exclude_none=True)
    with repository._connect() as db:
        db.execute("UPDATE book_structures SET structure_json=? WHERE book_id=?", (legacy_json, "book"))
    rows = [text_page("这个过程并不", "能创造新的基因，后续", "处理仍有其他限制。")]
    pages = pages_from(output / "normalized", rows)
    actual = repository.get_structure("book")
    expected = contextualize_structure(book, pages)
    assert actual == expected
    assert actual != book
    assert structure_fingerprint(actual) != structure_fingerprint(book)
    assert repository.source_fingerprint("book") == "original-source-hash"
    assert original_pdf.read_bytes() == b"%PDF-original"
    assert SQLiteOCRJobRepository(repository.database_path).get_structure("book") == actual
    with repository._connect() as db:
        stored = db.execute("SELECT structure_json FROM book_structures WHERE book_id=?", ("book",)).fetchone()
    assert stored[0] == legacy_json
    assert "anchor_quote" not in stored[0]


@pytest.mark.parametrize("payload", [None, "", "not-json\n"])
def test_missing_or_invalid_legacy_ocr_does_not_rewrite_or_invent_evidence(tmp_path: Path, payload):
    from adaptive_learning.ingestion.evidence_context import contextualize_stored_structure

    book = structure(SourceQuote(page_number=1, quote="保留原引"))
    if payload is not None:
        path = tmp_path / "normalized/pages.jsonl"
        path.parent.mkdir()
        path.write_text(payload)
    assert contextualize_stored_structure(book, tmp_path) == book
