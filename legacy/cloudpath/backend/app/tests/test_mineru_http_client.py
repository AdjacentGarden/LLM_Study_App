from __future__ import annotations

from collections.abc import Callable
from email import policy
from email.parser import BytesParser
import gzip
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
import pytest

from app.core.config import get_settings
from app.document.mineru.client import MinerUClient
from app.document.mineru.exceptions import (
    MinerUProtocolError,
    MinerURequestRejectedError,
    MinerUStaleResultError,
    MinerUSubmissionUncertainError,
    MinerUTaskExpiredError,
    MinerUTaskFailedError,
    MinerUTimeoutError,
    MinerUUnavailableError,
)
from app.document.mineru.models import MinerUClientConfig, MinerUParseOptions
from app.document.mineru.task_store import MinerUTaskStore


ENDPOINT = "http://127.0.0.1:8001"
TASK_ID = "task-123"
SECRET_TEXT = "SECRET_COURSE_SENTINEL"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def _config(**overrides: Any) -> MinerUClientConfig:
    values: dict[str, Any] = {
        "endpoint": ENDPOINT,
        "connect_timeout_seconds": 1.0,
        "request_timeout_seconds": 2.0,
        "total_timeout_seconds": 30.0,
        "poll_interval_seconds": 0.25,
        "max_retries": 2,
        "retry_backoff_seconds": 0.1,
        "retry_max_backoff_seconds": 2.0,
    }
    values.update(overrides)
    return MinerUClientConfig(**values)


def _store(prefix: str = "mineru_http") -> MinerUTaskStore:
    return MinerUTaskStore(namespace=f"{prefix}_{uuid4().hex}")


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    clock: FakeClock | None = None,
    store: MinerUTaskStore | None = None,
    **config_overrides: Any,
) -> MinerUClient:
    resolved_clock = clock or FakeClock()
    return MinerUClient(
        _config(**config_overrides),
        task_store=store or _store(),
        transport=httpx2.MockTransport(handler),
        sleeper=resolved_clock.sleep,
        monotonic=resolved_clock.monotonic,
    )


def _health(*, protocol_version: int = 2, status: str = "healthy") -> dict[str, Any]:
    return {
        "status": status,
        "version": "3.4.4",
        "protocol_version": protocol_version,
        "queued_tasks": 0,
        "processing_tasks": 0,
        "completed_tasks": 1,
        "failed_tasks": 0,
        "max_concurrent_requests": 1,
        "task_retention_seconds": 86400,
        "future_field": "allowed",
    }


def _task(
    status: str,
    *,
    task_id: str = TASK_ID,
    queued_ahead: int | None = 0,
    error: str | None = None,
    status_url: str | None = None,
    result_url: str | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "status": status,
        "backend": "pipeline",
        "file_names": ["fixture"],
        "queued_ahead": queued_ahead,
        "error": error,
        "status_url": status_url,
        "result_url": result_url,
    }


def _result(text: str = "parsed text") -> dict[str, Any]:
    return {
        "backend": "pipeline",
        "version": "3.4.4",
        "results": {
            "fixture": {
                "md_content": text,
                "middle_json": {"pdf_info": []},
                "content_list": [],
            }
        },
    }


def _multipart_parts(request: httpx2.Request) -> list[tuple[str, str | None, bytes]]:
    content_type = request.headers["content-type"]
    raw_message = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
        + request.read()
    )
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    assert message.is_multipart()
    parts: list[tuple[str, str | None, bytes]] = []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        assert isinstance(name, str)
        parts.append((name, part.get_filename(), part.get_payload(decode=True) or b""))
    return parts


