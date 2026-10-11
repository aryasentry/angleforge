import asyncio
import json
import pytest
import httpx
from unittest.mock import AsyncMock
from angleforge import providers


def run(coro):return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    providers._CACHE.clear()
    for name in ('OPENROUTER_API_KEY','OPENCODE_API_KEY','ANGLEFORGE_AI_PROVIDER','ANGLEFORGE_AI_MODEL','ANGLEFORGE_AI_MAX_TOKENS','ANGLEFORGE_AI_RETRIES','ANGLEFORGE_AI_TOTAL_OUTPUT_TOKENS'):
        monkeypatch.delenv(name,raising=False)


def transport(monkeypatch, handler):
    client_class = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kwargs:client_class(transport=httpx.MockTransport(handler),**kwargs))


def catalog(monkeypatch,provider='openrouter',identifier='test/model',strict=True):
    model=providers._normalize(provider,{'id':identifier,'supported_parameters':['structured_outputs'] if strict else [],'architecture':{'input_modalities':['text']}})
    monkeypatch.setattr(providers,'list_models',AsyncMock(return_value={'models':[model]}))


def test_status_never_exposes_key(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','very-secret-key')
    status=providers.provider_status()
    assert status['configured']
    assert 'very-secret-key' not in json.dumps(status)


def test_no_key_fails_before_catalog(monkeypatch):
    discovery=AsyncMock()
    monkeypatch.setattr(providers,'list_models',discovery)
    with pytest.raises(providers.ProviderError,match='not configured'):
        run(providers.generate_plan('openrouter','x',[],{}))
    discovery.assert_not_called()


def test_discovery_no_authorization_and_capabilities(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','secret')
    def handler(request):
        assert 'authorization' not in request.headers
        return httpx.Response(200,json={'data':[{'id':'a','architecture':{'input_modalities':['text','image']},'supported_parameters':['tools','structured_outputs']}]})
    transport(monkeypatch,handler)
    result=run(providers.list_models('openrouter'))['models'][0]
    assert result['tool_calling'] and result['structured_outputs'] and 'image' in result['input_modalities']


def test_zen_muse_responses_payload(monkeypatch):
    monkeypatch.setenv('OPENCODE_API_KEY','secret')
    catalog(monkeypatch,'zen','muse-spark-1.3')
    def handler(request):
        assert request.url.path == '/zen/v1/responses'
        payload=json.loads(request.content)
        assert payload['max_output_tokens']==1200 and 'input' in payload and not payload['store']
        assert 'response_format' not in payload
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"label":"x","summary":"test","operations":[]}'}]}],'usage':{'input_tokens':23,'output_tokens':20,'untrusted_echo':'ignored-provider-text'}})
    transport(monkeypatch,handler)
    result=run(providers.generate_plan('zen','muse-spark-1.3',[{'role':'user','content':'x'}],{}))
    assert result['proposal']['operations']==[]
    assert result['usage']=={'input_tokens':23,'output_tokens':20}
    assert not result['structured_output_enforced']


def test_openrouter_strict_payload(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','secret')
    catalog(monkeypatch)
    def handler(request):
        payload=json.loads(request.content)
        assert request.url.path == '/api/v1/chat/completions'
        assert payload['provider']['require_parameters']
        assert payload['response_format']['json_schema']['strict']
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"label":"x","summary":"ok","operations":[]}'}}],'usage':{'prompt_tokens':12}})
    transport(monkeypatch,handler)
    assert run(providers.generate_plan('openrouter','test/model',[],{}))['structured_output_enforced']


def test_zen_chat_and_unknown_route(monkeypatch):
    monkeypatch.setenv('OPENCODE_API_KEY','secret')
    catalog(monkeypatch,'zen','big-pickle')
    def handler(request):
        assert request.url.path=='/zen/v1/chat/completions'
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
    transport(monkeypatch,handler)
    assert run(providers.generate_plan('zen','big-pickle',[],{}))['proposal']=={}
    catalog(monkeypatch,'zen','unknown-alpha-model')
    with pytest.raises(providers.ProviderError,match='not verified'):
        run(providers.generate_plan('zen','unknown-alpha-model',[],{}))


def test_error_body_never_echoes_secret(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','private-key-DO-NOT-ECHO-927')
    catalog(monkeypatch)
    transport(monkeypatch,lambda req:httpx.Response(401,text='private-key-DO-NOT-ECHO-927'))
    with pytest.raises(providers.ProviderError) as error:
        run(providers.generate_plan('openrouter','test/model',[],{}))
    assert 'private-key-DO-NOT-ECHO-927' not in str(error.value)


def test_retry_and_token_budget(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','secret')
    monkeypatch.setenv('ANGLEFORGE_AI_RETRIES','2')
    monkeypatch.setenv('ANGLEFORGE_AI_TOTAL_OUTPUT_TOKENS','1200')
    catalog(monkeypatch)
    requests=[]
    def handler(req):requests.append(req);return httpx.Response(429)
    transport(monkeypatch,handler)
    with pytest.raises(providers.ProviderError):run(providers.generate_plan('openrouter','test/model',[],{}))
    assert len(requests)==1
    with pytest.raises(providers.ProviderError,match='max_tokens'):
        run(providers.generate_plan('openrouter','test/model',[],{},max_tokens=50000))


def test_malformed_and_incomplete_fail(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','secret')
    catalog(monkeypatch)
    transport(monkeypatch,lambda req:httpx.Response(200,json={'choices':[{'message':{'content':'not JSON'},'finish_reason':'stop'}]}))
    with pytest.raises(providers.ProviderError,match='valid JSON'):
        run(providers.generate_plan('openrouter','test/model',[],{}))


def test_cancellation(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','secret')
    class Context:
        def check_cancelled(self):raise RuntimeError('canceled')
    with pytest.raises(RuntimeError,match='canceled'):
        run(providers.generate_plan('openrouter','test/model',[],{},ctx=Context()))


def test_provider_cannot_echo_auth_key_in_valid_output(monkeypatch):
    secret='credential-test-unique-927'
    monkeypatch.setenv('OPENROUTER_API_KEY',secret)
    catalog(monkeypatch)
    transport(monkeypatch,lambda req:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'label':'x','summary':secret,'operations':[]})},'finish_reason':'stop'}]}))
    with pytest.raises(providers.ProviderError,match='credential material') as error:
        run(providers.generate_plan('openrouter','test/model',[],{}))
    assert secret not in str(error.value)


