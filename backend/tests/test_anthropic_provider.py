import json
import httpx
import pytest

from app.analysis.schemas import GatewayProfile
from app.analysis.gateway import Gateway, GatewayFailure
from app.analysis.service import _profile
from app.config import Settings


def test_direct_anthropic_sdk_preserves_schema_usage_and_never_calls_gateway():
    calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(200,json={'id':'msg-fixture','type':'message','role':'assistant','model':'claude-haiku-5-5',
            'content':[{'type':'text','text':json.dumps({'requirements':[],'facts':[],'findings':[]})}],
            'stop_reason':'end_turn','stop_sequence':None,'usage':{'input_tokens':30,'output_tokens':4}})
    profile=GatewayProfile(provider='anthropic',base_url='https://api.anthropic.com',model='claude-haiku-5-5',protocol='anthropic-messages-json-v1')
    result,usage=Gateway(profile,'fixture-anthropic-key',http_client=httpx.Client(transport=httpx.MockTransport(respond))).extract('synthetic','tender')
    assert result.requirements==[] and usage=={'input_tokens':30,'output_tokens':4}
    assert len(calls)==1 and str(calls[0].url)=='https://api.anthropic.com/v1/messages'
    assert calls[0].headers['x-api-key']=='fixture-anthropic-key'
    assert 'authorization' not in calls[0].headers
    body=json.loads(calls[0].content)
    assert body['model']=='claude-haiku-5-5' and 'schema' in body['system']
    assert body['messages']==[{'role':'user','content':'synthetic'}]
    assert 'response_format' not in body and 'temperature' not in body


@pytest.mark.parametrize('case,code',[('status','provider_status'),('rate','rate_limit'),('timeout','timeout'),('truncated','truncation'),('json','invalid_json'),('schema','invalid_schema'),('refusal','refusal')])
def test_anthropic_failures_are_bounded_and_local_schema_remains_strict(case,code):
    calls=[]
    def respond(request):
        calls.append(str(request.url))
        if case=='timeout':raise httpx.ReadTimeout('fixture',request=request)
        if case in {'status','rate'}:return httpx.Response(401 if case=='status' else 429,json={'type':'error','error':{'type':'authentication_error','message':'fixture'}})
        content='not-json' if case=='json' else json.dumps({'requirements':[],'facts':[],'findings':[],**({'evil':True} if case=='schema' else {})})
        return httpx.Response(200,json={'id':'fixture','type':'message','role':'assistant','model':'claude-haiku-5-5','content':[{'type':'text','text':content}],
            'stop_reason':'max_tokens' if case=='truncated' else 'refusal' if case=='refusal' else 'end_turn','stop_sequence':None,'usage':{'input_tokens':30,'output_tokens':4}})
    profile=GatewayProfile(provider='anthropic',base_url='https://api.anthropic.com',model='claude-haiku-5-5',protocol='anthropic-messages-json-v1')
    with pytest.raises(GatewayFailure) as error:Gateway(profile,'fixture',http_client=httpx.Client(transport=httpx.MockTransport(respond))).extract('test','tender')
    assert error.value.code==code and calls==['https://api.anthropic.com/v1/messages']


def test_full_runtime_defaults_to_direct_claude_and_keeps_credential_out_of_profile():
    settings=Settings(environment='test',database_url='sqlite://',session_secret='x'*40,sms_provider='mock',anthropic_api_key='private-fixture-key')
    profile=_profile(settings)
    assert profile.provider=='anthropic' and profile.model=='claude-haiku-5-5'
    assert profile.base_url=='https://api.anthropic.com' and profile.protocol=='anthropic-messages-json-v1'
    assert 'private-fixture-key' not in profile.model_dump_json()


def test_anthropic_key_cannot_be_forwarded_to_a_custom_gateway_host():
    with pytest.raises(ValueError):GatewayProfile(provider='anthropic',base_url='https://agentrouter.org/v1',protocol='anthropic-messages-json-v1')
