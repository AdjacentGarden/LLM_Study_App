from __future__ import annotations

import json

from ..llm.client import LLMError, OpenAICompatibleClient
from .models import PageExtraction, QualityBand
from .quality import evaluate_text_quality, quality_band

_CLEANING_SYSTEM = """你是教材 OCR 校对器。只修复扫描、OCR、断行、标点和阅读顺序错误，禁止补写原文没有的信息。
必须遵守：
1. 保留原意、术语、数字、公式占位符和页码边界。
2. 上下页仅用于判断断句和跨页连接，不能把邻页内容复制到当前页。
3. 无法确认的内容保留原片段并加入 unresolved_fragments，不能猜测。
4. 删除重复页眉页脚时必须写入 edit_ledger。
5. 返回 JSON：{"cleaned_text":"...","edit_ledger":[{"type":"...","before":"...","after":"...","reason":"..."}],"unresolved_fragments":["..."]}。
"""


class LLMTextCleaner:
    def __init__(
        self,
        client: OpenAICompatibleClient,
        *,
        accept_threshold: float,
        review_threshold: float,
    ) -> None:
        self.client = client
        self.accept_threshold = accept_threshold
        self.review_threshold = review_threshold

    def clean_pages(self, pages: list[PageExtraction]) -> list[PageExtraction]:
        for index, page in enumerate(pages):
            previous = pages[index - 1].raw_text[-800:] if index else ""
            following = pages[index + 1].raw_text[:800] if index + 1 < len(pages) else ""
            request = json.dumps(
                {
                    "page_number": page.page_number,
                    "previous_page_tail": previous,
                    "raw_text": page.raw_text,
                    "next_page_head": following,
                    "quality_signals": page.quality.model_dump(),
                },
                ensure_ascii=False,
            )
            try:
                result = self.client.structured(system=_CLEANING_SYSTEM, user=request)
                cleaned = str(result.get("cleaned_text", "")).strip()
                if not cleaned:
                    continue
                page.cleaned_text = cleaned
                page.edit_ledger = list(result.get("edit_ledger", []))[:100]
                page.unresolved_fragments = [
                    str(item) for item in result.get("unresolved_fragments", [])
                ][:50]
                page.quality = evaluate_text_quality(
                    cleaned,
                    expected_coverage=page.quality.coverage_score,
                    secondary_candidate=page.raw_text,
                )
                page.band = quality_band(
                    page.quality.overall_score,
                    accept=self.accept_threshold,
                    review=self.review_threshold,
                )
                if page.unresolved_fragments and page.band == QualityBand.ACCEPTED:
                    page.band = QualityBand.REVIEW
            except LLMError:
                page.cleaned_text = page.raw_text
        return pages
