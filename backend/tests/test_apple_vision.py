from __future__ import annotations

import json
import math
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from adaptive_learning.ingestion import apple_vision, ocr_job
from adaptive_learning.ingestion.chaptering import load_normalized_pages
from adaptive_learning.ingestion.models import ExtractionMethod


@pytest.fixture(scope="module")
def vision_render_probe(tmp_path_factory):
    if platform.system() != "Darwin" or not shutil.which("swiftc"):
        pytest.skip("The CoreGraphics rendering regression requires macOS and Swift")
    directory = tmp_path_factory.mktemp("vision-render-probe")
    helper_source = Path(apple_vision.__file__).with_name("apple_vision_ocr.swift").read_text()
    # Exercise the production renderer without running OCR or loading a model.
    render_source = helper_source.split("func main() throws {", 1)[0]
    probe_source = directory / "probe.swift"
    probe_source.write_text(render_source + r'''
let document = CGPDFDocument(URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL)!
let image = try render(document.page(at: 1)!, dpi: 200)
let bytes = CFDataGetBytePtr(image.dataProvider!.data!)!
var left = image.width, right = -1, bottom = image.height, top = -1
for y in 0..<image.height {
    for x in 0..<image.width {
        let offset = y * image.bytesPerRow + x * 4
        if bytes[offset] < 32 && bytes[offset + 1] < 32 && bytes[offset + 2] < 32 {
            left = min(left, x); right = max(right, x)
            bottom = min(bottom, y); top = max(top, y)
        }
    }
}
let result: [String: Any] = ["width": image.width, "height": image.height,
    "left": left, "right": right, "bottom": bottom, "top": top]
print(String(data: try JSONSerialization.data(withJSONObject: result), encoding: .utf8)!)
''')
    binary = directory / "probe"
    subprocess.run(["swiftc", str(probe_source), "-o", str(binary)],
                   check=True, capture_output=True, timeout=120)
    return binary


def _write_render_pdf(path: Path, *, rotation: int, cropped: bool) -> tuple[float, float]:
    # A black rectangle near every page edge exposes a 72-DPI page incorrectly
    # centered inside an otherwise correctly sized 200-DPI bitmap.
    crop = "/CropBox [20 30 575.28 811.89]" if cropped else ""
    rectangle = "30 40 535.28 761.89" if cropped else "10 10 575.28 821.89"
    stream = f"0 0 0 rg {rectangle} re f\n".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] "
         f"{crop} /Rotate {rotation} /Resources << >> /Contents 4 0 R >>").encode(),
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(data)
    width, height = (555.28, 781.89) if cropped else (595.28, 841.89)
    return (height, width) if rotation == 90 else (width, height)


@pytest.mark.parametrize("rotation,cropped", [(0, False), (90, False), (0, True), (90, True)])
def test_a4_200_dpi_scales_page_content_with_crop_and_rotation(
    tmp_path: Path, vision_render_probe: Path, rotation: int, cropped: bool
):
    source = tmp_path / "a4.pdf"
    width_points, height_points = _write_render_pdf(source, rotation=rotation, cropped=cropped)
    completed = subprocess.run([str(vision_render_probe), str(source)],
                               check=True, capture_output=True, text=True, timeout=30)
    result = json.loads(completed.stdout)
    assert result["width"] == math.ceil(width_points * 200 / 72)
    assert result["height"] == math.ceil(height_points * 200 / 72)
    assert result["left"] < result["width"] * 0.035
    assert result["bottom"] < result["height"] * 0.035
    assert result["right"] > result["width"] * 0.965
    assert result["top"] > result["height"] * 0.965


def test_apple_vision_refuses_other_platforms(monkeypatch):
    monkeypatch.setattr(apple_vision.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="requires macOS"):
        apple_vision.native_helper()


def test_apple_vision_requires_an_explicit_executable_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(apple_vision.platform, "system", lambda: "Darwin")
    binary = tmp_path / "not-executable"
    binary.write_text("fixture")
    monkeypatch.setenv("APPLE_VISION_OCR_BINARY", str(binary))
    with pytest.raises(RuntimeError, match="absolute executable"):
        apple_vision.native_helper()


@pytest.mark.parametrize("tamper", [None, "missing_page", "wrong_engine"])
def test_vision_report_retains_source_confidence_and_rejects_incomplete_provenance(
    monkeypatch, tmp_path: Path, tamper
):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    monkeypatch.setattr(ocr_job, "pdf_page_count", lambda _: 2)
    raw = tmp_path / "raw"
    raw.mkdir()
    metadata = {
        "status": "complete", "pages": 2, "page_numbers": [1, 2], "empty_pages": [2],
        "capabilities": {"engine": "apple-vision", "framework": "Vision", "resident_gpu": False},
    }
    if tamper == "missing_page":
        metadata["page_numbers"] = [1]
    if tamper == "wrong_engine":
        metadata["capabilities"]["engine"] = "unknown"
    (raw / "fast_ocr_metadata.json").write_text(json.dumps(metadata))
    (raw / "vision_content_list.json").write_text(json.dumps([
        {"page_idx": 0, "type": "text", "text": "可核验的教材原文与对应文字位置。",
         "confidence": 0.71, "bbox": [10, 20, 800, 90], "ocr_backend": "apple-vision"}
    ]))
    (raw / "vision.md").write_text("Original text")
    options = dict(source=source, raw_dir=raw, output_dir=tmp_path / "normalized",
                   backend="apple-vision", duration_seconds=1, log_path=raw / "vision.log")
    if tamper:
        with pytest.raises(RuntimeError):
            ocr_job.write_report(**options)
        assert not (tmp_path / "normalized/quality_report.json").exists()
        return
    report = ocr_job.write_report(**options)
    assert report["quality_band_counts"]["missing"] == 1
    pages = load_normalized_pages(tmp_path / "normalized/pages.jsonl")
    assert pages[0].method == ExtractionMethod.APPLE_VISION
    assert pages[0].blocks[0].confidence == 0.71
    assert pages[0].blocks[0].metadata["ocr_capabilities"]["framework"] == "Vision"
    assert pages[1].raw_text == ""
