import importlib
from dataclasses import replace

import pytest
from test_teaching_content import Client, course

from adaptive_learning.config import get_settings
from adaptive_learning.personalization.rubric_alignment import RUBRIC_ALIGNMENT_PROMPT
from adaptive_learning.personalization.teaching_content import (
    FACT_REVIEW_PROMPT,
    GENERATION_PROMPT,
    REVIEW_PROMPT,
    TeachingContentGenerator,
    TeachingServiceUnavailableError,
)
from adaptive_learning.personalization.teaching_scope import SCOPE_PLANNING_PROMPT


@pytest.fixture
def clean_review_env(monkeypatch):
    for key in ("TEACHING_REVIEW_PROVIDER", "TEACHING_REVIEW_MODEL", "PUCODING_API_KEY", "TEACHING_REVIEW_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("provider,model", [("unknown", "gpt-6-sol"), ("deepseek", "deepseek-flash"),
    ("pucoding", ""), ("", "gpt-6-sol"), ("pucoding", "sk-invalid-secret"), ("pucoding", "gpt-6-sol\nother")])
def test_strict_review_route_rejects_partial_unknown_and_secret_like_configuration(clean_review_env, monkeypatch, provider, model):
    monkeypatch.setenv("TEACHING_REVIEW_PROVIDER", provider)
    monkeypatch.setenv("TEACHING_REVIEW_MODEL", model)
    with pytest.raises(ValueError):
        get_settings()


def test_explicit_review_route_does_not_replace_generation_and_requires_own_key(clean_review_env, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setenv("TEACHING_REVIEW_PROVIDER", "pucoding")
    monkeypatch.setenv("TEACHING_REVIEW_MODEL", "gpt-6-sol")
    settings = get_settings()
    assert not settings.teaching_review_configured
    assert settings.text_model == "deepseek-flash" and settings.llm_provider == "deepseek"
    configured = replace(settings, pucoding_api_key="PRIVATE_KEY")
    assert configured.teaching_review_configured
    assert "PRIVATE" not in configured.teaching_review_revision
    assert replace(configured, teaching_review_model="gpt-other").teaching_review_revision != configured.teaching_review_revision


def test_explicit_missing_reviewer_stops_before_any_generation_call():
    bundle, chapter, evidence = course(2)
    generation = Client()
    with pytest.raises(TeachingServiceUnavailableError, match="独立教学审核服务尚未配置"):
        TeachingContentGenerator(generation, review_client=None).generate(bundle, chapter, evidence)
    assert generation.systems == []


def test_generation_planning_and_review_use_separate_clients_and_record_real_identity():
    bundle, chapter, evidence = course(2)
    generation, reviewer, events = Client(), Client(), []
    identity = {"provider": "pucoding", "model": "gpt-6-sol", "reasoning_effort": "low"}
    result = TeachingContentGenerator(generation, review_client=reviewer, review_identity=identity,
                                      audit_callback=events.append).generate(bundle, chapter, evidence)
    assert set(generation.systems) == {SCOPE_PLANNING_PROMPT, GENERATION_PROMPT}
    assert set(reviewer.systems) == {FACT_REVIEW_PROMPT, REVIEW_PROMPT, RUBRIC_ALIGNMENT_PROMPT}
    assert reviewer.systems.count(RUBRIC_ALIGNMENT_PROMPT) == 1
    assert generation.fact_calls == generation.review_calls == 0
    assert not reviewer.generation_batches
    assert result.teaching_fact_review["review_identity"] == result.teaching_review["review_identity"] == identity
    assert all(event["provider"] == "pucoding" and event["model"] == "gpt-6-sol"
        for event in events if event["event"] == "model_request" and event["model_role"] == "review")
    rubric_events = [event for event in events if event["event"] == "rubric_review"]
    assert len(rubric_events) == 1
    assert {key: rubric_events[0][key] for key in identity} == identity
    assert rubric_events[0]["review_stage"] == "rubric"
    assert result.teaching_review["rubric_alignment"]["review_identity"] == identity


def test_client_creation_failure_is_degraded_without_falling_back(clean_review_env):
    app = importlib.import_module("adaptive_learning.api.app")
    settings = replace(get_settings(), teaching_review_provider="pucoding", teaching_review_model="gpt-6-sol", pucoding_api_key="PRIVATE_KEY")
    def unavailable(config):
        raise RuntimeError("PRIVATE_KEY PRIVATE_URL")
    assert app._create_teaching_review_client(settings, unavailable) is None
    assert app._create_teaching_review_client(replace(settings, pucoding_api_key=""), unavailable) is None


def test_reviewer_model_change_invalidates_approved_cache_without_get_generation(tmp_path, monkeypatch):
    from test_teaching_publication import publication_environment
    env = publication_environment(tmp_path, monkeypatch)
    initial = env.client.post("/api/interviews/session/courses/morgan")
    assert initial.status_code == 200
    monkeypatch.setattr(env.module, "settings", replace(env.module.settings,
        teaching_review_provider="pucoding", teaching_review_model="gpt-6-sol"))
    stale = env.client.get("/api/interviews/session/courses/morgan")
    assert stale.status_code == 409 and env.generator.calls == 1


@pytest.mark.parametrize("configured_timeout,expected", [(None, 120), ("300", 300)])
def test_review_client_uses_validated_configured_timeout(clean_review_env, monkeypatch, configured_timeout, expected):
    from types import SimpleNamespace

    app = importlib.import_module("adaptive_learning.api.app")
    if configured_timeout is not None:
        monkeypatch.setenv("TEACHING_REVIEW_TIMEOUT_SECONDS", configured_timeout)
    settings = replace(get_settings(), teaching_review_provider="pucoding", teaching_review_model="gpt-6-sol", pucoding_api_key="fixture-key")
    client = app._create_teaching_review_client(settings, lambda config: SimpleNamespace(config=config))
    assert settings.teaching_review_timeout_seconds == expected
    assert client.config.timeout_seconds == expected
    assert client.config.reasoning_effort == "low"
    assert replace(settings, teaching_review_timeout_seconds=600).teaching_review_revision == settings.teaching_review_revision


@pytest.mark.parametrize("invalid", ["0", "-1", "nan", "inf", "not-a-number"])
def test_invalid_review_timeout_is_rejected_before_client_creation(clean_review_env, monkeypatch, invalid):
    monkeypatch.setenv("TEACHING_REVIEW_TIMEOUT_SECONDS", invalid)
    with pytest.raises(ValueError):
        get_settings()
