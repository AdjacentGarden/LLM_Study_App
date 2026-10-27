"""Offline provider payload and paid-job recovery contracts; no generated videos."""

import pytest
from PIL import Image
from fastapi import HTTPException
from test_community import community as community
from test_studio import media, run, setup as setup

from adaptive_learning.studio import Studio


@pytest.mark.parametrize("width,height", [(511, 1536), (2056, 1536), (2048, 1537), (2048, 0)])
def test_invalid_minimax_image_dimensions_rejected_before_submission(monkeypatch, width, height):
    studio = Studio.__new__(Studio)
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_IMAGE_WIDTH", str(width))
    monkeypatch.setenv("MINIMAX_IMAGE_HEIGHT", str(height))
    monkeypatch.setattr(studio, "minimax", lambda *args: pytest.fail("no paid submission"))
    with pytest.raises(ValueError, match="multiples of 8"):
        studio.image_generation_config()


@pytest.mark.parametrize("model,resolution,valid", [
    ("MiniMax-H3", "2K", True), ("MiniMax-H3", "768P", True),
    ("MiniMax-H3-Max", "768P", True), ("MiniMax-H3-Max", "480P", True),
    ("MiniMax-H3-Lite", "480P", True), ("MiniMax-H3-Lite", "768P", True),
    ("MiniMax-H3-Highspeed", "768P", True), ("MiniMax-H3-Lite", "2K", False),
    ("MiniMax-H3-Highspeed", "480P", False),
    ("MiniMax-Hailuo-02", "512P", True), ("MiniMax-Hailuo-02", "2K", False),
    ("MiniMax-H3-Max", "2K", False), ("MiniMax-H3", "1080P", False),
    ("MiniMax-H3", "480P", False), ("unknown", "2K", False),
])
def test_video_model_resolution_combinations_match_v2_contract(monkeypatch, model, resolution, valid):
    studio = Studio.__new__(Studio)
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", model)
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", resolution)
    if not valid:
        with pytest.raises(ValueError, match="model/resolution"):
            studio.video_generation_config()
    else:
        assert studio.video_generation_config() == {
            "provider": "MiniMax", "model": model, "resolution": resolution,
            "ratio": "4:3", "duration": 6,
        }


def test_pucoding_settings_are_kept_when_minimax_dimensions_are_invalid(monkeypatch):
    studio = Studio.__new__(Studio)
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "pucoding")
    monkeypatch.setenv("MINIMAX_IMAGE_WIDTH", "invalid")
    monkeypatch.setenv("PUCODING_IMAGE_SIZE", "1536x1024")
    monkeypatch.setenv("PUCODING_IMAGE_QUALITY", "high")
    config = studio.image_generation_config()
    assert config == {"provider": "PUCODING", "model": "gpt-image-2",
                      "size": "1536x1024", "quality": "high"}
    monkeypatch.setenv("PUCODING_IMAGE_SIZE", "1024x768")
    monkeypatch.setenv("PUCODING_IMAGE_QUALITY", "low")
    calls = []
    monkeypatch.setattr(studio, "pucoding_image", lambda payload: calls.append(payload) or {
        "data": [{"b64_json": "offline-image"}],
    })
    monkeypatch.setattr("adaptive_learning.studio.get_settings", lambda: type("Settings", (), {"pucoding_api_key": "fixture"})())
    assert studio.generate_image("offline prompt", config) == "offline-image"
    assert calls[0]["size"] == "1536x1024" and calls[0]["quality"] == "high"
    assert "width" not in calls[0] and "aspect_ratio" not in calls[0]


@pytest.mark.parametrize("kind", ["image", "video"])
def test_changed_resolution_gets_new_job_but_original_request_recovers_unchanged(tmp_path, monkeypatch, kind):
    studio = Studio(tmp_path)
    monkeypatch.setenv("STUDIO_MEDIA_DAILY_CNY", "20")
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_IMAGE_WIDTH", "1152")
    monkeypatch.setenv("MINIMAX_IMAGE_HEIGHT", "864")
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3-Max")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "768P")
    data = media(kind=kind)
    old = studio.submit("owner", data, kind)
    studio.update(old["id"], status="succeeded")
    before = dict(studio.job("owner", old["id"]))
    monkeypatch.setenv("MINIMAX_IMAGE_WIDTH", "2048")
    monkeypatch.setenv("MINIMAX_IMAGE_HEIGHT", "1536")
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "2K")
    assert studio.submit("owner", data, kind)["id"] == old["id"]
    assert dict(studio.job("owner", old["id"])) == before
    fresh = studio.submit("owner", {**data, "request_id": "fresh_high_resolution"}, kind)
    assert fresh["id"] != old["id"]
    snapshot = fresh["result"]
    if kind == "image":
        assert snapshot["width"] == 2048 and snapshot["height"] == 1536
    else:
        assert snapshot["model"] == "MiniMax-H3" and snapshot["resolution"] == "2K"
        assert snapshot["ratio"] == "4:3" and snapshot["duration"] == 6
    assert studio.submit("owner", {**data, "request_id": "same_high_resolution"}, kind)["id"] == fresh["id"]
    # Even a later invalid configuration cannot turn receipt recovery into new paid work.
    monkeypatch.setenv("MINIMAX_IMAGE_WIDTH", "invalid")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "invalid")
    assert studio.submit("owner", data, kind)["id"] == old["id"]


