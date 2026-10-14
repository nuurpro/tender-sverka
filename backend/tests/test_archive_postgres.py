"""Actual restricted PostgreSQL/private S3 plus isolated archive acceptance."""
from uuid import uuid4
import hashlib
import pytest
from sqlalchemy import select
from test_package_storage_postgres import actual_packages,real_storage
from test_archive_admission import public_compressed_rar,seven_zip,zipped
from app.packages.inputs import prepare_files
from app.workspace.database import tenant_transaction
from app.workspace.models import Membership,PackageRevision,Document,UploadAttempt,UploadIntent,AuditEvent,PackageInspection
from app.workspace.repository import NotFound


def admitted(service):
    original=zipped([('middle.7z',seven_zip([('inner.rar',public_compressed_rar('rar5'))]))])
    return prepare_files([('outer.zip',original),('bad.zip',b'PK\x03\x04bad')],settings=service.settings),original


def test_actual_archive_originals_provenance_idempotent_replay_and_scoped_access(actual_packages):
    service,engine,org,actor,project=actual_packages
    batch,original=admitted(service)
    result=service.upload(org,actor,project,'tender',None,batch,'actual-archive-'+uuid4().hex)
    package=result['package'];manifest=package['manifest']
    assert manifest['schema_version']==3 and manifest['archive_admission']==batch.admission
    assert manifest['accounting']['directory_entries']==3
    assert manifest['issues']==batch.issues and manifest['issues'][0]['path']=='bad.zip'
    rows=manifest['documents'];leaf=next(row for row in rows if row['provenance_role']=='document')
    assert len(leaf['source_chain'])==3
    source_by_path={file.path:file for file in batch.leaves+batch.containers}
    for row in rows:
        data,metadata=service.download(org,actor,project,package['id'],row['id'])
        assert data==source_by_path[row['relative_path']].data
        assert hashlib.sha256(data).hexdigest()==row['file_hash']
    with tenant_transaction(engine,org) as db:
        intent=db.scalar(select(UploadIntent).where(UploadIntent.package_id==package['id']))
        attempt=db.scalar(select(UploadAttempt).where(UploadAttempt.intent_id==intent.id))
        key=intent.idempotency_key
        for obj in attempt.objects:
            assert service.store.get(obj['object_key'])!=source_by_path[obj['relative_path']].data
    assert service.upload(org,actor,project,'tender',None,batch,key)==result
    with tenant_transaction(engine,org) as db:
        events=list(db.scalars(select(AuditEvent).where(AuditEvent.action=='archive.file.skipped')))
        assert len(events)==1 and events[0].after==batch.issues[0]
    with pytest.raises(NotFound):service.download(str(uuid4()),actor,project,package['id'],leaf['id'])
    with pytest.raises(NotFound):service.download(org,actor,str(uuid4()),package['id'],leaf['id'])
    with tenant_transaction(engine,org) as db:db.delete(db.scalar(select(Membership).where(Membership.user_id==actor)))
    with pytest.raises(NotFound):service.download(org,actor,project,package['id'],leaf['id'])
    with pytest.raises(NotFound):service.upload(org,actor,project,'tender',None,batch,key)


def test_actual_archive_revocation_after_storage_reconciles_all_uncommitted_originals(actual_packages):
    service,engine,org,actor,project=actual_packages
    batch,original=admitted(service);store=service.store
    class RevokeAfterPut:
        def __init__(self):self.keys=[]
        def put(self,key,data):
            store.put(key,data);self.keys.append(key)
            if len(self.keys)==1:
                with tenant_transaction(engine,org) as db:db.delete(db.scalar(select(Membership).where(Membership.user_id==actor)))
        def get(self,key):return store.get(key)
        def delete(self,key):store.delete(key)
    replacement=RevokeAfterPut();service.store=replacement
    with pytest.raises(NotFound):service.upload(org,actor,project,'tender',None,batch,'revoked-archive')
    with tenant_transaction(engine,org) as db:
        assert not list(db.scalars(select(PackageRevision))) and not list(db.scalars(select(Document)))
        attempt=db.scalar(select(UploadAttempt));assert attempt.status=='cleaned'
        keys=[obj['object_key'] for obj in attempt.objects]
    from app.packages.storage import StorageUnavailable
    for key in keys:
        with pytest.raises(StorageUnavailable):store.get(key)