def test_health_success_and_protocol_mismatch() -> None:
    calls: list[str] = []

    def healthy(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(200, json=_health())

    with _client(healthy) as client:
        response = client.health()

    assert response.version == "3.4.4"
    assert response.protocol_version == 2
    assert calls == ["/health"]

    def incompatible(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_health(protocol_version=99))

    with _client(incompatible) as client, pytest.raises(MinerUProtocolError, match="protocol version"):
        client.health()


def test_health_5xx_retry_limit_and_4xx_no_retry() -> None:
    retry_clock = FakeClock()
    retry_calls = 0

    def unavailable(_: httpx2.Request) -> httpx2.Response:
        nonlocal retry_calls
        retry_calls += 1
        return httpx2.Response(503, json={"detail": "busy"})

    with _client(unavailable, clock=retry_clock, max_retries=2) as client, pytest.raises(MinerUUnavailableError):
        client.health()

    assert retry_calls == 3
    assert retry_clock.sleeps == pytest.approx([0.1, 0.2])

    rejected_calls = 0

    def rejected(_: httpx2.Request) -> httpx2.Response:
        nonlocal rejected_calls
        rejected_calls += 1
        return httpx2.Response(422, json={"detail": "invalid"})

    with _client(rejected, max_retries=5) as client, pytest.raises(MinerURequestRejectedError) as captured:
        client.health()

    assert captured.value.status_code == 422
    assert rejected_calls == 1


def test_health_transport_timeout_is_bounded() -> None:
    clock = FakeClock()
    calls = 0

    def timeout(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        raise httpx2.ReadTimeout("upstream stalled", request=request)

    with _client(timeout, clock=clock, max_retries=2) as client, pytest.raises(MinerUTimeoutError):
        client.health()

    assert calls == 3
    assert clock.sleeps == pytest.approx([0.1, 0.2])


def test_total_deadline_stops_retries_before_retry_limit() -> None:
    clock = FakeClock()
    calls = 0

    def unavailable(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(503)

    with _client(
        unavailable,
        clock=clock,
        total_timeout_seconds=0.25,
        max_retries=10,
        retry_backoff_seconds=0.2,
        retry_max_backoff_seconds=0.2,
    ) as client, pytest.raises(MinerUTimeoutError):
        client.health()

    assert calls == 2
    assert clock.sleeps == pytest.approx([0.2])


def test_submit_multipart_has_repeated_languages_and_every_explicit_field(tmp_path: Path) -> None:
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"%PDF-test-payload")
    captured_parts: list[tuple[str, str | None, bytes]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == "/tasks"
        captured_parts.extend(_multipart_parts(request))
        return httpx2.Response(202, json=_task("pending"))

    options = MinerUParseOptions(
        lang_list=["ch", "korean"],
        backend="pipeline",
        effort="high",
        parse_method="ocr",
        formula_enable=False,
        table_enable=True,
        image_analysis=False,
        return_md=True,
        return_middle_json=True,
        return_model_output=False,
        return_content_list=True,
        return_images=True,
        response_format_zip=False,
        return_original_file=False,
        client_side_output_generation=False,
        start_page_id=2,
        end_page_id=10,
    )
    with _client(handler) as client:
        task = client.submit_task(source, options)

    assert task.task_id == TASK_ID
    fields: dict[str, list[str]] = {}
    file_parts: list[tuple[str | None, bytes]] = []
    for name, filename, payload in captured_parts:
        if name == "files":
            file_parts.append((filename, payload))
        else:
            fields.setdefault(name, []).append(payload.decode("utf-8"))

    assert fields == {
        "lang_list": ["ch", "korean"],
        "backend": ["pipeline"],
        "effort": ["high"],
        "parse_method": ["ocr"],
        "formula_enable": ["false"],
        "table_enable": ["true"],
        "image_analysis": ["false"],
        "return_md": ["true"],
        "return_middle_json": ["true"],
        "return_model_output": ["false"],
        "return_content_list": ["true"],
        "return_images": ["true"],
        "response_format_zip": ["false"],
        "return_original_file": ["false"],
        "client_side_output_generation": ["false"],
        "start_page_id": ["2"],
        "end_page_id": ["10"],
    }
    assert file_parts == [("fixture.pdf", b"%PDF-test-payload")]


@pytest.mark.parametrize(
    ("response_or_error", "error_type"),
    [
        (httpx2.Response(422, json={"detail": SECRET_TEXT}), MinerURequestRejectedError),
        (httpx2.Response(503, json={"detail": SECRET_TEXT}), MinerUSubmissionUncertainError),
        ("timeout", MinerUSubmissionUncertainError),
    ],
)
def test_submit_4xx_5xx_and_transport_failure_are_never_replayed(
    tmp_path: Path,
    response_or_error: httpx2.Response | str,
    error_type: type[Exception],
) -> None:
    source = tmp_path / "Private Course.pdf"
    source.write_text(SECRET_TEXT, encoding="utf-8")
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if response_or_error == "timeout":
            raise httpx2.ReadTimeout(f"{SECRET_TEXT} D:\\private folder\\lesson.pdf", request=request)
        assert isinstance(response_or_error, httpx2.Response)
        return response_or_error

    with _client(handler, max_retries=9) as client, pytest.raises(error_type) as captured:
        client.submit_task(source, MinerUParseOptions())

    assert calls == 1
    assert SECRET_TEXT not in str(captured.value)
    assert str(source) not in str(captured.value)
    assert captured.value.__dict__.get("detail") is None


def test_submit_retries_only_explicit_429_rejections(tmp_path: Path) -> None:
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"%PDF-rate-limit")
    clock = FakeClock()
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(429, headers={"Retry-After": "0.7"})
        return httpx2.Response(202, json=_task("pending"))

    with _client(handler, clock=clock, max_retries=1) as client:
        task = client.submit_task(source, MinerUParseOptions())

    assert task.task_id == TASK_ID
    assert calls == 2
    assert clock.sleeps == pytest.approx([0.7])


def test_accepted_submission_without_task_identity_is_never_resubmitted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "true")
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"%PDF-accepted-without-identity")
    store = _store("mineru_accepted_unknown")
    calls = {"health": 0, "post": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/health":
            calls["health"] += 1
            return httpx2.Response(200, json=_health())
        calls["post"] += 1
        return httpx2.Response(202, json={"status": "pending"})

    with _client(handler, store=store) as client, pytest.raises(MinerUSubmissionUncertainError):
        client.execute(book_id="book-unknown", file_path=source)
    with _client(handler, store=store) as client, pytest.raises(MinerUSubmissionUncertainError):
        client.execute(book_id="book-unknown", file_path=source)

    assert calls == {"health": 1, "post": 1}
    record = store.get_current("book-unknown")
    assert record is not None
    assert record.status == "submission_uncertain"


def test_poll_pending_processing_completed_and_queue_backoff() -> None:
    clock = FakeClock()
    statuses = iter(
        [
            _task("pending", queued_ahead=3),
            _task("processing", queued_ahead=0),
            _task("completed", queued_ahead=0),
        ]
    )
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        assert request.url.path == f"/tasks/{TASK_ID}"
        return httpx2.Response(200, json=next(statuses))

    with _client(handler, clock=clock) as client:
        task = client.poll_task(TASK_ID, on_status=lambda item: seen.append(item.status))

    assert task.status == "completed"
    assert seen == ["pending", "processing", "completed"]
    assert clock.sleeps == pytest.approx([1.0, 0.25])


def test_poll_failed_does_not_expose_upstream_error() -> None:
    upstream_error = f"{SECRET_TEXT} at D:\\private folder\\lesson.pdf"

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_task("failed", error=upstream_error))

    with _client(handler) as client, pytest.raises(MinerUTaskFailedError) as captured:
        client.poll_task(TASK_ID)

    assert SECRET_TEXT not in str(captured.value)
    assert "private folder" not in str(captured.value)
    assert captured.value.detail is None


