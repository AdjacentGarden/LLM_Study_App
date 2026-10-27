import base64
import io
import json

import httpx
import pytest
from PIL import Image
from test_community import community as community
from test_studio import media, run
from test_studio import setup as setup

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
    monkeypatch.delenv("MINIMAX_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("MINIMAX_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("STUDIO_IMAGE_PROVIDER", raising=False)
    monkeypatch.delenv("STUDIO_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("STUDIO_MEDIA_PAUSED_REASON", raising=False)
    monkeypatch.delenv("STUDIO_VIDEO_ENABLED", raising=False)
    monkeypatch.delenv("STUDIO_VIDEO_PAUSED_REASON", raising=False)
    monkeypatch.delenv("MINIMAX_VIDEO_BASE_URL", raising=False)
    monkeypatch.delenv("MINIMAX_VIDEO_MODEL", raising=False)
    monkeypatch.delenv("MINIMAX_VIDEO_RESOLUTION", raising=False)
    monkeypatch.delenv("LLM_HTTPS_PROXY", raising=False)
    get_settings.cache_clear()
    yield Studio.__new__(Studio)
    get_settings.cache_clear()


def test_explicit_pucoding_image_capability_does_not_require_minimax_key(providers, monkeypatch):
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "pucoding")
    capabilities = providers.capabilities()
    assert capabilities["image"]
    assert not capabilities["video"]
    assert capabilities["image_provider"] == "PUCODING"
    assert capabilities["image_model"] == "gpt-image-2"
    assert capabilities["video_model"] == "MiniMax-H3-Lite"
    assert capabilities["video_resolution"] == "480P"
    assert capabilities["video_estimate"] == 0.96
    assert capabilities["video_requires_image"] is False
    assert (capabilities["image_width"], capabilities["image_height"]) == (1024, 768)


def test_default_minimax_image_capability_uses_its_own_key(providers, monkeypatch):
    assert not providers.capabilities()["image"]
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    monkeypatch.setenv("PUCODING_API_KEY", "")
    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "off")
    get_settings.cache_clear()
    capabilities = providers.capabilities()
    assert capabilities["image"]
    assert capabilities["image_provider"] == "MiniMax"
    assert capabilities["image_model"] == "image-01"
    assert (capabilities["image_width"], capabilities["image_height"]) == (2048, 1536)


@pytest.mark.parametrize("disabled", ["false", "0", "off", "no"])
def test_video_can_be_paused_without_disabling_images_or_notes(providers, monkeypatch, disabled):
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    monkeypatch.setenv("STUDIO_FFMPEG", "fixture-ffmpeg")
    monkeypatch.setattr(providers, "voice_runtime_ready", lambda: False)
    assert providers.capabilities()["video"]
    monkeypatch.setenv("STUDIO_VIDEO_ENABLED", disabled)
    monkeypatch.setenv("STUDIO_VIDEO_PAUSED_REASON", "视频账户待充值")
    capabilities = providers.capabilities()
    assert not capabilities["video"]
    assert capabilities["image"] and capabilities["notes_ai"]
    assert capabilities["video_notice"] == "视频账户待充值"


def test_video_base_is_independent_of_text_base_and_retains_explicit_version(providers, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    monkeypatch.setenv("MINIMAX_VIDEO_BASE_URL", "https://api.minimax.cn/v1/")
    requests = []
    monkeypatch.setattr(providers, "media_request", lambda *args: requests.append(args) or {})
    assert providers.minimax("/v2/video_generation", {"model": "MiniMax-H3-Max"}) == {}
    assert requests == [("https://api.minimax.cn", "/v2/video_generation", "minimax-test",
                         {"model": "MiniMax-H3-Max"})]


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


def test_minimax_image_uses_documented_endpoint_key_payload_and_response(providers, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimax.cn/v1/")
    monkeypatch.setenv("MINIMAX_VIDEO_BASE_URL", "https://video.example.invalid/v1")
    requests = []
    original_client = httpx.Client

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"image_base64": ["sample"]},
                                         "base_resp": {"status_code": 0}})

    monkeypatch.setattr("adaptive_learning.studio.httpx.Client", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(handle), **kwargs))
    assert providers.generate_image("self-authored test", providers.image_identity()) == "sample"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.minimax.cn/v1/image_generation"
    assert requests[0].headers["authorization"] == "Bearer minimax-test"
    assert json.loads(requests[0].content) == {
        "model": "image-01", "prompt": "self-authored test", "width": 2048, "height": 1536,
        "response_format": "base64", "n": 1, "prompt_optimizer": False,
    }


@pytest.mark.parametrize("provider,missing_key", [("minimax", "MINIMAX_API_KEY"),
                                                 ("pucoding", "PUCODING_API_KEY")])
