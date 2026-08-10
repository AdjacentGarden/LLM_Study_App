from __future__ import annotations

from html import escape
import json
from pathlib import Path
import textwrap

from app.core.errors import AppError


def _xml_text(value: object) -> str:
    text = str(value)
    valid = "".join(
        character
        for character in text
        if character in "\t\n\r"
        or 0x20 <= ord(character) <= 0xD7FF
        or 0xE000 <= ord(character) <= 0xFFFD
        or 0x10000 <= ord(character) <= 0x10FFFF
    )
    return escape(valid)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError("office_preview_unavailable", "Office 预览数据尚不可用", status_code=404) from exc


def render_office_preview(artifact_root: Path, page: int) -> Path:
    """Render a safe text-only SVG when MinerU did not return a page image.

    User-controlled text is XML-escaped and never interpreted as markup.  The
    fallback deliberately represents logical Office units rather than claiming
    that DOCX has stable rendered page numbers.
    """

    scan_payload = _read_json(artifact_root / "scan_result.json")
    pages_payload = _read_json(artifact_root / "pages.json")
    if not isinstance(scan_payload, dict) or not isinstance(pages_payload, list):
        raise AppError("office_preview_unavailable", "Office 预览数据格式无效", status_code=404)

    page_payload = next(
        (
            item
            for item in pages_payload
            if isinstance(item, dict) and int(item.get("page") or 0) == page
        ),
        None,
    )
    if page_payload is None:
        raise AppError(
            "page_out_of_range",
            "位置超出原文范围",
            details={"page_count": int(scan_payload.get("page_count") or 0)},
            status_code=404,
        )

    locations = scan_payload.get("source_locations")
    location_items = locations if isinstance(locations, list) else []
    location = next(
        (
            item
            for item in location_items
            if isinstance(item, dict)
            and int(item.get("index") or 0) == page
        ),
        {},
    )
    label = str(location.get("label") or f"逻辑位置 {page}")
    source_format = str(scan_payload.get("file_type") or "office").upper()
    body = str(page_payload.get("text") or "解析内容暂不可用")
    lines: list[str] = []
    for paragraph in body.splitlines() or [body]:
        lines.extend(textwrap.wrap(paragraph, width=72, break_long_words=True) or [""])
    lines = lines[:28]

    output_dir = artifact_root / "page_images"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"office_{page:03d}.svg"
    text_nodes = "\n".join(
        f'<text x="72" y="{190 + index * 30}" class="body">{_xml_text(line)}</text>'
        for index, line in enumerate(lines)
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1000" viewBox="0 0 1200 1000">
  <rect width="1200" height="1000" fill="#f5f8ff"/>
  <rect x="42" y="42" width="1116" height="916" rx="20" fill="#ffffff" stroke="#ccd7ee" stroke-width="2"/>
  <text x="72" y="100" class="format">{_xml_text(source_format)} 安全预览</text>
  <text x="72" y="146" class="title">{_xml_text(label)}</text>
  {text_nodes}
  <style>
    .format {{ font: 600 24px system-ui, sans-serif; fill: #56709c; }}
    .title {{ font: 700 30px system-ui, sans-serif; fill: #17243c; }}
    .body {{ font: 21px system-ui, sans-serif; fill: #293a56; }}
  </style>
</svg>'''
    output.write_text(svg, encoding="utf-8")
    return output


__all__ = ["render_office_preview"]
