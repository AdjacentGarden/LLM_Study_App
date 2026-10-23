"""Both configuration families remain enforced after branch consolidation."""
from dataclasses import replace

import pytest

from adaptive_learning.config import get_settings


@pytest.fixture
def configuration(monkeypatch):
    for name in ("TEACHING_REVIEW_PROVIDER", "TEACHING_REVIEW_MODEL"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_native_worker_controls_and_independent_review_can_be_configured_together(configuration):
    configured = replace(
        configuration, studio_worker_enabled=False,
        teaching_review_provider="pucoding", teaching_review_model="gpt-6-sol",
        pucoding_api_key="fixture-key", rag_max_output_tokens=8000,
        rag_generation_timeout_seconds=90,
    )
    assert not configured.studio_worker_enabled
    assert configured.teaching_review_configured
    assert configured.rag_max_output_tokens == 8000
    assert configured.teaching_review_identity == {
        "provider": "pucoding", "model": "gpt-6-sol", "reasoning_effort": "low"
    }
    assert "fixture-key" not in configured.teaching_review_revision


@pytest.mark.parametrize("invalid", [
    {"rag_max_output_tokens": 10001},
    {"rag_generation_timeout_seconds": 0},
    {"quality_review": 0.95, "quality_accept": 0.9},
    {"ocr_page_structured_review": 0.7, "ocr_page_quality_review": 0.6},
    {"ocr_worker_poll_seconds": float("nan")},
    {"teaching_review_provider": "pucoding", "teaching_review_model": ""},
    {"teaching_review_provider": "deepseek", "teaching_review_model": "gpt-6-sol"},
    {"teaching_review_provider": "pucoding", "teaching_review_model": "invalid-token"},
])
def test_both_native_and_teaching_review_validators_remain_active(configuration, invalid):
    with pytest.raises(ValueError):
        replace(configuration, **invalid)
