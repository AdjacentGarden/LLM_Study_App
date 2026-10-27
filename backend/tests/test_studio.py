import base64
import io
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from studio_note_review_fixtures import note_review_fixture
from test_community import community as community

from adaptive_learning.config import get_settings
from adaptive_learning.studio import (
    ProviderRejected,
    Studio,
    StudioPlanContractError,
    get_studio,
    render_ink,
)
from adaptive_learning.studio_diagram import render_diagram
from adaptive_learning.studio_models import Recognition, TeachingPlan


@pytest.fixture
def setup(community, monkeypatch):
    a, b, repo, _ = community
    # Existing image fixtures explicitly exercise the legacy provider adapter.
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "pucoding")
    monkeypatch.setenv("PUCODING_API_KEY", "pucoding-test")
    # Existing scenarios exercise the optional V2 text-to-video adapter.
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3-Lite")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "480P")
    get_settings.cache_clear()
    studio = get_studio(repo.path.parent.parent)
    monkeypatch.setattr(
        Studio,
        "capabilities",
        lambda self: {"image": True, "video": True, "notes_ai": True, "voice_notes": True},
    )
    return a, b, studio


def note(**changes):
    return {
        "id": "note_123456",
        "book_id": "book-0",
        "revision": 0,
        "title": "我的笔记",
        "strokes": [
            {
                "points": [{"x": 10, "y": 10, "p": 0.5}, {"x": 100, "y": 50, "p": 0.5}],
                "width": 4,
                "color": "#243148",
            }
        ],
        **changes,
    }


def media(**changes):
    return {
        "book_id": "book-0",
        "excerpt": "函数调用自身，需要终止条件。",
        "kind": "image",
        "request_id": "request_12345",
        "consent": True,
        **changes,
    }


def visual_review(**changes):
    return {
        "observations": "实际可见教学对象",
        "objects_correct": True,
        "relationships_correct": True,
        "no_unwanted_text": True,
        "useful": True,
        "temporal_consistency": True,
        "reason": "已核对",
        **changes,
    }


def run(studio, client, key):
    owner, _, _ = studio.repo.visitor(client.cookies.get("zhiwo_visitor"), [])
    studio.process(studio.job(owner, key))


def test_studio_json_mode_is_explicit_in_user_input(setup, monkeypatch):
    _, _, s = setup
    calls = []

    class Client:
        def structured(self, **kwargs):
            assert "json" in kwargs["user"].lower()
            assert kwargs["user"].startswith("忠实转写笔记")
            calls.append(kwargs["images"])
            return {"transcript": "DNA", "uncertain": []}

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(s, "client", lambda vision: Client())
    ink = [("image/png", render_ink(note()["strokes"]))]
    assert s.llm("忠实转写笔记", ink)["transcript"] == "DNA"
    assert calls == [ink, "closed"]


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ReadTimeout])
def test_media_submission_uses_configured_proxy_and_never_replays_transport_error(setup, monkeypatch, failure):
    _, _, studio = setup
    monkeypatch.setenv("LLM_HTTPS_PROXY", "http://127.0.0.1:8011")
    monkeypatch.setenv("MINIMAX_API_KEY", "private-test-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    get_settings.cache_clear()
    options=[]
    requests=[]
    class Client:
        def __init__(self,**kwargs):options.append(kwargs)
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def post(self,url,**kwargs):
            requests.append(url)
            raise failure("request outcome unknown")
    monkeypatch.setattr("adaptive_learning.studio.httpx.Client", Client)
    try:
        with pytest.raises(failure):
            studio.minimax("/v1/image_generation", {"model":"image-01"})
        assert requests==["https://api.minimaxi.com/v1/image_generation"]
        assert options[0]["proxy"]=="http://127.0.0.1:8011"
        assert options[0]["trust_env"] is False
        assert options[0]["timeout"].connect==10
        assert options[0]["timeout"].read==180
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("base", ["https://api.minimaxi.com", "https://api.minimaxi.com/v1/"])
@pytest.mark.parametrize("path", ["/v1/image_generation", "/v2/video_generation"])
def test_minimax_media_version_is_selected_by_explicit_endpoint(setup, monkeypatch, base, path):
    _, _, studio = setup
    monkeypatch.setenv("MINIMAX_API_KEY", "test-media-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", base)
    calls = []
    monkeypatch.setattr(studio, "media_request", lambda *args: calls.append(args) or {})
    payload = {"model": "MiniMax-H3-Max"}
    assert studio.minimax(path, payload) == {}
    assert calls == [("https://api.minimaxi.com", path, "test-media-key", payload)]


def test_media_asset_download_uses_proxy_and_retains_private_address_guard(setup, monkeypatch):
    _, _, studio = setup
    monkeypatch.setenv("LLM_HTTPS_PROXY", "http://127.0.0.1:8011")
    get_settings.cache_clear()
    options=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def raise_for_status(self):pass
        def iter_bytes(self):yield b"raw-video"
    monkeypatch.setattr("adaptive_learning.studio.httpx.stream",lambda *args,**kwargs:options.append(kwargs) or Response())
    monkeypatch.setattr("adaptive_learning.studio.socket.getaddrinfo",lambda *args,**kwargs:[(None,None,None,None,("8.8.8.8",443))])
    try:
        assert studio.download("https://assets.example/video",20)==b"raw-video"
        assert options[0]["proxy"]=="http://127.0.0.1:8011"
        assert options[0]["trust_env"] is False
        with pytest.raises(ValueError,match="asset too large"):
            studio.download("https://assets.example/video",2)
        monkeypatch.setattr("adaptive_learning.studio.socket.getaddrinfo",lambda *args,**kwargs:[(None,None,None,None,("127.0.0.1",443))])
        with pytest.raises(ValueError,match="private asset URL"):
            studio.download("https://assets.example/video",20)
        assert len(options)==2
    finally:
        get_settings.cache_clear()


def test_media_provider_rejection_retains_only_numeric_safe_diagnostics(setup, monkeypatch):
    _, _, studio = setup
    monkeypatch.setenv("MINIMAX_API_KEY", "private-test-key")
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def post(self,url,**kwargs):
            return httpx.Response(200,json={"base_resp":{"status_code":1008,"status_msg":"untrusted private body"}},request=httpx.Request("POST",url))
    monkeypatch.setattr("adaptive_learning.studio.httpx.Client", Client)
    with pytest.raises(ProviderRejected) as caught:
        studio.minimax("/v1/image_generation",{})
    assert caught.value.http_status==200
    assert caught.value.provider_code==1008
    assert "untrusted private body" not in str(caught.value)
    assert "private-test-key" not in str(caught.value)


def test_studio_can_use_minimax_multimodal_fallback(setup, monkeypatch):
    _, _, studio = setup
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "minimax")
    monkeypatch.setenv("STUDIO_LLM_MODEL", "MiniMax-M3")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com")
    client = studio.client(vision=True)
    try:
        assert client.config.base_url == "https://api.minimaxi.com/v1"
        assert client.config.api_key == "test-key"
        assert client.config.model == "MiniMax-M3"
        assert client.config.max_retries == 1
    finally:
        client.close()


def test_studio_can_split_deepseek_planning_from_pucoding_vision(setup, monkeypatch):
    _, _, studio = setup
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")
    monkeypatch.setenv("PUCODING_API_KEY", "pucoding-test")
    get_settings.cache_clear()
    text = studio.client(vision=False)
    vision = studio.client(vision=True)
    try:
        assert text.config.base_url == get_settings().deepseek_base_url
        assert text.config.model == get_settings().deepseek_model
        assert vision.config.base_url == get_settings().pucoding_base_url
        assert vision.config.model == get_settings().pucoding_vision_model
    finally:
        text.close()
        vision.close()
        get_settings.cache_clear()


