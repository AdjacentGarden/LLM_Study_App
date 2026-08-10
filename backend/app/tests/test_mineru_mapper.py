from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
import pytest

from app.document.mineru.exceptions import MinerUStaleResultError
from app.document.mineru.mapper import MAPPER_VERSION, map_mineru_document
from app.document.mineru.models import MinerUResultDocument, MinerUResultResponse


ROOT = Path(__file__).resolve().parents[3]
PROBE_ROOT = ROOT / "quality" / "stage0" / "mineru_probe"


def _probe_document(name: str) -> tuple[MinerUResultDocument, str, str]:
    payload = json.loads((PROBE_ROOT / name).read_text(encoding="utf-8"))
    response = MinerUResultResponse.model_validate(payload)
    return next(iter(response.results.values())), response.backend, response.version


def _image_data_uri(*, color: tuple[int, int, int]) -> str:
    image = Image.new("RGB", (20, 12), color=color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def test_maps_real_native_probe_titles_pages_and_denormalized_bbox(tmp_path: Path) -> None:
    document, backend, version = _probe_document("native_text_pdf_result.json")

    mapped = map_mineru_document(
        "book_native",
        document,
        backend=backend,
        version=version,
        asset_root=tmp_path / "assets",
        expected_page_count=2,
    )

    assert mapped.mapper_version == MAPPER_VERSION
    assert [page.page for page in mapped.pages] == [1, 2]
    assert mapped.pages[0].pdf_page_index == 0
    assert mapped.pages[0].parser == "mineru"
    assert mapped.pages[0].source_width == 595
    assert mapped.pages[0].source_height == 842
    first = mapped.pages[0].blocks[0]
    assert first.type == "title"
    assert first.heading_level == 1
    assert first.text == "Synthetic Biology Course"
    assert first.bbox == pytest.approx([68.425, 61.466, 296.905, 85.884], abs=0.001)
    assert first.metadata["normalized_bbox"] == [115, 73, 499, 102]
    assert first.source_parser == "mineru"
    assert first.content_hash
    assert mapped.pages[0].layout_regions[0].bbox == first.bbox
    assert mapped.quality.passed is True
    assert mapped.quality.problem_pages == []
    assert mapped.raw_content[0]["text"] == "Synthetic Biology Course"
    assert mapped.raw_middle["_version_name"] == "3.4.4"

    repeated = map_mineru_document(
        "book_native",
        document,
        backend=backend,
        version=version,
        asset_root=tmp_path / "assets_repeat",
        expected_page_count=2,
    )
    assert [block.block_id for page in repeated.pages for block in page.blocks] == [
        block.block_id for page in mapped.pages for block in page.blocks
    ]


def test_maps_real_table_probe_with_structured_metadata(tmp_path: Path) -> None:
    document, backend, version = _probe_document("multicolumn_table_formula_pdf_result.json")

    mapped = map_mineru_document(
        "book_table",
        document,
        backend=backend,
        version=version,
        asset_root=tmp_path / "assets",
        expected_page_count=1,
    )

    table = next(block for block in mapped.pages[0].blocks if block.type == "table")
    assert table.content_format == "html"
    assert "Stage Chromosome state" in table.text
    assert "<table>" not in table.text
    assert "DNA replication" in table.text
    assert table.metadata["table_caption"] == ["Formula: P(A and B) = P(A) \\* P(B)"]
    assert table.metadata["table_body"].startswith("<table>")
    assert table.bbox == pytest.approx([47.6, 486.676, 519.435, 616.344], abs=0.001)
    assert any(warning.code == "mineru_image_payload_missing" for warning in mapped.warnings)
    assert mapped.quality.passed is True


def test_maps_all_required_types_and_materializes_validated_assets(tmp_path: Path) -> None:
    red = _image_data_uri(color=(220, 20, 20))
    blue = _image_data_uri(color=(20, 20, 220))
    green = _image_data_uri(color=(20, 220, 20))
    document = MinerUResultDocument(
        middle_json={
            "pdf_info": [
                {
                    "page_idx": 0,
                    "page_size": [200, 100],
                    "para_blocks": [{"score": 0.9}],
                }
            ]
        },
        content_list=[
            {"type": "text", "text": "Course title", "text_level": 1, "bbox": [10, 20, 500, 100], "page_idx": 0},
            {"type": "text", "text": "A sufficiently long paragraph for quality validation.", "bbox": [10, 120, 900, 220], "page_idx": 0},
            {"type": "list", "list_items": ["alpha", "beta"], "bbox": [10, 230, 500, 330], "page_idx": 0},
            {"type": "equation", "text": "E = mc^2", "text_format": "latex", "bbox": [10, 340, 400, 420], "page_idx": 0},
            {
                "type": "table",
                "table_caption": ["Measurements"],
                "table_body": "<table><tr><td>x</td><td>1</td></tr></table>",
                "table_footnote": ["SI units"],
                "img_path": "images/table.png",
                "bbox": [10, 430, 800, 650],
                "page_idx": 0,
            },
            {
                "type": "image",
                "image_caption": ["Cell structure"],
                "image_footnote": ["schematic"],
                "img_path": "images/figure.png",
                "bbox": [10, 660, 400, 950],
                "page_idx": 0,
            },
            {
                "type": "chart",
                "chart_caption": ["Growth curve"],
                "chart_footnote": [],
                "content": "Growth increases over time.",
                "img_path": "images/chart.png",
                "bbox": [420, 660, 900, 950],
                "page_idx": 0,
            },
        ],
        images={"table.png": red, "figure.png": blue, "chart.png": green},
    )

    mapped = map_mineru_document(
        "book_all_types",
        document,
        backend="pipeline",
        version="3.4.4",
        asset_root=tmp_path / "assets",
        expected_page_count=1,
    )

    blocks = mapped.pages[0].blocks
    assert [block.type for block in blocks] == [
        "title",
        "paragraph",
        "list",
        "formula",
        "table",
        "figure",
        "chart",
    ]
    assert blocks[0].bbox == [2.0, 2.0, 100.0, 10.0]
    assert blocks[2].text == "- alpha\n- beta"
    assert blocks[3].content_format == "latex"
    assert blocks[3].text == "E = mc^2"
    assert len(mapped.assets) == 3
    assert {asset.type for asset in mapped.assets} == {"table", "figure", "chart"}
    assert all(asset.source_parser == "mineru" for asset in mapped.assets)
    assert all(asset.content_hash for asset in mapped.assets)
    assert all(block.asset_ids for block in blocks[-3:])
    for asset in mapped.assets:
        files = list((tmp_path / "assets").glob(f"{asset.asset_id}.*"))
        thumbnails = list((tmp_path / "assets").glob(f"thumb_{asset.asset_id}.*"))
        assert len(files) == 1
        assert len(thumbnails) == 1
        assert "base64" not in json.dumps(asset.model_dump(mode="json"))
    assert mapped.quality.passed is True


def test_real_mixed_probe_marks_asset_only_chart_page_for_fallback(tmp_path: Path) -> None:
    document, backend, version = _probe_document("mixed_pdf_result.json")

    mapped = map_mineru_document(
        "book_mixed",
        document,
        backend=backend,
        version=version,
        asset_root=tmp_path / "assets",
        expected_page_count=2,
    )

    assert mapped.pages[0].needs_ocr is False
    assert mapped.pages[1].needs_ocr is True
    assert mapped.pages[1].blocks[0].type == "chart"
    assert mapped.pages[1].blocks[0].text == ""
    assert mapped.quality.passed is False
    assert mapped.quality.problem_pages == [2]
    assert mapped.quality.source_page_coverage == 1.0


def test_duplicate_unknown_out_of_range_and_missing_page_are_auditable(tmp_path: Path) -> None:
    repeated = {"type": "text", "text": "This is repeated semantic content.", "bbox": [0, 0, 500, 100], "page_idx": 0}
    document = MinerUResultDocument(
        middle_json={"pdf_info": [{"page_idx": 0, "page_size": [100, 200]}]},
        content_list=[
            repeated,
            dict(repeated),
            {"type": "future_type", "text": "unknown", "page_idx": 0},
            {"type": "text", "text": "outside", "page_idx": 9},
        ],
        images={},
    )

    mapped = map_mineru_document(
        "book_bad_shape",
        document,
        backend="pipeline",
        version="3.4.4",
        asset_root=tmp_path / "assets",
        expected_page_count=2,
    )

    assert len(mapped.pages[0].blocks) == 1
    assert mapped.stats[0].source_item_count == 3
    assert mapped.stats[0].mapped_item_count == 2
    assert mapped.stats[0].duplicate_count == 1
    assert mapped.stats[0].unknown_item_count == 1
    assert mapped.pages[1].metadata["source_page_missing"] is True
    assert mapped.quality.missing_pages == [2]
    # quality-v1 records the duplicate/unknown item penalties, but the usable
    # first page remains above the frozen 0.60 page threshold.
    assert mapped.quality.problem_pages == [2]
    codes = {warning.code for warning in mapped.warnings}
    assert "mineru_block_duplicate" in codes
    assert "mineru_content_type_unknown" in codes
    assert "mineru_content_page_out_of_range" in codes
    assert "mineru_source_page_missing" in codes


def test_generation_guard_rejects_stale_mapping_before_writes(tmp_path: Path) -> None:
    document = MinerUResultDocument(
        middle_json={"pdf_info": [{"page_idx": 0, "page_size": [20, 12]}]},
        content_list=[
            {
                "type": "image",
                "image_caption": ["guarded image"],
                "img_path": "images/guard.png",
                "page_idx": 0,
            }
        ],
        images={"guard.png": _image_data_uri(color=(1, 2, 3))},
    )

    with pytest.raises(MinerUStaleResultError):
        map_mineru_document(
            "book_stale",
            document,
            backend="pipeline",
            version="3.4.4",
            asset_root=tmp_path / "assets",
            expected_page_count=1,
            generation_guard=lambda: False,
        )

    assert not (tmp_path / "assets").exists()
