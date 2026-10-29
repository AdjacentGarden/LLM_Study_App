from __future__ import annotations

import json
from typing import Any, cast

from test_chaptering import page

from adaptive_learning.ingestion.chaptering import ChapterReconstructor
from adaptive_learning.llm.client import OpenAICompatibleClient


def test_long_chapter_keeps_more_than_twenty_distinct_learning_points() -> None:
    facts = [f"第{index}个独立概念仅在条件{index}成立时适用。" for index in range(25)]
    pages = [page(index + 1, ("text", fact, None)) for index, fact in enumerate(facts)]

    class Client:
        def structured(self, *, system: str, user: str, **kwargs: Any) -> dict[str, Any]:
            payload = json.loads(user)
            if "evidence" not in payload:
                return {"summary": "完整学习本章各个有条件的概念。",
                        "chapter_ids": payload["required_chapter_ids"]}
            evidence = payload["evidence"]
            return {"summary": "完整学习本章各个有条件的概念。",
                    "evidence_ids": [evidence[0]["id"]],
                    "knowledge_points": [{"text": text, "evidence_ids": [row["id"]]}
                                         for text, row in zip(facts, evidence, strict=True)]}

    result = ChapterReconstructor(cast(OpenAICompatibleClient, Client())).reconstruct_book(pages, "测试教材")
    assert result.chapters[0].knowledge_points == facts
    assert len(result.chapters[0].knowledge_point_evidence) == 25


def test_page_sampling_keeps_middle_sources_and_fills_remaining_budget() -> None:
    texts = [f"段落{index}：" + (f"条件{index}成立，" * 25) + "因此适用本段结论。" for index in range(15)]
    document = page(1, *(("text", text, None) for text in texts))
    payload, sources = ChapterReconstructor(max_chapter_characters=2000)._evidence_units([document])
    joined = "\n".join(str(row["text"]) for row in payload)
    assert "段落0：" in joined and "段落14：" in joined
    assert any(f"段落{index}：" in joined for index in range(5, 10))
    assert sum(len(source.quote) for source in sources.values()) <= 2000
    assert all(source.quote in document.raw_text for source in sources.values())


def test_ordinary_textbook_chapter_keeps_late_page_under_default_budget() -> None:
    texts = [f"段落{index}的条件与结论。" * 30 for index in range(70)]
    pages = [page(index + 1, ("text", text, None)) for index, text in enumerate(texts)]
    payload, _ = ChapterReconstructor()._evidence_units(pages)
    assert {row["page_number"] for row in payload} == set(range(1, 71))
