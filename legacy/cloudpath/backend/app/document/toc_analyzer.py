from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
import json
import re

from app.schemas.books import Chapter, ChapterEvidence, PageMapEntry, TocAnalysis, TocPageCandidate


TOC_ROW_RE = re.compile(r"^\s*(?P<title>.{2,80}?)(?:[\.·•…\s]{2,}|[\.·•…]+)\s*(?P<page>\d{1,4})\s*$")
TOC_ROW_LOOSE_RE = re.compile(
    r"^\s*(?P<title>(?:第\s*[一二三四五六七八九十百千万\d]+\s*[章节]|[一二三四五六七八九十百千万\d]+[、.．])\s*.{1,80}?)[\s\.·•…]*(?P<page>\d{1,4})\s*$"
)
CHAPTER_TITLE_RE = re.compile(r"^\s*第\s*[一二三四五六七八九十百千万\d]+\s*章")
SECTION_TITLE_RE = re.compile(r"^\s*第\s*[一二三四五六七八九十百千万\d]+\s*节")
PRINTED_PAGE_RE = re.compile(r"^\s*[-—–]?\s*(\d{1,4})\s*[-—–]?\s*$")


def _read_pages(artifact_path: Path) -> list[dict]:
    path = artifact_path / "pages.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _page_lines(page: dict) -> list[str]:
    lines: list[str] = []
    page_text = str(page.get("text") or "")
    for line in page_text.splitlines():
        cleaned = _clean_line(line)
        if cleaned:
            lines.append(cleaned)
    if lines:
        return lines

    for block in page.get("blocks", []):
        for line in str(block.get("text") or "").splitlines():
            cleaned = _clean_line(line)
            if cleaned:
                lines.append(cleaned)
    return lines


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u3000", " ")).strip()


def _normalize_title(value: str) -> str:
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[^\w一-龥]", "", value)
    return value.lower()


def _parse_toc_row(line: str) -> tuple[str, int, int] | None:
    match = TOC_ROW_RE.match(line) or TOC_ROW_LOOSE_RE.match(line)
    if not match:
        return None
    title = _clean_line(match.group("title").strip(".·•… "))
    if len(title) < 2:
        return None
    printed_page = int(match.group("page"))
    if printed_page <= 0:
        return None
    confidence = 78 if TOC_ROW_RE.match(line) else 66
    if CHAPTER_TITLE_RE.search(title) or SECTION_TITLE_RE.search(title):
        confidence += 8
    return title, printed_page, min(95, confidence)


def _detect_toc_pages(pages: list[dict]) -> list[TocPageCandidate]:
    candidates: list[TocPageCandidate] = []
    for page in pages[: min(len(pages), 40)]:
        page_number = int(page.get("page") or 0)
        lines = _page_lines(page)
        toc_rows = [line for line in lines if _parse_toc_row(line)]
        reasons: list[str] = []
        score = 0
        if any("目录" in line or "contents" in line.lower() for line in lines[:8]):
            score += 35
            reasons.append("页面顶部出现目录标题")
        if len(toc_rows) >= 3:
            score += min(45, len(toc_rows) * 9)
            reasons.append(f"识别到 {len(toc_rows)} 行标题+页码结构")
        elif len(toc_rows) > 0:
            score += len(toc_rows) * 8
            reasons.append(f"识别到 {len(toc_rows)} 行疑似目录结构")
        if sum(1 for line in lines if "……" in line or "..." in line or "·" in line) >= 2:
            score += 10
            reasons.append("多行存在目录连接符")
        if len(lines) >= 8 and len(toc_rows) / max(1, len(lines)) >= 0.25:
            score += 10
            reasons.append("目录结构在页面中占比较高")
        if score > 0:
            candidates.append(
                TocPageCandidate(
                    page=page_number,
                    score=min(100, score),
                    line_count=len(toc_rows),
                    reasons=reasons,
                    sample_lines=toc_rows[:5],
                )
            )
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def _extract_toc_entries(pages: list[dict], candidates: list[TocPageCandidate]) -> list[dict]:
    if not candidates or candidates[0].score < 35:
        return []
    candidate_pages = {item.page for item in candidates if item.score >= max(35, candidates[0].score - 15)}
    entries: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for page in pages:
        if int(page.get("page") or 0) not in candidate_pages:
            continue
        for line in _page_lines(page):
            parsed = _parse_toc_row(line)
            if not parsed:
                continue
            title, printed_page, confidence = parsed
            key = (_normalize_title(title), printed_page)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "title": title,
                    "printed_page": printed_page,
                    "level": _infer_level(title),
                    "toc_line_confidence": confidence,
                }
            )
    return entries


def _infer_level(title: str) -> int:
    if CHAPTER_TITLE_RE.search(title):
        return 1
    if SECTION_TITLE_RE.search(title):
        return 2
    if re.match(r"^\s*\d+[.．]\d+", title):
        return 2
    return 1


