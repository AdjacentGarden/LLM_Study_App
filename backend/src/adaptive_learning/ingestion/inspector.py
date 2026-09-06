from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from .models import PageKind


@dataclass(frozen=True, slots=True)
class PageInspection:
    page_number: int
    kind: PageKind
    native_character_count: int
    image_count: int
    image_coverage: float


@dataclass(frozen=True, slots=True)
class DocumentInspection:
    page_count: int
    digital_pages: int
    scanned_pages: int
    mixed_pages: int
    native_character_count: int
    pages: list[PageInspection]


class PDFInspector:
    def inspect(self, path: Path) -> DocumentInspection:
        document: Any = pymupdf.open(path)  # type: ignore[no-untyped-call]
        pages: list[PageInspection] = []
        for index, page in enumerate(document):
            text = page.get_text("text").strip()
            image_area = 0.0
            for image in page.get_images(full=True):
                for rectangle in page.get_image_rects(image[0]):
                    image_area += rectangle.width * rectangle.height
            coverage = min(1.0, image_area / max(1.0, page.rect.width * page.rect.height))
            kind = self._classify(len(text), coverage)
            pages.append(
                PageInspection(
                    page_number=index + 1,
                    kind=kind,
                    native_character_count=len(text),
                    image_count=len(page.get_images(full=True)),
                    image_coverage=round(coverage, 4),
                )
            )
        return DocumentInspection(
            page_count=len(pages),
            digital_pages=sum(value.kind == PageKind.DIGITAL for value in pages),
            scanned_pages=sum(value.kind == PageKind.SCANNED for value in pages),
            mixed_pages=sum(value.kind == PageKind.MIXED for value in pages),
            native_character_count=sum(value.native_character_count for value in pages),
            pages=pages,
        )

    @staticmethod
    def _classify(character_count: int, image_coverage: float) -> PageKind:
        if character_count >= 120 and image_coverage < 0.65:
            return PageKind.DIGITAL
        if character_count < 40 and image_coverage >= 0.65:
            return PageKind.SCANNED
        return PageKind.MIXED