def test_poll_429_honors_retry_after_without_submitting() -> None:
    clock = FakeClock()
    requests: list[tuple[str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append((request.method, request.url.path))
        if len(requests) == 1:
            return httpx2.Response(429, headers={"Retry-After": "0.6"})
        return httpx2.Response(200, json=_task("completed"))

    with _client(handler, clock=clock, max_retries=1) as client:
        task = client.poll_task(TASK_ID)

    assert task.status == "completed"
    assert requests == [("GET", f"/tasks/{TASK_ID}"), ("GET", f"/tasks/{TASK_ID}")]
    assert clock.sleeps == pytest.approx([0.6])


def test_pending_poll_respects_total_deadline() -> None:
    clock = FakeClock()
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200, json=_task("pending"))

    with _client(
        handler,
        clock=clock,
        total_timeout_seconds=0.5,
        poll_interval_seconds=0.3,
    ) as client, pytest.raises(MinerUTimeoutError):
        client.poll_task(TASK_ID)

    assert calls == 2
    assert clock.sleeps == pytest.approx([0.3])


def test_timed_out_remote_task_requires_explicit_invalidation_before_resubmit(tmp_path: Path) -> None:
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"%PDF-timeout")
    store = _store("mineru_timeout_block")
    clock = FakeClock()
    counts = {"health": 0, "post": 0, "status": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/health":
            counts["health"] += 1
            return httpx2.Response(200, json=_health())
        if request.url.path == "/tasks":
            counts["post"] += 1
            return httpx2.Response(202, json=_task("pending"))
        counts["status"] += 1
        return httpx2.Response(200, json=_task("pending"))

    with _client(
        handler,
        store=store,
        clock=clock,
        total_timeout_seconds=0.5,
        poll_interval_seconds=0.3,
    ) as client, pytest.raises(MinerUTimeoutError):
        client.execute(book_id="book-timeout", file_path=source)

    after_first = dict(counts)
    with _client(handler, store=store) as client, pytest.raises(MinerUTimeoutError, match="explicitly invalidated"):
        client.execute(book_id="book-timeout", file_path=source)

    assert counts == after_first
    assert counts["post"] == 1
    assert store.get_current("book-timeout").status == "timed_out"  # type: ignore[union-attr]


def test_result_202_then_200_and_embedded_json_decoding() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == f"/tasks/{TASK_ID}/result"
        if calls == 1:
            return httpx2.Response(202)
        payload = _result()
        payload["results"]["fixture"]["middle_json"] = json.dumps({"pdf_info": [{"page_idx": 0}]})
        payload["results"]["fixture"]["content_list"] = json.dumps([{"type": "text"}])
        return httpx2.Response(200, json=payload)

    with _client(handler) as client:
        assert client.get_result(TASK_ID) is None
        result = client.get_result(TASK_ID)

    assert result is not None
    assert result.results["fixture"].middle_json == {"pdf_info": [{"page_idx": 0}]}
    assert result.results["fixture"].content_list == [{"type": "text"}]


def test_result_404_and_corrupt_payloads_are_classified() -> None:
    def missing(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"detail": "Task not found"})

    with _client(missing) as client, pytest.raises(MinerUTaskExpiredError):
        client.get_result(TASK_ID)

    corrupt_responses = [
        httpx2.Response(200, content=b"{not-json", headers={"content-type": "application/json"}),
        httpx2.Response(200, json={"backend": "pipeline", "version": "3.4.4", "results": {}}),
        httpx2.Response(
            200,
            json={
                "backend": "pipeline",
                "version": "3.4.4",
                "results": {"fixture": {"middle_json": "{broken"}},
            },
        ),
    ]
    for corrupt in corrupt_responses:
        with _client(lambda _request, response=corrupt: response) as client, pytest.raises(MinerUProtocolError):
            client.get_result(TASK_ID)


