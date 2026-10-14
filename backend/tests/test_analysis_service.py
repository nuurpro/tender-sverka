"""Authenticated normalized-source to draft publication acceptance."""
import pytest
from sqlalchemy import select,func
from test_package_api import packages,upload
from test_full_auth_api import full_api,auth_headers
from test_document_artifacts import processing,execute,package
from app.workspace.database import tenant_transaction
from app.workspace.models import Job,Requirement,ChecklistVersion,AuditEvent,Membership
from app.jobs.service import JobService
from app.jobs.worker import ExecutionContext
from app.workspace.repository import ValidationError,Conflict


def setup(packages):
    try:
        from app.analysis.service import AnalysisService,update_policy
        from app.analysis.schemas import Extraction
    except ImportError:pytest.fail('Durable grounded extraction service is not implemented')
    documents,actor,_=processing(packages)
    client,engine,app,store,org,project=packages
    original=package(packages,data=b'Minimum power 25 W.\n')
    normalization=documents.start(org,actor,project,original['id'],'normalize',{})
    execute(documents,org,normalization)
    calls=[]
    class Synthetic:
        def extract(self,content,kind):
            import json
            data=json.loads(content);block=data['blocks'][0];calls.append(block)
            return Extraction.model_validate({'requirements':[{'scope':'product','category':'technical','parameter':'power','condition':'at least 25 W','importance':'mandatory','predicate':{'kind':'numeric','operator':'gte','value':'25','unit':'W'},'citations':[{'evidence_id':block['evidence_id'],'quote':'Minimum power 25 W.'}]}],'facts':[],'findings':[]}),{'input_tokens':100,'output_tokens':100}
    service=AnalysisService(engine,app.state.settings.model_copy(update={'analysis_provider':'agentrouter'}),documents=documents,gateway=Synthetic())
    options={'normalization_job_id':normalization['job']['id'],'normalization_attempt':1}
    return service,actor,original,options,calls


def policy(packages,actor):
    from app.analysis.service import update_policy
    from app.workspace.repository import Repository
    client,engine,app,store,org,project=packages
    with tenant_transaction(engine,org) as db:
        update_policy(Repository(db,org,actor),True,0,'synthetic test consent')


def test_policy_defaults_denied_preflight_then_idempotent_fenced_publication(packages):
    service,actor,original,options,calls=setup(packages)
    client,engine,app,store,org,project=packages
    preview=service.preflight(org,actor,project,original['id'],options)
    assert preview['external_ai_allowed'] is False and preview['input_token_upper_bound']>0
    with pytest.raises(ValidationError):service.start(org,actor,project,original['id'],'extract',options)
    policy(packages,actor)
    started=service.start(org,actor,project,original['id'],'extract',options)
    assert service.start(org,actor,project,original['id'],'extract',options)==started
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    service.handle(ExecutionContext(jobs,started['job']['id'],claim));jobs.finish(started['job']['id'],claim.token)
    result=service.detail(org,actor,project,started['run']['id'])
    assert result['run']['status']=='published' and len(calls)==1
    checklist=client.get(f'/api/organizations/{org}/projects/{project}/checklists/'+started['run']['checklist_id']).json()
    requirement=checklist['requirements'][0]
    assert requirement['display_id']=='T-1' and requirement['citations'][0]['filename']=='a.txt'
    assert requirement['citations'][0]['quote']=='Minimum power 25 W.'
    assert requirement['origin']['kind']=='document'
    assert client.post(f'/api/organizations/{org}/projects/{project}/checklists/'+checklist['id']+'/approve',json={'expected_revision':checklist['revision']},headers=auth_headers(client)).status_code==200


def test_concurrent_draft_edit_cannot_be_overwritten(packages):
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    client,engine,app,store,org,project=packages
    started=service.start(org,actor,project,original['id'],'extract',options)
    with tenant_transaction(engine,org) as db:
        db.get(ChecklistVersion,started['run']['checklist_id']).revision+=1
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    with pytest.raises(Conflict):service.handle(ExecutionContext(jobs,started['job']['id'],claim))
    with tenant_transaction(engine,org) as db:
        assert not list(db.scalars(select(Requirement)))


def test_revoked_policy_blocks_provider_before_first_call(packages):
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    client,engine,app,store,org,project=packages
    started=service.start(org,actor,project,original['id'],'extract',options)
    from app.analysis.service import update_policy
    from app.workspace.repository import Repository
    with tenant_transaction(engine,org) as db:update_policy(Repository(db,org,actor),False,1,'revoked')
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    with pytest.raises(ValidationError):service.handle(ExecutionContext(jobs,started['job']['id'],claim))
    assert calls==[]


