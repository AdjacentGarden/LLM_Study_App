from dataclasses import replace

from adaptive_learning.config import get_settings


def test_pucoding_selection_never_uses_deepseek_credentials():
    settings = replace(get_settings(), llm_provider="pucoding", pucoding_api_key="new-key",
                       pucoding_text_model="gpt-5.3-codex", deepseek_api_key="old-key")
    assert settings.text_api_key == "new-key"
    assert settings.text_model == "gpt-5.3-codex"
    assert settings.text_base_url == settings.pucoding_base_url


def test_missing_selected_key_does_not_silently_fallback():
    settings = replace(get_settings(), llm_provider="pucoding", pucoding_api_key="",
                       deepseek_api_key="old-key")
    assert settings.text_api_key == ""


def test_existing_deepseek_selection_remains_supported():
    settings = replace(get_settings(), llm_provider="deepseek", deepseek_api_key="old-key")
    assert settings.text_api_key == "old-key"
    assert settings.text_model == settings.deepseek_model