def test_result_size_limit_is_enforced_without_exposing_body() -> None:
    body = (SECRET_TEXT * 200).encode("utf-8")

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": "application/json"})

    with _client(handler, max_result_bytes=1024) as client, pytest.raises(MinerUProtocolError) as captured:
        client.get_result(TASK_ID)

    assert "size limit" in str(captured.value)
    assert SECRET_TEXT not in str(captured.value)


def test_gzip_result_is_decoded_exactly_once() -> None:
    encoded = gzip.compress(json.dumps(_result()).encode("utf-8"))

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            content=encoded,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
        )

    with _client(handler) as client:
        result = client.get_result(TASK_ID)

    assert result is not None
    assert result.results["fixture"].md_content == "parsed text"


def test_config_rejects_non_loopback_and_unsupported_result_modes() -> None:
    for endpoint in (
        "http://127.0.0.1.evil.test:8001",
        "http://user:secret@127.0.0.1:8001",
        "http://127.0.0.1:8001?target=evil",
        "http://127.0.0.1:8001/prefix",
    ):
        with pytest.raises(ValueError):
            MinerUClientConfig(endpoint=endpoint)

    with pytest.raises(ValueError):
        MinerUParseOptions(response_format_zip=True)
    with pytest.raises(ValueError):
        MinerUParseOptions(return_middle_json=False)
    with pytest.raises(ValueError):
        MinerUParseOptions(return_content_list=False)
    with pytest.raises(ValueError):
        MinerUParseOptions(start_page_id=3, end_page_id=2)