def _block_center(block: dict) -> tuple[float, float]:
    bbox = block.get("bbox") or [0, 0, 0, 0]
    if len(bbox) < 4:
        return 0.0, 0.0
    return (float(bbox[0]) + float(bbox[2])) / 2, (float(bbox[1]) + float(bbox[3])) / 2


def _page_bounds(page: dict) -> tuple[float, float]:
    ys: list[float] = []
    for block in page.get("blocks", []):
        bbox = block.get("bbox") or []
        if len(bbox) >= 4:
            ys.extend([float(bbox[1]), float(bbox[3])])
    return (min(ys), max(ys)) if ys else (0.0, 1.0)


def _detect_printed_page_number(page: dict) -> tuple[int | None, int, str | None]:
    min_y, max_y = _page_bounds(page)
    height = max(1.0, max_y - min_y)
    best: tuple[int | None, int, str | None] = (None, 0, None)
    for block in page.get("blocks", []):
        text = _clean_line(str(block.get("text") or ""))
        match = PRINTED_PAGE_RE.match(text)
        if not match:
            continue
        _, center_y = _block_center(block)
        relative_y = (center_y - min_y) / height
        edge_bonus = 35 if relative_y <= 0.18 or relative_y >= 0.82 else 0
        confidence = 45 + edge_bonus
        if confidence > best[1]:
            best = (int(match.group(1)), confidence, text)
    return best


def _build_page_map(pages: list[dict], page_count: int) -> list[PageMapEntry]:
    detected: dict[int, tuple[int, int, str]] = {}
    offsets: list[int] = []
    for page in pages:
        pdf_page = int(page.get("page") or 0)
        printed, confidence, evidence = _detect_printed_page_number(page)
        if pdf_page > 0 and printed is not None:
            detected[pdf_page] = (printed, confidence, evidence or str(printed))
            offsets.append(pdf_page - printed)

    offset = Counter(offsets).most_common(1)[0][0] if offsets else 0
    entries: list[PageMapEntry] = []
    for pdf_page in range(1, page_count + 1):
        if pdf_page in detected:
            printed, confidence, evidence = detected[pdf_page]
            entries.append(PageMapEntry(pdf_page=pdf_page, printed_page=printed, confidence=confidence, source="ocr_page_number", evidence=evidence))
            continue
        if offsets:
            printed = max(1, pdf_page - offset)
            entries.append(PageMapEntry(pdf_page=pdf_page, printed_page=printed, confidence=55, source="inferred_offset", evidence=f"pdf_page - printed_page = {offset}"))
        else:
            entries.append(PageMapEntry(pdf_page=pdf_page, printed_page=pdf_page, confidence=25, source="pdf_page_fallback", evidence="未识别到印刷页码，暂用 PDF 页码"))
    return entries


def _printed_to_pdf(page_map: list[PageMapEntry], printed_page: int, page_count: int) -> tuple[int, int]:
    exact = [entry for entry in page_map if entry.printed_page == printed_page]
    if exact:
        best = max(exact, key=lambda item: item.confidence)
        return best.pdf_page, best.confidence
    nearby = [entry for entry in page_map if entry.printed_page is not None]
    if not nearby:
        return max(1, min(page_count, printed_page)), 20
    best = min(nearby, key=lambda item: abs((item.printed_page or 0) - printed_page))
    delta = printed_page - (best.printed_page or printed_page)
    return max(1, min(page_count, best.pdf_page + delta)), max(20, best.confidence - 20)


def _line_similarity(left: str, right: str) -> int:
    left_norm = _normalize_title(left)
    right_norm = _normalize_title(right)
    if not left_norm or not right_norm:
        return 0
    if left_norm in right_norm or right_norm in left_norm:
        return 92
    return int(SequenceMatcher(None, left_norm, right_norm).ratio() * 100)


def _verify_title(pages: list[dict], title: str, pdf_page: int, page_count: int) -> tuple[int | None, int, list[str]]:
    start = max(1, pdf_page - 2)
    end = min(page_count, pdf_page + 2)
    best_page: int | None = None
    best_score = 0
    reasons: list[str] = []
    for page in pages:
        page_number = int(page.get("page") or 0)
        if page_number < start or page_number > end:
            continue
        min_y, max_y = _page_bounds(page)
        height = max(1.0, max_y - min_y)
        for block in page.get("blocks", []):
            for line in str(block.get("text") or "").splitlines():
                cleaned = _clean_line(line)
                if not cleaned:
                    continue
                score = _line_similarity(title, cleaned)
                _, center_y = _block_center(block)
                relative_y = (center_y - min_y) / height
                if relative_y <= 0.35:
                    score += 6
                if block.get("font_size") and float(block["font_size"]) >= 14:
                    score += 4
                score = min(100, score)
                if score > best_score:
                    best_score = score
                    best_page = page_number
    if best_page is not None and best_score >= 70:
        reasons.append(f"正文第 {best_page} 页附近找到相似标题")
    elif best_page is not None:
        reasons.append(f"正文第 {best_page} 页附近只有弱标题匹配")
    else:
        reasons.append("正文附近未找到标题匹配")
    return best_page, best_score, reasons


