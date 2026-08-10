from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os

from app.schemas.books import Chapter


@dataclass(frozen=True)
class ChapterSeed:
    keywords: tuple[str, ...]
    min_pages: int
    max_pages: int
    chapters: tuple[Chapter, ...]


DEFAULT_SEEDS: tuple[ChapterSeed, ...] = (
    ChapterSeed(
        keywords=("遗传与进化", "生物"),
        min_pages=100,
        max_pages=160,
        chapters=(
            Chapter(chapter_id="c1", level=1, source_title="第 1 章 遗传因子的发现", ai_title="课程 1：理解孟德尔遗传规律", page_start=1, page_end=25, confidence=70, status="需 OCR 复核", source="configured_seed_pending_ocr"),
            Chapter(chapter_id="c1s1", parent_id="c1", level=2, source_title="第 1 节 孟德尔的豌豆杂交实验（一）", ai_title="课程 1.1：从实验设计理解分离定律", page_start=11, page_end=18, confidence=70, status="需 OCR 复核", source="configured_seed_pending_ocr"),
            Chapter(chapter_id="c1s2", parent_id="c1", level=2, source_title="第 2 节 孟德尔的豌豆杂交实验（二）", ai_title="课程 1.2：用概率解释自由组合", page_start=19, page_end=25, confidence=70, status="需 OCR 复核", source="configured_seed_pending_ocr"),
            Chapter(chapter_id="c2", level=1, source_title="第 2 章 基因和染色体的关系", ai_title="课程 2：把基因放到染色体上理解", page_start=26, page_end=53, confidence=70, status="需 OCR 复核", source="configured_seed_pending_ocr"),
            Chapter(chapter_id="c2s1", parent_id="c2", level=2, source_title="第 1 节 减数分裂和受精作用", ai_title="课程 2.1：同源染色体如何分离", page_start=28, page_end=36, confidence=70, status="需 OCR 复核", source="configured_seed_pending_ocr"),
            Chapter(chapter_id="c3", level=1, source_title="第 3 章 基因的本质", ai_title="课程 3：理解 DNA 与遗传信息", page_start=54, page_end=76, confidence=70, status="需 OCR 复核", source="configured_seed_pending_ocr"),
        ),
    ),
)


def _load_external_seeds() -> tuple[ChapterSeed, ...]:
    path = os.environ.get("BOOKCOURSE_CHAPTER_SEED_FILE")
    if not path:
        return ()
    seed_path = Path(path)
    if not seed_path.exists():
        return ()
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    seeds: list[ChapterSeed] = []
    for item in payload:
        chapters = tuple(Chapter.model_validate(chapter) for chapter in item.get("chapters", []))
        seeds.append(
            ChapterSeed(
                keywords=tuple(item.get("keywords", [])),
                min_pages=int(item.get("min_pages", 1)),
                max_pages=int(item.get("max_pages", 10000)),
                chapters=chapters,
            )
        )
    return tuple(seeds)


def seed_chapters_for(filename: str, page_count: int) -> list[Chapter] | None:
    for seed in _load_external_seeds() + DEFAULT_SEEDS:
        if seed.min_pages <= page_count <= seed.max_pages and all(keyword in filename for keyword in seed.keywords):
            return [chapter.model_copy(deep=True) for chapter in seed.chapters]
    return None