def test_persisted_task_resumes_after_get_failure_without_second_post(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "true")
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    namespace = f"mineru_http_resume_{uuid4().hex}"
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"%PDF-resume")
    first_counts = {"health": 0, "post": 0, "status": 0}

    def first_handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/health":
            first_counts["health"] += 1
            return httpx2.Response(200, json=_health())
        if request.url.path == "/tasks" and request.method == "POST":
            first_counts["post"] += 1
            return httpx2.Response(202, json=_task("pending"))
        assert request.url.path == f"/tasks/{TASK_ID}"
        first_counts["status"] += 1
        return httpx2.Response(503)

    first_store = MinerUTaskStore(namespace=namespace)
    with _client(first_handler, store=first_store, max_retries=0) as client, pytest.raises(MinerUUnavailableError):
        client.execute(book_id="book-resume", file_path=source)

    first_record = first_store.get_current("book-resume")
    assert first_record is not None
    assert first_record.mineru_task_id == TASK_ID
    assert first_counts == {"health": 1, "post": 1, "status": 1}

    second_requests: list[tuple[str, str]] = []

    def second_handler(request: httpx2.Request) -> httpx2.Response:
        second_requests.append((request.method, request.url.path))
        if request.url.path == f"/tasks/{TASK_ID}":
            return httpx2.Response(200, json=_task("completed"))
        assert request.url.path == f"/tasks/{TASK_ID}/result"
        return httpx2.Response(200, json=_result())

    restarted_store = MinerUTaskStore(namespace=namespace)
    with _client(second_handler, store=restarted_store, max_retries=0) as client:
        execution = client.execute(book_id="book-resume", file_path=source)

    assert execution.resumed is True
    assert execution.parse_generation == first_record.parse_generation
    assert second_requests == [
        ("GET", f"/tasks/{TASK_ID}"),
        ("GET", f"/tasks/{TASK_ID}/result"),
    ]


def test_late_generation_guard_rejects_before_http_request() -> None:
    store = _store("mineru_late")
    old = store.begin_or_resume(
        book_id="book-late",
        file_sha256="a" * 64,
        options_sha256="b" * 64,
        endpoint=ENDPOINT,
    ).record
    store.mark_submitting("book-late", old.parse_generation)
    store.attach_remote_task("book-late", old.parse_generation, TASK_ID)
    store.begin_or_resume(
        book_id="book-late",
        file_sha256="a" * 64,
        options_sha256="c" * 64,
        endpoint=ENDPOINT,
    )
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200, json=_task("completed"))

    with _client(handler, store=store) as client, pytest.raises(MinerUStaleResultError):
        client.poll_task(
            TASK_ID,
            current_guard=lambda: store.is_current("book-late", old.parse_generation),
        )

    assert calls == 0


