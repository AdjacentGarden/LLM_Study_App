from __future__ import annotations

import re


_WINDOWS_PATH = re.compile(r"(?i)\b[a-z]:[\\/][^\s,;]+")
_UNIX_PATH = re.compile(r"(?<!:)\B/(?:[^\s/]+/)+[^\s,;]+")


def safe_error_detail(value: object, *, limit: int = 300) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    text = _WINDOWS_PATH.sub("[local-path]", text)
    text = _UNIX_PATH.sub("[local-path]", text)
    return text[:limit] or None


class MinerUError(RuntimeError):
    code = "mineru_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        task_id: str | None = None,
        detail: object = None,
    ) -> None:
        self.status_code = status_code
        self.task_id = task_id
        self.detail = safe_error_detail(detail)
        super().__init__(message)


class MinerUConfigurationError(MinerUError):
    code = "mineru_configuration_error"


class MinerUUnavailableError(MinerUError):
    code = "mineru_unavailable"
    retryable = True


class MinerUTimeoutError(MinerUError):
    code = "mineru_timeout"
    retryable = True


class MinerURateLimitError(MinerUError):
    code = "mineru_rate_limited"
    retryable = True


class MinerUSubmissionUncertainError(MinerUError):
    code = "mineru_submission_uncertain"


class MinerURequestRejectedError(MinerUError):
    code = "mineru_request_rejected"


class MinerUTaskFailedError(MinerUError):
    code = "mineru_task_failed"


class MinerUTaskExpiredError(MinerUError):
    code = "mineru_task_expired"


class MinerUProtocolError(MinerUError):
    code = "mineru_protocol_error"


class MinerUStaleResultError(MinerUError):
    code = "mineru_stale_result"