@pytest.mark.parametrize("model,resolution,expected", [
    ("MiniMax-H3", "2K", 4.80), ("MiniMax-H3", "768P", 3.00),
    ("MiniMax-H3-Max", "768P", 3.00), ("MiniMax-H3-Max", "480P", 1.98),
    ("MiniMax-H3-Lite", "480P", 0.96), ("MiniMax-H3-Lite", "768P", 1.56),
    ("MiniMax-H3-Highspeed", "768P", 2.40),
    ("MiniMax-Hailuo-02", "512P", 0.60),
])
def test_video_estimate_matches_six_second_output_pricing(monkeypatch, model, resolution, expected):
    studio = Studio.__new__(Studio)
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", model)
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", resolution)
    assert studio.video_estimate(studio.video_generation_config()) == expected


def test_high_definition_video_reserves_480_and_respects_unchanged_daily_limit(tmp_path, monkeypatch):
    studio = Studio(tmp_path)
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "2K")
    monkeypatch.setenv("STUDIO_MEDIA_DAILY_CNY", "5")
    first = studio.submit("owner", media(kind="video"), "video")
    assert studio.job("owner", first["id"])["reserved"] == 4.80
    # A duplicate/receipt refresh is free even when another job would exceed budget.
    assert studio.submit("owner", media(kind="video", request_id="same_video"), "video")["id"] == first["id"]
    with pytest.raises(HTTPException) as error:
        studio.submit("owner", media(kind="video", excerpt="另一个不同的教材选段。", request_id="another_video"), "video")
    assert error.value.status_code == 429
    assert studio.job("owner", first["id"])["reserved"] == 4.80


@pytest.mark.parametrize("limit,accepted", [("4.79", False), ("4.80", True)])
def test_video_budget_boundary_uses_current_high_definition_estimate(tmp_path, monkeypatch, limit, accepted):
    studio = Studio(tmp_path)
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "2K")
    monkeypatch.setenv("STUDIO_MEDIA_DAILY_CNY", limit)
    if accepted:
        job = studio.submit("owner", media(kind="video"), "video")
        assert studio.job("owner", job["id"])["reserved"] == 4.80
    else:
        with pytest.raises(HTTPException) as error:
            studio.submit("owner", media(kind="video"), "video")
        assert error.value.status_code == 429


@pytest.mark.parametrize("model,resolution", [("MiniMax-H3", "2K"), ("MiniMax-H3-Max", "768P")])
def test_queued_video_keeps_model_and_resolution_when_runtime_settings_change(setup, monkeypatch, model, resolution):
    a, _, studio = setup
    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "off")
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", model)
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", resolution)
    queued = a.post("/api/studio/media", json=media(kind="video")).json()
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3-Max" if model == "MiniMax-H3" else "MiniMax-H3")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "768P" if resolution == "2K" else "2K")
    monkeypatch.setattr(studio, "evidence", lambda *args: [{"page": 1, "text": "函数调用自身。"}])
    monkeypatch.setattr(studio, "reference_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(studio, "llm", lambda *args: {
        "title": "递归示意", "visual_scope": "有限嵌套", "visual_checks": ["嵌套"],
        "explanation": "函数调用自身。", "points": ["有限嵌套"],
        "visual_prompt": "Nested colorful boxes.", "caution": "辅助示意。",
        "supported": True, "video_suitable": True,
    })
    calls = []
    monkeypatch.setattr(studio, "minimax", lambda *args: calls.append(args) or {"task_id": "123456"})
    run(studio, a, queued["id"])
    assert len(calls) == 1 and calls[0][0] == "/v2/video_generation"
    payload = calls[0][1]
    assert payload["model"] == model and payload["resolution"] == resolution
    assert payload["ratio"] == "4:3" and payload["duration"] == 6
    assert ("extra" in payload) is (model == "MiniMax-H3-Max")
    if "extra" in payload:
        assert payload["extra"] == {"prompt_expansion_mode": "disabled"}
    result = a.get("/api/studio/jobs/" + queued["id"]).json()
    assert result["status"] == "polling"
    assert result["result"]["model"] == model and result["result"]["resolution"] == resolution


