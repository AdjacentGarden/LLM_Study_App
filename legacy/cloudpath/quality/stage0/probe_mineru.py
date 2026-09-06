from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import argparse
import json
import mimetypes
import os
import subprocess
import time
import uuid


def _request_json(request: Request, timeout: float) -> tuple[int, dict[str, object]]:
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw_body": body[:2000]}
        return exc.code, payload


def _get_json(url: str, timeout: float = 10) -> tuple[int, dict[str, object]]:
    return _request_json(Request(url, method="GET"), timeout)


def _multipart(file_path: Path) -> tuple[bytes, str]:
    boundary = f"----cloudpath-stage0-{uuid.uuid4().hex}"
    parts: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )

    fields = {
        "lang_list": "ch",
        "backend": "pipeline",
        "effort": "medium",
        "parse_method": "auto",
        "formula_enable": "true",
        "table_enable": "true",
        "image_analysis": "true",
        "return_md": "true",
        "return_middle_json": "true",
        "return_model_output": "false",
        "return_content_list": "true",
        "return_images": "false",
        "response_format_zip": "false",
        "return_original_file": "false",
        "client_side_output_generation": "false",
        "start_page_id": "0",
        "end_page_id": "99999",
    }
    for name, value in fields.items():
        add_field(name, value)

    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="files"; filename="{file_path.name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(parts), boundary


def _gpu_sample() -> dict[str, object] | None:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        ).strip()
        memory, utilization = [int(value.strip()) for value in output.splitlines()[0].split(",")]
        return {"memory_used_mib": memory, "utilization_percent": utilization}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _result_shape(value: object, depth: int = 0) -> object:
    if depth >= 3:
        if isinstance(value, list):
            return {"type": "list", "length": len(value)}
        if isinstance(value, dict):
            return {"type": "object", "keys": sorted(value)[:30]}
        if isinstance(value, str):
            return {"type": "string", "length": len(value)}
        return value
    if isinstance(value, dict):
        return {key: _result_shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return {"length": len(value), "sample": [_result_shape(item, depth + 1) for item in value[:2]]}
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    return value


def _result_metrics(result: dict[str, object]) -> dict[str, object]:
    documents = result.get("results")
    if not isinstance(documents, dict) or not documents:
        return {"document_count": 0}
    document_key, payload = next(iter(documents.items()))
    if not isinstance(payload, dict):
        return {"document_count": len(documents), "document_key": document_key}
    metrics: dict[str, object] = {
        "document_count": len(documents),
        "document_key": document_key,
        "markdown_chars": len(str(payload.get("md_content") or "")),
    }
    middle_raw = payload.get("middle_json")
    if isinstance(middle_raw, str):
        try:
            middle = json.loads(middle_raw)
            pages = middle.get("pdf_info", []) if isinstance(middle, dict) else []
            metrics["page_count"] = len(pages) if isinstance(pages, list) else 0
        except json.JSONDecodeError:
            metrics["middle_json_invalid"] = True
    content_raw = payload.get("content_list")
    if isinstance(content_raw, str):
        try:
            content = json.loads(content_raw)
            if isinstance(content, list):
                type_counts: dict[str, int] = {}
                page_indices: set[int] = set()
                text_levels: set[int] = set()
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("type") or "unknown")
                    type_counts[item_type] = type_counts.get(item_type, 0) + 1
                    if isinstance(item.get("page_idx"), int):
                        page_indices.add(int(item["page_idx"]))
                    if isinstance(item.get("text_level"), int):
                        text_levels.add(int(item["text_level"]))
                metrics["content_item_count"] = len(content)
                metrics["content_type_counts"] = type_counts
                metrics["content_page_indices"] = sorted(page_indices)
                metrics["text_levels"] = sorted(text_levels)
        except json.JSONDecodeError:
            metrics["content_list_invalid"] = True
    return metrics


def probe_file(endpoint: str, file_path: Path, output_dir: Path, timeout_seconds: float) -> dict[str, object]:
    started = time.monotonic()
    body, boundary = _multipart(file_path)
    status_code, submitted = _request_json(
        Request(
            f"{endpoint}/tasks",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        ),
        timeout=30,
    )
    summary: dict[str, object] = {
        "file": file_path.name,
        "bytes": file_path.stat().st_size,
        "sha256": sha256(file_path.read_bytes()).hexdigest(),
        "submit_status_code": status_code,
        "submit_response": submitted,
        "poll_count": 0,
        "gpu_samples": [],
    }
    task_id = submitted.get("task_id")
    if status_code != 202 or not isinstance(task_id, str):
        summary["final_status"] = "submit_rejected"
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return summary

    deadline = started + timeout_seconds
    while time.monotonic() < deadline:
        poll_code, poll = _get_json(f"{endpoint}/tasks/{task_id}")
        summary["poll_count"] = int(summary["poll_count"]) + 1
        sample = _gpu_sample()
        if sample is not None:
            summary["gpu_samples"].append(sample)  # type: ignore[union-attr]
        summary["last_poll_status_code"] = poll_code
        summary["last_poll"] = poll
        state = poll.get("status")
        if state == "completed":
            result_code, result = _get_json(f"{endpoint}/tasks/{task_id}/result", timeout=60)
            safe_input_name = file_path.name.replace(".", "_")
            result_path = output_dir / f"{safe_input_name}_result.json"
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            summary["result_status_code"] = result_code
            summary["result_file"] = result_path.name
            summary["result_shape"] = _result_shape(result)
            summary["result_metrics"] = _result_metrics(result)
            summary["final_status"] = "completed" if result_code == 200 else "result_error"
            break
        if state == "failed":
            summary["final_status"] = "failed"
            break
        time.sleep(1)
    else:
        summary["final_status"] = "probe_timeout"

    samples = summary["gpu_samples"]
    if isinstance(samples, list) and samples:
        summary["gpu_peak_memory_used_mib"] = max(int(item["memory_used_mib"]) for item in samples)
        summary["gpu_peak_utilization_percent"] = max(int(item["utilization_percent"]) for item in samples)
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--endpoint", default=os.environ.get("MINERU_ENDPOINT", "http://127.0.0.1:8001"))
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "mineru_probe")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    endpoint = args.endpoint.rstrip("/")
    args.output.mkdir(parents=True, exist_ok=True)
    health_code, health = _get_json(f"{endpoint}/health")
    report: dict[str, object] = {
        "schema_version": 1,
        "endpoint": endpoint,
        "health_status_code": health_code,
        "health": health,
        "probes": [],
    }
    if health_code != 200:
        (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2

    for file_path in args.files:
        report["probes"].append(probe_file(endpoint, file_path.resolve(), args.output, args.timeout))  # type: ignore[union-attr]
        (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
