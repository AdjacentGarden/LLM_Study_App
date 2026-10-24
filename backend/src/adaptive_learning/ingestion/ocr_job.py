from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from ..config import get_settings
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
    elif score >= get_settings().ocr_page_quality_accept and not signals.suspicious_fragments:
        band = "accepted"
    elif score >= get_settings().ocr_page_quality_review or (structured_content and score >= get_settings().ocr_page_structured_review):
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


def mineru_api_url() -> str | None:
    """Accept only an explicitly configured local HTTP MinerU API."""
    configured = os.environ.get("MINERU_API_URL", "").strip()
    if not configured:
        return None
    message = "MINERU_API_URL must be a loopback HTTP URL without credentials, query, or fragment"
    try:
        parsed = urlsplit(configured)
        hostname = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme != "http"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in configured)
            or (port is not None and port < 1)
        ):
            raise ValueError(message)
        if hostname.lower() != "localhost" and not ipaddress.ip_address(hostname).is_loopback:
            raise ValueError(message)
    except ValueError as error:
        raise ValueError(message) from error
    return configured.rstrip("/")


def mineru_subprocess_environment() -> dict[str, str]:
    """Keep MinerU's loopback API traffic away from the outbound LLM proxy."""
    environment = os.environ.copy()
    # ORT 1.29's telemetry uploader can wait on network retries during native
    # process teardown. Set this before the CLI (and its API child) imports ORT.
    environment["ORT_DISABLE_TELEMETRY"] = "1"
    loopback_hosts = ["localhost", "127.0.0.1", "::1"]
    api_url = mineru_api_url()
    if api_url:
        api_host = urlsplit(api_url).hostname
        if api_host and api_host not in loopback_hosts:
            loopback_hosts.append(api_host)
    configured = environment.get("NO_PROXY", environment.get("no_proxy", ""))
    entries = [entry.strip() for entry in configured.split(",") if entry.strip()]
    lowered = {entry.lower() for entry in entries}
    entries.extend(host for host in loopback_hosts if host.lower() not in lowered)
    no_proxy = ",".join(entries)
    environment["NO_PROXY"] = no_proxy
    environment["no_proxy"] = no_proxy
    return environment


def mineru_engine_arguments() -> list[str]:
    """Read bounded engine options without accepting arbitrary MinerU arguments."""
    configured = os.environ.get("MINERU_ENGINE_ARGS_JSON", "[]")
    try:
        arguments = json.loads(configured)
    except json.JSONDecodeError as error:
        raise ValueError("MINERU_ENGINE_ARGS_JSON must be a JSON argument list") from error
    if not isinstance(arguments, list) or len(arguments) % 2:
        raise ValueError("MINERU_ENGINE_ARGS_JSON must contain option/value pairs")
    fractions = {"--cache-max-entry-count", "--gpu-memory-utilization"}
    integers = {"--max-batch-size", "--session-len"}
    validated: list[str] = []
    seen: set[str] = set()
    for offset in range(0, len(arguments), 2):
        option, value = arguments[offset : offset + 2]
        if not isinstance(option, str) or option not in fractions | integers:
            raise ValueError("MINERU_ENGINE_ARGS_JSON contains an unsupported engine option")
        if option in seen:
            raise ValueError(f"MINERU_ENGINE_ARGS_JSON repeats {option}")
        seen.add(option)
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"{option} requires a numeric value")
        rendered = str(value)
        if option in integers:
            if not re.fullmatch(r"[0-9]+", rendered) or int(rendered) < 1:
                raise ValueError(f"{option} requires a positive integer")
        else:
            try:
                fraction = float(rendered)
            except ValueError as error:
                raise ValueError(
                    f"{option} requires a fraction greater than 0 and at most 1"
                ) from error
            if not math.isfinite(fraction) or not 0 < fraction <= 1:
                raise ValueError(f"{option} requires a fraction greater than 0 and at most 1")
        validated.extend((option, rendered))
    return validated


