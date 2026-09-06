#!/usr/bin/env python3
"""Exercise the public production SSO path with a real PDF and grounded RAG."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse


class CanaryError(RuntimeError):
    pass


class PublicClient:
    def __init__(self, base_url: str, cookie_file: Path) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise CanaryError("base URL must be an HTTPS origin without embedded credentials")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise CanaryError("base URL must not contain a path, query, or fragment")
        self.base_url = base_url.rstrip("/") + "/"
        self.origin = (parsed.scheme, parsed.hostname, parsed.port or 443)
        self.cookie_file = cookie_file
        self.events: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        payload: dict[str, Any] | None = None,
        upload: Path | None = None,
        timeout: int = 60,
    ) -> Any:
        url = urljoin(self.base_url, path_or_url)
        parsed = urlparse(url)
        target_origin = (parsed.scheme, parsed.hostname, parsed.port or 443)
        if target_origin != self.origin:
            raise CanaryError(f"server returned a cross-origin API URL: {url}")
        command = [
            "curl",
            "--fail-with-body",
            "--silent",
            "--show-error",
            "--max-time",
            str(timeout),
            "--cookie",
            str(self.cookie_file),
            "--request",
            method,
            "--write-out",
            "\n%{http_code}",
        ]
        if payload is not None:
            command.extend([
                "--header",
                "Content-Type: application/json",
                "--data-binary",
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ])
        if upload is not None:
            command.extend(["--form", f"file=@{upload};type=application/pdf"])
        command.append(url)
        started = time.perf_counter()
        result = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        body_text, separator, status_text = result.stdout.rpartition("\n")
        status = int(status_text) if separator and status_text.isdigit() else 0
        try:
            body: Any = json.loads(body_text)
        except json.JSONDecodeError:
            body = {"non_json_body": body_text[:500]}
        self.events.append(
            {
                "method": method,
                "path": parsed.path,
                "status": status,
                "elapsed_ms": elapsed_ms,
            }
        )
        if result.returncode != 0 or not 200 <= status < 300:
            detail = result.stderr.strip() or body
            raise CanaryError(f"{method} {parsed.path} failed with HTTP {status}: {detail}")
        return body


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CanaryError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--cookie-file", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--expected-token", required=True)
    parser.add_argument("--expected-user-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()

    result: dict[str, Any] = {"schema": "bookcourse.production-pdf-canary/v1", "status": "FAIL"}
    client: PublicClient | None = None
    try:
        require(arguments.cookie_file.is_file(), "SSO cookie file is missing")
        require(arguments.pdf.is_file(), "PDF fixture is missing")
        require(arguments.pdf.suffix.lower() == ".pdf", "fixture must be a PDF")
        pdf_bytes = arguments.pdf.read_bytes()
        require(pdf_bytes.startswith(b"%PDF-"), "fixture does not have a PDF signature")
        client = PublicClient(arguments.base_url, arguments.cookie_file)

        session = client.request("GET", "/api/session")
        require(session.get("user_id") == arguments.expected_user_id, "verified session user mismatch")
        require(session.get("is_admin") is False, "canary identity must not be an administrator")

        initialized = client.request(
            "POST",
            "/api/uploads/init",
            payload={
                "filename": arguments.pdf.name,
                "content_type": "application/pdf",
                "size_bytes": len(pdf_bytes),
            },
        )
        book_id = initialized.get("book_id")
        upload_url = initialized.get("upload_url")
        require(isinstance(book_id, str) and book_id, "upload init omitted book_id")
        require(isinstance(upload_url, str) and upload_url, "upload init omitted upload_url")

        uploaded = client.request("POST", upload_url, upload=arguments.pdf, timeout=180)
        require(uploaded.get("size_bytes") == len(pdf_bytes), "uploaded PDF size mismatch")

        parse_job = client.request(
            "POST",
            f"/api/books/{book_id}/parse",
            payload={"force": False},
            timeout=60,
        )
        job_id = parse_job.get("job_id")
        require(isinstance(job_id, str) and job_id, "parse response omitted job_id")
        deadline = time.monotonic() + 600
        final_job: dict[str, Any] = {}
        poll_count = 0
        while time.monotonic() < deadline:
            final_job = client.request("GET", f"/api/jobs/{job_id}")
            poll_count += 1
            if final_job.get("status") in {"done", "succeeded", "failed"}:
                break
            time.sleep(2)
        require(final_job.get("status") in {"done", "succeeded"}, f"parse did not succeed: {final_job}")

        chapters = client.request("GET", f"/api/books/{book_id}/chapters")
        chunks = client.request("GET", f"/api/books/{book_id}/chunks")
        assets = client.request("GET", f"/api/books/{book_id}/assets")
        require(isinstance(chapters, list) and chapters, "parse produced no chapters")
        require(isinstance(chunks, list) and chunks, "parse produced no chunks")
        require(isinstance(assets, list), "assets response was not a list")
        combined_text = "\n".join(str(chunk.get("text", "")) for chunk in chunks)
        chunk_ids = {str(chunk.get("chunk_id")) for chunk in chunks}
        require(arguments.expected_token in combined_text, "expected token is absent from parsed chunks")

        chapter_id = str(chapters[0].get("chapter_id", ""))
        source_chunk_id = str(chunks[0].get("chunk_id", ""))
        require(chapter_id != "" and source_chunk_id != "", "parsed artifacts omitted stable identifiers")

        lesson_job = client.request(
            "POST",
            f"/api/books/{book_id}/lessons/build",
            payload={"chapter_ids": [chapter_id], "force": False},
        )
        lesson_job_id = lesson_job.get("job_id")
        require(isinstance(lesson_job_id, str) and lesson_job_id, "lesson build omitted job_id")
        lesson_deadline = time.monotonic() + 600
        final_lesson_job: dict[str, Any] = {}
        while time.monotonic() < lesson_deadline:
            final_lesson_job = client.request(
                "GET",
                f"/api/lesson-generation/jobs/{lesson_job_id}",
                timeout=60,
            )
            if final_lesson_job.get("status") in {"done", "succeeded", "failed"}:
                break
            time.sleep(2)
        generated_lessons = final_lesson_job.get("lessons")
        require(
            final_lesson_job.get("status") in {"done", "succeeded"}
            and isinstance(generated_lessons, list)
            and bool(generated_lessons),
            f"real lesson provider did not produce a lesson: {final_lesson_job}",
        )
        require(
            any(
                source_id in chunk_ids
                for lesson in generated_lessons
                for source_id in lesson.get("source_chunk_ids", [])
            ),
            "generated lesson is not grounded in parsed chunks",
        )

        image_job = client.request(
            "POST",
            "/api/assets/generate",
            payload={
                "book_id": book_id,
                "chapter_id": chapter_id,
                "purpose": "Release canary diagram grounded in the uploaded textbook",
                "concepts": [arguments.expected_token],
                "source_chunk_ids": [source_chunk_id],
            },
        )
        image_job_id = image_job.get("job_id")
        require(isinstance(image_job_id, str) and image_job_id, "image generation omitted job_id")
        image_deadline = time.monotonic() + 600
        final_image_job: dict[str, Any] = {}
        while time.monotonic() < image_deadline:
            final_image_job = client.request(
                "GET",
                f"/api/image-generation/jobs/{image_job_id}",
                timeout=60,
            )
            if final_image_job.get("status") in {"done", "succeeded", "failed"}:
                break
            time.sleep(2)
        generated_asset = final_image_job.get("asset")
        require(
            final_image_job.get("status") in {"done", "succeeded"}
            and isinstance(generated_asset, dict),
            f"real image provider did not produce an asset: {final_image_job}",
        )
        require(
            generated_asset.get("generation_provider") == "openai_compatible",
            "generated asset did not use the production image provider",
        )
        require(
            source_chunk_id in generated_asset.get("source_chunk_ids", [])
            and bool(generated_asset.get("content_hash")),
            "generated asset is not validated and grounded in the source chunk",
        )

        question = f"What does the textbook say about the {arguments.expected_token} verification token?"
        rag_payload = {"book_id": book_id, "question": question, "history": []}
        cold_started = time.perf_counter()
        cold = client.request("POST", "/api/rag/query", payload=rag_payload, timeout=180)
        cold_ms = round((time.perf_counter() - cold_started) * 1000, 3)
        warm_started = time.perf_counter()
        warm = client.request("POST", "/api/rag/query", payload=rag_payload, timeout=180)
        warm_ms = round((time.perf_counter() - warm_started) * 1000, 3)
        require(arguments.expected_token in str(cold.get("answer", "")), "RAG answer omitted expected token")
        citations = cold.get("citations")
        require(isinstance(citations, list) and citations, "RAG answer has no citation")
        grounded_citations = [
            citation
            for citation in citations
            if citation.get("chunk_id") in chunk_ids
            and arguments.expected_token in str(citation.get("quote", ""))
        ]
        require(grounded_citations, "no citation grounds the expected token in a parsed chunk")
        for citation in citations:
            metadata = citation.get("source_metadata")
            require(isinstance(metadata, dict), "citation source metadata is missing")
            require(metadata.get("index_provider") == "pgvector", "RAG did not use pgvector")
            require(metadata.get("fallback_reason") in {None, ""}, "RAG used a fallback index")
            embedding = metadata.get("embedding")
            require(isinstance(embedding, dict), "citation embedding descriptor is missing")
            require(embedding.get("provider") == "bge_m3", "RAG did not use BGE-M3 embeddings")
            require(str(embedding.get("device", "")).startswith("cuda"), "RAG embedding did not use CUDA")
        require(warm.get("answer") == cold.get("answer"), "warm RAG answer changed")
        require(warm.get("performance", {}).get("response_cache_hit") is True, "warm RAG missed response cache")

        result.update(
            {
                "status": "PASS",
                "fixture": {
                    "filename": arguments.pdf.name,
                    "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                    "size_bytes": len(pdf_bytes),
                    "expected_token": arguments.expected_token,
                },
                "book_id": book_id,
                "parse": {"job_id": job_id, "poll_count": poll_count, "final": final_job},
                "artifacts": {
                    "chapters": len(chapters),
                    "chunks": len(chunks),
                    "assets": len(assets),
                },
                "lesson_generation": {
                    "job_id": lesson_job_id,
                    "status": final_lesson_job.get("status"),
                    "lessons": len(generated_lessons),
                },
                "image_generation": {
                    "job_id": image_job_id,
                    "status": final_image_job.get("status"),
                    "asset_id": generated_asset.get("asset_id"),
                    "generation_provider": generated_asset.get("generation_provider"),
                    "content_hash": generated_asset.get("content_hash"),
                },
                "rag": {
                    "cold_ms": cold_ms,
                    "warm_ms": warm_ms,
                    "cold_citations": len(citations),
                    "grounded_token_citations": len(grounded_citations),
                    "warm_cache_hit": True,
                },
            }
        )
    except (CanaryError, OSError, ValueError) as error:
        result["error"] = str(error)
    finally:
        result["events"] = client.events if client is not None else []
        arguments.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if result["status"] != "PASS":
        print(f"BookCourse production PDF canary: FAIL: {result.get('error')}", file=sys.stderr)
        return 1
    print("BookCourse production PDF canary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
