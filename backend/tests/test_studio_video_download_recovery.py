"""Existing paid video tasks survive asset transport failures without resubmission."""

import json
import socket
import time
from contextlib import contextmanager

import httpx
import pytest
from test_studio_media_errors import polling_job
from test_studio_media_errors import provider_settings as provider_settings

from adaptive_learning.studio import Studio

CDN = "algeng-video-infer.oss-cn-shanghai.aliyuncs.com"
ASSET = f"https://{CDN}/generated/video.mp4?signature=offline-fixture"


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("error_type", [httpx.ProxyError, httpx.ReadTimeout])
def test_generated_video_download_retries_only_original_task_gets(tmp_path, monkeypatch, version, error_type):
    studio, row = polling_job(tmp_path, version)
    original_result, original_reserve = row["result"], row["reserved"]
    queries, downloads = [], []

    def query(path, *args, **kwargs):
        queries.append(path)
        if version == 2:
            assert path == "/v2/query/video_generation/123456"
            return {"task": {"id": "123456", "status": "succeeded", "content": {"url": ASSET}}}
        if path == "/v1/query/video_generation?task_id=123456":
            return {"task_id": "123456", "status": "Success", "file_id": "654321"}
        assert path == "/v1/files/retrieve?file_id=654321"
        return {"file": {"download_url": ASSET}}

    def download(url, limit):
        downloads.append((url, limit))
        if len(downloads) == 1:
            raise error_type("offline proxy rejection or asset timeout")
        return b"existing-generated-video"

    monkeypatch.setattr(studio, "minimax", query)
    monkeypatch.setattr(studio, "download", download)
    monkeypatch.setattr(studio, "llm", lambda *args, **kwargs: pytest.fail("Never replan or regenerate"))
    started = time.time()
    studio.process(row)
    pending = studio.job("owner", row["id"])
    assert pending["status"] == "polling" and pending["provider_id"] == "123456"
    assert pending["next_poll"] > started
    assert pending["result"] == original_result and pending["reserved"] == original_reserve
    studio.process(pending)
    recovered = studio.job("owner", row["id"])
    assert recovered["status"] == "reviewing" and recovered["provider_id"] == "123456"
    assert recovered["reserved"] == original_reserve and recovered["result"] == original_result
    assert len(downloads) == 2 and downloads == [(ASSET, 60_000_000)] * 2
    assert queries == (["/v2/query/video_generation/123456"] * 2 if version == 2 else [
        "/v1/query/video_generation?task_id=123456", "/v1/files/retrieve?file_id=654321",
    ] * 2)
    assert (studio.assets / f"{row['id']}.mp4").read_bytes() == b"existing-generated-video"


def test_asset_http_rejection_retains_provider_task_without_regeneration(tmp_path, monkeypatch):
    studio, row = polling_job(tmp_path, 2)
    monkeypatch.setattr(studio, "minimax", lambda path: {
        "task": {"id": "123456", "status": "succeeded", "content": {"url": ASSET}},
    })

    def rejected(url, limit):
        response = httpx.Response(403, request=httpx.Request("GET", url))
        response.raise_for_status()

    monkeypatch.setattr(studio, "download", rejected)
    studio.process(row)
    current = studio.job("owner", row["id"])
    assert current["status"] == "uncertain" and current["provider_id"] == "123456"
    assert current["reserved"] == row["reserved"]
    diagnostic = json.loads(current["result"])["_diagnostic"]
    assert diagnostic["http_status"] == 403
    assert studio.claim_next(lane="media") is None


@pytest.mark.parametrize("url", [
    f"http://{CDN}/video.mp4", f"https://user:pass@{CDN}/video.mp4",
    f"https://{CDN}:444/video.mp4", "https://127.0.0.1/video.mp4",
    "https://localhost/video.mp4", "https://10.0.0.1/video.mp4",
])
def test_asset_download_still_rejects_unsafe_url_or_private_dns(tmp_path, monkeypatch, url):
    studio = Studio(tmp_path)
    monkeypatch.setattr("adaptive_learning.studio.socket.getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    monkeypatch.setattr("adaptive_learning.studio.httpx.stream",
                        lambda *args, **kwargs: pytest.fail("Unsafe asset must not open a connection"))
    with pytest.raises(ValueError, match="invalid asset URL|private asset URL"):
        studio.download(url, 1024)


def test_asset_download_does_not_follow_redirects_to_private_hosts(tmp_path, monkeypatch):
    studio, seen = Studio(tmp_path), []
    monkeypatch.setattr("adaptive_learning.studio.socket.getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ])

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})

    @contextmanager
    def stream(method, url, **kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            with client.stream(method, url) as response:
                yield response

    monkeypatch.setattr("adaptive_learning.studio.httpx.stream", stream)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        studio.download(ASSET, 1024)
    assert caught.value.response.status_code == 302
    assert len(seen) == 1 and seen[0].method == "GET" and seen[0].url.host == CDN
