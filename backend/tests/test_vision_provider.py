from dataclasses import replace
import pytest
from adaptive_learning.config import get_settings


def test_vision_prefers_deepseek_when_both_provider_keys_exist():
    settings = replace(get_settings(), vision_provider="auto", deepseek_api_key="deepseek-key",
        pucoding_api_key="pucoding-key", deepseek_model="deepseek-flash")
    assert settings.resolved_vision_provider == "deepseek"
    assert settings.vision_base_url == settings.deepseek_base_url
    assert settings.vision_api_key == "deepseek-key"
    assert settings.vision_model == "deepseek-flash"


def test_explicit_pucoding_and_missing_deepseek_key_keep_working():
    settings = replace(get_settings(), vision_provider="auto", deepseek_api_key="", pucoding_api_key="pucoding-key")
    assert settings.resolved_vision_provider == "pucoding"
    assert settings.vision_api_key == "pucoding-key"
    explicit = replace(settings, vision_provider="pucoding", deepseek_api_key="deepseek-key")
    assert explicit.vision_api_key == "pucoding-key"
    with pytest.raises(ValueError):
        replace(settings, vision_provider="unknown")
