from __future__ import annotations

from pathlib import Path

from app.document.ocr_cache import OCRResultCache
from app.schemas.books import QualityWarning, TextBlock


def test_ocr_cache_round_trip_and_content_fingerprint(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"stable-image-content")
    cache = OCRResultCache(tmp_path / "cache", enabled=True, ttl_seconds=60, max_items=4)
    key = cache.key_for(
        image,
        page=1,
        provider="paddleocr-vl",
        model="PaddleOCR-VL-1.6",
        language="ch",
        device="cpu",
        pipeline_version="v1.6",
        quality_profile="balanced",
    )
    blocks = [TextBlock(block_id="b1", page=1, type="text", text="教材内容", confidence=0.97)]
    warnings = [QualityWarning(page=1, code="notice", message="diagnostic")]

    assert cache.get(key) is None
    cache.put(key, blocks, warnings)
    restored = cache.get(key)

    assert restored is not None
    assert restored[0][0].text == "教材内容"
    assert restored[1][0].code == "notice"

    image.write_bytes(b"changed-image-content")
    changed_key = cache.key_for(
        image,
        page=1,
        provider="paddleocr-vl",
        model="PaddleOCR-VL-1.6",
        language="ch",
        device="cpu",
        pipeline_version="v1.6",
        quality_profile="balanced",
    )
    assert changed_key != key