def test_invalid_citation_never_publishes_approvable_requirement(packages):
    from app.analysis.schemas import Extraction
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    client,engine,app,store,org,project=packages
    class Bad:
        def extract(self,content,kind):
            return Extraction.model_validate({'requirements':[{'scope':'product','category':'technical','parameter':'power','condition':'999 W','importance':'mandatory','predicate':{'kind':'numeric','operator':'gte','value':'999','unit':'W'},'citations':[{'evidence_id':'made-up','quote':'999 W'}]}],'facts':[],'findings':[]}),None
    service.gateway=Bad()
    started=service.start(org,actor,project,original['id'],'bad',options)
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    service.handle(ExecutionContext(jobs,started['job']['id'],claim));jobs.finish(started['job']['id'],claim.token)
    base=f'/api/organizations/{org}/projects/{project}/checklists/'+started['run']['checklist_id']
    checklist=client.get(base).json()
    assert checklist['requirements']==[]
    assert client.post(base+'/approve',json={'expected_revision':checklist['revision']},headers=auth_headers(client)).status_code==422
    assert service.detail(org,actor,project,started['run']['id'])['run']['result']['findings'][0]['kind']=='invalid_evidence'


def test_analysis_manual_additions_require_reason_and_cannot_invent_citations(packages):
    from test_full_workspace_api import requirement
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    client,engine,app,store,org,project=packages
    started=service.start(org,actor,project,original['id'],'manual',options)
    base=f'/api/organizations/{org}/projects/{project}/checklists/'+started['run']['checklist_id']
    row=requirement()
    assert client.put(base,json={'expected_revision':1,'requirements':[row]},headers=auth_headers(client)).status_code==422
    row['citations']=[{'quote':'fake','validated':True}]
    assert client.put(base,json={'expected_revision':1,'reason':'manual add','requirements':[row]},headers=auth_headers(client)).status_code==422
    row['citations']=[]
    saved=client.put(base,json={'expected_revision':1,'reason':'expert addition','requirements':[row]},headers=auth_headers(client))
    assert saved.status_code==200 and saved.json()['requirements'][0]['origin']['kind']=='manual'


@pytest.mark.parametrize('attack',['citations','origin'])
def test_second_manual_save_cannot_add_citations_or_forge_origin(packages,attack):
    from test_full_workspace_api import requirement
    from app.workspace.repository import REQUIREMENT_FIELDS
    service,actor,original,options,_=setup(packages);policy(packages,actor)
    client,_,_,_,org,project=packages
    started=service.start(org,actor,project,original['id'],'manual',options)
    base=f'/api/organizations/{org}/projects/{project}/checklists/'+started['run']['checklist_id']
    saved=client.put(base,json={'expected_revision':1,'reason':'expert addition','requirements':[requirement()]},headers=auth_headers(client))
    assert saved.status_code==200
    checklist=saved.json();row={k:checklist['requirements'][0][k] for k in REQUIREMENT_FIELDS}
    if attack=='citations':row['citations']=[{'quote':'forged source','validated':True}]
    else:row['origin']['actor_id']='forged expert'
    assert client.put(base,json={'expected_revision':checklist['revision'],'reason':'second edit','requirements':[row]},headers=auth_headers(client)).status_code==422
    stored=client.get(base).json()['requirements'][0]
    assert stored['citations']==[] and stored['origin']==saved.json()['requirements'][0]['origin']


def test_empty_grounded_fork_retains_protection_against_new_fabricated_citations(packages):
    from test_full_workspace_api import requirement
    service,actor,original,options,_=setup(packages);policy(packages,actor)
    client,_,_,_,org,project=packages
    started=service.start(org,actor,project,original['id'],'extract',options);run_analysis(service,org,started)
    base=f'/api/organizations/{org}/projects/{project}/checklists/'
    checklist=client.get(base+started['run']['checklist_id']).json()
    assert client.post(base+checklist['id']+'/approve',json={'expected_revision':checklist['revision']},headers=auth_headers(client)).status_code==200
    fork=client.post(base+checklist['id']+'/fork',json={},headers=auth_headers(client)).json()
    cleared=client.put(base+fork['id'],json={'expected_revision':fork['revision'],'reason':'remove all','requirements':[]},headers=auth_headers(client))
    assert cleared.status_code==200
    forged=requirement(citations=[{'quote':'invented','validated':True}],origin={})
    assert client.put(base+fork['id'],json={'expected_revision':cleared.json()['revision'],'reason':'new row','requirements':[forged]},headers=auth_headers(client)).status_code==422


