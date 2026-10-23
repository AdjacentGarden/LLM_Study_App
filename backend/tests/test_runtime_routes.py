from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from adaptive_learning.api.runtime_routes import RuntimeSnapshot, runtime_router


def client_for(sampler):
    app = FastAPI()
    app.include_router(runtime_router(sampler))
    return TestClient(app)


def full_snapshot(**updates):
    return RuntimeSnapshot(**{**{name: True for name in RuntimeSnapshot.model_fields}, **updates})


def test_readiness_is_a_no_store_runtime_snapshot_and_not_an_inference_claim():
    calls = []
    def snapshot():
        calls.append(True)
        return full_snapshot()
    with client_for(snapshot) as client:
        response = client.get("/api/readiness")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["ok"] is True and not data["blocked_features"]
    assert data["check_scope"] == "configuration_and_workers"
    assert data["external_inference_verified"] is False
    assert calls == [True]
    assert all(type(value) is bool for value in data["checks"].values())


def test_configured_but_stopped_workers_do_not_announce_features_ready():
    with client_for(lambda: full_snapshot(ocr_worker_alive=False, course_worker_alive=False)) as client:
        data = client.get("/api/readiness").json()
    assert data["ok"] is False
    assert data["features"]["prepare"] is False
    assert data["features"]["courses"] is False
    assert data["features"]["qa"] is True


def test_note_and_media_lanes_report_independently_and_live_changes_are_visible():
    state = full_snapshot(media_worker_alive=False)
    with client_for(lambda: state) as client:
        data = client.get("/api/readiness").json()
        assert data["features"]["notes_ai"] and data["features"]["voice_notes"]
        assert not data["features"]["image"] and not data["features"]["video"]
        state.media_worker_alive = True
        state.note_worker_alive = False
        data = client.get("/api/readiness").json()
        assert not data["features"]["notes_ai"] and not data["features"]["voice_notes"]
        assert data["features"]["image"] and data["features"]["video"]


@pytest.mark.parametrize("fault", ["exception", "secret_field", "string_boolean"])
def test_sampler_failures_never_echo_secrets_paths_or_accept_truthy_strings(fault):
    def sampler():
        if fault == "exception":
            raise RuntimeError("PRIVATE_CREDENTIAL /private/path/model http://secret@internal")
        return ({"api_key": "PRIVATE_CREDENTIAL"} if fault == "secret_field"
                else {"text_configured": "false"})
    with client_for(sampler) as client:
        response = client.get("/api/readiness")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert not any(value in response.text for value in ("PRIVATE", "/private", "http://", "api_key", "text_configured"))


def test_default_snapshot_is_degraded_instead_of_assuming_optional_services_exist():
    with client_for(RuntimeSnapshot) as client:
        data = client.get("/api/readiness").json()
    assert data["ok"] is False and not any(data["features"].values())


def test_generation_configuration_does_not_make_courses_ready_without_reviewer():
    with client_for(lambda: full_snapshot(review_configured=False)) as client:
        data = client.get("/api/readiness").json()
    assert data["checks"]["review_configured"] is False
    assert data["features"]["courses"] is False
    assert data["features"]["prepare"] is True and data["features"]["qa"] is True
    assert data["external_inference_verified"] is False
