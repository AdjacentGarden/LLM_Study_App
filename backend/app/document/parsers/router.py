from __future__ import annotations

from collections import Counter
import time

from app.core.config import get_settings
from app.document.mineru.exceptions import MinerUError, MinerUStaleResultError
from app.document.parser_report import PageParserDecision, ParserAttempt, ParserReport
from app.document.parsers.base import DocumentParser, ParsedDocument, ParsedPage, ParserUnavailable, ParseRequest
from app.document.parsers.marker_parser import MarkerParser
from app.document.parsers.mineru_parser import MinerUParser
from app.document.parsers.ocr_parser import OCRParser
from app.document.parsers.pymupdf_parser import PyMuPDFParser
from app.schemas.books import QualityWarning
from app.services.file_types import OFFICE_EXTENSIONS


MIN_PAGE_QUALITY = 0.60
MIN_SEMANTIC_CHARACTERS = 20


def _semantic_text(page: ParsedPage) -> str:
    pieces: list[str] = []
    for block in page.text_blocks:
        if block.type in {"ocr_pending", "page_number", "header", "footer"}:
            continue
        text = block.text.strip()
        if text:
            pieces.append(text)
    if not pieces and page.text.strip():
        pieces.append(page.text.strip())
    return "\n".join(pieces)


def _invalid_character_ratio(text: str) -> float:
    if not text:
        return 0.0
    invalid = sum(1 for character in text if character == "\ufffd" or (ord(character) < 32 and character not in "\n\r\t"))
    return invalid / len(text)


def _page_is_usable(page: ParsedPage, *, parser: str | None = None) -> bool:
    if page.ocr_provider == "mock" or any(block.type == "ocr_pending" for block in page.text_blocks):
        return False
    semantic = _semantic_text(page)
    if len(semantic) < MIN_SEMANTIC_CHARACTERS:
        return False
    if _invalid_character_ratio(semantic) > 0.01:
        return False
    if page.needs_ocr:
        return False
    if page.quality_score is not None and page.quality_score < MIN_PAGE_QUALITY:
        return False
    # PyMuPDF is a text-layer fallback.  It must never be accepted for an
    # image-only page merely because the parser returned incidental metadata.
    if parser == "pymupdf" and not semantic.strip():
        return False
    return True