def test_approval_rejects_legacy_forged_row_in_grounded_fork(packages):
    from uuid import uuid4
    service,actor,original,options,_=setup(packages);policy(packages,actor)
    client,engine,_,_,org,project=packages
    started=service.start(org,actor,project,original['id'],'extract',options);run_analysis(service,org,started)
    base=f'/api/organizations/{org}/projects/{project}/checklists/'
    checklist=client.get(base+started['run']['checklist_id']).json()
    assert client.post(base+checklist['id']+'/approve',json={'expected_revision':checklist['revision']},headers=auth_headers(client)).status_code==200
    fork=client.post(base+checklist['id']+'/fork',json={},headers=auth_headers(client)).json()
    with tenant_transaction(engine,org) as db:
        db.add(Requirement(organization_id=org,checklist_id=fork['id'],logical_id=str(uuid4()),display_id='T-2',scope='product',
            category='technical',parameter='forged',condition='forged',importance='mandatory',predicate={},origin={},citations=[{'validated':True,'quote':'invented'}]))
    assert client.post(base+fork['id']+'/approve',json={'expected_revision':fork['revision']},headers=auth_headers(client)).status_code==422


def test_document_citations_cannot_be_forged_by_manual_edit(packages):
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    client,engine,app,store,org,project=packages
    started=service.start(org,actor,project,original['id'],'extract',options)
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    service.handle(ExecutionContext(jobs,started['job']['id'],claim));jobs.finish(started['job']['id'],claim.token)
    base=f'/api/organizations/{org}/projects/{project}/checklists/'+started['run']['checklist_id']
    checklist=client.get(base).json();row=checklist['requirements'][0]
    from app.workspace.repository import REQUIREMENT_FIELDS
    row={k:row[k] for k in REQUIREMENT_FIELDS};row['citations'][0]['quote']='invented text'
    assert client.put(base,json={'expected_revision':checklist['revision'],'reason':'edit','requirements':[row]},headers=auth_headers(client)).status_code==422


def test_long_reference_resolves_complete_target_with_bounded_iterative_requests(packages):
    import json
    from app.analysis.service import AnalysisService
    from app.analysis.schemas import Extraction
    documents,actor,_=processing(packages)
    _,engine,app,_,org,project=packages
    text='Power per section 9.\nIntroduction\n9. Specification\n'+('supporting evidence '*400)+'\nCertification required.\n10. Delivery\n'
    original=package(packages,data=text.encode())
    normalization=documents.start(org,actor,project,original['id'],'normalize',{})
    execute(documents,org,normalization);policy(packages,actor)
    observed=[]
    class Synthetic:
        def extract(self,content,kind):
            observed.append(json.loads(content))
            return Extraction(requirements=[],facts=[],findings=[]),{'input_tokens':100,'output_tokens':100}
    service=AnalysisService(engine,app.state.settings,documents=documents,gateway=Synthetic())
    options={'normalization_job_id':normalization['job']['id'],'normalization_attempt':1}
    preview=service.preflight(org,actor,project,original['id'],options)
    started=service.start(org,actor,project,original['id'],'references',options)
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    service.handle(ExecutionContext(jobs,started['job']['id'],claim))
    lookups=[call for call in observed if call['package_context']]
    assert len(lookups)>1 and preview['reference_lookup_count']==len(lookups)
    target=''.join(b['text'] for call in lookups for b in call['blocks'][1:])
    assert target=='9. Specification\n'+('supporting evidence '*400)+'\nCertification required.\n'
    assert all('section 9' in call['blocks'][0]['text'] for call in lookups)


def test_fact_callback_cannot_target_other_analysis_job_in_same_project(packages):
    from uuid import uuid4
    from app.jobs.scoped_writes import FencedWrites,ScopeViolation
    from app.workspace.models import ParticipantFact
    from app.workspace.repository import Repository
    service,actor,original,options,_=setup(packages);policy(packages,actor)
    _,engine,_,_,org,project=packages
    first=service.start(org,actor,project,original['id'],'first',options)
    other=service.start(org,actor,project,original['id'],'other',options)
    with tenant_transaction(engine,org) as db:
        participant=Repository(db,org,actor).create_participant(project,'Test bid',1)
        writes=FencedWrites(db,org,project,first['job']['id'])
        with pytest.raises(ScopeViolation):
            writes.add(ParticipantFact(id=str(uuid4()),organization_id=org,project_id=project,
                participant_id=participant.id,run_id=other['run']['id'],logical_id=str(uuid4()),data={}))


