"""Actual restricted PostgreSQL + encrypted normalized source + real SDK fixture."""
import json
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select,func,text
from test_document_artifacts_postgres import actual_documents,actual_packages,real_storage,normalized
from app.analysis.service import AnalysisService,update_policy
from app.analysis.gateway import Gateway
from app.analysis.schemas import GatewayProfile
from app.workspace.repository import Repository
from app.workspace.database import tenant_transaction
from app.workspace.models import AnalysisRun,AnalysisCall,ParticipantFact,Requirement,AuditEvent,Job,ChecklistVersion
from app.jobs.messages import envelope
from app.jobs.worker import HandlerRegistry,execute_message
from app.jobs.service import JobService
from pydantic import SecretStr


def fixture_gateway(requests,kind='tender'):
    def respond(request):
        body=json.loads(request.content);data=json.loads(body['messages'][1]['content']);requests.append(body)
        block=data['blocks'][0]
        citation={'evidence_id':block['evidence_id'],'quote':block['text'].strip()}
        requirements=[{'scope':'product','category':'technical','parameter':'power','condition':'25 W minimum','importance':'mandatory','predicate':{'kind':'numeric','operator':'gte','value':'25','unit':'W'},'citations':[citation]}]
        facts=[{'item':'pump','model':'synthetic-25','parameter':'power','value':'25','unit':'W','source_kind':'declaration','citations':[citation]}]
        output={'requirements':requirements if kind=='tender' else [],'facts':facts if kind=='bid' else [],'findings':[]}
        return httpx.Response(200,json={'id':'fixture','created':1,'model':'fixture','object':'chat.completion','choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(output)},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':100,'total_tokens':200}})
    return Gateway(GatewayProfile(),'synthetic-fixture',http_client=httpx.Client(transport=httpx.MockTransport(respond)))


def enabled(actual_documents):
    documents,engine,org,actor,project=actual_documents
    documents.settings=documents.settings.model_copy(update={'analysis_provider':'agentrouter'})
    with tenant_transaction(engine,org) as db:update_policy(Repository(db,org,actor),True,0,'Synthetic fixture consent')
    return documents,engine,org,actor,project


def test_actual_grounded_extraction_rls_sdk_audit_replay_approval(actual_documents):
    documents,engine,org,actor,project=enabled(actual_documents)
    original,normalization,artifact=normalized(actual_documents,'analysis-'+uuid4().hex,b'Synthetic power 25 W.\n')
    requests=[];service=AnalysisService(engine,documents.settings,documents=documents,gateway=fixture_gateway(requests))
    options={'normalization_job_id':normalization['job']['id'],'normalization_attempt':1}
    started=service.start(org,actor,project,original['id'],'extract',options)
    settings=documents.settings.model_copy(update={'job_signing_secret':SecretStr('j'*48)})
    registry=HandlerRegistry('test');registry.register('analysis.extract',service.handle)
    with tenant_transaction(engine,org) as db:job=db.get(Job,started['job']['id']);message=envelope(org,job.id,job.input_hash,'j'*48)
    assert execute_message(engine,settings,registry,message)=='succeeded'
    assert execute_message(engine,settings,registry,message)=='duplicate'
    with tenant_transaction(engine,org) as db:
        run=db.get(AnalysisRun,started['run']['id']);assert run.status=='published'
        calls=list(db.scalars(select(AnalysisCall).where(AnalysisCall.run_id==run.id)));assert len(calls)==1 and calls[0].usage=={'input_tokens':100,'output_tokens':100}
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id==run.id,AuditEvent.action=='analysis.published'))==1
        checklist=db.get(ChecklistVersion,run.checklist_id)
        repo=Repository(db,org,actor);approved=repo.approve_checklist(checklist.id,checklist.revision);assert approved.status=='approved'
        citation=repo.list_requirements(checklist.id)[0].citations[0]
        assert citation['artifact_id']==artifact['id'] and citation['quote']=='Synthetic power 25 W.'
    with tenant_transaction(engine,str(uuid4())) as db:
        assert not list(db.scalars(select(AnalysisRun))) and not list(db.scalars(select(AnalysisCall)))
    with engine.connect() as db:
        for table in ('organization_ai_policies','analysis_runs','analysis_calls','participant_facts'):
            assert db.execute(text('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:name'),{'name':table}).one()==(True,True)
    assert requests[0]['response_format']=={'type':'json_object'}


