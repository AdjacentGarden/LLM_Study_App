"""Private ASR → one Flash note draft; no live recordings or model requests."""

import json
import time
from types import SimpleNamespace

import httpx
import pytest
from test_community import community as community
from test_studio import note
from test_studio import setup as setup

from adaptive_learning.config import get_settings
from adaptive_learning.studio import Studio
from adaptive_learning.studio_models import Recognition

REAL_CAPABILITIES = Studio.capabilities
RAW_ASR = "今天学习放热反应和稀热反应，那个，后面没听清。"
FINAL = {"summary": "整理放热与吸热反应的学习要点。",
         "polished": "放热与吸热反应\n\n- 放热反应向外释放能量。\n- 吸热反应从环境吸收能量。\n- 后续例子的具体内容待补充。"}


def voice(client):
    response = client.post("/api/studio/notes", json=note(input_mode="voice", strokes=[], title="语音要点"))
    assert response.status_code == 200
    raw = b"FORM" + (80).to_bytes(4, "big") + b"AIFF" + b"\0" * 80
    response = client.put("/api/studio/notes/note_123456/audio", content=raw, headers={
        "content-type": "audio/aiff", "x-note-revision": "1", "x-audio-duration": "4.2",
    })
    assert response.status_code == 200
    return response.json()


def request(client, revision=2, request_id="voice_note_request_1", **extra):
    return client.post("/api/studio/notes/note_123456/analyze", json={
        "action": "voice_note", "request_id": request_id, "revision": revision, "consent": True, **extra,
    })


def work(studio, monkeypatch):
    claim = studio.claim_next

    def claim_once(**kwargs):
        row = claim(**kwargs)
        if row is None:
            studio.stop_event.set()
        return row

    monkeypatch.setattr(studio, "claim_next", claim_once)
    studio.loop("notes")


def adapters(studio, monkeypatch, response=None, callback=None):
    calls = []
    monkeypatch.setattr(studio, "transcribe_voice", lambda *args: Recognition(
        transcript=RAW_ASR, uncertain=["某些词不清楚"]))
    monkeypatch.setattr(studio, "evidence", lambda *args: [
        {"page": 1, "text": "吸热反应从环境吸收能量；放热反应向外释放能量。"}])
    monkeypatch.setattr(studio, "llm", lambda *args: pytest.fail("legacy five-call note route must not run"))

    class Client:
        def structured(self, **kwargs):
            calls.append(kwargs)
            if callback:
                callback()
            return FINAL if response is None else response

        def close(self):
            pass

    monkeypatch.setattr(studio, "voice_note_client", Client)
    return calls


def test_voice_note_organizes_uncertain_asr_directly_and_never_exposes_raw_text(setup, monkeypatch):
    a, b, studio = setup
    saved = voice(a)
    stages = []

    def during_generation():
        public = a.get("/api/studio/jobs/" + key).json()
        assert public["status"] == "submitting"
        assert public["result"]["phase"] == "organizing"
        assert RAW_ASR not in json.dumps(public, ensure_ascii=False)

    calls = adapters(studio, monkeypatch, callback=during_generation)
    old_update = studio.update

    def update(job_id, **changes):
        old_update(job_id, **changes)
        if "result" in changes:
            stages.append(a.get("/api/studio/jobs/" + job_id).json()["result"])

    monkeypatch.setattr(studio, "update", update)
    queued = request(a, transcript="client supplied text must not become the ASR source")
    assert queued.status_code == 200
    key = queued.json()["id"]
    work(studio, monkeypatch)
    final = a.get("/api/studio/jobs/" + key).json()
    assert final["status"] == "succeeded"
    assert final["result"] == {"phase": "ready", "provider": "DeepSeek", "model": "deepseek-flash",
                                **FINAL, "suggestions": []}
    assert len(calls) == 1 and "asr_source" in calls[0]["user"]
    assert "textbook_evidence" in calls[0]["user"] and "吸热反应从环境吸收能量" in calls[0]["user"]
    assert [stage["phase"] for stage in stages] == ["transcribing", "organizing", "ready"]
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    stored = studio.job(owner, key)
    assert json.loads(stored["result"])["_voice_asr"]["transcript"] == RAW_ASR
    assert "transcript" not in json.loads(stored["data"])
    assert (studio.voice_path(owner, "note_123456")).is_file()
    accepted = a.post("/api/studio/notes/note_123456/edition", json={
        "job_id": key, "revision": saved["revision"], "accepted": True,
    })
    assert accepted.status_code == 200
    for response in (final, a.get("/api/studio/jobs?book_id=book-0").json(),
                     a.get("/api/studio/jobs?book_id=book-0&request_id=voice_note_request_1").json(),
                     a.get("/api/studio/notes/note_123456").json(), a.get("/api/studio/notes?book_id=book-0").json()):
        wire = json.dumps(response, ensure_ascii=False)
        assert RAW_ASR not in wire and "_voice_asr" not in wire and "confirmed_transcript" not in wire
    assert a.get("/api/studio/notes/note_123456").json()["edition"]["polished"] == FINAL["polished"]
    assert b.get("/api/studio/jobs/" + key).status_code == 404
    assert b.get("/api/studio/notes/note_123456").status_code == 404


def test_voice_note_requires_recording_and_rejects_handwritten_notes(setup):
    a, _, _ = setup
    assert a.post("/api/studio/notes", json=note()).status_code == 200
    assert request(a, revision=1).status_code == 422
    # Different note ID avoids changing an existing note's input mode.
    response = a.post("/api/studio/notes", json=note(id="voice_other_123", input_mode="voice", strokes=[]))
    assert response.status_code == 200
    response = a.post("/api/studio/notes/voice_other_123/analyze", json={
        "action": "voice_note", "request_id": "voice_missing_audio", "revision": 1, "consent": True,
    })
    assert response.status_code == 422