def test_fenced_callback_cannot_mutate_frozen_analysis_budget(packages):
    from app.jobs.scoped_writes import ScopeViolation
    from app.workspace.models import AnalysisRun
    service,actor,original,options,_=setup(packages);policy(packages,actor)
    _,engine,_,_,org,project=packages
    started=service.start(org,actor,project,original['id'],'extract',options)
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    def attack(writes):
        row=writes.get(AnalysisRun,started['run']['id'])
        row.frozen={**row.frozen,'budget':{'max_requests':500,'max_tokens':20000000}}
    with pytest.raises(ScopeViolation):
        jobs.checkpoint(started['job']['id'],claim.token,'attack','1',{},attack)


@pytest.mark.parametrize('max_requests,expected_calls,published',[(1,1,False),(2,2,True)])
def test_gateway_retries_charge_durable_budget_and_do_not_publish_on_exhaustion(packages,max_requests,expected_calls,published):
    from app.analysis.gateway import GatewayFailure
    from app.workspace.models import AnalysisCall,AnalysisRun
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    _,engine,_,_,org,project=packages
    original_gateway=service.gateway;attempts=[]
    class Flaky:
        def extract(self,content,kind):
            attempts.append(1)
            if len(attempts)==1:raise GatewayFailure('timeout')
            return original_gateway.extract(content,kind)
    service.gateway=Flaky();options['budget']={'max_requests':max_requests,'max_tokens':100000}
    started=service.start(org,actor,project,original['id'],'extract',options)
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    if published:service.handle(ExecutionContext(jobs,started['job']['id'],claim))
    else:
        with pytest.raises(ValidationError,match='budget exhausted'):service.handle(ExecutionContext(jobs,started['job']['id'],claim))
    with tenant_transaction(engine,org) as db:
        accounted=list(db.scalars(select(AnalysisCall).where(AnalysisCall.run_id==started['run']['id']).order_by(AnalysisCall.sequence)))
        assert len(accounted)==expected_calls and len(attempts)==expected_calls
        assert accounted[0].status=='failed' and accounted[0].usage is None and accounted[0].error_code=='timeout'
        assert db.get(AnalysisRun,started['run']['id']).status==('published' if published else 'draft')
        if not published:assert not list(db.scalars(select(Requirement)))


def test_checkpointed_extraction_retry_avoids_second_provider_call(packages):
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    _,engine,_,_,org,project=packages
    started=service.start(org,actor,project,original['id'],'extract',options)
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    context=ExecutionContext(jobs,started['job']['id'],claim)
    class CrashBeforePublish:
        service=context.service;job_id=context.job_id;claim=context.claim;payload=context.payload
        def checkpoint(self,step,*args,**kwargs):
            if step=='published':raise RuntimeError('Synthetic crash after extraction')
            return context.checkpoint(step,*args,**kwargs)
    with pytest.raises(RuntimeError):service.handle(CrashBeforePublish())
    jobs.fail(started['job']['id'],claim.token,'handler_failed')
    with tenant_transaction(engine,org) as db:jobs.retry_in(db,started['job']['id'],project,actor,'retry',{})
    second=jobs.claim(started['job']['id'])
    service.handle(ExecutionContext(jobs,started['job']['id'],second));jobs.finish(started['job']['id'],second.token)
    assert len(calls)==1
    with tenant_transaction(engine,org) as db:
        assert db.scalar(select(func.count()).select_from(Requirement).where(Requirement.checklist_id==started['run']['checklist_id']))==1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id==started['run']['id'],AuditEvent.action=='analysis.published'))==1


def run_analysis(service,org,started):
    jobs=JobService(service.engine,org);claim=jobs.claim(started['job']['id'])
    service.handle(ExecutionContext(jobs,started['job']['id'],claim));jobs.finish(started['job']['id'],claim.token)