def test_actual_archive_historical_v2_manifest_is_read_without_upgrade(actual_packages):
    """A literal, explicitly synthetic historical contract fixture, not v2 requalification."""
    from app.documents.archives import prepare_files as trusted_synthetic_prepare
    service,engine,org,actor,project=actual_packages
    assert service.settings.environment=='test'
    batch=trusted_synthetic_prepare([('old.zip',zipped([('source.txt',b'public historical original')]))])
    legacy={'policy_version':'archive-admission-v2','code_hash':'80d832069766cab0a8fee27b1dd5b929a11f3b29e4d38cdd42ffe75a8e8252ad',
        'runtime_versions':{'python':'literal historical public fixture'},'archive_toolchain':{'libarchive':'libarchive 3.8.5'},
        'limits':{'max_files':20,'max_bytes':50000000,'max_entries':2048,'max_depth':3,'max_seconds':5},
        'runner_policy':{'cpu_seconds':20,'memory_bytes':805306368,'process_limit':32,'file_limit':64,'file_bytes':16777216,
                         'output_bytes':100262148,'timeout_seconds':30}}
    batch.admission=legacy
    result=service.upload(org,actor,project,'tender',None,batch,'literal-historical-v2')
    package=result['package'];detail=service.detail(org,actor,project,package['id'])
    assert detail['manifest']['archive_admission']==legacy
    assert 'max_metadata_bytes' not in detail['manifest']['archive_admission']['limits']
    assert detail['manifest']==service.history(org,actor,project)[0]['manifest']
    for document in detail['documents']:
        data,_=service.download(org,actor,project,package['id'],document['id'])
        assert data==next(file.data for file in batch.leaves+batch.containers if file.path==document['relative_path'])


def test_actual_all_bad_archive_persists_issues_inspection_original_and_one_audit(actual_packages):
    service,engine,org,actor,project=actual_packages
    original=b'PK\x03\x04public corrupt original'
    batch=prepare_files([('bad.zip',original)],settings=service.settings)
    key='actual-all-bad-'+uuid4().hex
    result=service.upload(org,actor,project,'tender',None,batch,key)
    package=result['package'];assert result['inspection_job_id'] is None
    assert service.upload(org,actor,project,'tender',None,batch,key)==result
    detail=service.preflight(org,actor,project,package['id'])
    assert detail['inspection_status']=='inspected' and detail['page_count']==detail['token_count']==0
    assert detail['blockers']==['no_available_documents'] and detail['issues']==batch.issues
    data,_=service.download(org,actor,project,package['id'],package['documents'][0]['id'])
    assert data==original
    with tenant_transaction(engine,org) as db:
        events=list(db.scalars(select(AuditEvent).where(AuditEvent.action=='archive.file.skipped')))
        assert len(events)==1 and events[0].after['code']=='ARCHIVE_CORRUPTED'
        assert db.scalar(select(PackageInspection)).result['issues']==batch.issues
    with pytest.raises(NotFound):service.preflight(str(uuid4()),actor,project,package['id'])


from test_document_artifacts_postgres import actual_documents


def test_actual_normalization_processes_only_surviving_archive_leaves(actual_documents):
    from app.documents.service import STAGE
    from app.jobs.messages import envelope
    from app.jobs.worker import HandlerRegistry,execute_message
    from app.workspace.models import Job,NormalizedArtifact
    from pydantic import SecretStr
    service,engine,org,actor,project=actual_documents
    original=b'public valid archived normalization source\n'
    batch=prepare_files([('good.zip',zipped([('source.txt',original)])),('bad.zip',b'PK\x03\x04bad')],settings=service.settings)
    assert len(batch.leaves)==1 and len(batch.issues)==1
    uploaded=service.packages.upload(org,actor,project,'tender',None,batch,'actual-standard-normalize-'+uuid4().hex)
    package=uploaded['package'];started=service.start(org,actor,project,package['id'],'actual-valid-only',{})
    with tenant_transaction(engine,org) as db:job=db.get(Job,started['job']['id']);input_hash=job.input_hash
    config=service.settings.model_copy(update={'job_signing_secret':SecretStr('j'*48)})
    registry=HandlerRegistry('test');registry.register(STAGE,service.handle)
    message=envelope(org,started['job']['id'],input_hash,'j'*48)
    assert execute_message(engine,config,registry,message)=='succeeded'
    assert execute_message(engine,config,registry,message)=='duplicate'
    listed=service.artifacts(org,actor,project,package['id'],started['job']['id'])
    assert len(listed['artifacts'])==1
    blocks=service.blocks(org,actor,project,package['id'],listed['artifacts'][0]['id'])
    assert blocks['blocks'][0]['original_text']==original.decode()
    assert service.packages.preflight(org,actor,project,package['id'])['issues']==batch.issues
    with tenant_transaction(engine,org) as db:
        row=db.scalar(select(NormalizedArtifact))
        leaf=next(document for document in package['documents'] if document['provenance_role']=='document')
        assert row.document_id==leaf['id']