def test_studio_pucoding_route_overrides_global_deepseek_and_splits_vision(setup, monkeypatch):
    _, _, studio = setup
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "pucoding")
    monkeypatch.setenv("STUDIO_LLM_MODEL", "MiniMax-M3")
    monkeypatch.setenv("STUDIO_VISION_MODEL", "vision-specific-model")
    monkeypatch.setenv("PUCODING_API_KEY", "pucoding-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")
    get_settings.cache_clear()
    text, vision = studio.client(), studio.client(vision=True)
    try:
        settings = get_settings()
        assert text.config.base_url == settings.pucoding_base_url
        assert text.config.api_key == "pucoding-test"
        assert text.config.model == "MiniMax-M3"
        assert vision.config.base_url == settings.pucoding_base_url
        assert vision.config.api_key == "pucoding-test"
        assert vision.config.model == "vision-specific-model"
        assert settings.text_model == settings.deepseek_model
    finally:
        text.close()
        vision.close()
        get_settings.cache_clear()


def test_studio_explicit_deepseek_keeps_configured_thinking_policy(setup, monkeypatch):
    _, _, studio = setup
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("TEXT_LLM_THINKING_ENABLED", "false")
    monkeypatch.setenv("TEXT_LLM_REASONING_EFFORT", "low")
    get_settings.cache_clear()
    client = studio.client()
    try:
        assert client.config.base_url == get_settings().deepseek_base_url
        assert client.config.thinking_enabled is False
        assert client.config.reasoning_effort == "low"
    finally:
        client.close()
        get_settings.cache_clear()


def test_studio_pucoding_capabilities_follow_studio_credentials_not_global_provider(community, monkeypatch):
    _, _, repo, _ = community
    studio = get_studio(repo.path.parent.parent)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "pucoding")
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")
    monkeypatch.setenv("STUDIO_FFMPEG", "/test/ffmpeg")
    monkeypatch.delenv("STUDIO_MEDIA_PAUSED_REASON", raising=False)
    monkeypatch.setenv("PUCODING_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert studio.capabilities()["image"] is False
        assert studio.capabilities()["video"] is False
        assert studio.capabilities()["notes_ai"] is False
        monkeypatch.setenv("PUCODING_API_KEY", "pucoding-test")
        get_settings.cache_clear()
        assert studio.capabilities()["image"] is True
        assert studio.capabilities()["video"] is True
    finally:
        get_settings.cache_clear()


def test_studio_official_minimax_text_can_keep_pucoding_vision_and_deepseek_notes(setup, monkeypatch):
    _, _, studio = setup
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "minimax")
    monkeypatch.setenv("STUDIO_LLM_MODEL", "MiniMax-M3")
    monkeypatch.setenv("STUDIO_LLM_THINKING_ENABLED", "false")
    monkeypatch.setenv("STUDIO_VISION_PROVIDER", "pucoding")
    monkeypatch.setenv("STUDIO_VISION_MODEL", "vision-model")
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    monkeypatch.setenv("PUCODING_API_KEY", "pucoding-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")
    monkeypatch.setenv("TEXT_LLM_THINKING_ENABLED", "false")
    get_settings.cache_clear()
    text, vision, note = studio.client(), studio.client(vision=True), studio.client(provider_override="deepseek")
    try:
        assert text.config.base_url == "https://api.minimaxi.com/v1"
        assert text.config.api_key == "minimax-test"
        assert text.config.model == "MiniMax-M3"
        assert text.config.thinking_enabled is False
        assert vision.config.api_key == "pucoding-test"
        assert vision.config.model == "vision-model"
        assert note.config.api_key == "deepseek-test"
        assert note.config.model == get_settings().deepseek_model
        assert note.config.thinking_enabled is False
    finally:
        text.close()
        vision.close()
        note.close()
        get_settings.cache_clear()


def test_studio_does_not_infer_minimax_thinking_policy_from_model_name(setup, monkeypatch):
    _, _, studio = setup
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "minimax")
    monkeypatch.setenv("STUDIO_LLM_MODEL", "MiniMax-M3")
    monkeypatch.delenv("STUDIO_LLM_THINKING_ENABLED", raising=False)
    client = studio.client()
    try:
        assert client.config.thinking_enabled is None
    finally:
        client.close()
    monkeypatch.setenv("STUDIO_LLM_THINKING_ENABLED", "invalid")
    with pytest.raises(ValueError, match="STUDIO_LLM_THINKING_ENABLED"):
        studio.client()


def test_note_pipeline_can_use_a_faster_provider_without_changing_media_planning(
    setup, monkeypatch
):
    _, _, studio = setup
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "pucoding")
    monkeypatch.setenv("STUDIO_NOTE_LLM_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_PROVIDER", "pucoding")
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    monkeypatch.setenv("PUCODING_API_KEY", "pucoding-test")
    get_settings.cache_clear()
    note = studio.client(vision=True, provider_override="minimax")
    media = studio.client(vision=False)
    try:
        assert note.config.model == "MiniMax-M3"
        assert note.config.api_key == "minimax-test"
        assert media.config.model == get_settings().pucoding_text_model
    finally:
        note.close()
        media.close()
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("审核笔记整理是否忠实", 1000),
        ("检查并局部补全笔记", 2000),
        ("下面的整理草稿未通过验证", 2000),
        ("根据审核意见修订整理版", 2000),
        ("普通结构化任务", 6000),
    ],
)
def test_handwriting_llm_uses_bounded_output_budgets(setup, monkeypatch, prompt, expected):
    _, _, s = setup
    seen = []

    class Client:
        def structured(self, **kwargs):
            seen.append(kwargs["max_tokens"])
            return {"ok": True}

        def close(self):
            pass

    monkeypatch.setattr(s, "client", lambda vision: Client())
    assert s.llm(prompt) == {"ok": True}
    assert seen == [expected]


def test_studio_worker_survives_transient_database_failure(tmp_path, monkeypatch):
    studio = Studio(tmp_path)
    original_connect = studio.repo.connect
    attempts = 0

    def flaky_connect():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("temporary data disk interruption")
        studio.stop_event.set()
        return original_connect()

    monkeypatch.setattr(studio.repo, "connect", flaky_connect)
    monkeypatch.setattr(studio.stop_event, "wait", lambda _: False)
    studio.loop()
    assert attempts == 2


def test_note_private_revision_retry_and_recovery(setup):
    a, b, s = setup
    first = a.post("/api/studio/notes", json=note())
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1
    assert a.post("/api/studio/notes", json=note()).json()["revision"] == 1
    assert a.post("/api/studio/notes", json=note(title="不同内容")).status_code == 409
    assert b.get("/api/studio/notes/note_123456").status_code == 404
    assert b.post("/api/studio/notes", json=note()).status_code == 404
    assert (
        a.post("/api/studio/notes", json=note(revision=1, title="第二版")).json()["revision"] == 2
    )
    assert a.get("/api/studio/notes?book_id=book-0").json()["items"][0]["title"] == "第二版"
    assert "strokes" not in a.get("/api/studio/notes?book_id=book-0").json()["items"][0]
    a.post("/api/library/books/book-0/remove")
    assert a.get("/api/studio/notes/note_123456").status_code == 403
    a.post("/api/library/books/book-0/restore")
    assert a.get("/api/studio/notes/note_123456").json()["revision"] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"title": ""},
        {"revision": -1},
        {"pages": [0]},
        {"strokes": [{"points": [{"x": 1001, "y": 0}]}]},
        {"strokes": [{"points": [{"x": 0, "y": 0}], "color": "red"}]},
        {"owner": "other"},
    ],
)
def test_note_bounds(setup, change):
    a, _, _ = setup
    assert a.post("/api/studio/notes", json=note(**change)).status_code == 422


