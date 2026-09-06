from __future__ import annotations

from pathlib import Path
import fitz

from app.schemas.books import Asset, Chapter, Chunk


MIN_FIGURE_AREA = 10_000


def _clip_for_page(page: fitz.Page, slot: int) -> fitz.Rect:
    rect = page.rect
    width = rect.width
    height = rect.height
    x0 = width * 0.12
    x1 = width * 0.88
    if slot == 0:
        y0, y1 = height * 0.18, height * 0.45
    elif slot == 1:
        y0, y1 = height * 0.38, height * 0.66
    else:
        y0, y1 = height * 0.58, height * 0.86
    return fitz.Rect(x0, y0, x1, y1)


def _save_thumbnail_from_page(page: fitz.Page, clip: fitz.Rect, target: Path) -> None:
    page.get_pixmap(matrix=fitz.Matrix(0.7, 0.7), clip=clip).save(target)


def _save_thumbnail_from_xref(doc: fitz.Document, xref: int, target: Path) -> None:
    pix = fitz.Pixmap(doc, xref)
    if pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    for _ in range(6):
        if pix.width <= 320 and pix.height <= 320:
            break
        pix.shrink(2)
    pix.save(target)


def _image_bbox_for_page(page: fitz.Page, xref: int) -> list[float]:
    rects = page.get_image_rects(xref)
    rect = rects[0] if rects else page.rect
    clamped = fitz.Rect(
        max(page.rect.x0, rect.x0),
        max(page.rect.y0, rect.y0),
        min(page.rect.x1, rect.x1),
        min(page.rect.y1, rect.y1),
    )
    return [round(clamped.x0, 2), round(clamped.y0, 2), round(clamped.x1, 2), round(clamped.y1, 2)]


def _chapter_for_page(chapters: list[Chapter], page_number: int) -> Chapter | None:
    candidates = [chapter for chapter in chapters if chapter.page_start <= page_number <= chapter.page_end]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.level, reverse=True)[0]


def extract_source_figures(book_id: str, file_path: Path, asset_root: Path, chapters: list[Chapter], chunks: list[Chunk] | None = None) -> list[Asset]:
    asset_root.mkdir(parents=True, exist_ok=True)
    assets: list[Asset] = []
    chunks = chunks or []
    with fitz.open(file_path) as doc:
        seen_xrefs: set[int] = set()
        for page_index in range(doc.page_count):
            page_number = page_index + 1
            chapter = _chapter_for_page(chapters, page_number)
            if not chapter:
                continue
            page = doc.load_page(page_index)
            for image_index, image_info in enumerate(page.get_images(full=True)):
                xref = int(image_info[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                pix = fitz.Pixmap(doc, xref)
                if pix.width * pix.height < MIN_FIGURE_AREA:
                    continue
                if pix.alpha:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                asset_id = f"img_{book_id}_{chapter.chapter_id}_p{page_number:03d}_{image_index + 1:02d}"
                image_path = asset_root / f"{asset_id}.png"
                thumb_path = asset_root / f"thumb_{asset_id}.png"
                pix.save(image_path)
                _save_thumbnail_from_xref(doc, xref, thumb_path)
                source_chunks = [chunk.chunk_id for chunk in chunks if chunk.chapter_id == chapter.chapter_id and chunk.page_start <= page_number <= chunk.page_end]
                assets.append(
                    Asset(
                        asset_id=asset_id,
                        book_id=book_id,
                        chapter_id=chapter.chapter_id,
                        source_type="extracted",
                        page=page_number,
                        type="figure",
                        caption=f"{chapter.source_title} 内嵌原书插图",
                        bbox=_image_bbox_for_page(page, xref),
                        image_url=f"/api/books/{book_id}/assets/{asset_id}/file",
                        thumbnail_url=f"/api/books/{book_id}/assets/{asset_id}/thumbnail",
                        source_page_image_url=f"/api/books/{book_id}/assets/{asset_id}/source-page",
                        source_chunk_ids=source_chunks,
                        concepts=[],
                        review_status="ready",
                    )
                )

        for chapter in chapters:
            if chapter.level > 2:
                continue
            sample_pages = list(range(chapter.page_start, min(chapter.page_end, chapter.page_start + 8) + 1, 3))
            for slot, page_number in enumerate(sample_pages[:3]):
                if page_number < 1 or page_number > doc.page_count:
                    continue
                page = doc.load_page(page_number - 1)
                page_image_name = f"page_{page_number:03d}.png"
                page_image_path = asset_root / page_image_name
                if not page_image_path.exists():
                    page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(page_image_path)

                clip = _clip_for_page(page, slot)
                if clip.get_area() < MIN_FIGURE_AREA:
                    continue
                asset_id = f"fig_{book_id}_{chapter.chapter_id}_p{page_number:03d}_{slot + 1:02d}"
                image_name = f"{asset_id}.png"
                thumb_name = f"thumb_{asset_id}.png"
                image_path = asset_root / image_name
                page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip).save(image_path)
                _save_thumbnail_from_page(page, clip, asset_root / thumb_name)
                source_chunks = [chunk.chunk_id for chunk in chunks if chunk.chapter_id == chapter.chapter_id and chunk.page_start <= page_number <= chunk.page_end]

                assets.append(
                    Asset(
                        asset_id=asset_id,
                        book_id=book_id,
                        chapter_id=chapter.chapter_id,
                        source_type="extracted",
                        page=page_number,
                        type="figure",
                        caption=f"{chapter.source_title} 相关原书插图候选",
                        bbox=[round(clip.x0, 2), round(clip.y0, 2), round(clip.x1, 2), round(clip.y1, 2)],
                        image_url=f"/api/books/{book_id}/assets/{asset_id}/file",
                        thumbnail_url=f"/api/books/{book_id}/assets/{asset_id}/thumbnail",
                        source_page_image_url=f"/api/books/{book_id}/assets/{asset_id}/source-page",
                        source_chunk_ids=source_chunks,
                        concepts=[],
                        review_status="candidate",
                    )
                )
    return assets
