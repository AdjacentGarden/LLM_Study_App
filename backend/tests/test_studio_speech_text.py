"""Device speech text is private input; no audio upload, Mac ASR or live model calls."""

import hashlib
import json

import httpx
import pytest
from test_community import community as community
from test_studio import note
from test_studio import setup as setup
from test_studio_voice_note import FINAL, REAL_CAPABILITIES, request, voice, work

from adaptive_learning.config import get_settings
from adaptive_learning.studio import Studio

RAW = "今天学习放热和稀热反应，那个，请记住能量变化。"


@pytest.fixture
def text_setup(setup, monkeypatch):
    monkeypatch.setattr(Studio, "capabilities", lambda self: {
        "image": True, "video": True, "notes_ai": False, "voice_notes": False,
        "voice_notes_legacy": False, "voice_text_notes": True,
    })
    return setup


def create(client, **extra):
    response = client.post("/api/studio/notes", json=note(input_mode="voice", strokes=[], **extra))
    assert response.status_code == 200, response.text
    return response.json()


def save(client, revision=1, text=RAW, **extra):
    return client.put("/api/studio/notes/note_123456/speech-text", json={
        "revision": revision, "text": text, "source": "apple-speech-on-device", "language": "zh-CN", **extra,
    })


def flash(studio, monkeypatch, callback=None):
    calls = []
    monkeypatch.setattr(studio, "transcribe_voice", lambda *args: pytest.fail("device text must never invoke Mac ASR"))
    monkeypatch.setattr(studio, "evidence", lambda *args: [{"page": 1, "text": "吸热反应吸收能量，放热反应释放能量。"}])

    class Client:
        def structured(self, **kwargs):
            calls.append(kwargs)
            if callback:
                callback()
            return FINAL

        def close(self):
            pass

    monkeypatch.setattr(studio, "voice_note_client", Client)
    return calls


def test_device_text_worker_generates_and_accepts_without_audio_and_never_exposes_asr(text_setup, monkeypatch):
    a, b, studio = text_setup
    create(a)
    saved = save(a).json()
    digest = hashlib.sha256(RAW.encode()).hexdigest()
    assert saved["speech_ready"] is True and saved["speech_sha256"] == digest
    assert saved["audio_ready"] is False and saved["revision"] == 2
    calls = flash(studio, monkeypatch)
    queued = request(a)
    assert queued.status_code == 200, queued.text
    key = queued.json()["id"]
    work(studio, monkeypatch)
    public = a.get("/api/studio/jobs/" + key).json()
    assert public["status"] == "succeeded" and public["result"]["polished"] == FINAL["polished"]
    assert len(calls) == 1 and RAW in calls[0]["user"]
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    assert not studio.voice_path(owner, "note_123456").exists()
    stored = json.loads(studio.job(owner, key)["result"])["_voice_asr"]
    assert stored["transcript"] == RAW and stored["source"] == "apple-speech-on-device"
    assert stored["language"] == "zh-CN" and stored["speech_sha256"] == digest
    assert a.post("/api/studio/notes/note_123456/edition", json={
        "job_id": key, "revision": 2, "accepted": True,
    }).status_code == 200
    for value in [saved, public, a.get("/api/studio/notes/note_123456").json(),
                  a.get("/api/studio/notes?book_id=book-0").json(),
                  a.get("/api/studio/jobs?book_id=book-0").json(),
                  a.get("/api/studio/jobs?book_id=book-0&request_id=voice_note_request_1").json()]:
        wire = json.dumps(value, ensure_ascii=False)
        assert RAW not in wire and "_speech_input" not in wire and "_voice_asr" not in wire
        assert "confirmed_transcript" not in wire
    assert b.get("/api/studio/notes/note_123456").status_code == 404
    assert save(b).status_code == 404
    assert a.get("/api/studio/notes/note_123456/audio").status_code == 404


def test_same_trimmed_text_is_idempotent_and_changes_require_current_revision(text_setup):
    a, _, _ = text_setup
    create(a)
    first = save(a, text=" \n" + RAW + "\n ").json()
    assert first["speech_sha256"] == hashlib.sha256(RAW.encode()).hexdigest()
    assert save(a).json() == first  # Lost PUT response: old revision, identical source.
    assert save(a, revision=2).json() == first  # No-op update does not bump.
    assert save(a, text="不同内容").status_code == 409
    changed = save(a, revision=2, text="下一段学习笔记").json()
    assert changed["revision"] == 3 and changed["speech_sha256"] != first["speech_sha256"]
    assert save(a, revision=1, text="下一段学习笔记").status_code == 409


