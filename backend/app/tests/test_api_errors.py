from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_validation_errors_use_standard_shape() -> None:
    client = TestClient(app)
    response = client.post("/api/uploads/init", json={})
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "validation_error"
    assert payload["message"]
    assert "errors" in payload["details"]