def test_voice_note_audio_is_private_versioned_and_processed(setup, monkeypatch):
    a, b, studio = setup
    voice = note(input_mode="voice", strokes=[], title="遗传学语音笔记")
    saved = a.post("/api/studio/notes", json=voice)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    payload = {
        "action": "complete",
        "request_id": "voice_before_audio",
        "revision": 1,
        "consent": True,
    }
    assert a.post("/api/studio/notes/note_123456/analyze", json=payload).status_code == 422

    audio = b"FORM" + (80).to_bytes(4, "big") + b"AIFF" + b"\0" * 80
    headers = {
        "content-type": "audio/aiff",
        "x-note-revision": "1",
        "x-audio-duration": "4.2",
    }
    uploaded = a.put("/api/studio/notes/note_123456/audio", content=audio, headers=headers)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["revision"] == 2
    assert uploaded.json()["audio_ready"] is True
    assert (
        a.put("/api/studio/notes/note_123456/audio", content=audio, headers=headers).json()[
            "revision"
        ]
        == 2
    )
    own = a.get("/api/studio/notes/note_123456/audio")
    assert own.content == audio and own.headers["cache-control"] == "private, no-store"
    assert b.get("/api/studio/notes/note_123456/audio").status_code == 404

    # Client attempts cannot forge or erase server-owned recording metadata.
    updated = a.post(
        "/api/studio/notes",
        json={
            **voice,
            "revision": 2,
            "title": "更新标题",
            "audio_ready": False,
            "audio_sha256": "fake",
        },
    ).json()
    assert updated["revision"] == 3 and updated["audio_ready"] is True

    monkeypatch.setattr(
        studio,
        "transcribe_voice",
        lambda owner, value: Recognition(
            transcript="DNA分子边解旋边复制，需要模板、原料、能量和酶。", uncertain=[]
        ),
    )
    result = {
        "summary": "语音要点已按教材核对。",
        "suggestions": [],
        "polished": "DNA 分子边解旋边复制，需要模板、原料、能量和酶。",
    }
    replies = iter([result, {"passed": True, "reason": "offline explicit approval fixture; not measured model quality"}])
    monkeypatch.setattr(studio, "llm", lambda *args: note_review_fixture(args[0], next(replies), global_failure="polished"))
    monkeypatch.setattr(
        studio,
        "evidence",
        lambda *args: [{"page": 1, "text": "DNA分子复制需要模板、原料、能量和酶。"}],
    )
    payload.update(request_id="voice_complete_1", revision=3)
    key = a.post("/api/studio/notes/note_123456/analyze", json=payload).json()["id"]
    run(studio, a, key)
    job = a.get("/api/studio/jobs/" + key).json()
    assert job["status"] == "succeeded"
    assert job["result"]["transcript"].startswith("DNA分子")
    assert job["result"]["polished"].endswith("酶。")


def test_voice_note_rejects_disguised_or_oversized_audio(setup):
    a, _, _ = setup
    a.post("/api/studio/notes", json=note(input_mode="voice", strokes=[]))
    headers = {
        "content-type": "audio/webm",
        "x-note-revision": "1",
        "x-audio-duration": "3",
    }
    assert (
        a.put(
            "/api/studio/notes/note_123456/audio", content=b"not audio" * 10, headers=headers
        ).status_code
        == 422
    )


