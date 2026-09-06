from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .models import ExtractionMethod, PageExtraction, PageKind, TextBlock
from .quality import evaluate_text_quality, quality_band


class MinerUOutputNormalizer:
    """Converts MinerU content_list.json into stable page/block contracts."""

    def __init__(self, *, accept_threshold: float, review_threshold: float) -> None:
        self.accept_threshold = accept_threshold
        self.review_threshold = review_threshold

    def load(
        self, content_list_path: Path, page_kinds: dict[int, PageKind]
    ) -> list[PageExtraction]:
        value: Any = json.loads(content_list_path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError("MinerU content_list.json must contain a list")
        grouped: dict[int, list[TextBlock]] = {}
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                continue
            page_number = int(item.get("page_idx", 0)) + 1
            text = self._block_text(item)
            if not text:
                continue
            bbox = item.get("bbox")
            normalized_bbox = None
            if isinstance(bbox, list) and len(bbox) == 4:
                normalized_bbox = (
                    float(bbox[0]),
                    float(bbox[1]),
                    float(bbox[2]),
                    float(bbox[3]),
                )
            grouped.setdefault(page_number, []).append(
                TextBlock(
                    block_id=f"block_{page_number}_{index}_{uuid.uuid4().hex[:8]}",
                    page_number=page_number,
                    block_type=str(item.get("type", "text")),
                    bbox=normalized_bbox,
                    text=text,
                    source_method=ExtractionMethod.MINERU_PIPELINE,
                    metadata={
                        "mineru_index": index,
                        "text_level": item.get("text_level"),
                    },
                )
            )
        pages: list[PageExtraction] = []
        for page_number, blocks in sorted(grouped.items()):
            raw_text = "\n".join(block.text for block in blocks)
            signals = evaluate_text_quality(raw_text)
            pages.append(
                PageExtraction(
                    page_number=page_number,
                    page_kind=page_kinds.get(page_number, PageKind.SCANNED),
                    method=ExtractionMethod.MINERU_PIPELINE,
                    raw_text=raw_text,
                    blocks=blocks,
                    quality=signals,
                    band=quality_band(
                        signals.overall_score,
                        accept=self.accept_threshold,
                        review=self.review_threshold,
                    ),
                )
            )
        return pages

    @staticmethod
    def _block_text(item: dict[str, Any]) -> str:
        for key in ("text", "content"):
            value = item.get(key)
            if isinstance(value, str):
                return value.strip()
        if item.get("type") == "table" and isinstance(item.get("table_body"), str):
            return str(item["table_body"]).strip()
        return ""
