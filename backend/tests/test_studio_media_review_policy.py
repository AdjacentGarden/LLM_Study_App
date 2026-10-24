"""Media review policy exercises the real job/API path with offline boundaries."""
import base64
import io
import json
from subprocess import CompletedProcess

import pytest
from PIL import Image
from test_community import community as community
from test_studio import media, note, run
from test_studio import setup as setup
from test_studio import test_note_review_repairs_once_and_rechecks as note_rejection_scenario

from adaptive_learning.config import get_settings
from adaptive_learning.studio import Studio
from adaptive_learning.studio_models import Review

_REAL_CAPABILITIES = Studio.capabilities
_REVIEW_NOTICE = "AI 生成的辅助示意，未进行 AI 质量复核，请结合教材使用。"


@pytest.fixture(autouse=True)
def media_review_off(monkeypatch):
    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "off")


def plan():
    # These valid structural fields deliberately fail the former quality gates.
    return {
        "title": "递归的辅助示意", "visual_scope": "观察嵌套形式", "visual_checks": ["嵌套形式"],
        "explanation": "借助嵌套形式辅助理解，实际知识以教材为准。",
        "points": ["辅助示意"], "visual_prompt": "Nested colorful boxes on a plain background.",
        "caution": "示意图不替代教材。", "supported": False, "video_suitable": False,
        "visual_mode": "diagram",
        "diagram_facts": [{"subject": "函数", "relation": "调用", "object": "自身"}],
    }


def png():
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "#68a4cf").save(output, "PNG")
    return output.getvalue()


def prepare_planner(studio, monkeypatch):
    calls = []

    def planner(prompt, images=None):
        assert not images, "off mode must not invoke a visual model"
        calls.append(prompt)
        return plan()

    monkeypatch.setattr(studio, "llm", planner)
    monkeypatch.setattr(studio, "evidence", lambda *args: [{"page": 1, "text": "函数调用自身。"}])
    monkeypatch.setattr(studio, "review_plan", lambda *args: pytest.fail("off plan review"))
    return calls


def finish_if_reviewing(studio, client, key):
    if client.get(f"/api/studio/jobs/{key}").json()["status"] == "reviewing":
        run(studio, client, key)
    return client.get(f"/api/studio/jobs/{key}").json()


@pytest.mark.parametrize("change_runtime_mode", [False, True])
def test_off_image_publishes_once_without_quality_gates_and_keeps_submitted_policy(
    setup, monkeypatch, change_runtime_mode,
):
    a, b, studio = setup
    planner_calls = prepare_planner(studio, monkeypatch)
    generated = []

    def generate(payload):
        generated.append(payload)
        return {"data": [{"b64_json": base64.b64encode(png()).decode()}]}

    monkeypatch.setattr(studio, "pucoding_image", generate)
    monkeypatch.setattr("adaptive_learning.studio.render_diagram", lambda *args: pytest.fail("off diagram gate"))
    created = a.post("/api/studio/media", json=media()).json()
    key = created["id"]
    assert created["result"]["media_review_mode"] == "off"
    if change_runtime_mode:
        monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "strict")
    run(studio, a, key)
    completed = finish_if_reviewing(studio, a, key)
    assert completed["status"] == "succeeded"
    assert completed["result"]["media_review_mode"] == "off"
    assert completed["result"]["visual_mode"] == "illustration"
    assert completed["result"]["review"] == _REVIEW_NOTICE
    assert "已复核" not in completed["result"]["review"]
    assert len(planner_calls) == len(generated) == 1
    assert generated[0]["n"] == 1
    assert "visual_prompt" not in completed["result"]
    asset = a.get(completed["asset_url"])
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "private, no-store"
    assert asset.headers["x-content-type-options"] == "nosniff"
    with Image.open(io.BytesIO(asset.content)) as image:
        image.verify()
    assert b.get(completed["asset_url"]).status_code == 404
    assert a.post("/api/studio/media", json=media()).json()["id"] == key
    assert len(generated) == 1


def fake_video_decoder(monkeypatch, *, duration=6, decode_error=False):
    calls = []
    monkeypatch.setenv("STUDIO_FFMPEG", "fixture-ffmpeg")

    def decode(command, **kwargs):
        calls.append(command)
        assert command[0] == "fixture-ffmpeg"
        assert "-ss" not in command and "image2pipe" not in command
        if command[1] == "-i":
            return CompletedProcess(command, 1, b"", f"Duration: 00:00:{duration:05.2f}, start: 0".encode())
        assert command[-3:] == ["-f", "null", "-"]
        assert "-xerror" in command
        assert command[command.index("-map") + 1] == "0:v:0"
        return CompletedProcess(command, int(decode_error), b"", b"invalid" if decode_error else b"")

    monkeypatch.setattr("adaptive_learning.studio.subprocess.run", decode)
    return calls


