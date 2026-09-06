import httpx
import pytest

from adaptive_learning.llm.client import LLMConfig, LLMError, LLMTimeoutError, OpenAICompatibleClient


def test_upstream_timeout_is_identifiable(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("slow upstream")
    monkeypatch.setattr(httpx.Client, 'post', timeout)
    client = OpenAICompatibleClient(LLMConfig('https://relay.test/v1','test','grok-4.3',max_retries=0))
    with pytest.raises(LLMTimeoutError):
        client.structured(system='s',user='u')
    client.close()


@pytest.mark.parametrize('model', ['grok-4-fast', 'grok-4.3'])
def test_responses_native_protocol_and_usage(monkeypatch, model):
    calls = []
    def post(self, url, **kwargs):
        calls.append((url,kwargs))
        return httpx.Response(200,request=httpx.Request('POST',url),json={
            'status':'completed','output':[
                {'type':'reasoning','summary':[]},
                {'type':'message','content':[{'type':'output_text','text':'{"ok":true}'}]}],
            'usage':{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':30}}})
    monkeypatch.setattr(httpx.Client,'post',post)
    client=OpenAICompatibleClient(LLMConfig('https://relay.test/v1','test',model))
    assert client.structured(system='JSON only',user='question',images=[('image/png',b'png')])=={'ok':True}
    url, options=calls[0]
    assert url.endswith('/v1/responses')
    assert options['json']['model'] == model
    assert options['json']['store'] is False
    assert options['json']['reasoning']['effort']=='low'
    assert options['json']['input'][1]['content'][1]['type']=='input_image'
    assert client.usage_totals()=={'responses':1,'input_tokens':100,'output_tokens':10,'cached_input_tokens':30}
    client.close()


@pytest.mark.parametrize('payload',[
    {'status':'incomplete','output':[]},
    {'status':'completed','output':[]},
    {'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'no'}]}]},
])
def test_incomplete_responses_are_not_valid_json_results(monkeypatch,payload):
    monkeypatch.setattr(httpx.Client,'post',lambda self,url,**kwargs:
        httpx.Response(200,request=httpx.Request('POST',url),json=payload))
    client=OpenAICompatibleClient(LLMConfig('https://relay.test/v1','test','grok-4-fast',max_retries=0))
    with pytest.raises(LLMError):
        client.structured(system='s',user='u')
    client.close()