def test_cross_run_cache_reuses_immutable_extraction_not_edited_checklist(packages):
    from app.workspace.repository import REQUIREMENT_FIELDS
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    client,engine,_,_,org,project=packages
    first=service.start(org,actor,project,original['id'],'first',options);run_analysis(service,org,first)
    base=f'/api/organizations/{org}/projects/{project}/checklists/'+first['run']['checklist_id']
    checklist=client.get(base).json();old=checklist['requirements'][0]
    edited={k:old[k] for k in REQUIREMENT_FIELDS};edited['condition']='Manual condition different from extraction'
    assert client.put(base,json={'expected_revision':checklist['revision'],'reason':'expert correction','requirements':[edited]},headers=auth_headers(client)).status_code==200
    second=service.start(org,actor,project,original['id'],'second',options);run_analysis(service,org,second)
    assert len(calls)==1
    result=service.detail(org,actor,project,second['run']['id'])
    assert result['run']['result']['cache']['source_run_id']==first['run']['id'] and result['calls']==[]
    fresh=client.get(f'/api/organizations/{org}/projects/{project}/checklists/'+second['run']['checklist_id']).json()['requirements'][0]
    assert fresh['condition']=='at least 25 W' and fresh['logical_id']!=old['logical_id'] and fresh['id']!=old['id']
    assert fresh['origin']['analysis_run_id']==second['run']['id'] and fresh['citations']==old['citations']
    with tenant_transaction(engine,org) as db:
        audit=db.scalar(select(AuditEvent).where(AuditEvent.entity_id==second['run']['id'],AuditEvent.action=='analysis.published'))
        assert audit.after['cache']['source_run_id']==first['run']['id']


@pytest.mark.parametrize('changed',['budget','model','context','pricing','policy','language','timezone'])
def test_cache_misses_when_frozen_policy_or_inputs_change(packages,changed):
    from app.analysis.service import update_policy
    from app.workspace.models import Project
    from app.workspace.repository import Repository
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    _,engine,_,_,org,project=packages
    first=service.start(org,actor,project,original['id'],'first',options);run_analysis(service,org,first)
    if changed=='budget':options={**options,'budget':{'max_requests':33}}
    elif changed=='model':service.settings=service.settings.model_copy(update={'gateway_model':'different-explicit-model'})
    elif changed=='context':service.settings=service.settings.model_copy(update={'analysis_context_tokens':40000})
    elif changed=='pricing':service.settings=service.settings.model_copy(update={'analysis_pricing_version':'different-price-v2'})
    elif changed=='policy':
        with tenant_transaction(engine,org) as db:update_policy(Repository(db,org,actor),True,1,'new consent version')
    else:
        with tenant_transaction(engine,org) as db:
            setattr(db.get(Project,project),'report_language' if changed=='language' else 'timezone','en' if changed=='language' else 'UTC')
    second=service.start(org,actor,project,original['id'],'second',options);run_analysis(service,org,second)
    assert len(calls)==2 and 'cache' not in service.detail(org,actor,project,second['run']['id'])['run']['result']


def test_cache_citation_revalidation_rejects_tampered_frozen_storage(packages,monkeypatch):
    from app.packages.crypto import AuthenticationFailure
    from app.workspace.models import NormalizedArtifact
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    _,engine,_,store,org,project=packages
    first=service.start(org,actor,project,original['id'],'first',options);run_analysis(service,org,first)
    second=service.start(org,actor,project,original['id'],'second',options)
    with tenant_transaction(engine,org) as db:
        artifact=db.scalar(select(NormalizedArtifact));key=artifact.object_key
    encrypted=store.get(key);store.put(key,encrypted[:-1])
    jobs=JobService(engine,org);claim=jobs.claim(second['job']['id'])
    with pytest.raises(AuthenticationFailure):service.handle(ExecutionContext(jobs,second['job']['id'],claim))
    assert len(calls)==1


def test_cached_checkpoint_retry_publishes_once_without_provider_call(packages):
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    _,engine,_,_,org,project=packages
    first=service.start(org,actor,project,original['id'],'first',options);run_analysis(service,org,first)
    second=service.start(org,actor,project,original['id'],'second',options)
    jobs=JobService(engine,org);claim=jobs.claim(second['job']['id'])
    context=ExecutionContext(jobs,second['job']['id'],claim)
    class Crash:
        service=context.service;job_id=context.job_id;claim=context.claim;payload=context.payload
        def checkpoint(self,step,*args,**kwargs):
            if step=='published':raise RuntimeError('Synthetic cache crash')
            return context.checkpoint(step,*args,**kwargs)
    with pytest.raises(RuntimeError):service.handle(Crash())
    jobs.fail(second['job']['id'],claim.token,'handler_failed')
    with tenant_transaction(engine,org) as db:jobs.retry_in(db,second['job']['id'],project,actor,'retry-cache',{})
    run_analysis(service,org,second)
    assert len(calls)==1
    with tenant_transaction(engine,org) as db:
        assert db.scalar(select(func.count()).select_from(Requirement).where(Requirement.checklist_id==second['run']['checklist_id']))==1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id==second['run']['id'],AuditEvent.action=='analysis.published'))==1


