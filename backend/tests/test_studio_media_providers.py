import json

import httpx
import pytest

from adaptive_learning.config import get_settings
from adaptive_learning.studio import ProviderRejected, Studio


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")
    monkeypatch.setenv("PUCODING_API_KEY", "pucoding-test")
    monkeypatch.setenv("PUCODING_BASE_URL", "https://pucoding.com/v1")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimax.cn")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("STUDIO_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("STUDIO_MEDIA_PAUSED_REASON", raising=False)
    monkeypatch.delenv("LLM_HTTPS_PROXY", raising=False)
    get_settings.cache_clear()
    yield Studio.__new__(Studio)
    get_settings.cache_clear()


def test_image_capability_does_not_require_minimax_key(providers):
    capabilities = providers.capabilities()
    assert capabilities["image"]
    assert not capabilities["video"]
    assert capabilities["image_provider"] == "PUCODING"
    assert capabilities["image_model"] == "gpt-image-2"
    assert capabilities["video_model"] == "MiniMax-H3-Max"


def test_pucoding_images_use_the_pucoding_key_and_documented_endpoint(providers, monkeypatch):
    requests = []
    options = []
    original_client = httpx.Client

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": "sample"}]})

    def client(**kwargs):
        options.append(kwargs)
        return original_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr("adaptive_learning.studio.httpx.Client", client)
    payload = {"model": "gpt-image-2", "prompt": "self-authored test", "n": 1,
               "response_format": "b64_json"}
    assert providers.pucoding_image(payload)["data"][0]["b64_json"] == "sample"
    assert str(requests[0].url) == "https://pucoding.com/v1/images/generations"
    assert requests[0].headers["authorization"] == "Bearer pucoding-test"
    assert json.loads(requests[0].content) == payload
    assert options[0]["trust_env"] is False
    assert len(requests) == 1


@pytest.mark.parametrize("status", [400, 401, 402, 403, 422, 429, 503])
def test_rejected_media_submission_is_never_retried(providers, monkeypatch, status):
    requests = []
    original_client = httpx.Client

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "private-provider-details"}})

    monkeypatch.setattr("adaptive_learning.studio.httpx.Client", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises((ProviderRejected, httpx.HTTPStatusError)) as error:
        providers.pucoding_image({"prompt": "test"})
    assert len(requests) == 1
    assert "private-provider-details" not in str(error.value)


def test_media_timeout_is_not_resubmitted(providers, monkeypatch):
    requests = []
    original_client = httpx.Client

    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("test timeout", request=request)

    monkeypatch.setattr("adaptive_learning.studio.httpx.Client", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(httpx.ReadTimeout):
        providers.pucoding_image({"prompt": "test"})
    assert len(requests) == 1
