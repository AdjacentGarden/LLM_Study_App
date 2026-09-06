from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from app.document.chunk_protocol import (  # noqa: E402
    BgeM3TokenCounter,
    FrozenChunkConfig,
    is_chunk_indexable,
    normalize_for_hash,
    render_embedding_text,
)
from app.document.chunker import build_chunks_v1  # noqa: E402
from app.document.chunker_v2 import build_chunks_v2  # noqa: E402
from app.schemas.books import Asset, Chapter  # noqa: E402


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_jsonl(path: Path, rows: list[object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            payload = row.model_dump(mode="json") if hasattr(row, "model_dump") else row
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def _paragraph(page: int, index: int, *, confidence: float | None = None) -> dict:
    sentence = (
        f"Module {page}.{index} explains how membrane gradients coordinate transport, "
        "energy conversion, feedback control, and measurable cellular responses. "
    )
    text = (sentence * 3).strip()
    return {
        "block_id": f"p{page:03d}_paragraph_{index:02d}",
        "page": page,
        "type": "paragraph",
        "text": text,
        "bbox": [80.0, 120.0 + index * 80, 520.0, 175.0 + index * 80],
        "confidence": confidence,
        "low_confidence": confidence is not None and confidence < 0.45,
        "source_parser": "mineru",
        "content_hash": f"paragraph-{page}-{index}",
        "metadata": {"page_size": [595.0, 842.0], "mineru_content_index": index},
    }


def _furniture(page: int) -> list[dict]:
    return [
        {
            "block_id": f"p{page:03d}_header",
            "page": page,
            "type": "paragraph",
            "text": "CloudPath frozen golden course",
            "bbox": [80.0, 20.0, 400.0, 45.0],
            "source_parser": "mineru",
            "metadata": {"page_size": [595.0, 842.0]},
        },
        {
            "block_id": f"p{page:03d}_number",
            "page": page,
            "type": "paragraph",
            "text": str(page),
            "bbox": [285.0, 810.0, 310.0, 830.0],
            "source_parser": "mineru",
            "metadata": {"page_size": [595.0, 842.0]},
        },
    ]


def _page(page: int, blocks: list[dict], *, quality: float = 0.98, metadata: dict | None = None) -> dict:
    return {
        "page": page,
        "pdf_page_index": page - 1,
        "text": "\n".join(str(block.get("text", "")) for block in blocks),
        "needs_ocr": False,
        "blocks": [*_furniture(page), *blocks],
        "ocr_provider": None,
        "ocr_confidence": 0.98,
        "parser": "mineru",
        "quality_score": quality,
        "source_width": 595.0,
        "source_height": 842.0,
        "metadata": {"mineru_version": "3.4.4", **(metadata or {})},
    }


def _fixture() -> tuple[list[dict], list[Chapter], list[Asset]]:
    pages: list[dict] = []
    title = {
        "block_id": "p001_title",
        "page": 1,
        "type": "title",
        "text": "Membrane energetics",
        "heading_level": 1,
        "bbox": [80.0, 70.0, 420.0, 105.0],
        "source_parser": "mineru",
        "metadata": {"page_size": [595.0, 842.0]},
    }
    pages.append(_page(1, [title, *[_paragraph(1, index) for index in range(1, 5)]]))
    pages.append(_page(2, [_paragraph(2, index) for index in range(1, 5)]))
    pages.append(_page(3, [_paragraph(3, index) for index in range(1, 5)]))

    rows = "".join(
        f"<tr><td>Pathway {index}</td><td>Signal {index}</td><td>{index * 2}</td></tr>"
        for index in range(1, 121)
    )
    table = {
        "block_id": "p004_table",
        "page": 4,
        "type": "table",
        "text": "Pathway Signal Value",
        "bbox": [60.0, 220.0, 535.0, 650.0],
        "source_parser": "mineru",
        "asset_ids": ["asset_table"],
        "content_hash": "large-table",
        "metadata": {
            "page_size": [595.0, 842.0],
            "table_caption": ["Measured pathway responses"],
            "table_body": (
                "<table><tr><th>Pathway</th><th>Signal</th><th>Value</th></tr>"
                f"{rows}</table>"
            ),
        },
    }
    before = _paragraph(4, 1)
    before["text"] = "Independent events multiply because one event does not alter the probability of the other."
    formula = {
        "block_id": "p004_formula",
        "page": 4,
        "type": "formula",
        "text": r"P(A \cap B)=P(A)P(B)",
        "bbox": [100.0, 150.0, 450.0, 195.0],
        "source_parser": "mineru",
        "content_hash": "formula",
        "metadata": {"page_size": [595.0, 842.0], "latex": r"P(A \cap B)=P(A)P(B)"},
    }
    after = _paragraph(4, 2)
    after["text"] = "The surrounding explanation supplies the assumptions and interpretation for the symbolic statement."
    pages.append(_page(4, [before, formula, after, table]))

    figure = {
        "block_id": "p005_figure",
        "page": 5,
        "type": "image",
        "text": "Diagram showing ATP-driven transport across a selective membrane.",
        "bbox": [90.0, 180.0, 500.0, 520.0],
        "source_parser": "mineru",
        "asset_ids": ["asset_figure"],
        "content_hash": "figure-caption",
        "metadata": {"page_size": [595.0, 842.0]},
    }
    low = _paragraph(5, 2, confidence=0.20)
    # Multiple independently poor quality-v1 dimensions make this fixture
    # unambiguously lower than 0.45; low OCR confidence alone is intentionally
    # not enough to override the frozen weighted formula.
    low["text"] = "\ufffd" * 10
    low["metadata"].update(
        {"mapping_completeness_score": 0.0, "duplicate_count": 1, "mapped_item_count": 1}
    )
    slide = _paragraph(5, 3)
    slide["metadata"].update({"slide_number": 7, "slide_title": "Transport summary", "source_format": "pptx"})
    sheet = _paragraph(5, 4)
    sheet["metadata"].update({"sheet_name": "Measurements", "cell_range": "A1:C24", "source_format": "xlsx"})
    pages.append(_page(5, [figure, low, slide, sheet]))

    chapters = [
        Chapter(
            chapter_id="chapter_root",
            level=1,
            source_title="Root course",
            ai_title="Root course",
            page_start=1,
            page_end=5,
            confidence=100,
            status="confirmed",
            source="golden",
        ),
        Chapter(
            chapter_id="chapter_deep",
            level=2,
            parent_id="chapter_root",
            source_title="Membrane systems",
            ai_title="Membrane systems",
            page_start=1,
            page_end=5,
            confidence=100,
            status="confirmed",
            source="golden",
        ),
    ]
    assets = [
        Asset(
            asset_id="asset_table",
            book_id="stage3_golden",
            chapter_id="chapter_deep",
            source_type="mineru",
            page=4,
            type="table",
            caption="Measured pathway responses",
            bbox=[60.0, 220.0, 535.0, 650.0],
            image_url="/golden/table",
            thumbnail_url="/golden/table-thumb",
            source_parser="mineru",
        ),
        Asset(
            asset_id="asset_figure",
            book_id="stage3_golden",
            chapter_id="chapter_deep",
            source_type="mineru",
            page=5,
            type="figure",
            caption="Diagram showing ATP-driven transport across a selective membrane.",
            bbox=[90.0, 180.0, 500.0, 520.0],
            image_url="/golden/figure",
            thumbnail_url="/golden/figure-thumb",
            source_parser="mineru",
        ),
    ]
    return pages, chapters, assets


def main() -> int:
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    run_dir = STAGE / "runs" / run_id
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=False)
    pages, chapters, assets = _fixture()
    _write_json(artifacts / "pages.json", pages)
    _write_json(artifacts / "assets.json", [asset.model_dump(mode="json") for asset in assets])

    config = FrozenChunkConfig()
    counter = BgeM3TokenCounter()
    first = build_chunks_v2("stage3_golden", artifacts, chapters, assets, config, counter)
    second = build_chunks_v2("stage3_golden", artifacts, chapters, assets, config, counter)
    legacy = build_chunks_v1("stage3_golden", artifacts, chapters)
    # Chunk V2 establishes both directions of every semantic Asset relation.
    # Persist the post-build Assets, rather than the unbound fixture input, so
    # this golden directory is a representative publishable pair.
    _write_json(artifacts / "assets.json", [asset.model_dump(mode="json") for asset in assets])
    _write_jsonl(artifacts / "chunks_v2.jsonl", first)
    _write_jsonl(artifacts / "chunks_v1.jsonl", legacy)

    rendered = [
        render_embedding_text(
            chunk.text,
            chunk.heading_path,
            str(chunk.metadata.get("overlap_text") or ""),
        )
        for chunk in first
    ]
    warnings = [str(value) for chunk in first for value in chunk.metadata.get("warnings", [])]
    standard = [chunk for chunk in first if chunk.content_type == "text"]
    atomic = [chunk for chunk in first if chunk.content_type in {"table", "formula", "figure", "chart"}]
    table_chunks = [chunk for chunk in first if chunk.content_type == "table"]
    formula_chunks = [chunk for chunk in first if chunk.content_type == "formula"]
    figure_chunks = [chunk for chunk in first if chunk.content_type in {"figure", "chart"}]
    quarantined = [chunk for chunk in first if not is_chunk_indexable(chunk)]
    overlap_tokens = sum(counter.count(str(chunk.metadata.get("overlap_text") or "")) for chunk in first)
    total_tokens = sum(int(chunk.token_count or 0) for chunk in first)
    duplicate_count = len(rendered) - len({normalize_for_hash(text) for text in rendered})
    short_unexplained = [
        chunk.chunk_id
        for chunk in standard
        if int(chunk.token_count or 0) < config.min_tokens
        and "short_tail" not in chunk.metadata.get("warnings", [])
    ]
    chunks_by_id = {chunk.chunk_id: chunk for chunk in first}
    assets_by_id = {asset.asset_id: asset for asset in assets}
    asset_links_bidirectional = all(
        asset_id in assets_by_id
        and chunk.chunk_id in assets_by_id[asset_id].source_chunk_ids
        for chunk in first
        for asset_id in chunk.asset_ids
    ) and all(
        source_id in chunks_by_id
        and asset.asset_id in chunks_by_id[source_id].asset_ids
        for asset in assets
        for source_id in asset.source_chunk_ids
    )
    overlap_protocol = True
    previous_standard = None
    for chunk in standard:
        overlap = str(chunk.metadata.get("overlap_text") or "")
        overlap_count = counter.count(overlap)
        if overlap_count > config.overlap_tokens:
            overlap_protocol = False
        if overlap and (
            previous_standard is None
            or not str(previous_standard.metadata.get("body") or previous_standard.text).endswith(overlap)
            or overlap_count / max(1, int(chunk.token_count or 0)) > 0.20
        ):
            overlap_protocol = False
        previous_standard = chunk

    checks = {
        "stable_chunk_ids": [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second],
        "all_v2": bool(first) and all(chunk.chunk_version == "v2" for chunk in first),
        "token_counts_exact": all(chunk.token_count == counter.count(text) for chunk, text in zip(first, rendered, strict=True)),
        "standard_max": all(int(chunk.token_count or 0) <= config.max_tokens for chunk in standard),
        "atomic_hard_max": all(int(chunk.token_count or 0) <= config.atomic_content_hard_max_tokens for chunk in atomic),
        "short_tail_protocol": not short_unexplained,
        "exact_duplicate_ratio": duplicate_count == 0,
        "overlap_ratio": (overlap_tokens / total_tokens if total_tokens else 0.0) <= 0.20,
        "per_chunk_overlap_protocol": overlap_protocol,
        "cross_page": any(chunk.page_start < chunk.page_end for chunk in standard),
        "deepest_chapter_only": all(chunk.chapter_id == "chapter_deep" for chunk in first),
        "heading_in_embedding": all(
            not chunk.heading_path or text.startswith(" > ".join(chunk.heading_path))
            for chunk, text in zip(first, rendered, strict=True)
        ),
        "table_split_with_header": len(table_chunks) >= 2 and all("Pathway | Signal | Value" in chunk.text for chunk in table_chunks),
        "formula_latex_and_context": bool(formula_chunks) and all("P(A" in chunk.text and "Explanation:" in chunk.text for chunk in formula_chunks),
        "figure_asset_binding": any("asset_figure" in chunk.asset_ids for chunk in figure_chunks),
        "table_asset_binding": all("asset_table" in chunk.asset_ids for chunk in table_chunks),
        "asset_links_bidirectional": asset_links_bidirectional,
        "furniture_filtered": all("CloudPath frozen golden course" not in chunk.text for chunk in first),
        "quality_quarantine": bool(quarantined) and all(not is_chunk_indexable(chunk) for chunk in quarantined),
        "office_location_metadata": any(chunk.metadata.get("slide_number") == 7 for chunk in first) and any(chunk.metadata.get("sheet_name") == "Measurements" for chunk in first),
        "legacy_comparison_available": bool(legacy),
    }
    failures = [name for name, passed in checks.items() if not passed]
    summary = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "config": {
            "target_tokens": config.target_tokens,
            "max_tokens": config.max_tokens,
            "min_tokens": config.min_tokens,
            "overlap_tokens": config.overlap_tokens,
            "atomic_hard_max_tokens": config.atomic_content_hard_max_tokens,
            "quality_threshold": config.quality_threshold,
        },
        "v1_chunk_count": len(legacy),
        "v2_chunk_count": len(first),
        "v2_indexable_count": sum(is_chunk_indexable(chunk) for chunk in first),
        "v2_quarantined_count": len(quarantined),
        "asset_count": len(assets),
        "asset_source_link_count": sum(len(asset.source_chunk_ids) for asset in assets),
        "token_counts": [chunk.token_count for chunk in first],
        "token_min": min((int(chunk.token_count or 0) for chunk in first), default=0),
        "token_max": max((int(chunk.token_count or 0) for chunk in first), default=0),
        "overlap_token_count": overlap_tokens,
        "overlap_duplicate_token_ratio": round(overlap_tokens / total_tokens, 6) if total_tokens else 0.0,
        "exact_duplicate_count": duplicate_count,
        "short_unexplained": short_unexplained,
        "warnings": warnings,
        "checks": checks,
        "failures": failures,
        "passed": not failures,
    }
    _write_json(run_dir / "summary.json", summary)
    _write_json(STAGE / "chunker_golden.json", summary)
    (STAGE / "latest_golden_run.txt").write_text(run_id + "\n", encoding="utf-8")
    print(json.dumps({"run_id": run_id, "v1": len(legacy), "v2": len(first), "failures": failures, "passed": not failures}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
