from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
import mimetypes
from pathlib import Path
import time
from typing import Any

import httpx2
from pydantic import ValidationError

from app.document.mineru.exceptions import (
    MinerUError,
    MinerUProtocolError,
    MinerURateLimitError,
    MinerURequestRejectedError,
    MinerUStaleResultError,
    MinerUSubmissionUncertainError,
    MinerUTaskExpiredError,
    MinerUTaskFailedError,
    MinerUTimeoutError,
    MinerUUnavailableError,
)
from app.document.mineru.models import (
    MinerUClientConfig,
    MinerUExecutionResult,
    MinerUHealthResponse,
    MinerUParseOptions,
    MinerUResultResponse,
    MinerUTaskResponse,
    sha256_file,
)
from app.document.mineru.task_store import MinerUTaskStore, mineru_task_store


EXPECTED_PROTOCOL_VERSION = 2
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
_MAX_METADATA_RESPONSE_BYTES = 1024 * 1024


class MinerUClient:
    """Synchronous client for MinerU's protocol-v2 task API.

    The CloudPath worker is synchronous, so this client deliberately uses a
    streaming synchronous upload. Safe GET operations have bounded retries.
    ``POST /tasks`` is never replayed after an ambiguous transport or 5xx
    failure because MinerU exposes neither an idempotency key nor a task lookup
    endpoint.
    """

    supports_cancellation = False

    def __init__(
        self,
        config: MinerUClientConfig,
        *,
        task_store: MinerUTaskStore | None = None,
        client: httpx2.Client | None = None,
        transport: httpx2.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("Pass either client or transport, not both")
        self.config = config
        self.task_store = task_store or mineru_task_store
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._owns_client = client is None
        self._client = client or httpx2.Client(
            base_url=config.endpoint,
            timeout=httpx2.Timeout(
                config.request_timeout_seconds,
                connect=config.connect_timeout_seconds,
            ),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __enter__(self) -> "MinerUClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def health(self, *, deadline: float | None = None) -> MinerUHealthResponse:
        resolved_deadline = deadline if deadline is not None else self._new_deadline()
        response = self._get_with_retry(
            "/health",
            acceptable_statuses={200},
            deadline=resolved_deadline,
            operation="health check",
        )
        payload = self._json_object(response, "health response")
        try:
            health = MinerUHealthResponse.model_validate(payload)
        except ValidationError:
            raise MinerUProtocolError("MinerU health response is invalid") from None
        if health.status.lower() != "healthy":
            raise MinerUUnavailableError("MinerU reported an unhealthy state", status_code=response.status_code)
        if health.protocol_version != EXPECTED_PROTOCOL_VERSION:
            raise MinerUProtocolError("MinerU protocol version is not supported")
        return health

    def submit_task(
        self,
        file_path: Path,
        options: MinerUParseOptions,
        *,
        deadline: float | None = None,
    ) -> MinerUTaskResponse:
        path = Path(file_path)
        if not path.is_file():
            raise MinerURequestRejectedError("MinerU input file is unavailable")
        resolved_deadline = deadline if deadline is not None else self._new_deadline()
        self._ensure_before_deadline(resolved_deadline, "MinerU submission deadline expired")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        attempt = 0
        while True:
            try:
                handle = path.open("rb")
            except OSError:
                # No HTTP request has started, so this is a known local input
                # failure rather than an ambiguous upstream submission.
                raise MinerURequestRejectedError("MinerU input file is unavailable") from None
            try:
                with handle:
                    multipart: list[tuple[str, tuple[Any, ...]]] = [
                        (name, (None, value)) for name, value in options.form_fields()
                    ]
                    multipart.append(("files", (path.name, handle, content_type)))
                    response = self._client.post(
                        "/tasks",
                        files=multipart,
                        timeout=self._request_timeout(resolved_deadline),
                    )
            except (httpx2.TimeoutException, httpx2.RequestError, OSError):
                # The request may already have reached MinerU. It is unsafe to
                # replay without an upstream idempotency or task-query facility.
                raise MinerUSubmissionUncertainError("MinerU task submission outcome is unknown") from None

            if response.status_code != 429:
                break
            # A 429 is an explicit rejection, so retrying only after its
            # bounded delay cannot duplicate an accepted MinerU task.
            if attempt >= self.config.max_retries:
                raise MinerURateLimitError(
                    "MinerU rejected the submission because it is rate limited",
                    status_code=429,
                )
            self._sleep(self._retry_after_delay(response, attempt), resolved_deadline)
            attempt += 1

        if response.status_code in _RETRYABLE_STATUS_CODES or response.status_code >= 500:
            raise MinerUSubmissionUncertainError(
                "MinerU task submission outcome is unknown",
                status_code=response.status_code,
            )
        if 400 <= response.status_code < 500:
            raise MinerURequestRejectedError(
                "MinerU rejected the task submission",
                status_code=response.status_code,
            )
        if 200 <= response.status_code < 300 and response.status_code != 202:
            raise MinerUSubmissionUncertainError(
                "MinerU returned an ambiguous successful submission status",
                status_code=response.status_code,
            )
        if response.status_code != 202:
            raise MinerUProtocolError("MinerU task submission returned an unexpected status", status_code=response.status_code)

        try:
            return self._parse_task(response, "task submission response")
        except MinerUProtocolError:
            # HTTP 202 means MinerU accepted something, but without a valid
            # task id CloudPath cannot recover or safely submit again.
            raise MinerUSubmissionUncertainError(
                "MinerU accepted the upload but returned no usable task identity",
                status_code=202,
            ) from None

    def get_task(self, task_id: str, *, deadline: float | None = None) -> MinerUTaskResponse:
        safe_task_id = self._validate_task_id(task_id)
        resolved_deadline = deadline if deadline is not None else self._new_deadline()
        response = self._get_with_retry(
            f"/tasks/{safe_task_id}",
            acceptable_statuses={200, 404},
            deadline=resolved_deadline,
            operation="task status",
        )
        if response.status_code == 404:
            raise MinerUTaskExpiredError("MinerU task is missing or expired", status_code=404, task_id=safe_task_id)
        task = self._parse_task(response, "task status response")
        if task.task_id != safe_task_id:
            raise MinerUProtocolError("MinerU task response id does not match the request", task_id=safe_task_id)
        return task

    def poll_task(
        self,
        task_id: str,
        *,
        deadline: float | None = None,
        on_status: Callable[[MinerUTaskResponse], None] | None = None,
        current_guard: Callable[[], bool] | None = None,
    ) -> MinerUTaskResponse:
        resolved_deadline = deadline if deadline is not None else self._new_deadline()
        while True:
            if current_guard is not None and not current_guard():
                raise MinerUStaleResultError("MinerU task belongs to an obsolete parse generation")
            task = self.get_task(task_id, deadline=resolved_deadline)
            if on_status is not None:
                on_status(task)
            if task.status == "completed":
                return task
            if task.status == "failed":
                # Do not carry the upstream error text into logs or persisted
                # state: MinerU may include a local path or document excerpt.
                raise MinerUTaskFailedError("MinerU task failed", task_id=task.task_id)
            self._sleep(self._poll_delay(task.queued_ahead), resolved_deadline)

    def get_result(
        self,
        task_id: str,
        *,
        deadline: float | None = None,
    ) -> MinerUResultResponse | None:
        safe_task_id = self._validate_task_id(task_id)
        resolved_deadline = deadline if deadline is not None else self._new_deadline()
        response = self._get_with_retry(
            f"/tasks/{safe_task_id}/result",
            acceptable_statuses={200, 202, 404, 409},
            deadline=resolved_deadline,
            operation="task result",
            max_response_bytes=self.config.max_result_bytes,
        )
        if response.status_code == 202:
            return None
        if response.status_code == 404:
            raise MinerUTaskExpiredError("MinerU task is missing or expired", status_code=404, task_id=safe_task_id)
        if response.status_code == 409:
            raise MinerUTaskFailedError("MinerU task failed", status_code=409, task_id=safe_task_id)

        payload = self._json_object(response, "task result")
        try:
            result = MinerUResultResponse.model_validate(payload)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
            raise MinerUProtocolError("MinerU task result is invalid", task_id=safe_task_id) from None
        if len(result.results) != 1:
            raise MinerUProtocolError("MinerU task result must contain exactly one document", task_id=safe_task_id)
        document = next(iter(result.results.values()))
        if document.middle_json is None or document.content_list is None:
            raise MinerUProtocolError(
                "MinerU task result is missing required structured output",
                task_id=safe_task_id,
            )
        # Hash the already buffered decoded response. This avoids serializing a
        # second full copy of a potentially large base64 image payload.
        result.set_response_sha256(sha256(response.content).hexdigest())
        return result

    def execute(
        self,
        *,
        book_id: str,
        file_path: Path,
        options: MinerUParseOptions | None = None,
        expected_generation: int | None = None,
        cloudpath_job_id: str | None = None,
    ) -> MinerUExecutionResult:
        """Submit or resume one persisted MinerU parse generation.

        This is intentionally not wired into the parser router in Stage 1. It
        is a standalone orchestration boundary that Stage 2 can call after the
        API route reserves the generation before queueing.
        """

        if expected_generation is not None:
            self.task_store.assert_current(
                book_id,
                expected_generation,
                cloudpath_job_id=cloudpath_job_id,
            )

        path = Path(file_path)
        resolved_options = options or MinerUParseOptions.from_settings()
        try:
            initial_stat = path.stat()
            if not path.is_file():
                raise OSError
            file_digest = sha256_file(path)
        except OSError:
            raise MinerURequestRejectedError("MinerU input file is unavailable") from None

        begin = self.task_store.begin_or_resume(
            book_id=book_id,
            file_sha256=file_digest,
            options_sha256=resolved_options.fingerprint(),
            endpoint=self.config.endpoint,
            expected_generation=expected_generation,
        )
        record = begin.record
        if cloudpath_job_id is not None and record.cloudpath_job_id != cloudpath_job_id:
            raise MinerUStaleResultError("CloudPath job is not bound to the reserved parse generation")
        deadline = self._new_deadline()

        if record.status == "timed_out" and record.mineru_task_id is not None:
            raise MinerUTimeoutError(
                "A prior MinerU task timed out and must be explicitly invalidated before resubmission",
                task_id=record.mineru_task_id,
            )
        if record.mineru_task_id is None and record.status in {"submitting", "submission_uncertain"}:
            raise MinerUSubmissionUncertainError("A prior MinerU submission outcome is unknown")

        try:
            if record.mineru_task_id is None:
                self.health(deadline=deadline)
                self._assert_file_unchanged(path, initial_stat.st_size, initial_stat.st_mtime_ns)
                try:
                    self.task_store.mark_submitting(book_id, record.parse_generation)
                except MinerUProtocolError:
                    # Another caller may have atomically claimed the same
                    # created generation. Never mark that winner's record as
                    # failed and never send a second upload.
                    refreshed = self.task_store.get_current(book_id)
                    if refreshed is None or refreshed.parse_generation != record.parse_generation:
                        raise MinerUStaleResultError("MinerU parse generation was superseded") from None
                    if refreshed.mineru_task_id:
                        record = refreshed
                    elif refreshed.status in {"submitting", "submission_uncertain"}:
                        raise MinerUSubmissionUncertainError(
                            "A MinerU submission is already in progress or its outcome is unknown"
                        ) from None
                    else:
                        raise
                if record.mineru_task_id is not None:
                    submitted = None
                else:
                    try:
                        submitted = self.submit_task(path, resolved_options, deadline=deadline)
                    except MinerUSubmissionUncertainError as exc:
                        self.task_store.mark_submission_uncertain(book_id, record.parse_generation)
                        raise exc
                if submitted is not None:
                    record = self.task_store.attach_remote_task(
                        book_id,
                        record.parse_generation,
                        submitted.task_id,
                        queued_ahead=submitted.queued_ahead,
                    )

            task_id = record.mineru_task_id
            if task_id is None:
                raise MinerUProtocolError("Persisted MinerU task has no remote task id")

            def _persist_status(task: MinerUTaskResponse) -> None:
                self.task_store.update_remote_status(
                    book_id,
                    record.parse_generation,
                    task.status,
                    queued_ahead=task.queued_ahead,
                )

            current_guard = lambda: self.task_store.is_current(
                book_id,
                record.parse_generation,
                cloudpath_job_id=cloudpath_job_id,
            )
            task = self.poll_task(
                task_id,
                deadline=deadline,
                on_status=_persist_status,
                current_guard=current_guard,
            )

            result: MinerUResultResponse | None = None
            while result is None:
                if not current_guard():
                    raise MinerUStaleResultError("MinerU result belongs to an obsolete parse generation")
                result = self.get_result(task_id, deadline=deadline)
                if result is None:
                    self._sleep(self.config.poll_interval_seconds, deadline)
                    task = self.poll_task(
                        task_id,
                        deadline=deadline,
                        on_status=_persist_status,
                        current_guard=current_guard,
                    )

            if task.file_names and set(result.results) != set(task.file_names):
                raise MinerUProtocolError(
                    "MinerU result document identity does not match the task",
                    task_id=task_id,
                )
            self._assert_file_unchanged(path, initial_stat.st_size, initial_stat.st_mtime_ns)
            if not current_guard():
                raise MinerUStaleResultError("MinerU result belongs to an obsolete parse generation")
            result_digest = result.response_sha256
            if result_digest is None:
                raise MinerUProtocolError("MinerU result digest is unavailable", task_id=task_id)
            accepted = self.task_store.accept_result(book_id, record.parse_generation, result_digest)
            return MinerUExecutionResult(
                task=task,
                result=result,
                parse_generation=accepted.parse_generation,
                idempotency_key=accepted.idempotency_key,
                resumed=begin.resumed,
            )
        except MinerUTimeoutError:
            self._mark_if_current(
                book_id,
                record.parse_generation,
                lambda: self.task_store.mark_timed_out(book_id, record.parse_generation),
            )
            raise
        except MinerUTaskExpiredError:
            self._mark_if_current(
                book_id,
                record.parse_generation,
                lambda: self.task_store.mark_expired(book_id, record.parse_generation),
            )
            raise
        except MinerUStaleResultError:
            raise
        except MinerUSubmissionUncertainError:
            raise
        except MinerURateLimitError:
            current = self.task_store.get_current(book_id)
            if (
                current is not None
                and current.parse_generation == record.parse_generation
                and current.mineru_task_id is None
                and current.status == "submitting"
            ):
                # A direct POST 429 explicitly rejected the upload, unlike a
                # transport failure. It is safe to close this local claim.
                self.task_store.mark_failed(
                    book_id,
                    record.parse_generation,
                    code="mineru_rate_limited",
                )
            raise
        except MinerUUnavailableError:
            # A safe GET may be retried by a later CloudPath worker. Preserve
            # an already persisted task id so that retry resumes the same
            # upstream task instead of uploading the file again. If the health
            # check failed before submission, the created record is also safe
            # to claim later.
            raise
        except MinerUTaskFailedError as exc:
            self._mark_if_current(
                book_id,
                record.parse_generation,
                lambda: self.task_store.mark_failed(
                    book_id,
                    record.parse_generation,
                    code=exc.code,
                ),
            )
            raise
        except MinerUError as exc:
            current = self.task_store.get_current(book_id)
            if (
                current is not None
                and current.parse_generation == record.parse_generation
                and current.mineru_task_id is not None
                and current.status in {"submitted", "pending", "processing", "remote_completed"}
            ):
                # With a known task id, even protocol/config errors remain
                # recoverable or manually invalidatable. Closing the record
                # here would let the next caller upload a duplicate task.
                raise
            self._mark_if_current(
                book_id,
                record.parse_generation,
                lambda: self.task_store.mark_failed(
                    book_id,
                    record.parse_generation,
                    code=exc.code,
                ),
            )
            raise

    def _get_with_retry(
        self,
        path: str,
        *,
        acceptable_statuses: set[int],
        deadline: float,
        operation: str,
        max_response_bytes: int = _MAX_METADATA_RESPONSE_BYTES,
    ) -> httpx2.Response:
        attempt = 0
        while True:
            self._ensure_before_deadline(deadline, f"MinerU {operation} exceeded the total timeout")
            try:
                response = self._streaming_get(
                    path,
                    deadline=deadline,
                    max_response_bytes=max_response_bytes,
                    operation=operation,
                )
            except httpx2.TimeoutException:
                if attempt < self.config.max_retries:
                    self._sleep(self._retry_delay(attempt), deadline)
                    attempt += 1
                    continue
                raise MinerUTimeoutError(f"MinerU {operation} timed out") from None
            except httpx2.RequestError:
                if attempt < self.config.max_retries:
                    self._sleep(self._retry_delay(attempt), deadline)
                    attempt += 1
                    continue
                raise MinerUUnavailableError(f"MinerU {operation} is unavailable") from None

            if response.status_code in acceptable_statuses:
                return response
            if response.status_code == 429:
                if attempt < self.config.max_retries:
                    self._sleep(self._retry_after_delay(response, attempt), deadline)
                    attempt += 1
                    continue
                raise MinerURateLimitError(
                    f"MinerU {operation} is rate limited",
                    status_code=response.status_code,
                )
            if response.status_code in _RETRYABLE_STATUS_CODES or response.status_code >= 500:
                if attempt < self.config.max_retries:
                    self._sleep(self._retry_delay(attempt), deadline)
                    attempt += 1
                    continue
                raise MinerUUnavailableError(
                    f"MinerU {operation} is unavailable",
                    status_code=response.status_code,
                )
            if 400 <= response.status_code < 500:
                raise MinerURequestRejectedError(
                    f"MinerU rejected the {operation} request",
                    status_code=response.status_code,
                )
            raise MinerUProtocolError(
                f"MinerU {operation} returned an unexpected status",
                status_code=response.status_code,
            )

    def _streaming_get(
        self,
        path: str,
        *,
        deadline: float,
        max_response_bytes: int,
        operation: str,
    ) -> httpx2.Response:
        with self._client.stream(
            "GET",
            path,
            timeout=self._request_timeout(deadline),
        ) as upstream:
            raw_length = upstream.headers.get("Content-Length")
            if raw_length is not None:
                try:
                    announced_length = int(raw_length)
                except ValueError:
                    announced_length = -1
                if announced_length > max_response_bytes:
                    raise MinerUProtocolError(f"MinerU {operation} response exceeds the configured size limit")

            content = bytearray()
            for chunk in upstream.iter_bytes():
                self._ensure_before_deadline(deadline, f"MinerU {operation} exceeded the total timeout")
                content.extend(chunk)
                if len(content) > max_response_bytes:
                    raise MinerUProtocolError(f"MinerU {operation} response exceeds the configured size limit")
            self._ensure_before_deadline(deadline, f"MinerU {operation} exceeded the total timeout")
            # iter_bytes() yields decoded content. Do not carry compression or
            # the upstream wire length into the buffered response, otherwise
            # httpx2 may try to decompress the already decoded JSON again.
            response_headers = [
                (name, value)
                for name, value in upstream.headers.multi_items()
                if name.lower() not in {"content-encoding", "content-length"}
            ]
            response_headers.append(("content-length", str(len(content))))
            return httpx2.Response(
                upstream.status_code,
                headers=response_headers,
                content=bytes(content),
                request=upstream.request,
            )

    def _parse_task(self, response: httpx2.Response, label: str) -> MinerUTaskResponse:
        payload = self._json_object(response, label)
        try:
            return MinerUTaskResponse.model_validate(payload)
        except ValidationError:
            raise MinerUProtocolError(f"MinerU {label} is invalid") from None

    @staticmethod
    def _json_object(response: httpx2.Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except (ValueError, TypeError, json.JSONDecodeError):
            raise MinerUProtocolError(f"MinerU {label} is not valid JSON") from None
        if not isinstance(payload, dict):
            raise MinerUProtocolError(f"MinerU {label} is not a JSON object")
        return payload

    @staticmethod
    def _validate_task_id(task_id: str) -> str:
        try:
            return MinerUTaskResponse.model_validate({"task_id": task_id, "status": "pending"}).task_id
        except ValidationError:
            raise MinerUProtocolError("MinerU task id is invalid") from None

    def _new_deadline(self) -> float:
        return self._monotonic() + self.config.total_timeout_seconds

    def _request_timeout(self, deadline: float) -> httpx2.Timeout:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise MinerUTimeoutError("MinerU total timeout expired")
        per_request = min(self.config.request_timeout_seconds, remaining)
        connect = min(self.config.connect_timeout_seconds, remaining)
        return httpx2.Timeout(per_request, connect=connect)

    def _ensure_before_deadline(self, deadline: float, message: str) -> None:
        if self._monotonic() >= deadline:
            raise MinerUTimeoutError(message)

    def _sleep(self, delay: float, deadline: float) -> None:
        self._ensure_before_deadline(deadline, "MinerU total timeout expired")
        remaining = deadline - self._monotonic()
        bounded_delay = max(0.0, delay)
        if bounded_delay >= remaining:
            raise MinerUTimeoutError("MinerU total timeout expired")
        if bounded_delay:
            self._sleeper(bounded_delay)
        self._ensure_before_deadline(deadline, "MinerU total timeout expired")

    def _retry_delay(self, attempt: int) -> float:
        delay = self.config.retry_backoff_seconds * (2**attempt)
        return min(delay, self.config.retry_max_backoff_seconds)

    def _retry_after_delay(self, response: httpx2.Response, attempt: int) -> float:
        raw_value = response.headers.get("Retry-After")
        try:
            parsed = float(raw_value) if raw_value is not None else None
        except ValueError:
            parsed = None
        fallback = self._retry_delay(attempt)
        if parsed is None or parsed < 0:
            return fallback
        return min(max(parsed, fallback), self.config.retry_max_backoff_seconds)

    def _poll_delay(self, queued_ahead: int | None) -> float:
        if not queued_ahead:
            return self.config.poll_interval_seconds
        scaled = self.config.poll_interval_seconds * (queued_ahead + 1)
        return min(scaled, max(self.config.poll_interval_seconds, self.config.retry_max_backoff_seconds))

    @staticmethod
    def _assert_file_unchanged(path: Path, expected_size: int, expected_mtime_ns: int) -> None:
        try:
            current = path.stat()
        except OSError:
            raise MinerUStaleResultError("MinerU input file changed during parsing") from None
        if current.st_size != expected_size or current.st_mtime_ns != expected_mtime_ns:
            raise MinerUStaleResultError("MinerU input file changed during parsing")

    def _mark_if_current(self, book_id: str, generation: int, action: Callable[[], object]) -> None:
        if not self.task_store.is_current(book_id, generation):
            return
        try:
            action()
        except MinerUStaleResultError:
            pass