class ParserRouter:
    """MinerU-first parser router with page-level typed fallbacks.

    ``auto`` and ``mineru`` always send the complete supported source to
    MinerU first.  Good MinerU pages are preserved.  Only missing/low-quality
    PDF pages are offered to PyMuPDF and then PaddleOCR; image pages go
    directly to PaddleOCR.  Marker remains available solely as an explicit
    rollback/debug selection.
    """

    def __init__(self, parsers: list[DocumentParser] | None = None) -> None:
        configured = parsers or [MinerUParser(), MarkerParser(), PyMuPDFParser(), OCRParser()]
        self._parsers = {parser.name: parser for parser in configured}

    def parse(self, request: ParseRequest) -> ParsedDocument:
        requested = request.preferred_parser or "mineru"
        attempts: list[ParserAttempt] = []
        accumulated_warnings: list[str] = []
        accepted: dict[int, ParsedPage] = {}
        mineru_document: ParsedDocument | None = None
        last_successful: ParsedDocument | None = None

        if requested == "marker":
            document = self._attempt("marker", request, attempts)
            if document is not None:
                return self._finalize(request, document, attempts, document.pages, requested=requested)
            return self._fallback_all(request, attempts, accumulated_warnings, requested=requested)

        if requested == "ocr":
            document = self._attempt("ocr", request, attempts)
            if document is not None:
                return self._finalize(request, document, attempts, document.pages, requested=requested)
            return self._empty_result(request, attempts, requested=requested)

        # Default and explicit MinerU mode share the same MinerU-first state
        # machine.  An unsupported configured value is treated as auto.
        # An explicit PyMuPDF selection skips MinerU, but it must still enter
        # the same page-level fallback state machine. Scanned PDFs have no
        # native text layer, so finalizing PyMuPDF's empty pages here would
        # bypass OCR and make a healthy source look unrecoverable.
        if requested != "pymupdf":
            mineru_document = self._attempt("mineru", request, attempts)
            if mineru_document is not None:
                last_successful = mineru_document
                for page in mineru_document.pages:
                    if _page_is_usable(page, parser="mineru"):
                        accepted[page.page_number] = page
                    else:
                        accumulated_warnings.append(f"page_{page.page_number}:mineru_quality_fallback")

        pending = [page for page in range(1, request.scan.page_count + 1) if page not in accepted]

        if pending and request.scan.file_type == "pdf":
            pymupdf = self._attempt("pymupdf", request, attempts, pages=pending)
            if pymupdf is not None:
                last_successful = pymupdf
                for page in pymupdf.pages:
                    if page.page_number in pending and _page_is_usable(page, parser="pymupdf"):
                        accepted[page.page_number] = page
            pending = [page for page in pending if page not in accepted]

        source_is_office = f".{request.scan.file_type.lower().lstrip('.')}" in OFFICE_EXTENSIONS
        if pending and not source_is_office:
            ocr = self._attempt("ocr", request, attempts, pages=pending)
            if ocr is not None:
                last_successful = ocr
                for page in ocr.pages:
                    if page.page_number in pending and _page_is_usable(page, parser="ocr"):
                        accepted[page.page_number] = page
            pending = [page for page in pending if page not in accepted]

        for page_number in pending:
            accepted[page_number] = ParsedPage(
                page_number=page_number,
                text="",
                needs_ocr=True,
                parser="unrecoverable",
                quality_warnings=[
                    QualityWarning(page=page_number, code="page_unrecoverable", message="No parser produced usable semantic content")
                ],
                quality_score=0.0,
            )
            accumulated_warnings.append(f"page_{page_number}:unrecoverable")

        source = mineru_document or last_successful or ParsedDocument(
            book_id=request.book_id,
            parser_name="unrecoverable",
            pages=[],
            scan=request.scan,
        )
        selected_pages = [accepted[index] for index in sorted(accepted)]
        # A low-quality text mapping does not make a decoded source image
        # invalid. Preserve the visual Asset, but annotate that its page text
        # came from a fallback so retrieval never treats an empty caption as
        # MinerU semantic evidence.
        final_page_parser = {page.page_number: page.parser for page in selected_pages}
        selected_assets = []
        for asset in mineru_document.assets if mineru_document else []:
            page_parser = final_page_parser.get(asset.page or -1)
            if page_parser is None:
                continue
            replaced = page_parser != "mineru"
            selected_assets.append(
                asset.model_copy(
                    update={
                        "review_status": "needs_review" if replaced and not asset.caption.strip() else asset.review_status,
                        "metadata": {
                            **asset.metadata,
                            "page_text_parser": page_parser,
                            "mineru_page_text_replaced": replaced,
                        },
                    }
                )
            )
        return self._finalize(
            request,
            source,
            attempts,
            selected_pages,
            requested=requested,
            extra_warnings=accumulated_warnings,
            assets=selected_assets,
        )

    def _attempt(
        self,
        parser_name: str,
        request: ParseRequest,
        attempts: list[ParserAttempt],
        *,
        pages: list[int] | None = None,
    ) -> ParsedDocument | None:
        parser = self._parsers.get(parser_name)
        if parser is None:
            attempts.append(
                ParserAttempt.failure(
                    parser=parser_name,
                    duration_ms=0,
                    error_code="parser_not_registered",
                    pages=pages,
                )
            )
            return None
        started = time.perf_counter()
        try:
            document = parser.parse(request)
        except MinerUStaleResultError:
            # A stale generation is not a parser outage.  Falling back after
            # this exception would let an obsolete worker write new artifacts.
            raise
        except MinerUError as exc:
            attempts.append(
                ParserAttempt.failure(
                    parser=parser_name,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    error_code=exc.code,
                    detail=exc.code,
                    pages=pages,
                    retryable=exc.retryable,
                )
            )
            return None
        except ParserUnavailable as exc:
            attempts.append(
                ParserAttempt.failure(
                    parser=parser_name,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    error_code=f"{parser_name}_unavailable",
                    detail=f"{parser_name} unavailable",
                    pages=pages,
                    retryable=True,
                )
            )
            return None
        except Exception as exc:
            attempts.append(
                ParserAttempt.failure(
                    parser=parser_name,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    error_code=f"{parser_name}_failed",
                    detail=exc.__class__.__name__,
                    pages=pages,
                    retryable=False,
                )
            )
            return None

        relevant = document.pages if pages is None else [page for page in document.pages if page.page_number in pages]
        usable = [page for page in relevant if _page_is_usable(page, parser=parser_name)]
        average_quality = (
            sum(page.quality_score if page.quality_score is not None else 1.0 for page in usable) / len(usable)
            if usable
            else 0.0
        )
        attempts.append(
            ParserAttempt(
                parser=parser_name,
                scope="pages" if pages else "document",
                pages=pages or [],
                status="succeeded" if len(usable) == len(relevant) and relevant else "degraded",
                duration_ms=round((time.perf_counter() - started) * 1000),
                quality_score=average_quality,
            )
        )
        return document

    def _fallback_all(
        self,
        request: ParseRequest,
        attempts: list[ParserAttempt],
        warnings: list[str],
        *,
        requested: str,
        skip_pymupdf: bool = False,
    ) -> ParsedDocument:
        if f".{request.scan.file_type.lower().lstrip('.')}" in OFFICE_EXTENSIONS:
            warnings.append("office_requires_mineru")
            return self._empty_result(request, attempts, requested=requested)
        if request.scan.file_type == "pdf" and not skip_pymupdf:
            pymupdf = self._attempt("pymupdf", request, attempts)
            if pymupdf and all(_page_is_usable(page, parser="pymupdf") for page in pymupdf.pages):
                return self._finalize(request, pymupdf, attempts, pymupdf.pages, requested=requested, extra_warnings=warnings)
        ocr = self._attempt("ocr", request, attempts)
        if ocr:
            return self._finalize(request, ocr, attempts, ocr.pages, requested=requested, extra_warnings=warnings)
        return self._empty_result(request, attempts, requested=requested)

    def _empty_result(self, request: ParseRequest, attempts: list[ParserAttempt], *, requested: str) -> ParsedDocument:
        pages = [
            ParsedPage(
                page_number=index,
                text="",
                needs_ocr=True,
                parser="unrecoverable",
                quality_score=0.0,
                quality_warnings=[
                    QualityWarning(page=index, code="page_unrecoverable", message="No parser produced usable semantic content")
                ],
            )
            for index in range(1, request.scan.page_count + 1)
        ]
        source = ParsedDocument(book_id=request.book_id, parser_name="unrecoverable", pages=[], scan=request.scan)
        return self._finalize(request, source, attempts, pages, requested=requested)

    def _finalize(
        self,
        request: ParseRequest,
        source: ParsedDocument,
        attempts: list[ParserAttempt],
        pages: list[ParsedPage],
        *,
        requested: str,
        extra_warnings: list[str] | None = None,
        assets=None,
    ) -> ParsedDocument:
        ordered = sorted({page.page_number: page for page in pages}.values(), key=lambda item: item.page_number)
        present = {page.page_number for page in ordered}
        missing = [index for index in range(1, request.scan.page_count + 1) if index not in present]
        parser_counts = Counter(page.parser for page in ordered)
        final_parser = next(iter(parser_counts)) if len(parser_counts) == 1 else "mixed"
        warnings = [*source.warnings, *(extra_warnings or [])]
        warnings.extend(f"missing_page:{page}" for page in missing)
        decisions = [
            PageParserDecision(
                page=page.page_number,
                parser=page.parser,
                quality_score=page.quality_score,
                status="unrecoverable" if page.parser == "unrecoverable" else "accepted",
                warnings=[warning.code for warning in page.quality_warnings],
            )
            for page in ordered
        ]
        report = ParserReport(
            book_id=request.book_id,
            requested_parser=requested,
            final_parser=final_parser,
            parse_generation=request.expected_generation,
            source_page_count=request.scan.page_count,
            output_page_count=len(ordered),
            attempts=attempts,
            pages=decisions,
            missing_pages=missing,
            warnings=warnings,
            mineru_backend=str(source.metadata.get("mineru_backend")) if source.metadata.get("mineru_backend") else None,
            mineru_version=str(source.metadata.get("mineru_version")) if source.metadata.get("mineru_version") else None,
        )
        fallback_messages = [
            f"{attempt.parser}:{attempt.error_code}"
            for attempt in attempts
            if attempt.status == "failed" and attempt.error_code
        ]
        metadata = {
            **source.metadata,
            "parser_report": report.model_dump(mode="json"),
            "parser_attempts": [attempt.model_dump(mode="json") for attempt in attempts],
            "parser_fallbacks": fallback_messages,
        }
        return ParsedDocument(
            book_id=request.book_id,
            parser_name=final_parser,
            pages=ordered,
            scan=source.scan,
            metadata=metadata,
            warnings=warnings,
            assets=list(source.assets if assets is None else assets),
        )


def get_parser_router() -> ParserRouter:
    return ParserRouter()


def get_configured_parser_name() -> str:
    provider = get_settings().parser_provider
    return provider if provider in {"auto", "pymupdf", "marker", "mineru", "ocr"} else "mineru"