def run_mineru(source: Path, raw_dir: Path, backend: str, language: str) -> tuple[float, Path]:
    api_url = mineru_api_url()
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_path = raw_dir.parent / "mineru.log"
    command = [
        os.getenv("OCR_PYTHON") or sys.executable,
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
    if api_url:
        # Model/runtime options belong to the resident service. A failed API call
        # must remain a failure rather than silently loading a second local model.
        command.extend(("--api-url", api_url))
    else:
        command.extend(mineru_engine_arguments())
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


def run_paddle_gpu(source: Path, raw_dir: Path) -> tuple[float, Path]:
    """Request the resident GPU text-line engine; never fall back to another OCR."""
    configured = os.getenv("FAST_OCR_API_URL", "http://127.0.0.1:18201").strip()
    message = "FAST_OCR_API_URL must be a loopback HTTP origin without credentials or a path"
    try:
        parsed = urlsplit(configured)
        hostname, port = parsed.hostname, parsed.port
        if (
            parsed.scheme != "http"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (port is not None and port < 1)
            or any(character.isspace() for character in configured)
        ):
            raise ValueError(message)
        if hostname != "localhost" and not ipaddress.ip_address(hostname).is_loopback:
            raise ValueError(message)
    except ValueError as error:
        raise ValueError(message) from error
    request = Request(
        configured.rstrip("/") + "/ocr",
        data=json.dumps(
            {"source": str(source.resolve()), "output": str(raw_dir.resolve())}
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Local paths are passed over loopback; outbound model proxies see no PDF.
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=4 * 60 * 60) as response:
        payload = response.read(65_537)
    if len(payload) > 65_536:
        raise RuntimeError("Fast OCR returned an oversized result")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise RuntimeError("Fast OCR did not return an object result")
    duration = result.get("duration_seconds")
    if (
        result.get("status") != "complete"
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
    ):
        raise RuntimeError("Fast OCR did not return a completed, timed GPU result")
    expected = raw_dir.resolve()
    artifacts = {}
    for name in ("content_list", "markdown", "log_path"):
        value = result.get(name)
        if not isinstance(value, str):
            raise RuntimeError("Fast OCR omitted an output artifact")
        path = Path(value).resolve(strict=True)
        if not path.is_relative_to(expected) or not path.is_file():
            raise RuntimeError("Fast OCR returned an artifact outside the requested output")
        artifacts[name] = path
    if not artifacts["content_list"].name.endswith("_content_list.json"):
        raise RuntimeError("Fast OCR returned an invalid content-list artifact")
    return float(duration), artifacts["log_path"]


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
    fast_metadata = None
    if backend == "paddle-gpu":
        fast_metadata = json.loads((raw_dir / "fast_ocr_metadata.json").read_text(encoding="utf-8"))
        if (
            not isinstance(fast_metadata, dict)
            or expected_pages is None
            or fast_metadata.get("status") != "complete"
            or type(fast_metadata.get("pages")) is not int
            or fast_metadata["pages"] != expected_pages
            or fast_metadata.get("page_numbers") != list(range(1, expected_pages + 1))
        ):
            raise RuntimeError("Fast OCR metadata does not cover the entire source PDF")
        capabilities = fast_metadata.get("capabilities")
        if not isinstance(capabilities, dict) or not isinstance(capabilities.get("engine"), str):
            raise RuntimeError("Fast OCR metadata omitted engine capabilities")
        raw_blocks = json.loads(content_list.read_text(encoding="utf-8"))
        if not isinstance(raw_blocks, list) or any(
            not isinstance(block, dict)
            or type(block.get("page_idx")) is not int
            or not 0 <= block["page_idx"] < expected_pages
            or not isinstance(block.get("text"), str)
            for block in raw_blocks
        ):
            raise RuntimeError("Fast OCR returned an invalid or out-of-range source page")
        empty_pages = [
            page
            for page in range(1, expected_pages + 1)
            if not any(
                block["text"].strip() for block in raw_blocks if block["page_idx"] == page - 1
            )
        ]
        if fast_metadata.get("empty_pages") != empty_pages:
            raise RuntimeError("Fast OCR metadata disagrees with recognized page coverage")
    pages = load_pages(content_list, expected_pages)
    if fast_metadata is not None:
        for page in pages:
            page["extraction_method"] = "paddle_gpu"
            page["ocr_capabilities"] = fast_metadata["capabilities"]
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
    if fast_metadata is not None:
        manifest["ocr_capabilities"] = fast_metadata["capabilities"]
        manifest["resident_ocr_pid"] = fast_metadata.get("pid")
        manifest["inferred_headings"] = fast_metadata.get("inferred_headings", 0)
        manifest["reading_order_quality_verified"] = False
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
    if fast_metadata is not None:
        summary += "\n> 快速模式使用 PP-OCRv5 GPU 文本检测和识别，不执行 VLM 版面分析、表格结构还原或专用公式识别；跨栏阅读顺序未经验证，章标题提示由真实 OCR 大字和目录原文交叉推断。\n"
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
        if args.backend == "paddle-gpu":
            metadata = json.loads((raw_dir / "fast_ocr_metadata.json").read_text(encoding="utf-8"))
            log_path = raw_dir / "paddle.log"
            duration = float(metadata["duration_seconds"])
        else:
            log_path = output / "mineru.log"
            duration = infer_log_duration(log_path)
    elif args.backend == "paddle-gpu":
        duration, log_path = run_paddle_gpu(source, raw_dir)
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