def test_media_dedupe_permissions_budget_and_request_conflict(setup, monkeypatch):
    a, b, s = setup
    a.get("/api/library")
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(
            pool.map(lambda _: a.post("/api/studio/media", json=media()).json(), range(8))
        )
    key = result[0]["id"]
    assert all(x["id"] == key for x in result)
    assert a.post("/api/studio/media", json=media(request_id="other_request")).json()["id"] == key
    assert a.post("/api/studio/media", json=media(kind="video")).status_code == 409
    assert b.get("/api/studio/jobs/" + key).status_code == 404
    assert b.get("/api/studio/jobs/" + key + "/asset").status_code == 404
    assert a.get("/api/studio/jobs/" + key + "/asset").status_code == 404
    assert (
        a.post(
            "/api/studio/media", json=media(), headers={"origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert a.post("/api/studio/media", json=media(book_id="book-9")).status_code == 403
    monkeypatch.setenv("STUDIO_MEDIA_DAILY_CNY", "0.026")
    assert (
        a.post(
            "/api/studio/media", json=media(request_id="different_id", excerpt="另外一段教材选文")
        ).status_code
        == 429
    )


@pytest.mark.parametrize(
    "change",
    [
        {"consent": False},
        {"excerpt": "字"},
        {"kind": "audio"},
        {"request_id": "../unsafe"},
        {"pages": [-1]},
    ],
)
def test_media_bounds(setup, change):
    a, _, _ = setup
    assert a.post("/api/studio/media", json=media(**change)).status_code == 422


def test_media_plan_generation_review_private_asset(setup, monkeypatch):
    a, b, s = setup
    evidence = [{"page": 1, "text": "函数调用自身，需要终止条件。"}]
    plan = {
        "title": "理解递归",
        "visual_scope": "展示函数的自身调用与终止条件",
        "visual_checks": ["画面明确表达有限的自身嵌套"],
        "explanation": "函数调用自身，需要终止条件。",
        "points": ["需要终止条件"],
        "visual_prompt": "An illustration with nested boxes, no text.",
        "caution": "辅助示意",
        "supported": True,
        "video_suitable": False,
    }
    responses = iter([plan, {"passed": True, "reason": "offline explicit approval fixture; not measured model quality"}, visual_review()])
    monkeypatch.setattr(s, "llm", lambda *args: next(responses))
    monkeypatch.setattr(s, "evidence", lambda *args: evidence)
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, "PNG")
    called = []

    def generate(payload):
        called.append(payload)
        return {"data": [{"b64_json": base64.b64encode(image.getvalue()).decode()}]}

    monkeypatch.setattr(s, "pucoding_image", generate)
    key = a.post("/api/studio/media", json=media()).json()["id"]
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "reviewing"
    assert a.get("/api/studio/jobs/" + key + "/asset").status_code == 404
    run(s, a, key)
    value = a.get("/api/studio/jobs/" + key).json()
    assert value["status"] == "succeeded"
    assert "visual_prompt" not in value["result"]
    assert len(called) == 1 and called[0]["n"] == 1
    assert called[0]["model"] == "gpt-image-2"
    assert called[0]["response_format"] == "b64_json"
    assert called[0]["size"] == "1024x768"
    assert value["result"]["provider"] == "PUCODING"
    assert "Colorful modern educational illustration" in called[0]["prompt"]
    assert "no wireframe-only look" in called[0]["prompt"]
    asset = a.get(value["asset_url"])
    assert asset.status_code == 200 and asset.headers["cache-control"] == "private, no-store"
    assert b.get(value["asset_url"]).status_code == 404


def test_malformed_media_plan_is_repaired_once_before_factual_review(setup, monkeypatch):
    a, _, studio = setup
    evidence = [{"page": 1, "text": "函数调用自身，需要终止条件。"}]
    draft = {
        "title": "理解递归",
        "visual_scope": "展示函数的自身调用与终止条件",
        "visual_checks": ["画面明确表达有限的自身嵌套"],
        "explanation": "函数调用自身，需要终止条件。",
        "points": ["需要终止条件"],
        "visual_prompt": "A clear colorful educational illustration of nested boxes.",
        "caution": "辅助示意",
        "supported": True,
        # A provider omitted this required field; no paid request may start yet.
    }
    repaired = {**draft, "video_suitable": False}
    responses = iter([draft, repaired, {"passed": True, "reason": "教材事实正确"}])
    prompts = []
    calls = []

    def llm(prompt, images=None):
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr(studio, "llm", llm)
    monkeypatch.setattr(studio, "evidence", lambda *args: evidence)
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "#8bcab7").save(image, "PNG")

    def generate(payload):
        calls.append(payload)
        return {"data": [{"b64_json": base64.b64encode(image.getvalue()).decode()}]}

    monkeypatch.setattr(studio, "pucoding_image", generate)
    key = a.post("/api/studio/media", json=media()).json()["id"]
    run(studio, a, key)
    assert "video_suitable" in prompts[1]
    assert prompts[2].startswith("审核以下教学方案")
    assert len(calls) == 1
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "reviewing"


def test_malformed_media_plan_never_skips_factual_review(setup, monkeypatch):
    a, _, studio = setup
    draft = {
        "title": "理解递归",
        "visual_scope": "展示函数的自身调用与终止条件",
        "visual_checks": ["画面明确表达有限的自身嵌套"],
        "explanation": "函数调用自身，需要终止条件。",
        "points": ["需要终止条件"],
        "visual_prompt": "A clear colorful educational illustration of nested boxes.",
        "caution": "辅助示意",
        "supported": True,
    }
    repaired = {**draft, "video_suitable": False}
    responses = iter(
        [
            draft,
            repaired,
            {"passed": False, "reason": "教材事实不符"},
            {**repaired, "supported": False},
        ]
    )
    monkeypatch.setattr(studio, "llm", lambda *args: next(responses))
    monkeypatch.setattr(
        studio, "evidence", lambda *args: [{"page": 1, "text": "函数调用自身，需要终止条件。"}]
    )
    calls = []
    monkeypatch.setattr(studio, "minimax", lambda *args: calls.append(args))
    monkeypatch.setattr(studio, "pucoding_image", lambda *args: calls.append(args))
    key = a.post("/api/studio/media", json=media()).json()["id"]
    run(studio, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "failed"
    assert calls == []


def test_plan_root_constraint_repair_receives_actual_error_and_schema(setup, monkeypatch):
    _, _, studio = setup
    draft = {"title":"外观对比", "explanation":"只对比可见外观。", "points":["长"*121],
             "visual_prompt":"Two colorful flowers with distinct visible shapes.", "caution":"仅作外观示意。",
             "supported":True, "video_suitable":True, "visual_scope":"只比较花的可见外观。", "visual_checks":["花形不同"]}
    prompts=[]
    def llm(prompt):
        prompts.append(prompt)
        return draft if len(prompts)==1 else {**draft,"points":["两种花的可见形态不同"]}
    monkeypatch.setattr(studio,"llm",llm)
    assert studio.teaching_plan("基于教材证据为选中段落规划", "原文").points == ["两种花的可见形态不同"]
    assert '"maxLength":1200' in prompts[0]
    assert '"loc": []' in prompts[1]
    assert "teaching points and visual checks must stay concise" in prompts[1]
    assert '"input"' not in prompts[1]


def test_factual_plan_revision_uses_schema_repair_and_is_independently_reviewed(setup, monkeypatch):
    a, _, studio = setup
    plan={"title":"花形对比","explanation":"只对比外观，不推断遗传或物种。","points":["只比较可见的代表性外观"],
        "visual_prompt":"Two groups of consistent representative flowers, with different forms and colors.",
        "caution":"示意分组不表示自然种群完全没有变异。","supported":True,"video_suitable":True,
        "visual_scope":"仅比较两组花的可见外观。","visual_checks":["两组外观不同"]}
    replies=iter([plan,{"passed":False,"reason":"需更清晰地区分两组"},{**plan,"points":["长"*121]},
                  plan,{"passed":True,"reason":"已核对"}])
    prompts=[]
    def llm(prompt,*args):
        prompts.append(prompt)
        return next(replies)
    monkeypatch.setattr(studio,"llm",llm)
    monkeypatch.setattr(studio,"evidence",lambda *args:[{"page":1,"text":"两组植株的形态和花色不同。"}])
    calls=[]
    monkeypatch.setattr(studio,"minimax",lambda *args:calls.append(args) or {"task_id":"123"})
    key=a.post("/api/studio/media",json=media(kind="video")).json()["id"]
    run(studio,a,key)
    assert prompts[2].startswith("根据审核意见最小修改")
    assert "JSON schema" in prompts[2]
    assert prompts[3].startswith("修复上一份教学画面方案")
    assert prompts[4].startswith("审核以下教学方案")
    assert len(calls)==1
    assert a.get("/api/studio/jobs/"+key).json()["status"]=="polling"


@pytest.mark.parametrize("changed",[False,True])
def test_plan_review_format_repair_cannot_turn_rejection_into_approval(setup,monkeypatch,changed):
    _, _, studio=setup
    replies=iter([{"passed":False,"reason":"拒绝原因"*300},{"passed":changed,"reason":"压缩后的拒绝依据"}])
    monkeypatch.setattr(studio,"llm",lambda *args:next(replies))
    if changed:
        with pytest.raises(StudioPlanContractError,match="contract"):
            studio.review_plan("审核以下教学方案")
    else:
        assert studio.review_plan("审核以下教学方案").passed is False


def test_media_raw_output_trace_precedes_validation_and_excludes_credentials(setup,monkeypatch):
    from pydantic import ValidationError

    from adaptive_learning.studio_models import Review
    a, _, studio=setup
    key=a.post("/api/studio/media",json=media()).json()["id"]
    studio._media_trace.job_id=key
    raw={"passed":False,"reason":"拒绝原因"*300}
    class Client:
        config=SimpleNamespace(model="MiniMax-M3",base_url="https://api.minimaxi.com/v1",api_key="private-never-store")
        def structured(self,**kwargs):return raw
        def close(self):pass
    monkeypatch.setattr(studio,"client",lambda *args:Client())
    result=studio.llm("审核以下教学方案")
    with pytest.raises(ValidationError):
        Review.model_validate(result)
    directory=studio.assets/"private-media-traces"
    files=list(directory.glob(key+"-*.json"))
    assert len(files)==1
    saved=files[0].read_text()
    assert json.loads(saved)["response"]==raw
    assert "private-never-store" not in saved
    assert files[0].stat().st_mode & 0o777==0o600
    assert directory.stat().st_mode & 0o777==0o700
    assert "private-media-traces" not in json.dumps(a.get("/api/studio/jobs/"+key).json())


def test_invalid_repair_keeps_private_feedback_and_never_submits_media(setup, monkeypatch):
    a, _, studio = setup
    draft = {"title":"外观对比", "explanation":"只对比可见外观。", "points":["长"*121],
             "visual_prompt":"Two colorful flowers with distinct visible shapes.", "caution":"仅作外观示意。",
             "supported":True, "video_suitable":True, "visual_scope":"只比较花的可见外观。", "visual_checks":["花形不同"]}
    calls=[]
    monkeypatch.setattr(studio,"llm",lambda *args:draft)
    monkeypatch.setattr(studio,"evidence",lambda *args:[{"page":1,"text":"两种花的形态不同。"}])
    monkeypatch.setattr(studio,"minimax",lambda *args:calls.append(args))
    key=a.post("/api/studio/media",json=media()).json()["id"]
    update=studio.update
    def update_then_stop(job_id,**fields):
        update(job_id,**fields)
        if fields.get("status")=="failed":
            studio.stop_event.set()
    monkeypatch.setattr(studio,"update",update_then_stop)
    studio.loop("media")
    public=a.get("/api/studio/jobs/"+key).json()
    assert public["status"]=="failed"
    assert "_diagnostic" not in public["result"]
    assert "candidate" not in json.dumps(public)
    with studio.repo.connect() as db:
        stored=json.loads(db.execute("SELECT result FROM studio_jobs WHERE id=?",(key,)).fetchone()[0])
    private=stored["_diagnostic"]["plan_contract"]
    assert private["repaired_candidate"]["points"]==draft["points"]
    assert private["repair_errors"][0]["loc"]==[]
    assert "input" not in private["repair_errors"][0]
    assert calls==[]


def test_ink_render_and_note_analysis_confirmation_accept_undo(setup, monkeypatch):
    a, b, s = setup
    a.post("/api/studio/notes", json=note())
    assert Image.open(io.BytesIO(render_ink(note()["strokes"]))).size == (1000, 1400)
    evidence = [{"page": 1, "text": "函数调用自身，需要终止条件。"}]
    output = {
        "summary": "补充终止条件",
        "suggestions": [
            {
                "original": "函数调用自己",
                "kind": "缺少条件",
                "suggestion": "需要终止条件。",
                "evidence": "需要终止条件",
                "page": 1,
            }
        ],
        "polished": "函数调用自身，需要终止条件。",
    }
    responses = iter(
        [{"transcript": "函数调用自己", "uncertain": []}, output, {"passed": True, "reason": "offline explicit approval fixture; not measured model quality"}]
    )
    monkeypatch.setattr(s, "llm", lambda *args: note_review_fixture(args[0], next(responses), global_failure="polished"))
    monkeypatch.setattr(s, "evidence", lambda *args: evidence)
    action = {"action": "recognize", "request_id": "analysis_111", "revision": 1, "consent": True}
    key = a.post("/api/studio/notes/note_123456/analyze", json=action).json()["id"]
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["result"]["transcript"] == "函数调用自己"
    assert (
        a.post(
            "/api/studio/notes/note_123456/analyze",
            json={**action, "action": "improve", "transcript": "[待确认]"},
        ).status_code
        == 422
    )
    key2 = a.post(
        "/api/studio/notes/note_123456/analyze",
        json={
            **action,
            "request_id": "analysis_222",
            "action": "improve",
            "transcript": "函数调用自己",
        },
    ).json()["id"]
    run(s, a, key2)
    accept = {"job_id": key2, "revision": 1, "accepted": True}
    assert b.post("/api/studio/notes/note_123456/edition", json=accept).status_code == 404
    assert a.post("/api/studio/notes/note_123456/edition", json=accept).status_code == 200
    saved = a.get("/api/studio/notes/note_123456").json()
    assert (
        saved["strokes"] == note()["strokes"] and saved["edition"]["polished"] == output["polished"]
    )
    assert (
        a.post(
            "/api/studio/notes/note_123456/edition", json={**accept, "accepted": False}
        ).status_code
        == 200
    )
    assert a.get("/api/studio/notes/note_123456").json()["edition"] is None
    a.post("/api/studio/notes", json=note(revision=1, title="更新"))
    assert a.post("/api/studio/notes/note_123456/edition", json=accept).status_code == 409


@pytest.mark.parametrize("unclear", [False, True])
def test_complete_one_request_and_uncertainty_gate(setup, monkeypatch, unclear):
    a, b, s = setup
    a.post("/api/studio/notes", json=note())
    output = {
        "summary": "仅是提纲，不推断掌握程度。",
        "suggestions": [],
        "polished": "函数需要终止条件。",
    }
    replies = iter(
        [
            {"transcript": "函数", "uncertain": []},
            {
                "transcript": "[待确认]" if unclear else "函数",
                "uncertain": ["第二字"] if unclear else [],
            },
            output,
            {"passed": True, "reason": "offline explicit approval fixture; not measured model quality"},
        ]
    )
    calls = []

    def llm(prompt, images=None):
        calls.append(bool(images))
        return note_review_fixture(prompt, next(replies), global_failure="polished")

    monkeypatch.setattr(s, "llm", llm)
    monkeypatch.setattr(s, "evidence", lambda *args: [{"page": 1, "text": "函数需要终止条件。"}])
    payload = {"action": "complete", "request_id": "complete_111", "revision": 1, "consent": True}
    url = "/api/studio/notes/note_123456/analyze"
    key = a.post(url, json=payload).json()["id"]
    assert a.post(url, json=payload).json()["id"] == key
    assert b.get("/api/studio/jobs/" + key).status_code == 404
    run(s, a, key)
    job = a.get("/api/studio/jobs/" + key).json()
    assert calls == ([True, True] if unclear else [True, True, False, False])
    assert job["status"] == ("needs_confirmation" if unclear else "succeeded")
    if unclear:
        assert "polished" not in job["result"]
    else:
        accept = {"job_id": key, "revision": 1, "accepted": True}
        assert a.post("/api/studio/notes/note_123456/edition", json=accept).status_code == 200
        assert (
            a.get("/api/studio/notes/note_123456").json()["edition"]["polished"]
            == output["polished"]
        )
    assert a.get("/api/studio/notes/note_123456").json()["strokes"] == note()["strokes"]


def test_complete_runs_independent_vision_reads_concurrently(setup, monkeypatch):
    a, _, s = setup
    a.post("/api/studio/notes", json=note())
    barrier = threading.Barrier(2)
    text_replies = iter(
        [
            {"summary": "提纲", "suggestions": [], "polished": "函数"},
            {"passed": True, "reason": "offline explicit approval fixture; not measured model quality"},
        ]
    )

    def llm(prompt, images=None):
        if images:
            barrier.wait(timeout=1)
            return {"transcript": "函数", "uncertain": []}
        return note_review_fixture(prompt, next(text_replies))

    monkeypatch.setattr(s, "llm", llm)
    monkeypatch.setattr(s, "evidence", lambda *args: [{"page": 1, "text": "函数"}])
    updates = []
    original_update = s.update

    def update(key, **changes):
        if "result" in changes:
            updates.append(json.loads(changes["result"]))
        return original_update(key, **changes)

    monkeypatch.setattr(s, "update", update)
    key = a.post(
        "/api/studio/notes/note_123456/analyze",
        json={
            "action": "complete",
            "request_id": "parallel_111",
            "revision": 1,
            "consent": True,
        },
    ).json()["id"]
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "succeeded"
    preview = next(item for item in updates if item.get("phase") == "checking")
    assert preview["provisional"] is True
    assert preview["polished"] == "函数"


def test_recognition_normalizes_harmless_gateway_shape_drift(setup):
    _, _, studio = setup
    value = studio.recognition(
        {
            "output": {
                "recognized_text": "DNA",
                "uncertain": " 末尾字母较浅 ",
                "confidence": 0.91,
            },
            "usage": {"tokens": 12},
        }
    )
    assert value.transcript == "DNA"
    assert value.uncertain == ["末尾字母较浅"]
    assert studio.recognition({"transcript": "DNA", "uncertain": None}).uncertain == []


def test_complete_keeps_valid_read_when_independent_shape_is_invalid(setup, monkeypatch):
    a, _, studio = setup
    a.post("/api/studio/notes", json=note())

    def llm(prompt, images=None):
        if images and prompt.startswith("独立逐笔转写"):
            return {"message": "gateway returned an incomplete object"}
        if images:
            return {"transcript": "函数", "uncertain": [], "confidence": 0.99}
        raise AssertionError("uncertain recognition must stop before textbook improvement")

    monkeypatch.setattr(studio, "llm", llm)
    key = a.post(
        "/api/studio/notes/note_123456/analyze",
        json={
            "action": "complete",
            "request_id": "shape_fallback_111",
            "revision": 1,
            "consent": True,
        },
    ).json()["id"]
    run(studio, a, key)
    job = a.get("/api/studio/jobs/" + key).json()
    assert job["status"] == "needs_confirmation"
    assert job["result"]["transcript"] == "函数"
    assert job["result"]["uncertain"] == ["独立复核结果无法核验，请确认转写文字"]


def test_complete_repairs_malformed_improvement_without_repeating_vision(setup, monkeypatch):
    a, _, s = setup
    a.post("/api/studio/notes", json=note())
    text_replies = iter(
        [
            {"summary": "字段不完整"},
            {"summary": "提纲", "suggestions": [], "polished": "函数"},
            {"passed": True, "reason": "offline explicit approval fixture; not measured model quality"},
        ]
    )
    calls = {"vision": 0, "text": 0}

    def llm(prompt, images=None):
        if images:
            calls["vision"] += 1
            return {"transcript": "函数", "uncertain": []}
        calls["text"] += 1
        return note_review_fixture(prompt, next(text_replies))

    monkeypatch.setattr(s, "llm", llm)
    monkeypatch.setattr(s, "evidence", lambda *args: [{"page": 1, "text": "函数"}])
    key = a.post(
        "/api/studio/notes/note_123456/analyze",
        json={
            "action": "complete",
            "request_id": "repair_shape_111",
            "revision": 1,
            "consent": True,
        },
    ).json()["id"]
    run(s, a, key)
    job = a.get("/api/studio/jobs/" + key).json()
    assert job["status"] == "succeeded"
    assert job["result"]["polished"] == "函数"
    assert calls == {"vision": 2, "text": 3}


@pytest.mark.parametrize("repair_passes", [False, True])
def test_note_review_repairs_once_and_rechecks(setup, monkeypatch, repair_passes):
    a, _, s = setup
    a.post("/api/studio/notes", json=note())
    original = {"summary": "补充", "suggestions": [], "polished": "先执行 A，再执行 B。"}
    repaired = {**original, "polished": "A 与 B 同时进行。"}
    replies = iter(
        [
            original,
            {"passed": False, "reason": "教材为同时，不能改成先后"},
            repaired,
            {"passed": repair_passes, "reason": "复核"},
        ]
    )
    monkeypatch.setattr(s, "llm", lambda *args: note_review_fixture(args[0], next(replies), global_failure="polished"))
    monkeypatch.setattr(s, "evidence", lambda *args: [{"page": 1, "text": "A 与 B 同时进行。"}])
    key = a.post(
        "/api/studio/notes/note_123456/analyze",
        json={
            "action": "improve",
            "transcript": "A 和 B",
            "request_id": "repair_111",
            "revision": 1,
            "consent": True,
        },
    ).json()["id"]
    if repair_passes:
        run(s, a, key)
        assert a.get("/api/studio/jobs/" + key).json()["result"]["polished"] == repaired["polished"]
    else:
        with pytest.raises(ValueError, match="note review failed"):
            run(s, a, key)
        assert a.get("/api/studio/jobs/" + key).json()["status"] != "succeeded"


def test_complete_never_publishes_result_after_ink_changes(setup, monkeypatch):
    a, _, s = setup
    a.post("/api/studio/notes", json=note())
    responses = iter(
        [{"transcript": "函数", "uncertain": []}] * 2
        + [
            {"summary": "提纲", "suggestions": [], "polished": "函数"},
            {"passed": True, "reason": "offline explicit approval fixture; not measured model quality"},
        ]
    )

    def llm(*args):
        result = next(responses)
        if "passed" in result:
            a.post("/api/studio/notes", json=note(revision=1, title="新的笔记"))
        return note_review_fixture(args[0], result)

    monkeypatch.setattr(s, "llm", llm)
    monkeypatch.setattr(s, "evidence", lambda *args: [{"page": 1, "text": "函数"}])
    key = a.post(
        "/api/studio/notes/note_123456/analyze",
        json={"action": "complete", "request_id": "stale_111", "revision": 1, "consent": True},
    ).json()["id"]
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "failed"


def test_restart_never_resubmits_paid_request(setup, monkeypatch):
    a, _, s = setup
    key = a.post("/api/studio/media", json=media()).json()["id"]
    s.update(key, status="submitting")
    monkeypatch.setattr(s, "loop", lambda lane='all': None)
    s.start()
    s.stop()
    value = a.get("/api/studio/jobs/" + key).json()
    assert value["status"] == "uncertain"
    assert a.post("/api/studio/media", json=media(request_id="after_restart")).json()["id"] == key


def test_note_and_media_lanes_claim_disjoint_leased_jobs(setup):
    a, _, studio = setup
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    image = studio.submit(owner, media(request_id="leased-media-lane"), "image")
    note_job = studio.submit(owner, {
        "book_id": "book-0", "request_id": "leased-note-lane",
        "note_id": "note_123456", "revision": 0,
    }, "improve")
    image_claim = studio.claim_next(lane="media")
    note_claim = studio.claim_next(lane="notes")
    assert image_claim["id"] == image["id"]
    assert note_claim["id"] == note_job["id"]
    assert image_claim["lease_owner"] != note_claim["lease_owner"]
    assert studio.claim_next(lane="media") is None
    assert studio.claim_next(lane="notes") is None
    studio.release_lease(image_claim["id"], image_claim["lease_owner"])
    assert studio.claim_next(lane="notes") is None
    assert studio.claim_next(lane="media")["id"] == image["id"]
    with pytest.raises(ValueError, match="lane"):
        studio.claim_next(lane="unrecognized")


def test_two_lane_start_preserves_other_workers_valid_planning_and_paid_leases(setup, monkeypatch):
    a, _, studio = setup
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    studio.submit(owner, media(request_id="active-paid-worker"), "image")
    studio.submit(owner, {
        "book_id": "book-0", "request_id": "active-note-worker",
        "note_id": "note_123456", "revision": 0,
    }, "improve")
    image = studio.claim_next(lane="media")
    note_job = studio.claim_next(lane="notes")
    for claim, status in ((image, "submitting"), (note_job, "planning")):
        studio._lease.token = claim["lease_owner"]
        studio.update(claim["id"], status=status)
    studio._lease.token = None
    monkeypatch.setattr(studio, "loop", lambda lane="all": None)
    studio.start()
    studio.stop()
    for claim, status in ((image, "submitting"), (note_job, "planning")):
        current = studio.job(owner, claim["id"])
        assert current["status"] == status
        assert current["lease_owner"] == claim["lease_owner"]
        assert current["attempts"] == 0


def test_stale_media_worker_cannot_refund_or_fail_reclaimed_planning_job(setup):
    import time

    from adaptive_learning.studio import StudioLeaseLost

    a, _, studio = setup
    key = a.post("/api/studio/media", json=media()).json()["id"]
    old = studio.claim_next(lane="media")
    studio._lease.token = old["lease_owner"]
    studio.update(key, status="planning")
    current = studio.claim_next(lane="media", now=time.time() + 100)
    assert current["id"] == key
    assert current["lease_owner"] != old["lease_owner"]
    with pytest.raises(StudioLeaseLost):
        studio.update(key, status="failed", error="stale failure before submission")
    current = studio.job(current["owner"], key)
    assert current["status"] == "queued"
    assert current["reserved"] == 0.025
    studio._lease.token = None


def test_planning_failure_releases_reserve_but_unknown_submission_retains_it(setup):
    a, _, studio = setup
    first=a.post("/api/studio/media",json=media()).json()["id"]
    studio.update(first,status="planning")
    studio.update(first,status="failed",error="方案不适合")
    second=a.post("/api/studio/media",json=media(request_id="other_plan",excerpt="另一个有依据的选段")).json()["id"]
    studio.update(second,status="submitting")
    studio.update(second,status="uncertain",error="回复丢失")
    third=a.post("/api/studio/media",json=media(request_id="asset_already_exists",excerpt="已经有资产的原文")).json()["id"]
    (studio.assets/(third+".jpg")).write_bytes(b"an existing asset")
    studio.update(third,status="planning")
    studio.update(third,status="failed",error="保守保留已有资产的占额")
    with studio.repo.connect() as db:
        rows={row["id"]:row["reserved"] for row in db.execute("SELECT id,reserved FROM studio_jobs")}
    assert rows[first]==0
    assert rows[second]==0.025
    assert rows[third]==0.025


@pytest.mark.parametrize("status,stage,provider,provider_id,asset,refunded",[
    ("failed","planning",None,"",False,True),
    ("failed","plan_review",None,"",False,True),
    ("failed","submitting","MiniMax","",False,False),
    ("failed","reviewing","MiniMax","",False,False),
    ("uncertain","planning",None,"",False,False),
    ("failed","planning",None,"123",False,False),
    ("failed","planning",None,"",True,False),
    ("failed","planning","MiniMax","",False,False),
])
def test_restart_reconciles_only_proven_unsubmitted_reserves(setup,monkeypatch,status,stage,provider,provider_id,asset,refunded):
    a, _, studio=setup
    key=a.post("/api/studio/media",json=media()).json()["id"]
    result={"_diagnostic":{"stage":stage}}
    if provider:
        result["provider"]=provider
    with studio.repo.connect() as db:
        db.execute("UPDATE studio_jobs SET status=?,result=?,provider_id=? WHERE id=?",
                   (status,json.dumps(result),provider_id,key))
    if asset:
        (studio.assets/(key+".jpg")).write_bytes(b"existing provider asset")
    monkeypatch.setattr(studio,"loop",lambda lane="all":None)
    studio.start()
    studio.stop()
    with studio.repo.connect() as db:
        row=db.execute("SELECT reserved FROM studio_jobs WHERE id=?",(key,)).fetchone()
    assert row["reserved"]==(0 if refunded else 0.025)


def test_failed_visual_review_retains_private_reason_and_hides_asset(setup, monkeypatch):
    a, b, s = setup
    key = a.post("/api/studio/media", json=media()).json()["id"]
    Image.new("RGB", (64, 64), "white").save(s.assets / f"{key}.jpg")
    s.update(key, status="reviewing", result=json.dumps({"title": "对象核对"}))
    monkeypatch.setattr(
        s, "llm", lambda *args: visual_review(objects_correct=False, reason="把器物误画成生物")
    )
    monkeypatch.setattr(s, "minimax", lambda *args: pytest.fail("must not regenerate"))
    monkeypatch.setattr(s, "pucoding_image", lambda *args: pytest.fail("must not regenerate"))
    run(s, a, key)
    value = a.get("/api/studio/jobs/" + key).json()
    assert value["status"] == "failed" and value["asset_url"] is None
    assert "_diagnostic" not in value["result"]
    with s.repo.connect() as db:
        row = db.execute("SELECT result FROM studio_jobs WHERE id=?", (key,)).fetchone()
    assert json.loads(row["result"])["_diagnostic"]["reason"] == "把器物误画成生物"
    assert a.get(f"/api/studio/jobs/{key}/asset").status_code != 200
    assert b.get(f"/api/studio/jobs/{key}/asset").status_code == 404


def test_stale_note_job_is_not_applied(setup):
    a, _, s = setup
    a.post("/api/studio/notes", json=note())
    key = a.post(
        "/api/studio/notes/note_123456/analyze",
        json={"action": "recognize", "request_id": "analysis_111", "revision": 1, "consent": True},
    ).json()["id"]
    a.post("/api/studio/notes", json=note(revision=1, title="新内容"))
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "failed"


def test_video_unsuitable_is_rejected_before_paid_call(setup, monkeypatch):
    a, _, s = setup
    monkeypatch.setattr(s, "evidence", lambda *args: [{"page": 1, "text": "准确计算"}])
    monkeypatch.setattr(
        s,
        "llm",
        lambda *args: {
            "title": "计算",
            "visual_scope": "精确计算关系",
            "visual_checks": ["计算关系完整"],
            "explanation": "精确计算",
            "points": ["精确计算"],
            "visual_prompt": "Do not generate this video.",
            "caution": "",
            "supported": True,
            "video_suitable": False,
        },
    )

    def no_call(*args):
        raise AssertionError("must not charge")

    monkeypatch.setattr(s, "minimax", no_call)
    key = a.post("/api/studio/media", json=media(kind="video")).json()["id"]
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "failed"


@pytest.mark.parametrize(
    "criterion",
    [
        "objects_correct",
        "relationships_correct",
        "no_unwanted_text",
        "useful",
        "temporal_consistency",
    ],
)
def test_each_visual_criterion_is_a_hard_gate(setup, monkeypatch, criterion):
    a, _, s = setup
    key = a.post("/api/studio/media", json=media()).json()["id"]
    Image.new("RGB", (64, 64)).save(s.assets / f"{key}.jpg")
    s.update(
        key,
        status="reviewing",
        result=json.dumps({"title": "test", "visual_prompt": "SECRET_TARGET"}),
    )

    def judge(prompt, images):
        assert "SECRET_TARGET" not in prompt
        return visual_review(**{criterion: False})

    monkeypatch.setattr(s, "llm", judge)
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["asset_url"] is None


def test_video_reference_requires_approved_same_owner_and_context(setup):
    a, b, s = setup
    key = a.post("/api/studio/media", json=media()).json()["id"]
    owner, _, _ = s.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    other, _, _ = s.repo.visitor(b.cookies.get("zhiwo_visitor"), [])
    data = json.loads(s.job(owner, key)["data"])
    Image.new("RGB", (64, 64)).save(s.assets / f"{key}.jpg")
    assert s.reference_image(owner, data) is None
    s.update(key, status="succeeded")
    assert s.reference_image(owner, data)[0] == key
    assert s.reference_image(other, data) is None
    for field, value in [
        ("excerpt", "其他内容"),
        ("pages", [9]),
        ("level", "进阶"),
        ("book_id", "different"),
    ]:
        assert s.reference_image(owner, {**data, field: value}) is None
    s.update(key, result=json.dumps({"visual_mode": "diagram"}))
    assert s.reference_image(owner, data) is None


def diagram_plan():
    return {
        "title": "DNA 内外结构关系",
        "visual_scope": "区分外侧骨架与内侧碱基",
        "visual_checks": ["骨架在外，碱基在内"],
        "explanation": "骨架在外，碱基在内。",
        "points": ["骨架在外侧"],
        "visual_prompt": "Render a source-grounded relationship diagram.",
        "caution": "关系图，不是实物结构图",
        "supported": True,
        "video_suitable": False,
        "visual_mode": "diagram",
        "diagram_facts": [
            {"subject": "脱氧核糖与磷酸", "relation": "交替连接形成", "object": "外侧骨架"},
            {"subject": "碱基", "relation": "排列在", "object": "内侧"},
        ],
    }


def test_diagram_uses_no_paid_media_and_is_still_reviewed(setup, monkeypatch):
    a, _, s = setup
    monkeypatch.setattr(s, "evidence", lambda *args: [{"page": 44, "text": "骨架在外，碱基在内。"}])
    answers = iter([diagram_plan(), {"passed": True, "reason": "ok"}, visual_review()])
    monkeypatch.setattr(s, "llm", lambda *args: next(answers))
    monkeypatch.setattr(
        s, "minimax", lambda *args: pytest.fail("diagram must not invoke media API")
    )
    key = a.post("/api/studio/media", json=media()).json()["id"]
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "reviewing"
    assert a.get(f"/api/studio/jobs/{key}/asset").status_code == 404
    run(s, a, key)
    value = a.get("/api/studio/jobs/" + key).json()
    assert (
        value["status"] == "succeeded" and value["result"]["provider"] == "source-grounded-diagram"
    )
    image = Image.open(io.BytesIO(a.get(value["asset_url"]).content))
    assert image.size == (1152, 864)
    pixels = list(image.resize((96, 72)).getdata())
    assert sum(max(pixel) - min(pixel) > 25 for pixel in pixels) > 300


def test_diagram_labels_fit_maximum_lengths():
    data = diagram_plan()
    data["title"] = "标题" * 50
    data["diagram_facts"] = [
        {"subject": "科学" * 18, "relation": "关系" * 10, "object": "知识" * 18}
    ] * 4
    assert render_diagram(TeachingPlan.model_validate(data))
    data["diagram_facts"] = []
    with pytest.raises(ValueError):
        TeachingPlan.model_validate(data)


def test_plan_has_one_text_only_repair_before_generation(setup, monkeypatch):
    a, _, s = setup
    monkeypatch.setattr(s, "evidence", lambda *args: [{"page": 44, "text": "骨架在外，碱基在内。"}])
    responses = iter(
        [
            diagram_plan(),
            {"passed": False, "reason": "关系谓语需完整"},
            diagram_plan(),
            {"passed": True, "reason": "ok"},
        ]
    )
    monkeypatch.setattr(s, "llm", lambda *args: next(responses))
    monkeypatch.setattr(s, "minimax", lambda *args: pytest.fail("no paid call for diagram"))
    monkeypatch.setattr(s, "pucoding_image", lambda *args: pytest.fail("no paid call for diagram"))
    key = a.post("/api/studio/media", json=media()).json()["id"]
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "reviewing"


@pytest.mark.parametrize("final_pass", [True, False])
def test_caption_repair_does_not_redraw_or_bypass_review(setup, monkeypatch, final_pass):
    a, _, s = setup
    key = a.post("/api/studio/media", json=media()).json()["id"]
    Image.new("RGB", (64, 64)).save(s.assets / f"{key}.jpg")
    s.update(
        key,
        status="reviewing",
        result=json.dumps({"title": "器物", "visual_mode": "illustration", "evidence": []}),
    )
    fixed = {
        **diagram_plan(),
        "visual_mode": "illustration",
        "diagram_facts": [],
        "title": "看清器物",
    }
    responses = iter(
        [
            visual_review(useful=False),
            fixed,
            {"passed": True, "reason": "ok"},
            visual_review(useful=final_pass),
        ]
    )
    monkeypatch.setattr(s, "llm", lambda *args: next(responses))
    monkeypatch.setattr(s, "minimax", lambda *args: pytest.fail("must reuse existing image"))
    monkeypatch.setattr(s, "pucoding_image", lambda *args: pytest.fail("must reuse existing image"))
    raw = (s.assets / f"{key}.jpg").read_bytes()
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "reviewing"
    run(s, a, key)
    value = a.get("/api/studio/jobs/" + key).json()
    assert value["status"] == ("succeeded" if final_pass else "failed")
    assert "_previous_media_review" not in value["result"]
    assert (s.assets / f"{key}.jpg").read_bytes() == raw


def test_video_reuses_first_frame_without_extra_generation(setup, monkeypatch):
    a, _, s = setup
    image_id = a.post("/api/studio/media", json=media()).json()["id"]
    Image.new("RGB", (64, 64)).save(s.assets / f"{image_id}.jpg")
    s.update(image_id, status="succeeded")
    monkeypatch.setattr(
        s, "evidence", lambda *args: [{"page": 1, "text": "函数调用自身，需要终止条件。"}]
    )
    answers = iter(
        [
            {
                "title": "理解递归",
        "visual_scope": "展示函数的自身调用与终止条件",
        "visual_checks": ["画面明确表达有限的自身嵌套"],
                "explanation": "类比理解",
                "points": ["嵌套"],
                "visual_prompt": "Nested boxes opening gently.",
                "caution": "类比",
                "supported": True,
                "video_suitable": True,
            },
            {"passed": True, "reason": "ok"},
        ]
    )
    monkeypatch.setattr(s, "llm", lambda *args: next(answers))
    calls = []

    def provider(path, payload):
        calls.append(path)
        assert payload["model"] == "MiniMax-H3-Lite"
        assert payload["resolution"] == "480P"
        assert "extra" not in payload
        assert payload["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert payload["content"][1]["role"] == "first_frame"
        assert payload["ratio"] == "adaptive"
        assert payload["duration"] == 6
        assert "Colorful modern educational illustration" in payload["content"][0]["text"]
        return {"task_id": "12345"}

    monkeypatch.setattr(s, "minimax", provider)
    key = a.post(
        "/api/studio/media", json=media(kind="video", request_id="video_reference_test")
    ).json()["id"]
    run(s, a, key)
    assert calls == ["/v2/video_generation"]
    value = a.get("/api/studio/jobs/" + key).json()
    assert value["status"] == "polling" and value["result"]["reference_image_id"] == image_id
    assert value["result"]["ratio"] == "adaptive"


def test_video_poll_is_resumable_and_does_not_generate_again(setup, monkeypatch):
    a, _, s = setup
    key = a.post("/api/studio/media", json=media(kind="video")).json()["id"]
    s.update(key, status="polling", provider_id="12345678")
    calls = []

    def provider(path, payload=None):
        assert payload is None
        calls.append(path)
        if "query" in path:
            return {"status": "Success", "file_id": "987654"}
        return {"file": {"download_url": "https://assets.minimaxi.com/test.mp4"}}

    monkeypatch.setattr(s, "minimax", provider)
    monkeypatch.setattr(s, "download", lambda *args: b"test-video-bytes")
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "reviewing"
    assert a.get("/api/studio/jobs/" + key + "/asset").status_code == 404
    assert len(calls) == 2


@pytest.mark.parametrize("status,expected", [("succeeded", "reviewing"), ("failed", "failed"),
                                            ("running", "polling"), ("queued", "polling")])
def test_h3_video_poll_uses_v2_and_downloads_the_result_once(setup, monkeypatch, status, expected):
    a, _, s = setup
    key = a.post("/api/studio/media", json=media(kind="video")).json()["id"]
    s.update(key, status="polling", provider_id="12345678",
             result=json.dumps({"video_api_version": 2, "model": "MiniMax-H3-Max"}))
    calls = []
    downloads = []

    def provider(path, payload=None):
        calls.append(path)
        assert payload is None
        return {"task": {"status": status, "content": {"url": "https://cdn.example.com/test.mp4"}}}

    monkeypatch.setattr(s, "minimax", provider)
    monkeypatch.setattr(s, "download", lambda url, limit: downloads.append(url) or b"video")
    run(s, a, key)
    value = a.get("/api/studio/jobs/" + key).json()
    assert value["status"] == expected
    assert calls == ["/v2/query/video_generation/12345678"]
    assert len(downloads) == (1 if status == "succeeded" else 0)


def test_foreign_or_invented_note_citation_fails_closed(setup, monkeypatch):
    a, _, s = setup
    a.post("/api/studio/notes", json=note())
    monkeypatch.setattr(s, "evidence", lambda *args: [{"page": 1, "text": "函数需要终止条件"}])
    monkeypatch.setattr(
        s,
        "llm",
        lambda *args: {
            "summary": "补全",
            "suggestions": [
                {
                    "original": "递归",
                    "kind": "可以补充",
                    "suggestion": "条件",
                    "evidence": "不存在的原文",
                    "page": 99,
                }
            ],
            "polished": "新的内容",
        },
    )
    key = a.post(
        "/api/studio/notes/note_123456/analyze",
        json={
            "request_id": "analysis_bad",
            "revision": 1,
            "action": "improve",
            "transcript": "递归",
            "consent": True,
        },
    ).json()["id"]
    with pytest.raises(ValueError, match="unsupported suggestion"):
        run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] != "succeeded"


def test_media_review_timeout_reuses_asset_and_is_bounded(setup):
    from adaptive_learning.llm.client import LLMTimeoutError
    a, _, studio = setup
    key = a.post("/api/studio/media", json=media()).json()["id"]
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    asset = studio.assets / f"{key}.jpg"
    Image.new("RGB", (32, 32), "white").save(asset)
    studio.update(key, status="reviewing", result=json.dumps({"title": "示意"}))
    assert studio.retry_media_review(studio.job(owner, key), LLMTimeoutError("timeout"))
    row = studio.job(owner, key)
    assert row["status"] == "reviewing" and asset.exists()
    assert json.loads(row["result"])["_review_retries"] == 1
    assert "_review_retries" not in studio.public_job(row)["result"]
    assert not studio.retry_media_review(row, LLMTimeoutError("timeout"))
    assert not studio.retry_media_review(row, ValueError("bad content"))
    studio.update(key, status="submitting")
    assert not studio.retry_media_review(studio.job(owner, key), LLMTimeoutError("timeout"))


def test_failed_media_feedback_is_scoped_to_owner_and_exact_selection(setup):
    a, b, studio = setup
    first = a.post("/api/studio/media", json=media()).json()["id"]
    studio.update(first, status="failed", result=json.dumps({"_diagnostic": {
        "stage": "media_review", "observations": "实体类别错误", "reason": "器物画成生物",
    }}))
    retry = a.post("/api/studio/media", json=media(request_id="retry_123456")).json()["id"]
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    assert studio.previous_media_feedback(studio.job(owner, retry))["observations"] == "实体类别错误"
    other = a.post("/api/studio/media", json=media(excerpt="这是另一个选段的教材内容。", request_id="other_123456")).json()["id"]
    assert studio.previous_media_feedback(studio.job(owner, other)) is None
    outsider = b.post("/api/studio/media", json=media(request_id="outside_123456")).json()["id"]
    second_owner, _, _ = studio.repo.visitor(b.cookies.get("zhiwo_visitor"), [])
    assert studio.previous_media_feedback(studio.job(second_owner, outsider)) is None


@pytest.mark.parametrize("fields", [{"visual_scope": ""}, {"visual_checks": []}, {"visual_checks": [" "]}])
def test_new_supported_media_plans_require_quality_scope(fields):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TeachingPlan.model_validate({**diagram_plan(), **fields})


def test_visual_review_uses_original_goal_and_never_generator_prompt(setup, monkeypatch):
    a, _, studio = setup
    key = a.post("/api/studio/media", json=media(excerpt="鱼灯与龙灯是动物造型的灯彩。", goal="意思")).json()["id"]
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    Image.new("RGB", (32, 32), "white").save(studio.assets / f"{key}.jpg")
    studio.update(key, status="reviewing", result=json.dumps({"title": "灯彩", "visual_prompt": "PRIVATE_GENERATOR_PROMPT", "evidence": []}))
    def review(prompt, images):
        assert '"original_selection": "鱼灯与龙灯是动物造型的灯彩。"' in prompt
        assert '"learning_goal": "意思"' in prompt
        assert "PRIVATE_GENERATOR_PROMPT" not in prompt
        return visual_review(objects_correct=False)
    monkeypatch.setattr(studio, "llm", review)
    run(studio, a, key)
    job = studio.public_job(studio.job(owner, key))
    assert job["status"] == "failed" and job["asset_url"] is None
