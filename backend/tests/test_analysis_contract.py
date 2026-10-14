import json
import httpx
import pytest
from pydantic import ValidationError


def modules():
    try:
        from app.analysis.schemas import Extraction, GatewayProfile, RunBudget
        from app.analysis.evidence import bind_blocks, resolve_citation, chunk_blocks
        from app.analysis.gateway import Gateway, GatewayFailure
        from app.analysis.budgets import BudgetLedger, BudgetExceeded
    except ImportError:
        pytest.fail('Grounded analysis contract is not implemented')
    return locals()


def test_server_evidence_rejects_metadata_fabrication_and_wrong_quote():
    m=modules()
    blocks=m['bind_blocks']('a', 'd', 'original.txt', [{'id':'local:1','original_text':'Power  25\n W','anchor':{'kind':'text','line_start':1},'review_flags':['formula_review']}])
    citation=m['resolve_citation']({'evidence_id':blocks[0]['evidence_id'],'quote':'Power 25 W'},blocks)
    assert citation['artifact_id']=='a' and citation['filename']=='original.txt'
    assert citation['quote']=='Power  25\n W' and citation['review_flags']==['formula_review']
    with pytest.raises(ValueError): m['resolve_citation']({'evidence_id':'other','quote':'Power'},blocks)
    with pytest.raises(ValueError): m['resolve_citation']({'evidence_id':blocks[0]['evidence_id'],'quote':'Power 99 W'},blocks)
    with pytest.raises(ValueError): m['Extraction'].model_validate({'requirements':[],'facts':[],'findings':[],'object_key':'evil'})


def test_chunking_retains_oversized_original_offsets_and_ocr_provenance():
    m=modules()
    blocks=m['bind_blocks']('a','d','scan.png',[{'id':'ocr:1','transcription_text':'abcdefghi','confidence':70.0,'anchor':{'kind':'image'},'review_flags':['ocr_review']}])
    chunks=m['chunk_blocks'](blocks,5)
    assert ''.join(b['text'] for chunk in chunks for b in chunk)=='abcdefghi'
    assert [(b['source_start'],b['source_end']) for chunk in chunks for b in chunk]==[(0,5),(5,9)]
    cited=m['resolve_citation']({'evidence_id':chunks[1][0]['evidence_id'],'quote':'ghi'},[b for c in chunks for b in c])
    assert cited['quote_kind']=='ocr_transcription' and cited['confidence']==70.0


def test_budget_unknown_usage_spends_reservation_and_cannot_overrun():
    m=modules(); ledger=m['BudgetLedger'](m['RunBudget'](max_requests=1,max_tokens=50,cost_ceiling='1'))
    reservation=ledger.reserve(20,30)
    ledger.reconcile(reservation,None)
    assert ledger.tokens_spent==50 and ledger.usage_unknown
    with pytest.raises(m['BudgetExceeded']): ledger.reserve(1,1)