def test_off_video_resume_keeps_one_paid_submission_and_validates_file_without_visual_frames(setup, monkeypatch):
    a, b, studio = setup
    planner_calls = prepare_planner(studio, monkeypatch)
    decode_calls = fake_video_decoder(monkeypatch)
    submits, queries, downloads = [], [], []

    def provider(path, payload=None):
        if payload is not None:
            submits.append((path, payload))
            assert path == "/v2/video_generation"
            return {"task_id": "12345678"}
        queries.append(path)
        return {"task": {"status": "running" if len(queries) == 1 else "succeeded",
                         "content": {"url": "https://assets.example.test/clip.mp4"}}}

    monkeypatch.setattr(studio, "minimax", provider)
    monkeypatch.setattr(studio, "download", lambda url, limit: downloads.append((url, limit)) or b"decoder-boundary-fixture")
    payload = media(kind="video")
    key = a.post("/api/studio/media", json=payload).json()["id"]
    run(studio, a, key)
    assert a.get(f"/api/studio/jobs/{key}").json()["status"] == "polling"
    run(studio, a, key)
    assert a.get(f"/api/studio/jobs/{key}").json()["status"] == "polling"
    assert a.post("/api/studio/media", json=payload).json()["id"] == key
    run(studio, a, key)
    completed = finish_if_reviewing(studio, a, key)
    assert completed["status"] == "succeeded"
    assert completed["result"]["review"] == _REVIEW_NOTICE
    assert len(submits) == len(planner_calls) == len(downloads) == 1
    assert len(queries) == 2 and len(decode_calls) == 2
    assert a.get(completed["asset_url"]).status_code == 200
    assert b.get(completed["asset_url"]).status_code == 404


def test_off_rejects_corrupt_existing_image_before_exposing_asset(setup, monkeypatch):
    a, _, studio = setup
    key = a.post("/api/studio/media", json=media()).json()["id"]
    (studio.assets / f"{key}.jpg").write_bytes(b"not an image")
    studio.update(key, status="reviewing", result=json.dumps({"media_review_mode": "off"}))
    monkeypatch.setattr(studio, "llm", lambda *args: pytest.fail("no quality review"))
    with pytest.raises((OSError, ValueError)):
        run(studio, a, key)
    assert a.get(f"/api/studio/jobs/{key}").json()["asset_url"] is None
    assert a.get(f"/api/studio/jobs/{key}/asset").status_code == 404


@pytest.mark.parametrize("duration,decode_error", [(16, False), (6, True)])
def test_off_still_rejects_overlong_or_undecodable_video(setup, monkeypatch, duration, decode_error):
    a, _, studio = setup
    key = a.post("/api/studio/media", json=media(kind="video")).json()["id"]
    (studio.assets / f"{key}.mp4").write_bytes(b"decoder-boundary-fixture")
    studio.update(key, status="reviewing", result=json.dumps({"media_review_mode": "off"}))
    calls = fake_video_decoder(monkeypatch, duration=duration, decode_error=decode_error)
    monkeypatch.setattr(studio, "llm", lambda *args: pytest.fail("no quality review"))
    with pytest.raises(ValueError):
        run(studio, a, key)
    assert len(calls) == (1 if duration > 15 else 2)
    assert a.get(f"/api/studio/jobs/{key}").json()["asset_url"] is None
    assert a.get(f"/api/studio/jobs/{key}/asset").status_code == 404


def test_media_policy_separates_fresh_cache_but_preserves_original_request_identity(setup, monkeypatch):
    a, _, _ = setup
    original = a.post("/api/studio/media", json=media()).json()
    off_id = original["id"]
    assert original["result"]["media_review_mode"] == "off"
    assert a.post("/api/studio/media", json=media(request_id="other_off_request")).json()["id"] == off_id
    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "strict")
    assert a.post("/api/studio/media", json=media()).json()["id"] == off_id
    strict = a.post("/api/studio/media", json=media(request_id="strict_request")).json()
    assert strict["id"] != off_id
    assert strict["result"]["media_review_mode"] == "strict"
    assert a.post("/api/studio/media", json=media(request_id="strict_repeated")).json()["id"] == strict["id"]
    assert a.post("/api/studio/media", json=media(excerpt="不同的教材内容。")).status_code == 409


def test_off_media_capabilities_do_not_require_visual_review_but_notes_still_do(setup, monkeypatch):
    _, _, studio = setup
    monkeypatch.setattr(Studio, "capabilities", _REAL_CAPABILITIES)
    for name, value in {
        "STUDIO_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "fixture-planner",
        "PUCODING_API_KEY": "fixture-image", "MINIMAX_API_KEY": "fixture-video",
        "STUDIO_VISION_PROVIDER": "unconfigured-reviewer", "STUDIO_FFMPEG": "fixture-ffmpeg",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("STUDIO_MEDIA_PAUSED_REASON", raising=False)
    get_settings.cache_clear()
    try:
        relaxed = studio.capabilities()
        assert relaxed["image"] and relaxed["video"]
        assert not relaxed["notes_ai"]
        monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "strict")
        strict = studio.capabilities()
        assert not strict["image"] and not strict["video"]
        assert not strict["notes_ai"]
    finally:
        get_settings.cache_clear()