def test_voice_note_capability_requires_deepseek_and_local_asr_without_pucoding(setup, monkeypatch):
    _, _, studio = setup
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-flash-key")
    monkeypatch.setenv("PUCODING_API_KEY", "")
    monkeypatch.setenv("STUDIO_NOTE_LLM_PROVIDER", "pucoding")
    monkeypatch.setenv("STUDIO_VISION_PROVIDER", "pucoding")
    monkeypatch.setattr(studio, "voice_runtime_ready", lambda: True)
    get_settings.cache_clear()
    try:
        caps = REAL_CAPABILITIES(studio)
        assert caps["voice_notes"] is True and caps["notes_ai"] is False
        assert caps["voice_notes_provider"] == "DeepSeek" and caps["voice_notes_model"] == "deepseek-flash"
        monkeypatch.setenv("DEEPSEEK_API_KEY", "")
        get_settings.cache_clear()
        assert REAL_CAPABILITIES(studio)["voice_notes"] is False
    finally:
        get_settings.cache_clear()


def test_voice_note_client_is_explicit_flash_with_no_provider_fallback_or_paid_retry(setup, monkeypatch):
    _, _, studio = setup
    captured = []
    monkeypatch.setattr("adaptive_learning.studio.get_settings", lambda: SimpleNamespace(
        deepseek_api_key="offline-deepseek-key", deepseek_base_url="https://api.deepseek.com/v1", llm_https_proxy=None))
    monkeypatch.setattr("adaptive_learning.studio.OpenAICompatibleClient", lambda config: captured.append(config) or object())
    studio.voice_note_client()
    assert captured[0].model == "deepseek-flash" and captured[0].max_retries == 0
    assert captured[0].api_key == "offline-deepseek-key" and captured[0].thinking_enabled is False


@pytest.mark.parametrize("draft", [{"summary": "摘要", "polished": ""},
                                    {**FINAL, "transcript": RAW_ASR}])
def test_invalid_voice_note_draft_fails_without_publishing_asr_and_allows_explicit_new_attempt(setup, monkeypatch, draft):
    a, _, studio = setup
    voice(a)
    calls = adapters(studio, monkeypatch, response=draft)
    key = request(a).json()["id"]
    work(studio, monkeypatch)
    result = a.get("/api/studio/jobs/" + key).json()
    assert result["status"] == "failed" and RAW_ASR not in json.dumps(result, ensure_ascii=False)
    assert request(a).json()["id"] == key  # Acknowledged IDs never execute a new attempt.
    assert request(a, request_id="explicit_failed_retry").json()["id"] != key
    assert len(calls) == 1


def test_voice_note_timeout_preserves_private_asr_and_never_resubmits_uncertain_job(setup, monkeypatch):
    a, _, studio = setup
    voice(a)

    def timeout():
        raise httpx.ReadTimeout("offline ambiguous response")

    calls = adapters(studio, monkeypatch, callback=timeout)
    key = request(a).json()["id"]
    claim = studio.claim_next

    def claim_once(**kwargs):
        row = claim(**kwargs)
        if row is None:
            studio.stop_event.set()
        return row

    monkeypatch.setattr(studio, "claim_next", claim_once)
    studio.loop("notes")
    final = a.get("/api/studio/jobs/" + key).json()
    assert final["status"] == "uncertain"
    assert RAW_ASR not in json.dumps(final, ensure_ascii=False)
    assert request(a, request_id="do_not_replay_unknown").json()["id"] == key
    assert len(calls) == 1


@pytest.mark.parametrize("stage", ["asr", "model"])
def test_voice_note_never_publishes_against_changed_recording_revision(setup, monkeypatch, stage):
    a, _, studio = setup
    voice(a)

    def edit():
        assert a.post("/api/studio/notes", json=note(input_mode="voice", strokes=[], revision=2, title="新录音版本")).status_code == 200

    calls = adapters(studio, monkeypatch, callback=edit if stage == "model" else None)
    if stage == "asr":
        def asr(*args):
            edit()
            return Recognition(transcript=RAW_ASR, uncertain=[])
        monkeypatch.setattr(studio, "transcribe_voice", asr)
    key = request(a).json()["id"]
    work(studio, monkeypatch)
    final = a.get("/api/studio/jobs/" + key).json()
    assert final["status"] == "failed" and "polished" not in final["result"]
    assert len(calls) == (1 if stage == "model" else 0)


def test_note_worker_lane_claims_voice_note_and_restart_does_not_replay_submitting_call(setup, monkeypatch):
    a, _, studio = setup
    voice(a)
    key = request(a).json()["id"]
    claimed = studio.claim_next(lane="notes")
    assert claimed["id"] == key and claimed["kind"] == "voice_note"
    studio._lease.token = claimed["lease_owner"]
    studio.update(key, status="submitting", result=json.dumps({
        "phase": "organizing", "provider": "DeepSeek", "model": "deepseek-flash",
        "_voice_asr": {"transcript": RAW_ASR, "uncertain": ["词语不清"]},
    }))
    studio._lease.token = None
    restarted = Studio(studio.assets.parent)
    monkeypatch.setattr(restarted, "voice_note_client", lambda: pytest.fail("never replay the submitted model call"))
    assert restarted.claim_next(lane="notes", now=time.time() + 100) is None
    assert restarted.job(claimed["owner"], key)["status"] == "uncertain"
    assert request(a, request_id="reopen_after_interruption").json()["id"] == key
    assert RAW_ASR not in json.dumps(a.get("/api/studio/jobs/" + key).json(), ensure_ascii=False)