def test_real_sdk_json_mode_strict_schema_no_fallback():
    m=modules(); requests=[]
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'x','object':'chat.completion','created':1,'model':'fixture','choices':[{'index':0,'message':{'role':'assistant','content':json.dumps({'requirements':[],'facts':[],'findings':[]})},'finish_reason':'stop'}], 'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13}})
    profile=m['GatewayProfile'](base_url='https://agentrouter.org/v1',model='fixture')
    gateway=m['Gateway'](profile,'fixture-key',http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    result,usage=gateway.extract('synthetic', 'tender')
    assert result.requirements==[] and usage=={'input_tokens':10,'output_tokens':3}
    assert requests[0]['response_format']=={'type':'json_object'} and 'schema' in requests[0]['messages'][0]['content']


@pytest.mark.parametrize('body,code',[(None,'invalid_json'),({'requirements':[],'facts':[],'findings':[],'evil':True},'invalid_schema')])
def test_sdk_invalid_json_and_schema_are_safe(body,code):
    m=modules()
    def respond(request):
        return httpx.Response(200,json={'id':'x','created':1,'model':'fixture','object':'chat.completion','choices':[{'index':0,'message':{'role':'assistant','content':'bad' if body is None else json.dumps(body)},'finish_reason':'stop'}]})
    gateway=m['Gateway'](m['GatewayProfile'](), 'fixture', http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(m['GatewayFailure']) as error: gateway.extract('test','tender')
    assert error.value.code==code


@pytest.mark.parametrize('case,code',[('refusal','refusal'),('length','truncation'),('status','provider_status'),('rate','rate_limit'),('timeout','timeout')])
def test_real_sdk_bounded_failure_categories_no_alternate_provider(case,code):
    m=modules();calls=[]
    def respond(request):
        calls.append(str(request.url))
        if case=='timeout':raise httpx.ReadTimeout('fixture timeout',request=request)
        if case in {'status','rate'}:return httpx.Response(401 if case=='status' else 429,json={'error':{'message':'fixture'}})
        return httpx.Response(200,json={'id':'x','created':1,'model':'fixture','object':'chat.completion','choices':[{'index':0,'message':{'role':'assistant','content':'{}','refusal':'refused' if case=='refusal' else None},'finish_reason':'length' if case=='length' else 'stop'}]})
    gateway=m['Gateway'](m['GatewayProfile'](),'synthetic',http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(m['GatewayFailure']) as error:gateway.extract('synthetic','tender')
    assert error.value.code==code and calls==['https://agentrouter.org/v1/chat/completions']
    if case in {'status','rate'}:assert error.value.status_code==(401 if case=='status' else 429)


def test_decimal_predicates_reject_numeric_coercion_and_nonfinite_values():
    from app.analysis.schemas import Numeric
    for value in [25,'NaN','Infinity','25e0']:
        with pytest.raises(ValueError):Numeric(kind='numeric',operator='gte',value=value,unit='W')
    with pytest.raises(ValueError):Numeric(kind='numeric',operator='between',value='25',upper='24',unit='W')


def test_budget_enforces_actual_cost_ceiling_and_retains_bad_usage():
    m=modules();profile=m['GatewayProfile'](input_price_per_million='1000',output_price_per_million='1000',pricing_version='fixture-v1')
    ledger=m['BudgetLedger'](m['RunBudget'](max_tokens=1000,cost_ceiling='0.1'),profile)
    reservation=ledger.reserve(50,50)
    ledger.reconcile(reservation,{'input_tokens':999,'output_tokens':1})
    assert ledger.tokens_spent==100 and ledger.usage_unknown
    with pytest.raises(m['BudgetExceeded']):ledger.reserve(1,1)


def test_full_package_reference_index_finds_target_across_chunks_and_retains_ambiguity():
    try:from app.analysis.evidence import reference_links
    except ImportError:pytest.fail('Whole-package reference lookup is missing')
    from app.analysis.evidence import bind_blocks
    blocks=bind_blocks('a','d','tender.txt',[{'id':'1','original_text':'Power per section 9.','anchor':{'kind':'text'},'review_flags':[]},{'id':'2','original_text':'9. Power must be 25 W.','anchor':{'kind':'text'},'review_flags':[]}])
    links=reference_links(blocks)
    assert len(links)==1 and links[0]['target_ids']==[blocks[1]['evidence_id']] and links[0]['state']=='resolved'
    blocks+=bind_blocks('b','e','other.txt',[{'id':'3','original_text':'9. Power must be 30 W.','anchor':{'kind':'text'},'review_flags':[]}])
    links=reference_links(blocks)
    assert links[0]['state']=='ambiguous' and len(links[0]['target_ids'])==2


def test_reference_plan_indexes_multiline_sections_and_all_continuation_blocks():
    from app.analysis.evidence import bind_blocks, reference_plan
    blocks=bind_blocks('a','d','tender.txt',[
        {'id':'1','original_text':'Power per section 9.','anchor':{'kind':'text'},'review_flags':[]},
        {'id':'2','original_text':'Introduction\n9. Power specification\nMinimum 25 W.','anchor':{'kind':'text'},'review_flags':[]},
        {'id':'3','original_text':'Certificate required.\n10. Delivery\nWithin 5 days.','anchor':{'kind':'text'},'review_flags':[]}])
    links, targets=reference_plan(blocks)
    assert len(links)==1 and links[0]['state']=='resolved'
    target=targets[links[0]['target_ids'][0]]
    assert ''.join(b['text'] for b in target)=='9. Power specification\nMinimum 25 W.Certificate required.\n'
    assert target[0]['source_start']==len('Introduction\n')
    assert target[1]['source_end']==len('Certificate required.\n')
    assert all(b['document_id']=='d' for b in target)


def test_grounded_findings_mark_affected_requirements_for_review():
    from app.analysis.service import AnalysisService
    from app.analysis.evidence import bind_blocks
    from app.analysis.schemas import Extraction
    blocks=bind_blocks('a','d','tender.txt',[{'id':'1','original_text':'Acme only.','anchor':{'kind':'text'},'review_flags':[]}])
    citation={'evidence_id':blocks[0]['evidence_id'],'quote':'Acme only.'}
    extraction=Extraction.model_validate({'requirements':[{'scope':'product','category':'technical','parameter':'brand','condition':'Acme only','importance':'mandatory','predicate':{'kind':'semantic','text':'Acme only'},'citations':[citation]}],'facts':[],'findings':[{'kind':'restrictive_language','detail':'Only one brand permitted','citations':[citation]}]})
    service=object.__new__(AnalysisService)
    bound=service._bound(extraction,blocks)
    combined=service._combine([bound],{'coverage':[],'source_policy':'declaration-is-not-proof-v1'})
    assert 'restrictive_language_review' in combined['requirements'][0]['review_flags']
    assert 'restrictive_language_review' in combined['findings'][0]['review_flags']


def test_date_timezone_requires_iana_and_provider_context_carries_frozen_zone():
    from app.analysis.schemas import DatePredicate
    from app.analysis.service import _content
    for timezone in ('invented/zone','UTC+5',''):
        with pytest.raises(ValueError):DatePredicate(kind='date',operator='by',value='2026-10-15',basis='tender_deadline',timezone=timezone)
    content=json.loads(_content([], 'tender','ru','2026-10-15T17:00:00+05:00',timezone='Asia/Tashkent'))
    assert content['timezone']=='Asia/Tashkent'


@pytest.mark.parametrize('timezone,value,deadline',[('UTC','2026-10-15','2026-10-15T17:00:00+05:00'),('Asia/Tashkent','2026-10-16','2026-10-15T17:00:00+05:00'),('Asia/Tashkent','2026-10-15',None)])
def test_deadline_predicate_cannot_guess_a_different_frozen_basis(timezone,value,deadline):
    from app.analysis.service import AnalysisService
    from app.analysis.evidence import bind_blocks
    from app.analysis.schemas import Extraction
    blocks=bind_blocks('a','d','tender.txt',[{'id':'1','original_text':'Valid until tender deadline.','anchor':{'kind':'text'},'review_flags':[]}])
    citation={'evidence_id':blocks[0]['evidence_id'],'quote':blocks[0]['text']}
    extracted=Extraction.model_validate({'requirements':[{'scope':'participant','category':'validity','parameter':'validity','condition':'Valid until tender deadline','importance':'mandatory','predicate':{'kind':'date','operator':'by','value':value,'basis':'tender_deadline','timezone':timezone},'citations':[citation]}],'facts':[],'findings':[]})
    service=object.__new__(AnalysisService)
    bound=service._bound(extracted,blocks,{'timezone':'Asia/Tashkent','deadline':deadline})
    assert bound['requirements'][0]['predicate']['kind']=='semantic'
    assert 'deadline_basis_review' in bound['requirements'][0]['review_flags']


def reference_fixture(mentions=30,size=20000,attempts=2):
    from app.analysis.service import AnalysisService
    from app.analysis.schemas import GatewayProfile,RunBudget
    from app.analysis.evidence import bind_blocks
    blocks=bind_blocks('a','d','tender.txt',[
        {'id':'1','original_text':('Power per section 9.\n'*mentions),'anchor':{'kind':'text'},'review_flags':[]},
        {'id':'2','original_text':'9. Specification\n'+('e'*size)+'\nCertification required.','anchor':{'kind':'text'},'review_flags':[]}])
    frozen={'kind':'tender','language':'ru','deadline':None,'timezone':'Asia/Tashkent',
        'profile':GatewayProfile(max_attempts=attempts).model_dump(mode='json'),'budget':RunBudget(max_requests=1).model_dump(mode='json')}
    return object.__new__(AnalysisService),frozen,blocks


def test_reference_requests_are_lazy_and_materialize_only_consumed_content(monkeypatch):
    import app.analysis.service as module
    service,frozen,blocks=reference_fixture()
    original=module._content;produced=[]
    def content(*args,**kwargs):
        if len(args)>4 and args[4]:produced.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'_content',content)
    links,requests=service._references(frozen,blocks)
    assert iter(requests) is requests and produced==[] and len(links)==30
    first=next(requests)
    assert first.step=='reference:0:0' and len(produced)==1
    assert 'section 9' in first.blocks[0]['text']


def test_planning_stops_at_supported_attempt_cap_without_materializing_crossproduct(monkeypatch):
    import app.analysis.service as module
    from app.workspace.repository import ValidationError
    service,frozen,blocks=reference_fixture(mentions=1000)
    original=module._content;produced=[]
    def content(*args,**kwargs):
        if len(args)>4 and args[4]:produced.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'_content',content)
    with pytest.raises(ValidationError,match='planning.*attempt'):
        service._planning(frozen,blocks)
    assert len(produced)<=251


def test_streaming_planning_counts_every_tail_fragment_within_supported_limits():
    service,frozen,blocks=reference_fixture(mentions=2,size=8000,attempts=1)
    plan=service._planning(frozen,blocks)
    links,requests=service._references(frozen,blocks)
    visited=0;tail=0
    for request in requests:
        visited+=1
        if any('Certification required.' in b['text'] for b in request.blocks[1:]):tail+=1
    assert visited==plan.reference_count and tail==len(links)==2
    assert plan.max_provider_attempts==plan.request_count and plan.worst_case_tokens>0


def test_cache_binding_index_contains_only_needed_unique_fragments_without_request_json(monkeypatch):
    import app.analysis.service as module
    service,frozen,blocks=reference_fixture(mentions=2,size=8000,attempts=1)
    links,requests=service._references(frozen,blocks)
    first=next(requests);needed={b['evidence_id'] for b in first.blocks}
    original=module._content;produced=[]
    def content(*args,**kwargs):
        if len(args)>4 and args[4]:produced.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'_content',content)
    evidence=service._cache_evidence(frozen,blocks,needed)
    assert set(evidence)==needed and produced==[]
    assert all(evidence[b['evidence_id']]['text']==b['text'] for b in first.blocks)


