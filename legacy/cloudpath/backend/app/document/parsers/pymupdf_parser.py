from __future__ import annotations

from app.document.parsers.base import ParsedDocument, ParseRequest, parsed_page_from_page_result
from app.document.pdf_extractor import extract_pages


class PyMuPDFParser:
    name = "pymupdf"

    def parse(self, request: ParseRequest) -> ParsedDocument:
        pages = extract_pages(request.file_path)
        parsed_pages = [parsed_page_from_page_result(page, self.name) for page in pages]
        return ParsedDocument(
            book_id=request.book_id,
            parser_name=self.name,
            pages=parsed_pages,
            scan=request.scan,
            metadata={"file_type": request.scan.file_type, "text_layer": request.scan.has_text_layer},
        )
