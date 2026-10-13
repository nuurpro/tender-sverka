"""Opt-in actual private S3 and restricted PostgreSQL acceptance; no fallback."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func,select,text
from sqlalchemy.exc import IntegrityError

from app.workspace.database import create_engine,tenant_transaction,verify_runtime_database
from app.workspace.models import (Organization,User,Membership,PackageRevision,Document,DocumentKeyEnvelope,
    UploadAttempt,UploadIntent,PackageInspection,Job,AuditEvent,OutboxEvent,utcnow)
from app.workspace.repository import Repository,Conflict,NotFound
from app.packages.crypto import EnvelopeCipher,AuthenticationFailure
from app.packages.inputs import prepare_files
from app.packages.service import PackageService
from app.packages.storage import S3Store
from test_full_api_postgres import pg_api,pg_login


@pytest.fixture
def real_storage():
    from app.config import Settings
    prefix='WORKSPACE_TEST_'
    endpoint=os.environ.get(prefix+'S3_ENDPOINT_URL')
    if not endpoint:
        pytest.skip('Explicit actual S3 acceptance endpoint required')
    fields=['S3_ENDPOINT_URL','S3_REGION','S3_BUCKET','S3_ACCESS_KEY','S3_SECRET_KEY','DOCUMENT_ENCRYPTION_KEY']
    if any(not os.environ.get(prefix+field) for field in fields):
        pytest.fail('Configured actual S3 acceptance requires every storage setting')
    document_fields={field.lower():os.environ[prefix+field] for field in
        ('DOCUMENT_RUNNER_MODE','DOCUMENT_RUNTIME_PYTHON','DOCUMENT_RUNTIME_PYTHON_SHA256','DOCUMENT_PARSER_CODE_PATH','DOCUMENT_WSL_DISTRIBUTION')
        if os.environ.get(prefix+field)}
    settings=Settings(environment='test',database_url='sqlite://',session_secret='s'*48,
        **{field.lower():os.environ[prefix+field] for field in fields},**document_fields)
    store=S3Store(settings)
    cipher=EnvelopeCipher({'v1':bytes.fromhex(settings.document_encryption_key.get_secret_value())},'v1')
    return settings,store,cipher


@pytest.fixture
def actual_packages(real_storage):
    runtime=os.environ.get('WORKSPACE_TEST_RUNTIME_DATABASE_URL')
    if not runtime:
        pytest.fail('Actual package acceptance requires restricted PostgreSQL runtime URL')
    settings,store,cipher=real_storage
    engine=create_engine(runtime);verify_runtime_database(engine)
    org,actor=str(uuid4()),str(uuid4())
    with tenant_transaction(engine,org) as db:
        db.add(Organization(id=org,name='Actual encrypted storage fixture'))
        db.add(User(id=actor,phone='+998'+str(uuid4().int)[:9]));db.flush()
        db.add(Membership(organization_id=org,user_id=actor,role='admin'));db.flush()
        project=Repository(db,org,actor).create_project('buyer','Actual package fixture')
        project_id=project.id
    service=PackageService(engine,settings,store,cipher)
    yield service,engine,org,actor,project_id
    # Persist committed originals for provenance acceptance. Attempts use random
    # server namespaces and the dedicated private fixture bucket, not user data.
    engine.dispose()


def test_actual_private_s3_cipher_roundtrip_tamper_truncation_and_wrong_scope(real_storage):
    settings,store,cipher=real_storage
    org,project,document=str(uuid4()),str(uuid4()),str(uuid4())
    key=f'org/{org}/project/{project}/attempt/'+('a'*64)+f'/{document}'
    scope={'organization_id':org,'project_id':project,'document_id':document,'object_kind':'original'}
    original=b'original bytes that must never be public'*10000
    ciphertext,envelope=cipher.encrypt(original,scope)
    try:
        store.put(key,ciphertext)
        stored=store.get(key)
        assert original[:100] not in stored and stored==ciphertext
        assert cipher.decrypt(stored,envelope,scope,len(original))==original
        response=httpx.get(settings.s3_endpoint_url+'/'+settings.s3_bucket+'/'+key,timeout=10)
        assert response.status_code==403
        for changed in [stored[:-1],stored[:-30]+bytes([stored[-30]^1])+stored[-29:]]:
            store.put(key,changed)
            with pytest.raises(AuthenticationFailure):
                cipher.decrypt(store.get(key),envelope,scope,len(original))
        for field in ['organization_id','project_id','document_id','object_kind']:
            with pytest.raises(AuthenticationFailure):
                cipher.decrypt(ciphertext,envelope,{**scope,field:'other'},len(original))
        with pytest.raises(AuthenticationFailure):
            EnvelopeCipher({'v1':b'x'*32},'v1').decrypt(ciphertext,envelope,scope,len(original))
    finally:
        store.delete(key)


def test_actual_upload_s3_sql_transaction_boundary_atomicity_and_tenant_rls(actual_packages):
    service,engine,org,actor,project=actual_packages
    batch=prepare_files([('folder/name.txt',b'actual original')])
    original_put=service.store.put
    def put_without_sql_locks(key,data):
        with tenant_transaction(engine,org) as db:
            assert db.scalar(select(PackageRevision).where(PackageRevision.project_id==project)) is None
            db.scalar(select(UploadAttempt).where(UploadAttempt.project_id==project).with_for_update(nowait=True))
        original_put(key,data)
    service.store.put=put_without_sql_locks
    response=service.upload(org,actor,project,'tender',None,batch,'first')
    service.store.put=original_put
    package=response['package'];document=package['documents'][0]
    assert service.download(org,actor,project,package['id'],document['id'])[0]==b'actual original'
    assert service.upload(org,actor,project,'tender',None,batch,'first')==response
    with tenant_transaction(engine,org) as db:
        for model in [PackageRevision,Document,DocumentKeyEnvelope,UploadIntent,UploadAttempt,Job,OutboxEvent]:
            assert db.scalar(select(func.count()).select_from(model).where(model.organization_id==org))==1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action=='package.uploaded',AuditEvent.organization_id==org))==1
        row=db.get(Document,document['id']);object_key=row.object_key
    assert b'actual original' not in service.store.get(object_key)
    with tenant_transaction(engine,str(uuid4())) as db:
        for model in [PackageRevision,Document,DocumentKeyEnvelope,UploadIntent,UploadAttempt,PackageInspection]:
            assert list(db.scalars(select(model)))==[]
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")) is False
        for table in ['upload_intents','upload_attempts','document_key_envelopes','package_inspections']:
            assert conn.execute(text('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:name'),{'name':table}).one()==(True,True)
    for model,field,value in [(Document,'relative_path','changed'),(Document,'object_key','changed'),
                              (UploadAttempt,'objects',[]),(UploadIntent,'input_digest','a'*64)]:
        with pytest.raises(IntegrityError):
            with tenant_transaction(engine,org) as db:
                row=db.scalar(select(model).where(model.organization_id==org));setattr(row,field,value)
    with pytest.raises(NotFound):
        service.download(str(uuid4()),actor,project,package['id'],document['id'])


def test_actual_revision_allocation_and_idempotency_races(actual_packages):
    service,engine,org,actor,project=actual_packages
    batch=prepare_files([('original.txt',b'concurrent original')])
    def upload(key):
        try:
            return service.upload(org,actor,project,'tender',None,batch,key)
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses=list(pool.map(upload,['same']*4))
    committed=[response for response in responses if response]
    assert committed and all(response==committed[0] for response in committed)
    assert service.upload(org,actor,project,'tender',None,batch,'same')==committed[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        revisions=list(pool.map(upload,[f'new-{i}' for i in range(4)]))
    assert sorted(response['package']['version'] for response in revisions)==[2,3,4,5]
    with tenant_transaction(engine,org) as db:
        assert db.scalar(select(func.count()).select_from(PackageRevision).where(PackageRevision.organization_id==org))==5
        assert db.scalar(select(func.count()).select_from(Job).where(Job.organization_id==org))==5


def test_ambiguous_commit_recheck_preserves_committed_originals(actual_packages,monkeypatch):
    service,engine,org,actor,project=actual_packages
    batch=prepare_files([('original.txt',b'committed even if acknowledgment lost')])
    finalize=service._finalize
    def commit_ack_lost(*args,**kwargs):
        finalize(*args,**kwargs)
        raise RuntimeError('simulated commit acknowledgment lost')
    monkeypatch.setattr(service,'_finalize',commit_ack_lost)
    result=service.upload(org,actor,project,'tender',None,batch,'ambiguous')
    doc=result['package']['documents'][0]
    assert service.download(org,actor,project,result['package']['id'],doc['id'])[0]==batch.leaves[0].data
    assert service.reconcile(org)==0


def test_expired_attempt_cannot_finalize_the_replacement_attempt(actual_packages):
    service,engine,org,actor,project=actual_packages
    instant=[utcnow()];service.clock=lambda:instant[0]
    batch=prepare_files([('original.txt',b'expiry fixture')])
    put=service.store.put
    def expire_during_put(key,data):
        put(key,data);instant[0]+=timedelta(minutes=6)
    service.store.put=expire_during_put
    with pytest.raises(Conflict):
        service.upload(org,actor,project,'tender',None,batch,'same')
    service.store.put=put
    with tenant_transaction(engine,org) as db:
        old=db.scalar(select(UploadAttempt).where(UploadAttempt.organization_id==org))
        old_id,intent,token,old_key=old.id,old.intent_id,old.attempt_token,old.objects[0]['object_key']
        assert old.status=='cleaned'
    response=service.upload(org,actor,project,'tender',None,batch,'same')
    assert response['package']['version']==1
    with pytest.raises(Conflict):
        service._finalize(org,actor,project,'tender',None,batch,intent,old_id,token)


def test_database_unavailable_defers_owned_orphan_cleanup_until_proof_is_available(actual_packages,monkeypatch):
    import app.packages.service as module
    from sqlalchemy.exc import OperationalError
    service,engine,org,actor,project=actual_packages
    instant=[utcnow()];service.clock=lambda:instant[0]
    batch=prepare_files([('original.txt',b'owned orphan during unavailable proof')])
    transaction=module.tenant_transaction
    def unavailable(*args,**kwargs):
        raise OperationalError('test-only database outage',None,Exception())
    def fail_finalization(*args,**kwargs):
        monkeypatch.setattr(module,'tenant_transaction',unavailable)
        raise OperationalError('test-only ambiguous SQL boundary',None,Exception())
    monkeypatch.setattr(service,'_finalize',fail_finalization)
    with pytest.raises(OperationalError):
        service.upload(org,actor,project,'tender',None,batch,'outage')
    monkeypatch.setattr(module,'tenant_transaction',transaction)
    with tenant_transaction(engine,org) as db:
        attempt=db.scalar(select(UploadAttempt).where(UploadAttempt.organization_id==org))
        key=attempt.objects[0]['object_key'];attempt_id=attempt.id
        assert attempt.status=='uploading' and not list(db.scalars(select(Document)))
    assert service.store.get(key)  # No destructive guess during unavailable proof.
    instant[0]+=timedelta(minutes=6)
    assert service.reconcile(org)==1
    from app.packages.storage import StorageUnavailable
    with pytest.raises(StorageUnavailable):
        service.store.get(key)
    with tenant_transaction(engine,org) as db:
        assert db.get(UploadAttempt,attempt_id).status=='cleaned'


def test_audit_failure_rolls_back_originals_revision_job_and_outbox_atomically(actual_packages,monkeypatch):
    service,engine,org,actor,project=actual_packages
    audit=Repository._audit
    def reject_audit(repo,action,*args,**kwargs):
        if action=='package.uploaded':
            raise IntegrityError('test-only audit rejection',None,Exception())
        return audit(repo,action,*args,**kwargs)
    monkeypatch.setattr(Repository,'_audit',reject_audit)
    with pytest.raises(IntegrityError):
        service.upload(org,actor,project,'tender',None,prepare_files([('a.txt',b'atomic rollback')]),'audit-failure')
    with tenant_transaction(engine,org) as db:
        for model in [PackageRevision,Document,DocumentKeyEnvelope,Job,OutboxEvent]:
            assert not list(db.scalars(select(model)))
        attempt=db.scalar(select(UploadAttempt));assert attempt.status=='cleaned'
    from app.packages.storage import StorageUnavailable
    with pytest.raises(StorageUnavailable):
        service.store.get(attempt.objects[0]['object_key'])


def test_actual_authenticated_api_enforces_csrf_viewer_nested_and_anonymous_storage_access(pg_api,real_storage):
    from test_full_auth_api import auth_headers
    from test_full_workspace_api import create_org,project
    from test_package_api import upload
    client,engine,_,_,app=pg_api
    settings,store,cipher=real_storage
    app.state.packages=PackageService(engine,settings,store,cipher)
    actor=pg_login(pg_api)['user']['id'];org=create_org(client);pid=project(client,org)
    base=f'/api/organizations/{org}/projects/{pid}'
    assert client.post(base+'/packages',content=b'no multipart').status_code==403
    result=upload(client,base).json()['package'];doc=result['documents'][0]
    download=base+f'/packages/{result["id"]}/documents/{doc["id"]}/download'
    assert client.get(download).content==b'original text'
    other=project(client,org)
    assert client.get(download.replace(pid,other)).status_code==404
    with tenant_transaction(engine,org) as db:
        db.scalar(select(Membership).where(Membership.user_id==actor)).role='viewer'
    assert upload(client,base,key='viewer').status_code==403
    assert client.get(download).content==b'original text'
    client.cookies.clear()
    assert client.get(download).status_code==401
    assert client.post(base+'/packages',content=b'no multipart').status_code==401


def test_actual_encrypted_inspection_worker_signed_message_checkpoint_and_duplicate_delivery(actual_packages):
    from app.jobs.worker import HandlerRegistry,execute_message
    from app.jobs.messages import envelope
    from app.packages.inspection import inspection_handler
    from app.workspace.models import JobCheckpoint
    from test_package_inspection import pdf
    service,engine,org,actor,project=actual_packages
    if service.settings.document_runner_mode=='disabled':
        pytest.skip('Explicit actual isolated document runner required for inspection acceptance')
    response=service.upload(org,actor,project,'tender',None,prepare_files([('document.pdf',pdf())]),'worker')
    job_id=response['inspection_job_id']
    with tenant_transaction(engine,org) as db:
        digest=db.get(Job,job_id).input_hash
    settings=service.settings.model_copy(update={'job_signing_secret':__import__('pydantic').SecretStr('j'*48)})
    registry=HandlerRegistry('test');registry.register('package.inspect',inspection_handler(settings,packages=service))
    message=envelope(org,job_id,digest,'j'*48)
    assert execute_message(engine,settings,registry,message)=='succeeded'
    assert execute_message(engine,settings,registry,message)=='duplicate'
    report=service.preflight(org,actor,project,response['package']['id'])
    assert report['page_count']==2 and report['analysis_allowed'] is False
    assert 'ocr_pending' in report['blockers'] and report['token_count'] is None
    with tenant_transaction(engine,org) as db:
        assert db.scalar(select(func.count()).select_from(PackageInspection))==1
        assert db.scalar(select(func.count()).select_from(JobCheckpoint))==1


def test_expiry_during_sql_finalization_rolls_back_the_whole_package(actual_packages,monkeypatch):
    service,engine,org,actor,project=actual_packages
    instant=[utcnow()];service.clock=lambda:instant[0]
    audit=Repository._audit
    def delayed_final_audit(repo,action,*args,**kwargs):
        audit(repo,action,*args,**kwargs)
        if action=='package.uploaded':
            instant[0]+=timedelta(minutes=6)
    monkeypatch.setattr(Repository,'_audit',delayed_final_audit)
    with pytest.raises(Conflict):
        service.upload(org,actor,project,'tender',None,prepare_files([('original.txt',b'expired SQL boundary')]),'expired-finalization')
    with tenant_transaction(engine,org) as db:
        assert not list(db.scalars(select(PackageRevision))) and not list(db.scalars(select(Job)))
        assert db.scalar(select(UploadAttempt)).status=='cleaned'


def test_bounded_cleanup_scan_fairly_revisits_delayed_puts_without_starving_other_attempts(actual_packages):
    from app.packages.storage import StorageUnavailable
    service,engine,org,actor,project=actual_packages
    instant=[utcnow()];service.clock=lambda:instant[0]
    captured={};put=service.store.put
    def expired_put(key,data):
        captured[key]=data;put(key,data);instant[0]+=timedelta(minutes=6)
    service.store.put=expired_put
    for key in ['one','two']:
        with pytest.raises(Conflict):
            service.upload(org,actor,project,'tender',None,prepare_files([('a.txt',b'fair cleanup')]),key)
    service.store.put=put
    with tenant_transaction(engine,org) as db:
        first=db.scalar(select(UploadAttempt).where(UploadAttempt.organization_id==org).limit(1))
        other=db.scalar(select(UploadAttempt).where(UploadAttempt.organization_id==org,UploadAttempt.id!=first.id))
        delayed_key=other.objects[0]['object_key']
    put(delayed_key,captured[delayed_key])  # Late completion of an already-abandoned original PUT.
    for _ in range(3):
        service.reconcile(org,limit=1)
    with pytest.raises(StorageUnavailable):
        service.store.get(delayed_key)


def test_reconciliation_never_deletes_an_object_outside_its_owned_attempt_namespace(actual_packages):
    from app.packages.storage import StorageUnavailable
    service,engine,org,actor,project=actual_packages
    foreign_org,foreign_project,document=str(uuid4()),str(uuid4()),str(uuid4())
    token=uuid4().hex+uuid4().hex
    key=f'org/{foreign_org}/project/{foreign_project}/attempt/{token}/{document}'
    scope={'organization_id':foreign_org,'project_id':foreign_project,'document_id':document,'object_kind':'original'}
    encrypted,_=service.cipher.encrypt(b'foreign original must be preserved',scope)
    service.store.put(key,encrypted)
    try:
        with tenant_transaction(engine,org) as db:
            intent=UploadIntent(organization_id=org,project_id=project,idempotency_key='bad-owned-key',input_digest='a'*64)
            db.add(intent);db.flush()
            db.add(UploadAttempt(organization_id=org,project_id=project,intent_id=intent.id,actor_id=actor,
                attempt_token=token,expires_at=utcnow()-timedelta(seconds=1),status='abandoned',
                objects=[{'id':document,'object_key':key}]))
        with pytest.raises(StorageUnavailable):
            service.reconcile(org)
        assert service.store.get(key)==encrypted
    finally:
        service.store.delete(key)
