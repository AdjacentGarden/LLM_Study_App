from __future__ import annotations

from app.document.mineru.client import MinerUClient
from app.document.mineru.exceptions import MinerUProtocolError, MinerUStaleResultError
from app.document.mineru.mapper import map_mineru_document
from app.document.mineru.models import MinerUClientConfig, MinerUParseOptions
from app.document.mineru.task_store import mineru_task_store
from app.document.parsers.base import ParsedDocument, ParserUnavailable, ParseRequest, parsed_page_from_page_result
from app.services.storage import asset_dir
from app.services.file_types import OFFICE_EXTENSIONS


def _source_metadata(request: ParseRequest, page_number: int) -> dict[str, object]:
    location = next(
        (
            item
            for item in request.scan.source_locations
            if int(item.get("index", -1)) == page_number
        ),
        {},
    )
    return {
        "source_format": request.scan.file_type,
        "source_unit": request.scan.source_unit,
        **location,
    }


def _annotate_source_semantics(request: ParseRequest, mapped):
    pages = []
    is_docx = request.scan.file_type == "docx"
    for page in mapped.pages:
        source_metadata = _source_metadata(request, page.page)
        blocks = []
        for block_number, block in enumerate(page.blocks, start=1):
            block_metadata = {**block.metadata, **source_metadata}
            if is_docx:
                block_metadata.update(
                    {
                        "document_block_number": block_number,
                        "has_stable_page": False,
                        "source_label": f"块 {block_number}",
                    }
                )
            blocks.append(block.model_copy(update={"metadata": block_metadata}))
        pages.append(
            page.model_copy(
                update={
                    "blocks": blocks,
                    "metadata": {**page.metadata, **source_metadata},
                }
            )
        )

    is_office = f".{request.scan.file_type.lower().lstrip('.')}" in OFFICE_EXTENSIONS
    assets = []
    for asset in mapped.assets:
        source_metadata = _source_metadata(request, asset.page or 1)
        assets.append(
            asset.model_copy(
                update={
                    "metadata": {**asset.metadata, **source_metadata},
                    "source_page_image_url": (
                        f"/api/books/{request.book_id}/pages/{asset.page}/image"
                        if is_office and asset.page is not None
                        else asset.source_page_image_url
                    ),
                }
            )
        )
    return mapped.model_copy(update={"pages": pages, "assets": assets})


class MinerUParser:
    name = "mineru"

    def parse(self, request: ParseRequest) -> ParsedDocument:
        try:
            config = MinerUClientConfig.from_settings()
            options = MinerUParseOptions.from_settings()
        except (TypeError, ValueError) as exc:
            raise ParserUnavailable(self.name, str(exc)) from None

        with MinerUClient(config, task_store=mineru_task_store) as client:
            execution = client.execute(
                book_id=request.book_id,
                file_path=request.file_path,
                options=options,
                expected_generation=request.expected_generation,
                cloudpath_job_id=request.cloudpath_job_id,
            )

        if request.expected_generation is not None and execution.parse_generation != request.expected_generation:
            raise MinerUStaleResultError("MinerU result does not belong to the worker's reserved generation")
        if len(execution.result.results) != 1:
            raise MinerUProtocolError("MinerU returned an unexpected document count", task_id=execution.task.task_id)
        document = next(iter(execution.result.results.values()))

        if request.expected_generation is not None and not mineru_task_store.is_current(
            request.book_id, request.expected_generation
        ):
            raise MinerUStaleResultError("MinerU result became obsolete before mapping")

        mapped = map_mineru_document(
            request.book_id,
            document,
            backend=execution.result.backend,
            version=execution.result.version,
            asset_root=asset_dir(request.book_id),
            expected_page_count=request.scan.page_count,
            generation_guard=(
                (lambda: mineru_task_store.is_current(request.book_id, request.expected_generation))
                if request.expected_generation is not None
                else None
            ),
        )
        mapped = _annotate_source_semantics(request, mapped)

        if request.expected_generation is not None and not mineru_task_store.is_current(
            request.book_id, request.expected_generation
        ):
            raise MinerUStaleResultError("MinerU mapping became obsolete before publication")

        pages = [parsed_page_from_page_result(page, self.name) for page in mapped.pages]
        return ParsedDocument(
            book_id=request.book_id,
            parser_name=self.name,
            pages=pages,
            scan=request.scan,
            metadata={
                "mineru_backend": execution.result.backend,
                "mineru_version": execution.result.version,
                "mineru_task_id": execution.task.task_id,
                "parse_generation": execution.parse_generation,
                "mineru_resumed": execution.resumed,
                "mineru_mapper_version": mapped.mapper_version,
                "mineru_quality": mapped.quality.model_dump(mode="json"),
                "mineru_mapping_stats": [item.model_dump(mode="json") for item in mapped.stats],
                "mineru_middle_json": mapped.raw_middle,
                "mineru_content_list": mapped.raw_content,
                "source_format": request.scan.file_type,
                "source_unit": request.scan.source_unit,
            },
            warnings=[warning.code for warning in mapped.warnings],
            assets=mapped.assets,
        )