def test_media_off_does_not_relax_note_rejection_or_edition_acceptance(setup, monkeypatch):
    a, _, _ = setup
    note_rejection_scenario(setup, monkeypatch, repair_passes=False)
    jobs = a.get("/api/studio/jobs?book_id=book-0").json()["items"]
    rejected = next(job for job in jobs if job["kind"] == "improve")
    assert rejected["status"] != "succeeded"
    assert a.post("/api/studio/notes/note_123456/edition", json={
        "job_id": rejected["id"], "revision": 1, "accepted": True,
    }).status_code == 404
    assert a.get("/api/studio/notes/note_123456").json()["title"] == note()["title"]


@pytest.mark.parametrize("submitted_mode,runtime_mode,expects_reference", [
    ("strict", "off", False),
    ("off", "strict", True),
])
def test_video_first_frame_uses_queued_policy_after_global_mode_changes(
    setup, monkeypatch, submitted_mode, runtime_mode, expects_reference,
):
    a, _, studio = setup
    prepare_planner(studio, monkeypatch)
    image_calls = []

    def generate_image(payload):
        image_calls.append(payload)
        return {"data": [{"b64_json": base64.b64encode(png()).decode()}]}

    monkeypatch.setattr(studio, "pucoding_image", generate_image)
    image_id = a.post("/api/studio/media", json=media()).json()["id"]
    run(studio, a, image_id)
    image = finish_if_reviewing(studio, a, image_id)
    assert image["status"] == "succeeded"
    assert image["result"]["media_review_mode"] == "off"
    assert len(image_calls) == 1

    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", submitted_mode)
    queued = a.post("/api/studio/media", json=media(
        kind="video", request_id="video_snapshot_" + submitted_mode,
    )).json()
    assert queued["result"]["media_review_mode"] == submitted_mode
    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", runtime_mode)
    video_plan = {**plan(), "supported": True, "video_suitable": True,
                  "visual_mode": "illustration", "diagram_facts": []}
    monkeypatch.setattr(studio, "llm", lambda *args: video_plan)
    reviews = []

    def approve_plan(prompt):
        reviews.append(prompt)
        return Review(passed=True, reason="offline explicit plan review")

    monkeypatch.setattr(studio, "review_plan", approve_plan)
    monkeypatch.setattr(studio, "pucoding_image", lambda *args: pytest.fail("must reuse existing image only"))
    submissions = []

    def submit_video(path, payload):
        submissions.append((path, payload))
        return {"task_id": "12345678"}

    monkeypatch.setattr(studio, "minimax", submit_video)
    run(studio, a, queued["id"])
    assert len(submissions) == 1 and submissions[0][0] == "/v2/video_generation"
    payload = submissions[0][1]
    references = [part for part in payload["content"] if part["type"] == "image_url"]
    assert bool(references) is expects_reference
    if expects_reference:
        assert len(references) == 1
        assert references[0]["role"] == "first_frame"
        assert references[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    output = a.get(f"/api/studio/jobs/{queued['id']}").json()
    assert output["status"] == "polling"
    assert output["result"]["media_review_mode"] == submitted_mode
    assert output["result"].get("reference_image_id") == (image_id if expects_reference else None)
    assert len(reviews) == (1 if submitted_mode == "strict" else 0)


def test_off_rejects_truncated_jpeg_even_when_header_verification_passes(setup, monkeypatch):
    a, _, studio = setup
    raw = io.BytesIO()
    Image.new("RGB", (64, 64), "#688aca").save(raw, "JPEG", quality=90)
    truncated = raw.getvalue()[:-8]
    # This fixture demonstrates the actual PIL boundary: verify() accepts the
    # header, but decoding the image fails before any quality-policy decision.
    with Image.open(io.BytesIO(truncated)) as image:
        image.verify()
    with pytest.raises(OSError):
        with Image.open(io.BytesIO(truncated)) as image:
            image.load()
    key = a.post("/api/studio/media", json=media()).json()["id"]
    (studio.assets / f"{key}.jpg").write_bytes(truncated)
    studio.update(key, status="reviewing", result=json.dumps({"media_review_mode": "off"}))
    monkeypatch.setattr(studio, "llm", lambda *args: pytest.fail("must decode before skipping quality review"))
    with pytest.raises(OSError):
        run(studio, a, key)
    assert a.get(f"/api/studio/jobs/{key}").json()["asset_url"] is None
    assert a.get(f"/api/studio/jobs/{key}/asset").status_code == 404