def test_cancel_inflight_http_request(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','test-inflight-credential')
    catalog(monkeypatch)
    state={'entered':False,'canceled':False}
    async def handler(req):
        state['entered']=True
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            state['canceled']=True
            raise
        return httpx.Response(200,json={})
    transport(monkeypatch,handler)
    class Context:
        def check_cancelled(self):
            if state['entered']:raise RuntimeError('canceled while awaiting HTTP')
        def progress(self,*args):pass
    with pytest.raises(RuntimeError,match='awaiting HTTP'):
        run(providers.generate_plan('openrouter','test/model',[],{},ctx=Context()))
    assert state['entered'] and state['canceled']


def test_timeout_not_retried(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','test-timeout-credential')
    catalog(monkeypatch)
    requests=[]
    def handler(req):
        requests.append(req)
        raise httpx.ReadTimeout('may have been billed',request=req)
    transport(monkeypatch,handler)
    with pytest.raises(providers.ProviderError,match='not automatically resubmitted'):
        run(providers.generate_plan('openrouter','test/model',[],{}))
    assert len(requests)==1


def test_non_strict_model_uses_plain_json_without_unsupported_parameter(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','test-plain-credential')
    catalog(monkeypatch,strict=False)
    def handler(request):
        assert 'response_format' not in json.loads(request.content)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
    transport(monkeypatch,handler)
    assert not run(providers.generate_plan('openrouter','test/model',[],{}))['structured_output_enforced']


def test_catalog_image_capability_required_before_inference(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','test-image-credential')
    catalog(monkeypatch)
    messages=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,/9j/2Q=='}}]}]
    requests=[]
    transport(monkeypatch,lambda req:requests.append(req))
    with pytest.raises(providers.ProviderError,match='no verified image'):
        run(providers.generate_plan('openrouter','test/model',messages,{}))
    assert requests==[]


def test_image_capable_openrouter_payload_preserves_sparse_frames(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','test-image-credential')
    item=providers._normalize('openrouter',{'id':'test/image-model','architecture':{'input_modalities':['text','image']},'supported_parameters':['structured_outputs']})
    monkeypatch.setattr(providers,'list_models',AsyncMock(return_value={'models':[item]}))
    messages=[{'role':'user','content':[{'type':'text','text':'source A at 1s'},{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,/9j/2Q=='}}]}]
    def handler(req):
        payload=json.loads(req.content)
        assert payload['messages']==messages
        assert payload['provider']['require_parameters']
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
    transport(monkeypatch,handler)
    assert run(providers.generate_plan('openrouter','test/image-model',messages,{}))['proposal']=={}


def test_zen_frames_refused_even_if_caller_supplies_them(monkeypatch):
    monkeypatch.setenv('OPENCODE_API_KEY','test-zen-image-credential')
    catalog(monkeypatch,'zen','muse-spark-1.3')
    messages=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,/9j/2Q=='}}]}]
    with pytest.raises(providers.ProviderError,match='no verified image'):
        run(providers.generate_plan('zen','muse-spark-1.3',messages,{}))