def test_actual_publication_protects_concurrent_manual_revision(actual_documents):
    from app.workspace.repository import Conflict
    from app.jobs.worker import ExecutionContext
    documents,engine,org,actor,project=enabled(actual_documents)
    original,normalization,artifact=normalized(actual_documents,'conflict-'+uuid4().hex,b'Synthetic 25 W.\n')
    requests=[];service=AnalysisService(engine,documents.settings,documents=documents,gateway=fixture_gateway(requests))
    started=service.start(org,actor,project,original['id'],'extract',{'normalization_job_id':normalization['job']['id'],'normalization_attempt':1})
    with tenant_transaction(engine,org) as db:db.get(ChecklistVersion,started['run']['checklist_id']).revision+=1
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    with pytest.raises(Conflict):service.handle(ExecutionContext(jobs,started['job']['id'],claim))
    with tenant_transaction(engine,org) as db:
        assert not list(db.scalars(select(Requirement).where(Requirement.checklist_id==started['run']['checklist_id'])))


def test_actual_frozen_inputs_cannot_change_even_inside_valid_lease(actual_documents):
    from sqlalchemy.exc import IntegrityError
    documents,engine,org,actor,project=enabled(actual_documents)
    original,normalization,_=normalized(actual_documents,'immutable-'+uuid4().hex,b'Synthetic 25 W.\n')
    service=AnalysisService(engine,documents.settings,documents=documents,gateway=fixture_gateway([]))
    started=service.start(org,actor,project,original['id'],'extract',{'normalization_job_id':normalization['job']['id'],'normalization_attempt':1})
    jobs=JobService(engine,org);claim=jobs.claim(started['job']['id'])
    with pytest.raises(IntegrityError):
        with jobs._transaction() as db:
            jobs._fence(db,started['job']['id'],claim.token)
            run=db.get(AnalysisRun,started['run']['id'])
            run.frozen={**run.frozen,'budget':{'max_requests':500,'max_tokens':20000000}}


def test_actual_bid_facts_are_participant_scoped_and_published_once(actual_documents):
    from app.packages.inputs import prepare_files
    from app.documents.service import STAGE
    documents,engine,org,actor,project=enabled(actual_documents)
    with tenant_transaction(engine,org) as db:
        participant=Repository(db,org,actor).create_participant(project,'Bid A',1)
        participant_id=participant.id
    original=documents.packages.upload(org,actor,project,'bid',participant_id,
        prepare_files([('bid.txt',b'Synthetic power 25 W.\n')]),'bid')['package']
    normalization=documents.start(org,actor,project,original['id'],'normalize',{})
    settings=documents.settings.model_copy(update={'job_signing_secret':SecretStr('j'*48)})
    registry=HandlerRegistry('test');registry.register(STAGE,documents.handle)
    with tenant_transaction(engine,org) as db:
        job=db.get(Job,normalization['job']['id']);message=envelope(org,job.id,job.input_hash,'j'*48)
    assert execute_message(engine,settings,registry,message)=='succeeded'
    service=AnalysisService(engine,documents.settings,documents=documents,gateway=fixture_gateway([],kind='bid'))
    started=service.start(org,actor,project,original['id'],'extract',{'normalization_job_id':normalization['job']['id'],'normalization_attempt':1})
    registry.register('analysis.extract',service.handle)
    with tenant_transaction(engine,org) as db:
        job=db.get(Job,started['job']['id']);message=envelope(org,job.id,job.input_hash,'j'*48)
    assert execute_message(engine,settings,registry,message)=='succeeded'
    assert execute_message(engine,settings,registry,message)=='duplicate'
    with tenant_transaction(engine,org) as db:
        facts=list(db.scalars(select(ParticipantFact).where(ParticipantFact.run_id==started['run']['id'])))
        assert len(facts)==1 and facts[0].participant_id==participant_id
        assert facts[0].data['source_kind']=='declaration' and facts[0].data['source_priority']==1
        assert facts[0].data['citations'][0]['filename']=='bid.txt'
        assert db.get(AnalysisRun,started['run']['id']).checklist_id is None


