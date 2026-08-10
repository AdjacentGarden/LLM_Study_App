from __future__ import annotations

from pathlib import Path
import json
import re

from app.document.chapter_seeds import seed_chapters_for
from app.document.toc_analyzer import chapters_from_toc_analysis, read_toc_analysis
from app.schemas.books import Chapter


CHAPTER_RE = re.compile(r"^\s*第\s*([一二三四五六七八九十百千万\d]+)\s*章\s+(.+)$")
SECTION_RE = re.compile(r"^\s*第\s*([一二三四五六七八九十百千万\d]+)\s*节\s+(.+)$")


def recognize_chapters(book_id: str, filename: str, artifact_path: Path, page_count: int) -> list[Chapter]:
    pages_path = artifact_path / "pages.json"
    pages = json.loads(pages_path.read_text(encoding="utf-8")) if pages_path.exists() else []

    toc_analysis = read_toc_analysis(book_id, artifact_path)
    toc_chapters = chapters_from_toc_analysis(book_id, toc_analysis)
    if toc_chapters:
        return toc_chapters

    headings: list[tuple[int, int, str, str | None]] = []
    has_ocr_low_confidence = any(page.get("needs_ocr") for page in pages)

    for page in pages:
        page_number = int(page["page"])
        for block in page.get("blocks", []):
            first_line = block.get("text", "").splitlines()[0].strip() if block.get("text") else ""
            chapter = CHAPTER_RE.match(first_line)
            section = SECTION_RE.match(first_line)
            if chapter:
                headings.append((page_number, 1, first_line, None))
            elif section:
                parent_index = next((index for index, item in reversed(list(enumerate(headings))) if item[1] == 1), None)
                parent = f"ch_{parent_index + 1:03d}" if parent_index is not None else None
                headings.append((page_number, 2, first_line, parent))

    if headings:
        chapters: list[Chapter] = []
        for index, (page_start, level, title, parent_id) in enumerate(headings):
            next_same_or_higher = next((item[0] for item in headings[index + 1 :] if item[1] <= level), page_count + 1)
            chapter_id = f"ch_{index + 1:03d}"
            chapters.append(
                Chapter(
                    chapter_id=chapter_id,
                    parent_id=parent_id,
                    level=level,
                    source_title=title,
                    ai_title=f"课程：{title}",
                    page_start=page_start,
                    page_end=max(page_start, next_same_or_higher - 1),
                    confidence=82,
                    status="需检查",
                    source="heading_rule",
                )
            )
        return chapters

    seed = seed_chapters_for(filename, page_count)
    if seed:
        return seed

    fallback_title = "整本文档（OCR 低置信度，需复核）" if has_ocr_low_confidence else "整本文档（未识别到清晰目录）"
    return [
        Chapter(
            chapter_id="ch_001",
            level=1,
            source_title=fallback_title,
            ai_title="课程：整本文档导读",
            page_start=1,
            page_end=max(1, page_count),
            confidence=20,
            status="需人工确认",
            source="single_document_fallback",
        )
    ]