def test_missing_selected_image_key_never_falls_back(providers, monkeypatch, provider, missing_key):
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    monkeypatch.setenv("PUCODING_API_KEY", "pucoding-test")
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", provider)
    monkeypatch.setenv(missing_key, "")
    get_settings.cache_clear()
    monkeypatch.setattr(providers, "media_request", lambda *args: pytest.fail("no paid submission"))
    assert not providers.capabilities()["image"]
    with pytest.raises(ValueError, match="not configured"):
        providers.generate_image("self-authored test", providers.image_identity())


@pytest.mark.parametrize("response", [{}, {"data": []}, {"data": {"image_base64": "wrong"}},
                                    {"data": {"image_base64": []}},
                                    {"data": {"image_base64": ["a", "b"]}}])
def test_invalid_minimax_image_response_is_not_retried(providers, monkeypatch, response):
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    calls = []
    monkeypatch.setattr(providers, "minimax", lambda *args: calls.append(args) or response)
    with pytest.raises(ValueError, match="invalid MiniMax image response"):
        providers.generate_image("self-authored test", providers.image_identity())
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["rejected", "timeout"])
def test_minimax_image_rejection_or_timeout_never_resubmits_or_falls_back(providers, monkeypatch, failure):
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    requests = []
    original_client = httpx.Client

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        return httpx.Response(200, json={"base_resp": {"status_code": 1008,
                                                     "status_msg": "private-provider-details"}})

    monkeypatch.setattr("adaptive_learning.studio.httpx.Client", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises((ProviderRejected, httpx.ReadTimeout)) as error:
        providers.generate_image("self-authored test", providers.image_identity())
    assert len(requests) == 1
    assert "private-provider-details" not in str(error.value)


def test_minimax_prompt_limit_rejects_without_truncation_or_paid_submission(providers, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    monkeypatch.setattr(providers, "minimax", lambda *args: pytest.fail("no paid submission"))
    with pytest.raises(ValueError, match="1500-character"):
        providers.generate_image("a" * 1501, providers.image_identity())


def test_real_image_job_records_minimax_and_keeps_queued_provider_snapshot(setup, monkeypatch):
    a, _, studio = setup
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "off")
    monkeypatch.setattr(studio, "evidence", lambda *args: [{"page": 1, "text": "函数调用自身。"}])
    monkeypatch.setattr(studio, "llm", lambda *args: {
        "title": "递归示意", "visual_scope": "观察有限的嵌套结构", "visual_checks": ["嵌套"],
        "explanation": "函数调用自身，需要终止条件。", "points": ["有限嵌套"],
        "visual_prompt": "Nested colorful boxes on a plain background.", "caution": "辅助示意。",
        "supported": True, "video_suitable": False,
    })
    raw = io.BytesIO()
    Image.new("RGB", (2048, 1536), "#68a4cf").save(raw, "PNG")
    calls = []
    monkeypatch.setattr(studio, "minimax", lambda *args: calls.append(args) or {
        "data": {"image_base64": [base64.b64encode(raw.getvalue()).decode()]},
        "base_resp": {"status_code": 0},
    })
    monkeypatch.setattr(studio, "pucoding_image", lambda *args: pytest.fail("no provider fallback"))
    key = a.post("/api/studio/media", json=media()).json()["id"]
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "pucoding")
    monkeypatch.setenv("MINIMAX_IMAGE_WIDTH", "1024")
    monkeypatch.setenv("MINIMAX_IMAGE_HEIGHT", "768")
    run(studio, a, key)
    run(studio, a, key)
    completed = a.get(f"/api/studio/jobs/{key}").json()
    assert completed["status"] == "succeeded"
    assert completed["result"]["provider"] == "MiniMax"
    assert completed["result"]["model"] == "image-01"
    assert len(calls) == 1 and calls[0][0] == "/v1/image_generation"
    assert calls[0][1]["width"] == 2048 and calls[0][1]["height"] == 1536
    assert "aspect_ratio" not in calls[0][1]
    asset = a.get(completed["asset_url"])
    assert asset.status_code == 200
    with Image.open(io.BytesIO(asset.content)) as image:
        assert image.format == "JPEG" and image.size == (2048, 1536)


def test_new_image_provider_does_not_reuse_previous_provider_job(setup, monkeypatch):
    a, _, _ = setup
    old = a.post("/api/studio/media", json=media()).json()
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "minimax")
    new = a.post("/api/studio/media", json=media(request_id="minimax_request")).json()
    assert new["id"] != old["id"]
    # The exact acknowledged request still resolves to its original task.
    assert a.post("/api/studio/media", json=media()).json()["id"] == old["id"]
