import base64
import io
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image
from test_community import community as community

from adaptive_learning.config import get_settings
from adaptive_learning.studio import Studio, get_studio, render_ink
from adaptive_learning.studio_diagram import render_diagram
from adaptive_learning.studio_models import Recognition, TeachingPlan


@pytest.fixture
def setup(community, monkeypatch):
    a, b, repo, _ = community
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
    replies = iter([result, {"passed": True, "reason": ""}])
    monkeypatch.setattr(studio, "llm", lambda *args: next(replies))
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
        "explanation": "函数调用自身，需要终止条件。",
        "points": ["需要终止条件"],
        "visual_prompt": "An illustration with nested boxes, no text.",
        "caution": "辅助示意",
        "supported": True,
        "video_suitable": False,
    }
    responses = iter([plan, {"passed": True, "reason": ""}, visual_review()])
    monkeypatch.setattr(s, "llm", lambda *args: next(responses))
    monkeypatch.setattr(s, "evidence", lambda *args: evidence)
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, "PNG")
    called = []

    def generate(path, payload):
        called.append(payload)
        return {"data": {"image_base64": [base64.b64encode(image.getvalue()).decode()]}}

    monkeypatch.setattr(s, "minimax", generate)
    key = a.post("/api/studio/media", json=media()).json()["id"]
    run(s, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "reviewing"
    assert a.get("/api/studio/jobs/" + key + "/asset").status_code == 404
    run(s, a, key)
    value = a.get("/api/studio/jobs/" + key).json()
    assert value["status"] == "succeeded"
    assert "visual_prompt" not in value["result"]
    assert len(called) == 1 and called[0]["n"] == 1 and not called[0]["prompt_optimizer"]
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

    def minimax(path, payload):
        calls.append((path, payload))
        return {"data": {"image_base64": [base64.b64encode(image.getvalue()).decode()]}}

    monkeypatch.setattr(studio, "minimax", minimax)
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
    key = a.post("/api/studio/media", json=media()).json()["id"]
    run(studio, a, key)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "failed"
    assert calls == []


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
        [{"transcript": "函数调用自己", "uncertain": []}, output, {"passed": True, "reason": ""}]
    )
    monkeypatch.setattr(s, "llm", lambda *args: next(responses))
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
            {"passed": True, "reason": ""},
        ]
    )
    calls = []

    def llm(prompt, images=None):
        calls.append(bool(images))
        return next(replies)

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
            {"passed": True, "reason": ""},
        ]
    )

    def llm(prompt, images=None):
        if images:
            barrier.wait(timeout=1)
            return {"transcript": "函数", "uncertain": []}
        return next(text_replies)

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


def test_complete_repairs_malformed_improvement_without_repeating_vision(setup, monkeypatch):
    a, _, s = setup
    a.post("/api/studio/notes", json=note())
    text_replies = iter(
        [
            {"summary": "字段不完整"},
            {"summary": "提纲", "suggestions": [], "polished": "函数"},
            {"passed": True, "reason": ""},
        ]
    )
    calls = {"vision": 0, "text": 0}

    def llm(prompt, images=None):
        if images:
            calls["vision"] += 1
            return {"transcript": "函数", "uncertain": []}
        calls["text"] += 1
        return next(text_replies)

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
    monkeypatch.setattr(s, "llm", lambda *args: next(replies))
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
            {"passed": True, "reason": ""},
        ]
    )

    def llm(*args):
        result = next(responses)
        if "passed" in result:
            a.post("/api/studio/notes", json=note(revision=1, title="新的笔记"))
        return result

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
    monkeypatch.setattr(s, "loop", lambda: None)
    s.start()
    s.stop()
    value = a.get("/api/studio/jobs/" + key).json()
    assert value["status"] == "uncertain"
    assert a.post("/api/studio/media", json=media(request_id="after_restart")).json()["id"] == key


def test_failed_visual_review_retains_private_reason_and_hides_asset(setup, monkeypatch):
    a, b, s = setup
    key = a.post("/api/studio/media", json=media()).json()["id"]
    Image.new("RGB", (64, 64), "white").save(s.assets / f"{key}.jpg")
    s.update(key, status="reviewing", result=json.dumps({"title": "对象核对"}))
    monkeypatch.setattr(
        s, "llm", lambda *args: visual_review(objects_correct=False, reason="把器物误画成生物")
    )
    monkeypatch.setattr(s, "minimax", lambda *args: pytest.fail("must not regenerate"))
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
        assert payload["first_frame_image"].startswith("data:image/jpeg;base64,")
        assert payload["duration"] == 6
        assert "Colorful modern educational illustration" in payload["prompt"]
        return {"task_id": "12345"}

    monkeypatch.setattr(s, "minimax", provider)
    key = a.post(
        "/api/studio/media", json=media(kind="video", request_id="video_reference_test")
    ).json()["id"]
    run(s, a, key)
    assert calls == ["/v1/video_generation"]
    value = a.get("/api/studio/jobs/" + key).json()
    assert value["status"] == "polling" and value["result"]["reference_image_id"] == image_id


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