def _status_for(confidence: int) -> str:
    if confidence >= 85:
        return "匹配良好"
    if confidence >= 60:
        return "需检查"
    return "需人工确认"


def _evidence_to_chapters(book_id: str, evidence: list[ChapterEvidence]) -> list[Chapter]:
    chapters: list[Chapter] = []
    last_level_one_id: str | None = None
    for item in evidence:
        parent_id = last_level_one_id if item.level > 1 else None
        if item.level == 1:
            last_level_one_id = item.chapter_id
        chapters.append(
            Chapter(
                chapter_id=item.chapter_id,
                parent_id=parent_id,
                level=item.level,
                source_title=item.source_title,
                ai_title=f"课程：{item.source_title}",
                page_start=item.pdf_page_start,
                page_end=item.pdf_page_end,
                confidence=item.confidence,
                status=item.status,
                source="toc_page_map_title_verify",
            )
        )
    return chapters


def chapters_from_toc_analysis(book_id: str, analysis: TocAnalysis) -> list[Chapter]:
    return _evidence_to_chapters(book_id, analysis.chapter_evidence)


def analyze_toc_structure(book_id: str, artifact_path: Path, page_count: int) -> TocAnalysis:
    pages = _read_pages(artifact_path)
    warnings: list[str] = []
    if not pages:
        return TocAnalysis(book_id=book_id, status="no_pages", warnings=["缺少 OCR/text 页面结果"])

    toc_pages = _detect_toc_pages(pages)
    toc_entries = _extract_toc_entries(pages, toc_pages)
    page_map = _build_page_map(pages, page_count)

    if not toc_entries:
        warnings.append("未识别到可靠目录页，后续将退回正文标题规则或整本文档导读")
        return TocAnalysis(book_id=book_id, status="toc_not_found", toc_pages=toc_pages, page_map=page_map, warnings=warnings)

    starts: list[dict] = []
    for entry in toc_entries:
        pdf_page, page_map_confidence = _printed_to_pdf(page_map, int(entry["printed_page"]), page_count)
        starts.append({**entry, "pdf_page": pdf_page, "page_map_confidence": page_map_confidence})
    starts.sort(key=lambda item: (item["pdf_page"], item["level"], item["title"]))

    chapter_evidence: list[ChapterEvidence] = []
    for index, entry in enumerate(starts):
        next_same_or_higher = next((item for item in starts[index + 1 :] if int(item["level"]) <= int(entry["level"])), None)
        page_start = max(1, min(page_count, int(entry["pdf_page"])))
        page_end = max(page_start, min(page_count, int(next_same_or_higher["pdf_page"]) - 1 if next_same_or_higher else page_count))
        title_match_page, title_match_score, reasons = _verify_title(pages, str(entry["title"]), page_start, page_count)
        confidence = min(
            96,
            max(
                35,
                int(entry["toc_line_confidence"] * 0.42 + entry["page_map_confidence"] * 0.28 + title_match_score * 0.30),
            ),
        )
        if entry["page_map_confidence"] >= 55:
            reasons.append("印刷页码已映射到 PDF 页码")
        else:
            reasons.append("印刷页码映射较弱，使用 PDF 页码兜底")
        chapter_evidence.append(
            ChapterEvidence(
                chapter_id=f"ch_{index + 1:03d}",
                source_title=str(entry["title"]),
                level=int(entry["level"]),
                printed_page_start=int(entry["printed_page"]),
                pdf_page_start=page_start,
                pdf_page_end=page_end,
                toc_line_confidence=int(entry["toc_line_confidence"]),
                page_map_confidence=int(entry["page_map_confidence"]),
                title_match_page=title_match_page,
                title_match_score=title_match_score,
                confidence=confidence,
                status=_status_for(confidence),
                reasons=reasons,
            )
        )

    status = "ready" if any(item.confidence >= 60 for item in chapter_evidence) else "needs_review"
    return TocAnalysis(book_id=book_id, status=status, toc_pages=toc_pages, page_map=page_map, chapter_evidence=chapter_evidence, warnings=warnings)


def write_toc_analysis(artifact_path: Path, analysis: TocAnalysis) -> None:
    (artifact_path / "toc_analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")


def read_toc_analysis(book_id: str, artifact_path: Path) -> TocAnalysis:
    path = artifact_path / "toc_analysis.json"
    if not path.exists():
        return TocAnalysis(book_id=book_id, status="missing", warnings=["目录分析结果不存在，请先解析文件"])
    return TocAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
