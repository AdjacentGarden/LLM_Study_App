"""Actual generator entry point must stop before writer/planner cost."""

import pytest
from adaptive_learning.llm import client as module
from adaptive_learning.llm.model_admission import ModelAdmission, route_key
from adaptive_learning.personalization.teaching_content import (
    TeachingContentGenerator,
    TeachingServiceUnavailableError,
)
from test_llm_admission import client, success


@pytest.mark.parametrize("blocked_role", ["writer", "reviewer"])
def test_known_cooldown_stops_before_any_paid_prerequisite(monkeypatch, blocked_role):
    gate = ModelAdmission()
    monkeypatch.setattr(module, "admission", gate)
    sends = []

    def transport(request):
        sends.append(request)
        return success()

    writer = client(transport)
    reviewer = client(transport, provider="pucoding", model="gpt-6-sol")
    blocked = writer if blocked_role == "writer" else reviewer
    key = route_key(blocked.config.base_url, blocked.config.model, blocked.config.api_key)
    gate.finish(gate.begin(key), overload=30)
    generator = TeachingContentGenerator(writer, review_client=reviewer)
    with pytest.raises(TeachingServiceUnavailableError, match="冷却"):
        generator._generate(None, None, None)
    assert sends == []
    writer.close()
    reviewer.close()


def test_admitted_preflight_is_not_a_health_probe_or_semantic_pass(monkeypatch):
    monkeypatch.setattr(module, "admission", ModelAdmission())
    sends = []
    writer = client(lambda request: (sends.append(request) or success()))
    reviewer = client(lambda request: (sends.append(request) or success()), provider="pucoding")
    generator = TeachingContentGenerator(writer, review_client=reviewer)

    def next_ordinary_stage(*args):
        raise RuntimeError("reached normal evidence stage")

    monkeypatch.setattr(generator, "_inputs", next_ordinary_stage)
    with pytest.raises(RuntimeError, match="normal evidence"):
        generator._generate(None, None, None)
    assert sends == []
    writer.close()
    reviewer.close()