def test_planning_stops_at_twenty_million_token_cap_before_provider_attempt_cap():
    from app.analysis.schemas import GatewayProfile
    from app.workspace.repository import ValidationError
    service,frozen,blocks=reference_fixture(mentions=0,size=5_000_000,attempts=3)
    frozen['profile']=GatewayProfile(context_tokens=1_000_000,output_tokens=32768,max_attempts=3).model_dump(mode='json')
    with pytest.raises(ValidationError,match='planning token limit.*20000000'):
        service._planning(frozen,blocks)


def test_aggregate_requirement_limit_matches_editable_checklist_schema():
    from app.analysis.service import AnalysisService
    from app.full_workspace.schemas import ChecklistCreate
    from app.workspace.repository import ValidationError
    service=object.__new__(AnalysisService)
    frozen={'coverage':[],'source_policy':'declaration-is-not-proof-v1'}
    rows=[{'scope':'product','item':None,'category':'technical','parameter':str(i),'condition':'one',
        'importance':'mandatory','predicate':{'kind':'semantic','text':'one'},'citations':[],'review_flags':[]} for i in range(2001)]
    with pytest.raises(ValidationError,match='2000'):
        service._combine([{'requirements':rows[:1500],'facts':[],'findings':[]},{'requirements':rows[1500:],'facts':[],'findings':[]}],frozen)
    editable=[{key:value for key,value in row.items() if key!='review_flags'}|{'display_id':'T-'+str(i+1)} for i,row in enumerate(rows[:2000])]
    assert len(ChecklistCreate(requirements=editable).requirements)==2000