def test_explicit_hailuo_missing_matching_image_rejects_without_queuing(setup, monkeypatch):
    a, _, studio = setup
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", "MiniMax-Hailuo-02")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "512P")
    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "off")
    monkeypatch.setattr(studio, "minimax", lambda *args: pytest.fail("no paid request"))
    monkeypatch.setattr(studio, "llm", lambda *args: pytest.fail("no planning request"))
    with studio.repo.connect() as db:
        before = db.execute("SELECT COUNT(*),COALESCE(SUM(reserved),0) FROM studio_jobs").fetchone()
    response = a.post("/api/studio/media", json=media(kind="video"))
    assert response.status_code == 409
    assert "请先生成辅助图" in response.json()["detail"]
    with studio.repo.connect() as db:
        after = db.execute("SELECT COUNT(*),COALESCE(SUM(reserved),0) FROM studio_jobs").fetchone()
    assert tuple(after) == tuple(before)
    # A completed image from another selection also cannot be used.
    image_id = a.post("/api/studio/media", json=media(excerpt="另一个不同的教材选段。", request_id="other_image")).json()["id"]
    Image.new("RGB", (64, 48)).save(studio.assets / f"{image_id}.jpg")
    studio.update(image_id, status="succeeded")
    response = a.post("/api/studio/media", json=media(kind="video", request_id="still_missing_image"))
    assert response.status_code == 409
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs WHERE kind='video'").fetchone()[0] == 0


def test_explicit_hailuo_512p_uses_one_v1_i2v_request_and_resumes_v1_polling(setup, monkeypatch):
    a, _, studio = setup
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", "MiniMax-Hailuo-02")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "512P")
    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "off")
    image_id = a.post("/api/studio/media", json=media()).json()["id"]
    Image.new("RGB", (64, 48)).save(studio.assets / f"{image_id}.jpg")
    studio.update(image_id, status="succeeded")
    request = media(kind="video", request_id="default_low_cost_video")
    queued = a.post("/api/studio/media", json=request).json()
    assert queued["result"]["model"] == "MiniMax-Hailuo-02"
    assert queued["result"]["resolution"] == "512P"
    assert queued["result"]["reference_image_id"] == image_id
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    assert studio.job(owner, queued["id"])["reserved"] == 0.60
    # A changed runtime setting cannot change an acknowledged low-cost job.
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "2K")
    monkeypatch.setattr(studio, "evidence", lambda *args: [{"page": 1, "text": "函数调用自身。"}])
    monkeypatch.setattr(studio, "llm", lambda *args: {
        "title": "递归示意", "visual_scope": "有限嵌套", "visual_checks": ["嵌套"],
        "explanation": "函数调用自身。", "points": ["有限嵌套"],
        "visual_prompt": "Nested colorful boxes.", "caution": "辅助示意。",
        "supported": True, "video_suitable": True,
    })
    calls = []

    def provider(path, payload=None):
        calls.append((path, payload))
        if payload is not None:
            return {"task_id": "123456"}
        if "query" in path:
            return {"status": "Success", "file_id": "654321"}
        return {"file": {"download_url": "https://cdn.example.com/video.mp4"}}

    monkeypatch.setattr(studio, "minimax", provider)
    monkeypatch.setattr(studio, "download", lambda url, limit: b"offline-video-bytes")
    run(studio, a, queued["id"])
    assert calls[0][0] == "/v1/video_generation"
    payload = calls[0][1]
    assert set(payload) == {"model", "prompt", "first_frame_image", "resolution", "duration", "prompt_optimizer"}
    assert payload["model"] == "MiniMax-Hailuo-02" and payload["resolution"] == "512P"
    assert payload["duration"] == 6 and payload["prompt_optimizer"] is False
    assert payload["first_frame_image"].startswith("data:image/jpeg;base64,")
    pending = a.get("/api/studio/jobs/" + queued["id"]).json()
    assert pending["status"] == "polling" and pending["result"]["video_api_version"] == 1
    assert pending["result"]["ratio"] == "adaptive"
    run(studio, a, queued["id"])
    assert [path for path, _ in calls] == ["/v1/video_generation", "/v1/query/video_generation?task_id=123456",
                                         "/v1/files/retrieve?file_id=654321"]
    assert (studio.assets / f"{queued['id']}.mp4").read_bytes() == b"offline-video-bytes"
    assert a.get("/api/studio/jobs/" + queued["id"]).json()["status"] == "reviewing"
    # Loss of the original image does not make receipt recovery create another job.
    (studio.assets / f"{image_id}.jpg").unlink()
    assert a.post("/api/studio/media", json=request).json()["id"] == queued["id"]
    assert len(calls) == 3
