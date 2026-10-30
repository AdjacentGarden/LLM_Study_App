"""Recover received string line breaks without another provider request."""
from __future__ import annotations

import json

import httpx
import pytest

from adaptive_learning.llm.client import (
    LLMConfig,
    LLMError,
    LLMRefusalError,
    OpenAICompatibleClient,
)


@pytest.mark.parametrize(('content', 'expected'), [
    ('{"answer":"第一段\n第二段"}', {'answer': '第一段\n第二段'}),
    ('{"answer":"合力 = ma\t各量须对应同一物体。"}', {'answer': '合力 = ma\t各量须对应同一物体。'}),
    ('{"answer":"正常正文","nested":{"items":["第一行\n第二行","制表\t说明"]}}',
     {'answer': '正常正文', 'nested': {'items': ['第一行\n第二行', '制表\t说明']}}),
    (r'{"answer":"已转义\n换行\t制表","nested":{"ok":true}}',
     {'answer': '已转义\n换行\t制表', 'nested': {'ok': True}}),
])
def test_received_newline_tab_or_escaped_json_roundtrips_in_one_http_call(content, expected):
    calls = []
    def provider(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body['response_format'] == {'type': 'json_object'}
        assert body['thinking'] == {'type': 'disabled'}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})

    client = OpenAICompatibleClient(LLMConfig('https://api.deepseek.com/v1', 'test-key',
                                            'deepseek-flash', max_retries=0))
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(provider))
    try:
        result = client.structured(system='Return JSON.', user='解释概念')
        assert result == expected
        assert len(calls) == 1 and calls[0].method == 'POST'
        assert calls[0].url.path == '/v1/chat/completions'
    finally:
        client.close()


@pytest.mark.parametrize('content', [
    '{"answer":"引号未闭合}',
    '{"answer":"先换行\n仍未闭合}',
    '{"answer":"正常"} trailing text',
    '["非对象\n即使可解析"]',
    '{"answer":"正常"\n 缺少逗号}',
])
def test_other_bad_json_or_nonobject_still_fails_without_a_replayed_post(content):
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})

    client = OpenAICompatibleClient(LLMConfig('https://api.deepseek.com/v1', 'test-key',
                                            'deepseek-flash', max_retries=0))
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(provider))
    try:
        with pytest.raises(LLMError):
            client.structured(system='Return JSON.', user='解释概念')
        assert len(calls) == 1
    finally:
        client.close()


def test_explicit_provider_refusal_precedes_json_normalization():
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {
            'content': '{"answer":"不能返回的正文\n下一行"}', 'refusal': 'provider declined',
        }}]})
    client = OpenAICompatibleClient(LLMConfig('https://api.deepseek.com/v1', 'test-key',
                                            'deepseek-flash', max_retries=0))
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(provider))
    try:
        with pytest.raises(LLMRefusalError):
            client.structured(system='Return JSON.', user='解释概念')
        assert len(calls) == 1
    finally:
        client.close()
