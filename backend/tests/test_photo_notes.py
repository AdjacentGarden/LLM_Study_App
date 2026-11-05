import base64
import hashlib
import io
import json

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError
from test_community import community as community
from test_studio import setup as setup
from test_studio_voice_note import work

from adaptive_learning.photo_notes import PhotoNoteInput, decode_photo, recognition_images


def payload(size=(800, 1200), **changes):
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, "JPEG")
    image = output.getvalue()
    return {"book_id": "book-0", "request_id": "photo_test_request_1", "consent": True,
            "image_base64": base64.b64encode(image).decode(),
            "image_sha256": hashlib.sha256(image).hexdigest(), **changes}


def enable(studio, monkeypatch):
    monkeypatch.setattr(studio, "capabilities", lambda: {"photo_notes": True})


def test_photo_input_rejects_forged_hash_invalid_bytes_and_missing_consent():
    for changes in ({"image_sha256": "a" * 64}, {"image_base64": "!" * 40}, {"consent": False}):
        with pytest.raises(ValidationError):
            PhotoNoteInput.model_validate(payload(**changes))
    with pytest.raises(ValidationError):
        PhotoNoteInput.model_validate(payload(size=(5000, 32)))


def test_truncated_photo_is_rejected_before_model_submission():
    raw = base64.b64decode(payload()["image_base64"])[:250]
    with pytest.raises(ValueError):
        decode_photo(base64.b64encode(raw).decode(), hashlib.sha256(raw).hexdigest())


def test_large_photo_keeps_overview_and_ordered_detail_crops():
    body = payload(size=(1600, 2400))
    images = recognition_images(decode_photo(body["image_base64"], body["image_sha256"]))
    assert len(images) == 3
    sizes = [Image.open(io.BytesIO(data)).size for _, data in images]
    assert sizes == [(1600, 2400), (1600, 1280), (1600, 1280)]


def test_photo_extracts_then_summarizes_preserving_raw_uncertainties_and_owner(setup, monkeypatch):
    a, b, studio = setup
    enable(studio, monkeypatch)
    calls = []
    class Client:
        def structured(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {"transcript": "DNA只复制1次。2n→n。[待确认]", "uncertain": ["末行单位不清楚"]}
            return {"title": "减数分裂", "summary": "整理染色体变化。", "polished": "DNA只复制1次。2n→n。末行单位待确认。"}
        def close(self): pass
    monkeypatch.setattr(studio, "voice_note_client", Client)
    request = payload()
    first = a.post("/api/studio/photo-notes", json=request)
    assert first.status_code == 200
    key = first.json()["id"]
    assert a.post("/api/studio/photo-notes", json=request).json()["id"] == key
    work(studio, monkeypatch)
    final = a.get("/api/studio/jobs/" + key).json()
    assert final["status"] == "succeeded"
    assert final["result"]["transcript"] == "DNA只复制1次。2n→n。[待确认]"
    assert final["result"]["uncertain"] == ["末行单位不清楚"]
    assert final["result"]["image_sha256"] == request["image_sha256"]
    assert len(calls) == 2 and calls[0]["images"] and "images" not in calls[1]
    assert "2n→n" in calls[1]["user"]
    assert b.get("/api/studio/jobs/" + key).status_code == 404
    receipt = a.get("/api/studio/jobs?book_id=book-0&request_id=" + request["request_id"]).json()
    assert receipt["items"][0]["id"] == key
    assert "image_base64" not in json.dumps(final)
    assert a.post("/api/studio/photo-notes", json=payload(size=(801, 1200))).status_code == 409


def test_blank_photo_fails_without_summary_and_retains_input(setup, monkeypatch):
    a, _, studio = setup
    enable(studio, monkeypatch)
    calls = []
    class Client:
        def structured(self, **kwargs):
            calls.append(kwargs)
            return {"transcript": "", "uncertain": []}
        def close(self): pass
    monkeypatch.setattr(studio, "voice_note_client", Client)
    first = a.post("/api/studio/photo-notes", json=payload()).json()
    work(studio, monkeypatch)
    final = a.get("/api/studio/jobs/" + first["id"]).json()
    assert final["status"] == "failed" and len(calls) == 1
    owner, _, _ = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    assert json.loads(studio.job(owner, first["id"])["data"])["image_base64"]


def test_photo_timeout_is_uncertain_and_never_replayed(setup, monkeypatch):
    a, _, studio = setup
    enable(studio, monkeypatch)
    calls = []
    class Client:
        def structured(self, **kwargs):
            calls.append(kwargs)
            raise httpx.ReadTimeout("request outcome unknown")
        def close(self): pass
    monkeypatch.setattr(studio, "voice_note_client", Client)
    request = payload()
    first = a.post("/api/studio/photo-notes", json=request).json()
    work(studio, monkeypatch)
    final = a.get("/api/studio/jobs/" + first["id"]).json()
    assert final["status"] == "uncertain"
    assert a.post("/api/studio/photo-notes", json=request).json()["id"] == first["id"]
    assert len(calls) == 1


def test_photo_capability_is_independent_of_other_vision_provider(setup, monkeypatch):
    a, _, studio = setup
    monkeypatch.setattr(studio, "capabilities", lambda: {"photo_notes": False})
    assert a.post("/api/studio/photo-notes", json=payload()).status_code == 503
    assert a.post("/api/studio/photo-notes", json=payload(book_id="unknown-book")).status_code == 403
