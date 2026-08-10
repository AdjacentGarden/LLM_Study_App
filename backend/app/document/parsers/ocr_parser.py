from __future__ import annotations

from app.document.ocr import OCRUnavailable
from app.document.ocr_pipeline import extract_ocr_pages
from app.document.parsers.base import ParsedDocument, ParserUnavailable, ParseRequest, parsed_page_from_page_result


class OCRParser:
    name = "ocr"

    def parse(self, request: ParseRequest) -> ParsedDocument:
        def report_page(completed: int, total: int) -> None:
            if request.progress_callback is None or total <= 0:
                return
            progress = 18 + round((completed / total) * 50)
            request.progress_callback(
                "ocr_running",
                min(68, progress),
                f"OCR 正在识别第 {completed}/{total} 页",
            )

        try:
            pages, scan = extract_ocr_pages(
                request.book_id,
                request.file_path,
                request.artifact_path,
                request.scan,
                on_page_done=report_page,
            )
        except OCRUnavailable as exc:
            raise ParserUnavailable(self.name, f"{exc.provider}:{exc.reason}") from None
        parsed_pages = [parsed_page_from_page_result(page, self.name) for page in pages]
        return ParsedDocument(
            book_id=request.book_id,
            parser_name=self.name,
            pages=parsed_pages,
            scan=scan,
            metadata={
                "file_type": scan.file_type,
                "ocr_provider": parsed_pages[0].ocr_provider if parsed_pages else None,
            },
            warnings=[warning.code for warning in scan.quality_warnings],
        )

