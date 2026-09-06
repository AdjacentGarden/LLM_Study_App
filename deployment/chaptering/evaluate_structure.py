from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from adaptive_learning.ingestion.chaptering import load_normalized_pages
from adaptive_learning.ingestion.models import BookStructure


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministically validate a book structure")
    parser.add_argument("--pages", type=Path, required=True)
    parser.add_argument("--structure", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pages = load_normalized_pages(args.pages)
    structure = BookStructure.model_validate_json(args.structure.read_text(encoding="utf-8"))
    page_text = {
        page.page_number: normalized(page.cleaned_text or page.raw_text) for page in pages
    }
    errors: list[str] = []
    evidence_total = 0
    evidence_exact = 0
    point_total = 0
    points_with_evidence = 0
    previous_end: int | None = None
    for chapter in structure.chapters:
        if previous_end is not None and chapter.start_page != previous_end + 1:
            errors.append(f"non-contiguous boundary before {chapter.chapter_id}")
        previous_end = chapter.end_page
        if not chapter.summary.strip():
            errors.append(f"empty chapter summary: {chapter.chapter_id}")
        start_text = page_text.get(chapter.start_page, "")
        if chapter.title != "全书内容" and normalized(chapter.title) not in start_text:
            errors.append(f"chapter title absent from start page: {chapter.chapter_id}")
        for quote in chapter.evidence:
            evidence_total += 1
            if normalized(quote.quote) in page_text.get(quote.page_number, ""):
                evidence_exact += 1
            else:
                errors.append(f"invalid summary quote: {chapter.chapter_id}:{quote.page_number}")
        for point in chapter.knowledge_points:
            point_total += 1
            sources = chapter.knowledge_point_evidence.get(point, [])
            if sources:
                points_with_evidence += 1
            else:
                errors.append(f"knowledge point has no evidence: {chapter.chapter_id}")
            for quote in sources:
                evidence_total += 1
                if normalized(quote.quote) in page_text.get(quote.page_number, ""):
                    evidence_exact += 1
                else:
                    errors.append(f"invalid point quote: {chapter.chapter_id}:{quote.page_number}")
    if structure.chapters and structure.chapters[-1].end_page != pages[-1].page_number:
        errors.append("last chapter does not reach the final source page")
    report = {
        "passed": not errors,
        "page_count": len(pages),
        "chapter_count": len(structure.chapters),
        "chapter_boundary_count": len(structure.chapters),
        "knowledge_points": point_total,
        "knowledge_points_with_evidence": points_with_evidence,
        "knowledge_point_evidence_coverage": points_with_evidence / max(1, point_total),
        "evidence_quotes": evidence_total,
        "verbatim_evidence_quotes": evidence_exact,
        "verbatim_evidence_accuracy": evidence_exact / max(1, evidence_total),
        "errors": errors,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
