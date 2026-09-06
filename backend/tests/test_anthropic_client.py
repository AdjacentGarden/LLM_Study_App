import base64

import httpx
import pytest

from adaptive_learning.llm.client import LLMConfig, LLMError, OpenAICompatibleClient


def test_anthropic_text_and_image_use_native_endpoint(monkeypatch):
    calls = []
    def post(self, url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'content':[{'type':'thinking','thinking':'private'},
                       {'type':'text','text':'{"ok":true}'}], 'stop_reason':'end_turn'})
    monkeypatch.setattr(httpx.Client, 'post', post)
    client = OpenAICompatibleClient(LLMConfig('https://relay.test/v1', 'secret', 'claude-haiku-test'))
    assert client.structured(system='check', user='page', images=[('image/png', b'png')]) == {'ok':True}
    url, kwargs = calls[0]
    assert url == 'https://relay.test/v1/messages'
    assert kwargs['headers']['anthropic-version'] == '2023-06-01'
    assert 'response_format' not in kwargs['json']
    parts = kwargs['json']['messages'][0]['content']
    assert base64.b64decode(parts[0]['source']['data']) == b'png'
    assert parts[1] == {'type':'text','text':'page'}
    client.close()


@pytest.mark.parametrize('payload', [
    {'content':[], 'stop_reason':'end_turn'},
    {'content':[{'type':'text','text':'{}'}], 'stop_reason':'max_tokens'},
    {'content':[{'type':'text','text':'{}'}], 'stop_reason':'refusal'},
    {'content':[{'type':'text','text':'not JSON'}], 'stop_reason':'end_turn'},
])
def test_anthropic_invalid_output_is_not_accepted(monkeypatch, payload):
    def post(self, url, **kwargs):
        return httpx.Response(200, request=httpx.Request('POST', url), json=payload)
    monkeypatch.setattr(httpx.Client, 'post', post)
    client = OpenAICompatibleClient(LLMConfig('https://relay.test/v1', 'secret', 'claude-haiku-test', max_retries=0))
    with pytest.raises(LLMError):
        client.structured(system='check', user='page')
    client.close()
