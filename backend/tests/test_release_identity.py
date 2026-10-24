import importlib


def test_health_exposes_the_deployed_commit_without_environment_secrets(monkeypatch):
    api = importlib.import_module("adaptive_learning.api.app")
    monkeypatch.setenv("CLOUDPATH_RELEASE_SHA", "a" * 40)
    monkeypatch.setenv("UNRELATED_API_KEY", "never-return-this-value")
    health = api.health()
    assert health["release_commit"] == "a" * 40
    assert "never-return-this-value" not in str(health)