def test_cache_publication_rechecks_current_policy_version_after_checkpoint(packages):
    from app.analysis.service import update_policy
    from app.workspace.repository import Repository
    from app.workspace.models import AnalysisRun
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    _,engine,_,_,org,project=packages
    first=service.start(org,actor,project,original['id'],'first',options);run_analysis(service,org,first)
    second=service.start(org,actor,project,original['id'],'second',options)
    jobs=JobService(engine,org);claim=jobs.claim(second['job']['id'])
    context=ExecutionContext(jobs,second['job']['id'],claim)
    class Changed:
        service=context.service;job_id=context.job_id;claim=context.claim;payload=context.payload
        def checkpoint(self,step,*args,**kwargs):
            result=context.checkpoint(step,*args,**kwargs)
            if step=='cache':
                with tenant_transaction(engine,org) as db:update_policy(Repository(db,org,actor),True,1,'new consent after cache checkpoint')
            return result
    with pytest.raises(ValidationError):service.handle(Changed())
    assert len(calls)==1
    with tenant_transaction(engine,org) as db:
        assert db.get(AnalysisRun,second['run']['id']).status=='draft'
        assert not list(db.scalars(select(Requirement).where(Requirement.checklist_id==second['run']['checklist_id'])))


@pytest.mark.parametrize('scope',['project','organization'])
def test_same_content_in_foreign_scope_does_not_reuse_cached_extraction(packages,scope):
    from test_full_workspace_api import create_org,project as make_project
    from app.packages.inputs import prepare_files
    from app.analysis.service import update_policy
    from app.workspace.repository import Repository
    service,actor,original,options,calls=setup(packages);policy(packages,actor)
    client,engine,_,_,org,project=packages
    first=service.start(org,actor,project,original['id'],'first',options);run_analysis(service,org,first)
    other_org=create_org(client,'Other organization') if scope=='organization' else org
    other_project=make_project(client,other_org)
    if scope=='organization':
        with tenant_transaction(engine,other_org) as db:update_policy(Repository(db,other_org,actor),True,0,'other organization consent')
    other=service.documents.packages.upload(other_org,actor,other_project,'tender',None,
        prepare_files([('same.txt',b'Minimum power 25 W.\n')]),'other-upload')['package']
    normalized=service.documents.start(other_org,actor,other_project,other['id'],'normalize',{})
    execute(service.documents,other_org,normalized)
    second=service.start(other_org,actor,other_project,other['id'],'second',{'normalization_job_id':normalized['job']['id'],'normalization_attempt':1})
    run_analysis(service,other_org,second)
    result=service.detail(other_org,actor,other_project,second['run']['id'])['run']['result']
    assert len(calls)==2 and 'cache' not in result
    assert result['requirements'][0]['citations'][0]['document_id']==other['documents'][0]['id']


def test_analysis_history_is_nested_readable_by_viewer_and_restores_latest_run(packages):
    from uuid import uuid4
    from test_full_workspace_api import project as make_project
    service,actor,original,options,_=setup(packages);policy(packages,actor)
    client,engine,app,_,org,project=packages;app.state.analysis=service
    first=service.start(org,actor,project,original['id'],'first',options)
    latest=service.start(org,actor,project,original['id'],'latest',options)
    base=f'/api/organizations/{org}/projects/{project}/packages/{original["id"]}/analysis'
    response=client.get(base);assert response.status_code==200
    rows=response.json();assert [r['run']['id'] for r in rows]==[latest['run']['id'],first['run']['id']]
    assert set(rows[0])=={'run','job'} and rows[0]['job']['id']==latest['job']['id']
    assert all(secret not in response.text for secret in ('object_key','payload','wrapped_key','CODEX_GATEWAY'))
    other=make_project(client,org)
    assert client.get(base.replace(project,other)).status_code==404
    assert client.get(base.replace(org,str(uuid4()))).status_code==404
    with tenant_transaction(engine,org) as db:db.scalar(select(Membership).where(Membership.user_id==actor)).role='viewer'
    assert client.get(base).status_code==200
    with tenant_transaction(engine,org) as db:db.delete(db.scalar(select(Membership).where(Membership.user_id==actor)))
    assert client.get(base).status_code==404