def test_general_note_save_preserves_server_owned_speech_but_cannot_forge_ready(text_setup):
    a, _, studio = text_setup
    created = create(a, speech_ready=True, speech_sha256="a" * 64)
    assert created["speech_ready"] is False and created["speech_sha256"] == ""
    saved = save(a).json()
    edited = a.post("/api/studio/notes", json=note(
        revision=2, input_mode="voice", strokes=[], title="修改标题", speech_ready=False, speech_sha256=""))
    assert edited.status_code == 200
    assert edited.json()["speech_ready"] is True
    assert edited.json()["speech_sha256"] == saved["speech_sha256"]
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    assert studio.voice_speech_input(owner, "note_123456", 3)["text"] == RAW
    assert RAW not in edited.text


def test_speech_and_audio_inputs_supersede_each_other_without_raw_text_leak(text_setup):
    a, _, studio = text_setup
    voice(a)
    saved = save(a, revision=2).json()
    assert saved["revision"] == 3 and saved["speech_ready"] and not saved["audio_ready"]
    assert saved["audio_sha256"] == "" and a.get("/api/studio/notes/note_123456/audio").status_code == 404
    raw_audio = b"FORM" + (80).to_bytes(4, "big") + b"AIFF" + b"\0" * 80
    uploaded = a.put("/api/studio/notes/note_123456/audio", content=raw_audio, headers={
        "content-type": "audio/aiff", "x-note-revision": "3", "x-audio-duration": "4.2",
    })
    assert uploaded.status_code == 200
    assert uploaded.json()["audio_ready"] and not uploaded.json()["speech_ready"]
    assert uploaded.json()["speech_sha256"] == "" and RAW not in uploaded.text
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    with studio.repo.connect() as db:
        data = json.loads(db.execute("SELECT data FROM studio_notes WHERE id=? AND owner=?", ("note_123456", owner)).fetchone()[0])
    assert "_speech_input" not in data


@pytest.mark.parametrize("change", [{"text": " \n "}, {"text": "长" * 12001},
                                   {"source": "cloud-asr"}, {"language": "en-US"}, {"revision": 0}])
def test_speech_text_contract_rejects_invalid_input(text_setup, change):
    a, _, _ = text_setup
    create(a)
    assert save(a, **change).status_code == 422
    assert a.get("/api/studio/notes/note_123456").json()["revision"] == 1


def test_handwriting_does_not_accept_speech_and_legacy_recognize_requires_audio(text_setup):
    a, _, _ = text_setup
    assert a.post("/api/studio/notes", json=note()).status_code == 200
    assert save(a).status_code == 422


def test_text_capability_needs_only_deepseek_and_failure_never_invokes_mac_asr(text_setup, monkeypatch):
    a, _, studio = text_setup
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    monkeypatch.setenv("PUCODING_API_KEY", "")
    monkeypatch.setattr(studio, "voice_runtime_ready", lambda: False)
    get_settings.cache_clear()
    try:
        caps = REAL_CAPABILITIES(studio)
        assert caps["voice_text_notes"] and not caps["voice_notes"]
        monkeypatch.setattr(Studio, "capabilities", lambda self: caps)
        create(a)
        save(a)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "")
        get_settings.cache_clear()
        assert not REAL_CAPABILITIES(studio)["voice_text_notes"]
        caps["voice_text_notes"] = False
        assert request(a).status_code == 503
        with studio.repo.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 0
    finally:
        get_settings.cache_clear()


def test_text_timeout_keeps_private_source_and_never_replays_model_request(text_setup, monkeypatch):
    a, _, studio = text_setup
    create(a)
    save(a)

    def timeout():
        raise httpx.ReadTimeout("unknown model outcome")

    calls = flash(studio, monkeypatch, timeout)
    key = request(a).json()["id"]
    work(studio, monkeypatch)
    final = a.get("/api/studio/jobs/" + key).json()
    assert final["status"] == "uncertain"
    assert request(a, request_id="device_reopen_no_replay").json()["id"] == key
    assert len(calls) == 1 and RAW not in json.dumps(final, ensure_ascii=False)


def test_queued_text_source_changed_revision_fails_before_paid_call(text_setup, monkeypatch):
    a, _, studio = text_setup
    create(a)
    save(a)
    calls = flash(studio, monkeypatch)
    key = request(a).json()["id"]
    assert save(a, revision=2, text="改过的新语音").status_code == 200
    work(studio, monkeypatch)
    assert a.get("/api/studio/jobs/" + key).json()["status"] == "failed"
    assert calls == []
