from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .quality import evaluate_text_quality

_CJK = re.compile(r"[\u3400-\u9fff]")
_LOG_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)")
_VLM_REFUSAL = re.compile(
    r"^(?:\[unreadable\]|the image is too .+|i (?:cannot|can't|am unable to) .+)$",
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strings(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        value = value.strip()
        if value:
            found.append(value)
    elif isinstance(value, list):
        for item in value:
            found.extend(_strings(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in {"path", "image_path", "img_path"}:
                found.extend(_strings(item))
    return found


def _legacy_block_text(block: dict[str, Any]) -> str:
    block_type = str(block.get("type", "unknown"))
    preferred: tuple[str, ...]
    if block_type == "image":
        preferred = ("image_caption",)
    elif block_type == "table":
        preferred = ("table_body", "table_caption")
    else:
        preferred = ("text", "code_body", "content", "list_items")
    values: list[str] = []
    for key in preferred:
        if key in block:
            values.extend(_strings(block[key]))
    return "\n".join(dict.fromkeys(values)).strip()


def _clean_blocks(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove obvious generation runaways while preserving an auditable record.

    A normal textbook page in this corpus stays below 2,000 characters. A scanned facsimile on
    one chapter opener caused the VLM to repeat invented English for tens of thousands of
    characters. The guard only activates on a page above 10,000 characters, then removes large
    foreign-background blocks, plus any single runaway block above 8,000 characters.
    """
    total_characters = sum(len(str(block["text"])) for block in blocks)
    severe_page_anomaly = total_characters > 10_000 or any(
        len(str(block["text"])) > 8_000 for block in blocks
    )
    kept: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for block in blocks:
        text = str(block["text"])
        compact_length = len(text.replace("\n", ""))
        cjk_ratio = len(_CJK.findall(text)) / max(1, compact_length)
        reason: str | None = None
        if _VLM_REFUSAL.fullmatch(text.strip()) or (
            str(block["type"]) in {"header", "footer"} and text.strip().lower() == "no"
        ):
            reason = "model_refusal_artifact"
        elif len(text) > 8_000:
            reason = "oversized_generation_runaway"
        elif severe_page_anomaly and len(text) >= 3 and cjk_ratio < 0.05:
            reason = "foreign_background_companion"
        if reason is None:
            kept.append(block)
        else:
            discarded.append(
                {
                    "block_index": block["block_index"],
                    "type": block["type"],
                    "text_characters": len(text),
                    "reason": reason,
                    "preview": text[:160],
                }
            )
    return kept, discarded


def load_pages(content_list_path: Path, expected_pages: int | None) -> list[dict[str, Any]]:
    raw = json.loads(content_list_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("content_list.json must contain a list")
    grouped: dict[int, list[dict[str, Any]]] = {}
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            continue
        page_number = int(value.get("page_idx", 0)) + 1
        text = _legacy_block_text(value)
        grouped.setdefault(page_number, []).append(
            {
                "block_index": index,
                "type": str(value.get("type", "unknown")),
                "bbox": value.get("bbox"),
                "text": text,
                "raw": value,
            }
        )
    page_count = expected_pages or max(grouped, default=0)
    pages: list[dict[str, Any]] = []
    for page_number in range(1, page_count + 1):
        blocks, discarded_blocks = _clean_blocks(grouped.get(page_number, []))
        pages.append(
            {
                "page_number": page_number,
                "blocks": blocks,
                "discarded_blocks": discarded_blocks,
                "text": "\n".join(block["text"] for block in blocks if block["text"]),
            }
        )
    return pages


def evaluate_page(page: dict[str, Any]) -> dict[str, Any]:
    text = str(page["text"])
    signals = evaluate_text_quality(text, expected_coverage=1.0 if page["blocks"] else 0.0)
    block_types = Counter(str(block["type"]) for block in page["blocks"])
    structured_content = bool({"table", "equation"} & set(block_types))
    score = (
        0.36 * signals.character_score
        + 0.32 * signals.language_score
        + 0.18 * signals.layout_score
        + 0.14 * (1.0 if page["blocks"] else 0.0)
    )
    score -= min(0.25, len(signals.suspicious_fragments) * 0.035)
    score = min(1.0, max(0.0, score))
    if not page["blocks"]:
        band = "missing"
    elif len(text.strip()) < 12:
        band = "visual_or_low_text"
    elif score >= 0.82 and not signals.suspicious_fragments:
        band = "accepted"
    elif score >= 0.66 or (structured_content and score >= 0.5):
        band = "review"
    else:
        band = "rescue"
    return {
        "page_number": page["page_number"],
        "text_characters": len(text),
        "block_count": len(page["blocks"]),
        "block_types": dict(block_types),
        "discarded_block_count": len(page.get("discarded_blocks", [])),
        "discarded_text_characters": sum(
            int(block["text_characters"]) for block in page.get("discarded_blocks", [])
        ),
        "heuristic_score": round(score, 4),
        "band": band,
        "quality_signals": signals.model_dump(mode="json"),
    }


def find_artifact(raw_dir: Path, suffix: str) -> Path:
    matches = sorted(raw_dir.rglob(f"*{suffix}"))
    if not matches:
        raise FileNotFoundError(f"MinerU output missing *{suffix}")
    return matches[0]


def infer_log_duration(log_path: Path) -> float:
    if not log_path.is_file():
        return 0.0
    timestamps: list[datetime] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LOG_TIMESTAMP.match(line)
        if match:
            timestamps.append(datetime.fromisoformat(match.group(1)))
    if len(timestamps) < 2:
        return 0.0
    return max(0.0, (timestamps[-1] - timestamps[0]).total_seconds())


def pdf_page_count(source: Path) -> int | None:
    try:
        import pymupdf

        with pymupdf.open(source) as document:  # type: ignore[no-untyped-call]
            return len(document)
    except (ImportError, OSError, RuntimeError):
        try:
            from pypdf import PdfReader

            return len(PdfReader(source).pages)
        except (ImportError, OSError, RuntimeError, ValueError):
            return None


def mineru_subprocess_environment() -> dict[str, str]:
    """Keep MinerU's loopback API traffic away from the outbound LLM proxy."""
    environment = os.environ.copy()
    loopback_hosts = ("localhost", "127.0.0.1", "::1")
    configured = environment.get("NO_PROXY", environment.get("no_proxy", ""))
    entries = [entry.strip() for entry in configured.split(",") if entry.strip()]
    lowered = {entry.lower() for entry in entries}
    entries.extend(host for host in loopback_hosts if host.lower() not in lowered)
    no_proxy = ",".join(entries)
    environment["NO_PROXY"] = no_proxy
    environment["no_proxy"] = no_proxy
    return environment


def run_mineru(source: Path, raw_dir: Path, backend: str, language: str) -> tuple[float, Path]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_path = raw_dir.parent / "mineru.log"
    command = [
        sys.executable,
        "-m",
        "mineru.cli.client",
        "-p",
        str(source),
        "-o",
        str(raw_dir),
        "-b",
        backend,
        "-l",
        language,
        "--image-analysis",
        "false",
    ]
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=4 * 60 * 60,
            env=mineru_subprocess_environment(),
        )
    duration = time.monotonic() - started
    if completed.returncode != 0:
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
        raise RuntimeError(f"MinerU exited with {completed.returncode}:\n{tail}")
    return duration, log_path


def write_report(
    *,
    source: Path,
    output_dir: Path,
    raw_dir: Path,
    backend: str,
    duration_seconds: float,
    log_path: Path,
) -> dict[str, Any]:
    content_list = find_artifact(raw_dir, "_content_list.json")
    markdown = find_artifact(raw_dir, ".md")
    expected_pages = pdf_page_count(source)
    pages = load_pages(content_list, expected_pages)
    page_reports = [evaluate_page(page) for page in pages]
    bands = Counter(report["band"] for report in page_reports)
    scores = [report["heuristic_score"] for report in page_reports if report["band"] != "missing"]
    manifest = {
        "source": str(source),
        "source_sha256": sha256_file(source),
        "backend": backend,
        "started_or_reported_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(duration_seconds, 2),
        "page_count_expected": expected_pages,
        "page_count_normalized": len(pages),
        "text_characters": sum(len(str(page["text"])) for page in pages),
        "blocks": sum(len(page["blocks"]) for page in pages),
        "discarded_blocks": sum(len(page["discarded_blocks"]) for page in pages),
        "discarded_text_characters": sum(
            int(block["text_characters"]) for page in pages for block in page["discarded_blocks"]
        ),
        "quality_band_counts": dict(bands),
        "mean_heuristic_score": round(sum(scores) / max(1, len(scores)), 4),
        "note": "heuristic_score is an automatic anomaly signal, not ground-truth CER accuracy",
        "artifacts": {
            "mineru_markdown": str(markdown),
            "mineru_content_list": str(content_list),
            "mineru_log": str(log_path),
        },
        "pages": page_reports,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "quality_report.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output_dir / "pages.jsonl").open("w", encoding="utf-8") as stream:
        for page in pages:
            stream.write(json.dumps(page, ensure_ascii=False) + "\n")
    cleaned_markdown = "\n\n".join(
        f"<!-- PDF page {page['page_number']} -->\n\n{page['text']}" for page in pages
    )
    (output_dir / "full_text.md").write_text(cleaned_markdown + "\n", encoding="utf-8")
    review_pages = [
        str(report["page_number"]) for report in page_reports if report["band"] == "review"
    ]
    critical_pages = [
        str(report["page_number"])
        for report in page_reports
        if report["band"] in {"rescue", "missing"}
    ]
    summary = f"""# OCR 质量报告

- 输入文件：{source.name}
- SHA-256：`{manifest["source_sha256"]}`
- 后端：`{backend}`
- 页数：{expected_pages}（标准化输出 {len(pages)} 页）
- 识别文本字符：{manifest["text_characters"]}
- 结构化块：{manifest["blocks"]}
- 已隔离异常块：{manifest["discarded_blocks"]}（{manifest["discarded_text_characters"]} 字符）
- 耗时：{duration_seconds:.1f} 秒
- 自动质量分布：{dict(bands)}
- 建议抽查页：{", ".join(review_pages) if review_pages else "无"}
- 缺失或救援页：{", ".join(critical_pages) if critical_pages else "无"}

> 自动质量分用于发现乱码、碎片、漏页和版面异常，不等同于有人工真值对照的字符错误率（CER）。
"""
    (output_dir / "quality_report.md").write_text(summary, encoding="utf-8")
    artifact_paths = [
        output_dir / "quality_report.json",
        output_dir / "quality_report.md",
        output_dir / "pages.jsonl",
        output_dir / "full_text.md",
        content_list,
        markdown,
    ]
    hashes = "\n".join(
        f"{sha256_file(path)}  {path.relative_to(output_dir.parent)}" for path in artifact_paths
    )
    (output_dir / "SHA256SUMS").write_text(hashes + "\n", encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MinerU and create auditable OCR artifacts")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", default="vlm-auto-engine")
    parser.add_argument("--language", default="ch")
    parser.add_argument("--skip-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    raw_dir = output / "raw"
    if not source.is_file():
        raise FileNotFoundError(source)
    if args.skip_run:
        log_path = output / "mineru.log"
        duration = infer_log_duration(log_path)
    else:
        duration, log_path = run_mineru(source, raw_dir, args.backend, args.language)
    report = write_report(
        source=source,
        output_dir=output / "normalized",
        raw_dir=raw_dir,
        backend=args.backend,
        duration_seconds=duration,
        log_path=log_path,
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "pages"}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