def test_execute_requires_the_reserved_generation_and_bound_cloudpath_job(tmp_path: Path) -> None:
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"%PDF-1.4\nreserved generation")
    options = MinerUParseOptions()
    store = _store("mineru_expected_generation")
    from app.document.mineru.models import sha256_file

    reserved = store.begin_or_resume(
        book_id="book-expected",
        file_sha256=sha256_file(source),
        options_sha256=options.fingerprint(),
        endpoint=ENDPOINT,
    ).record
    store.bind_cloudpath_job(
        "book-expected",
        reserved.parse_generation,
        "job-expected",
    )
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500)

    with _client(handler, store=store) as client:
        with pytest.raises(MinerUStaleResultError, match="not bound"):
            client.execute(
                book_id="book-expected",
                file_path=source,
                options=options,
                expected_generation=reserved.parse_generation,
                cloudpath_job_id="job-wrong",
            )
        with pytest.raises(MinerUStaleResultError, match="no longer current"):
            client.execute(
                book_id="book-expected",
                file_path=source,
                options=options,
                expected_generation=reserved.parse_generation + 1,
                cloudpath_job_id="job-expected",
            )

    assert calls == 0


@pytest.mark.parametrize("malicious_id", ["../escape", "task/escape", "https://evil.test", "%2fescape", ""])
def test_malicious_task_id_is_rejected_without_http(malicious_id: str) -> None:
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200, json=_task("completed"))

    with _client(handler) as client, pytest.raises(MinerUProtocolError):
        client.get_task(malicious_id)

    assert calls == 0


def test_execute_ignores_untrusted_response_urls(tmp_path: Path) -> None:
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"%PDF-safe-origin")
    requests: list[httpx2.URL] = []
    evil_status = "https://evil.test/steal-status"
    evil_result = "https://evil.test/steal-result"

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url)
        if request.url.path == "/health":
            return httpx2.Response(200, json=_health())
        if request.url.path == "/tasks":
            return httpx2.Response(
                202,
                json=_task("pending", status_url=evil_status, result_url=evil_result),
            )
        if request.url.path == f"/tasks/{TASK_ID}":
            return httpx2.Response(
                200,
                json=_task("completed", status_url=evil_status, result_url=evil_result),
            )
        assert request.url.path == f"/tasks/{TASK_ID}/result"
        return httpx2.Response(200, json=_result())

    with _client(handler) as client:
        execution = client.execute(book_id="book-origin", file_path=source)

    assert execution.task.status == "completed"
    assert [url.host for url in requests] == ["127.0.0.1"] * 4
    assert all("evil.test" not in str(url) for url in requests)


def test_failed_execute_never_persists_or_reports_body_and_local_path(monkeypatch, tmp_path: Path, caplog) -> None:
    storage = tmp_path / "storage"
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "true")
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(storage))
    get_settings.cache_clear()
    namespace = f"mineru_http_privacy_{uuid4().hex}"
    source = tmp_path / "Private Course Name.pdf"
    source.write_text(SECRET_TEXT, encoding="utf-8")
    remote_error = f"{SECRET_TEXT} found at D:\\private folder\\Private Course Name.pdf"

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/health":
            return httpx2.Response(200, json=_health())
        if request.url.path == "/tasks":
            return httpx2.Response(202, json=_task("pending"))
        assert request.url.path == f"/tasks/{TASK_ID}"
        return httpx2.Response(200, json=_task("failed", error=remote_error))

    store = MinerUTaskStore(namespace=namespace)
    with _client(handler, store=store) as client, pytest.raises(MinerUTaskFailedError) as captured:
        client.execute(book_id="book-private", file_path=source)

    persisted_path = storage / "_state" / f"{namespace}.json"
    persisted = persisted_path.read_text(encoding="utf-8")
    combined = "\n".join((str(captured.value), str(captured.value.detail), caplog.text, persisted))
    assert SECRET_TEXT not in combined
    assert "Private Course Name.pdf" not in combined
    assert "D:\\private folder" not in combined
    record = store.get_current("book-private")
    assert record is not None
    assert record.status == "failed"
    assert record.error_code == "mineru_task_failed"
    assert record.error_detail is None