def test_actual_rabbit_analysis_confirmed_duplicate_publishes_one_audit(actual_documents,tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    from kombu import Connection,Queue
    from app.jobs.publisher import OutboxPublisher,confirmed_sender
    from app.jobs.worker import create_celery_app
    from test_jobs_broker import wait_until
    broker=os.environ.get('WORKSPACE_TEST_BROKER_URL')
    if not broker:pytest.skip('Explicit actual RabbitMQ URL required')
    documents,engine,org,actor,project=enabled(actual_documents)
    original,normalization,_=normalized(actual_documents,'rabbit-'+uuid4().hex,b'Synthetic power 25 W.\n')
    service=AnalysisService(engine,documents.settings,documents=documents,gateway=fixture_gateway([]))
    started=service.start(org,actor,project,original['id'],'extract',{'normalization_job_id':normalization['job']['id'],'normalization_attempt':1})
    queue='tender.test.'+uuid4().hex;secret='j'*48
    settings=documents.settings.model_copy(update={'database_url':SecretStr(os.environ['WORKSPACE_TEST_RUNTIME_DATABASE_URL']),
        'broker_url':SecretStr(broker),'job_queue':queue,'job_signing_secret':SecretStr(secret)})
    app=create_celery_app(settings)
    allowed={'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC'}
    env={k:v for k,v in os.environ.items() if k.upper() in allowed}
    env.update(APP_ENVIRONMENT='test',DATABASE_URL=os.environ['WORKSPACE_TEST_RUNTIME_DATABASE_URL'],
        APP_SESSION_SECRET='s'*48,APP_SMS_PROVIDER='mock',CELERY_BROKER_URL=broker,
        APP_JOB_SIGNING_SECRET=secret,APP_JOB_QUEUE=queue,PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    for key in ('S3_ENDPOINT_URL','S3_REGION','S3_BUCKET','S3_ACCESS_KEY','S3_SECRET_KEY','DOCUMENT_ENCRYPTION_KEY'):
        env[key]=os.environ['WORKSPACE_TEST_'+key]
    def published():
        with tenant_transaction(engine,org) as db:return db.get(Job,started['job']['id']).status=='succeeded'
    with (tmp_path/'analysis-worker.log').open('wb') as log:
        process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('analysis_fixture_worker.py'))],
            cwd=Path(__file__).resolve().parents[1],env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            publisher=OutboxPublisher(engine,secret,confirmed_sender(app))
            # Only this fixture organization is dispatched; never use global scan.
            for _ in range(10):
                claim=publisher.claim(org);assert claim is not None
                if claim.message['job_id']==started['job']['id']:
                    publisher.publish(claim.message);publisher.publish(claim.message)
                    assert publisher.mark_published(org,claim);break
                assert publisher.send(org,claim)
            else:pytest.fail('Owned analysis outbox was not found')
            wait_until(published,timeout=35)
            with tenant_transaction(engine,org) as db:
                assert db.scalar(select(func.count()).select_from(AnalysisCall).where(AnalysisCall.run_id==started['run']['id']))==1
                assert db.scalar(select(func.count()).select_from(Requirement).where(Requirement.checklist_id==started['run']['checklist_id']))==1
                assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id==started['run']['id'],AuditEvent.action=='analysis.published'))==1
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            with Connection(broker,connect_timeout=5) as conn:
                Queue(queue,durable=True).bind(conn).delete(if_unused=False,if_empty=False)
            app.close()


def test_actual_cross_run_cache_rebinds_evidence_and_publishes_fresh_ids_once(actual_documents):
    from app.workspace.repository import REQUIREMENT_FIELDS
    from app.jobs.worker import ExecutionContext
    documents,engine,org,actor,project=enabled(actual_documents)
    original,normalization,artifact=normalized(actual_documents,'cache-'+uuid4().hex,b'Synthetic power 25 W.\n')
    requests=[];service=AnalysisService(engine,documents.settings,documents=documents,gateway=fixture_gateway(requests))
    options={'normalization_job_id':normalization['job']['id'],'normalization_attempt':1}
    first=service.start(org,actor,project,original['id'],'first',options)
    jobs=JobService(engine,org);claim=jobs.claim(first['job']['id'])
    service.handle(ExecutionContext(jobs,first['job']['id'],claim));jobs.finish(first['job']['id'],claim.token)
    with tenant_transaction(engine,org) as db:
        repo=Repository(db,org,actor);checklist=repo.get_checklist(first['run']['checklist_id'])
        old=repo.list_requirements(checklist.id)[0]
        old_id,old_logical=old.id,old.logical_id
        edited={k:getattr(old,k) for k in REQUIREMENT_FIELDS};edited['condition']='Manual changed condition'
        repo.save_checklist_draft(checklist.id,[edited],checklist.revision,'expert edit')
    second=service.start(org,actor,project,original['id'],'second',options)
    settings=documents.settings.model_copy(update={'job_signing_secret':SecretStr('j'*48)})
    registry=HandlerRegistry('test');registry.register('analysis.extract',service.handle)
    with tenant_transaction(engine,org) as db:
        job=db.get(Job,second['job']['id']);message=envelope(org,job.id,job.input_hash,'j'*48)
    assert execute_message(engine,settings,registry,message)=='succeeded'
    assert execute_message(engine,settings,registry,message)=='duplicate' and len(requests)==1
    with tenant_transaction(engine,org) as db:
        run=db.get(AnalysisRun,second['run']['id']);cache=run.result['cache']
        assert cache['source_run_id']==first['run']['id'] and cache['policy_version']==1
        fresh=Repository(db,org,actor).list_requirements(run.checklist_id)[0]
        assert fresh.id!=old_id and fresh.logical_id!=old_logical and fresh.condition=='25 W minimum'
        assert fresh.citations[0]['artifact_id']==artifact['id']
        assert db.scalar(select(func.count()).select_from(AnalysisCall).where(AnalysisCall.run_id==run.id))==0
        audits=list(db.scalars(select(AuditEvent).where(AuditEvent.entity_id==run.id,AuditEvent.action=='analysis.published')))
        assert len(audits)==1 and audits[0].after['cache']==cache
