"""Offline paid-request and existing-task error contracts; no generated media."""

import json
import time

import httpx
import pytest

from adaptive_learning.config import get_settings
from adaptive_learning.studio import Studio


@pytest.fixture(autouse=True)
def provider_settings(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-text-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "offline-media-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimax.cn")
    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "off")
    for name in ("MINIMAX_VIDEO_MODEL", "MINIMAX_VIDEO_RESOLUTION", "MINIMAX_VIDEO_BASE_URL", "LLM_HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def video_input():
    return {"book_id": "book-1", "excerpt": "函数调用自身，需要终止条件。", "pages": [1],
            "chapter_id": "chapter-1", "goal": "意思", "level": "入门", "kind": "video",
            "consent": True, "request_id": "offline_video_123"}


def polling_job(tmp_path, version):
    studio = Studio(tmp_path)
    job = studio.submit("owner", video_input(), "video")
    result = {**job["result"], "video_api_version": version}
    if version == 1:
        result.update(model="MiniMax-Hailuo-02", resolution="512P")
    studio.update(job["id"], status="polling", provider_id="123456",
                  result=json.dumps(result))
    return studio, studio.job("owner", job["id"])


def transport(monkeypatch, handler):
    original_client = httpx.Client
    requests = []

    def wrapped(request):
        requests.append(request)
        return handler(request)

    monkeypatch.setattr("adaptive_learning.studio.httpx.Client", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(wrapped), **kwargs))
    return requests


@pytest.mark.parametrize("version", [1, 2])
def test_terminal_system_1033_fails_existing_task_without_auth_claim_or_new_post(tmp_path, monkeypatch, version):
    studio, row = polling_job(tmp_path, version)
    body = {"base_resp": {"status_code": 1033, "status_msg": "untrusted private body"}}
    if version == 1:
        body.update(task_id="123456", status="Fail")
    else:
        body["task"] = {"id": "123456", "status": "failed", "error": {"code": "1033"}}
    requests = transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    studio.poll_video(row)
    current = studio.job("owner", row["id"])
    assert current["status"] == "failed" and current["provider_id"] == "123456"
    assert "1033" in current["error"] and "系统错误" in current["error"]
    assert "授权" not in current["error"] and "额度" not in current["error"]
    assert "untrusted private body" not in current["error"]
    diagnostic = json.loads(current["result"])["_diagnostic"]
    assert diagnostic["provider_code"] == 1033
    assert diagnostic["type"] == "ProviderTaskFailed"
    assert studio.claim_next(lane="media") is None
    assert studio.submit("owner", video_input(), "video")["id"] == row["id"]
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("code", [1033, 1002])
def test_nonterminal_system_or_rate_limit_retries_query_only(tmp_path, monkeypatch, code):
    studio, row = polling_job(tmp_path, 1)
    requests = transport(monkeypatch, lambda request: httpx.Response(200, json={
        "task_id": "123456", "status": "Processing", "base_resp": {"status_code": code},
    }))
    started = time.time()
    studio.poll_video(row)
    current = studio.job("owner", row["id"])
    assert current["status"] == "polling" and current["provider_id"] == "123456"
    assert current["next_poll"] > started
    assert json.loads(current["result"])["_diagnostic"]["provider_code"] == code
    studio.poll_video(current)
    assert len(requests) == 2 and all(request.method == "GET" for request in requests)


@pytest.mark.parametrize("status", [429, 500, 503, 529])
def test_temporary_http_query_failure_keeps_existing_task_without_submission(tmp_path, monkeypatch, status):
    studio, row = polling_job(tmp_path, 2)
    requests = transport(monkeypatch, lambda request: httpx.Response(status, json={"error": "private"}))
    started = time.time()
    studio.poll_video(row)
    current = studio.job("owner", row["id"])
    assert current["status"] == "polling" and current["provider_id"] == "123456"
    assert current["next_poll"] > started
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("status,code", [(401, None), (403, None), (404, None),
                                        (200, 1004), (200, 2049), (200, 7777)])
def test_auth_or_unknown_query_rejection_stops_polling_and_retains_task(tmp_path, monkeypatch, status, code):
    studio, row = polling_job(tmp_path, 1)
    body = {"task_id": "123456", "status": "Processing", "base_resp": {"status_code": code or 0}}
    requests = transport(monkeypatch, lambda request: httpx.Response(status, json=body))
    studio.poll_video(row)
    current = studio.job("owner", row["id"])
    assert current["status"] == "uncertain" and current["provider_id"] == "123456"
    assert "已停止自动查询" in current["error"]
    assert studio.claim_next(lane="media") is None
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("task_id,code,expected", [("999999", 1033, "polling"),
                                                  ("123456", 1004, "uncertain")])
def test_terminal_error_does_not_override_task_identity_or_authentication(tmp_path, monkeypatch, task_id, code, expected):
    studio, row = polling_job(tmp_path, 1)
    requests = transport(monkeypatch, lambda request: httpx.Response(200, json={
        "task_id": task_id, "status": "Fail", "base_resp": {"status_code": code},
    }))
    studio.poll_video(row)
    assert studio.job("owner", row["id"])["status"] == expected
    assert len(requests) == 1 and requests[0].method == "GET"


def test_default_lite_480p_cost_and_single_v2_submission(tmp_path, monkeypatch):
    studio = Studio(tmp_path)
    queued = studio.submit("owner", video_input(), "video")
    assert queued["result"]["model"] == "MiniMax-H3-Lite"
    assert queued["result"]["resolution"] == "480P"
    assert studio.job("owner", queued["id"])["reserved"] == 0.96
    monkeypatch.setattr(studio, "evidence", lambda *args: [{"page": 1, "text": "函数调用自身。"}])
    monkeypatch.setattr(studio, "llm", lambda *args: {
        "title": "递归示意", "visual_scope": "有限嵌套", "visual_checks": ["嵌套"],
        "explanation": "函数调用自身。", "points": ["有限嵌套"],
        "visual_prompt": "Nested colorful boxes.", "caution": "辅助示意。",
        "supported": True, "video_suitable": True,
    })
    requests = transport(monkeypatch, lambda request: httpx.Response(200, json={"task_id": "123456"}))
    studio.process(studio.job("owner", queued["id"]))
    assert len(requests) == 1 and requests[0].method == "POST"
    assert requests[0].url.path == "/v2/video_generation"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "MiniMax-H3-Lite" and payload["resolution"] == "480P"
    assert payload["duration"] == 6 and payload["ratio"] == "4:3"
    assert "extra" not in payload and "first_frame_image" not in payload
    assert studio.job("owner", queued["id"])["status"] == "polling"
