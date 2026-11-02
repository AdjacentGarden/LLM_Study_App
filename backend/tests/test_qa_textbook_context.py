"""Routing receives bounded metadata from the authorized resource's structure."""
from __future__ import annotations

import json

import pytest
from native_harness import structure
from test_native_api import native  # noqa: F401
from test_qa_reading_context import _capture_services, _workspace

from adaptive_learning import native_catalog
from adaptive_learning.rag.textbook_context import textbook_context


def test_context_keeps_book_summary_current_concepts_and_other_chapters():
    book = structure()
    first = book.chapters[0].model_copy(update={
        "chapter_id": "vectors", "title": "平面向量", "summary": "理解大小和方向",
        "knowledge_points": ["向量的概念", "相等向量", "数量积"],
    })
    other = first.model_copy(update={"chapter_id": "complex", "title": "复数", "knowledge_points": ["虚数单位"]})
    book = book.model_copy(update={"title": "数学必修", "summary": "学习向量和复数", "chapters": [first, other]})
    context = textbook_context(book, "vectors", subject="数学")
    assert context["textbook"]["title"] == "数学必修"
    assert context["textbook"]["subject"] == "数学"
    assert context["chapter"]["knowledge_points"] == ["向量的概念", "相等向量", "数量积"]
    assert any(c["title"] == "复数" for c in context["textbook"]["outline"])
    assert "knowledge_point_evidence" not in json.dumps(context)
    assert "source_block_ids" not in json.dumps(context)


def test_large_structure_stays_small_and_retains_selected_chapter():
    book = structure()
    chapters = [book.chapters[0].model_copy(update={
        "chapter_id": f"c{i}", "title": f"第{i}章" + "长标题" * 200,
        "summary": "长摘要" * 1000, "knowledge_points": ["长知识点" * 100] * 100,
    }) for i in range(100)]
    book = book.model_copy(update={"chapters": chapters, "summary": "教材摘要" * 1000})
    context = textbook_context(book, "c99")
    assert len(json.dumps(context, ensure_ascii=False)) < 6000
    assert context["chapter"]["id"] == "c99"
    assert context["textbook"]["outline_truncated"]
    assert any(c["title"].startswith("第99章") for c in context["textbook"]["outline"])


@pytest.mark.parametrize("chapter_id", [None, "c2s1"])
def test_course_api_supplies_real_metadata_even_without_a_selected_chapter(native, monkeypatch, chapter_id):  # noqa: F811
    owner, other, _ = native
    calls = _capture_services(monkeypatch)
    _, path = _workspace(owner)
    body = {"question": "解释一下这个概念", "textbook": {"title": "客户端伪造标题"}}
    if chapter_id:
        body["chapter_id"] = chapter_id
    response = owner.post(path + "/qa", json=body)
    assert response.status_code == 200, response.text
    context = calls[-1]["conversation_context"]
    assert context["textbook"]["title"] == native_catalog.structure().title
    assert context["textbook"]["outline"]
    assert "客户端伪造标题" not in json.dumps(context, ensure_ascii=False)
    if chapter_id:
        assert context["chapter"]["id"] == chapter_id
        assert "knowledge_points" in context["chapter"] and "summary" in context["chapter"]
    else:
        assert "chapter" not in context
    before = len(calls)
    assert other.post(path + "/qa", json=body).status_code == 404
    assert len(calls) == before
