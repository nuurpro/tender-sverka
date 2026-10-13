"""Actual encrypted OCR evidence uses the same fenced ownership/storage path."""
import os
import pytest
from sqlalchemy import select,func

from test_document_artifacts_postgres import actual_documents
from test_package_storage_postgres import actual_packages,real_storage
from test_image_ocr import image_fixture,real_ocr
from app.documents.service import DocumentService
from app.documents.runner import DocumentRunnerError
from app.packages.inputs import prepare_files
from app.workspace.database import tenant_transaction
from app.workspace.models import NormalizedArtifact,ArtifactUploadAttempt,Membership,Job
from app.jobs.service import JobService
from app.jobs.worker import ExecutionContext


@pytest.fixture
def actual_ocr_documents(actual_documents):
    service,engine,org,actor,project=actual_documents
    config=service.settings.model_copy(update={'document_ocr_mode':'tesseract'})
    return DocumentService(engine,config,packages=service.packages),engine,org,actor,project


def upload_ocr(fixture,key='original',name='public.png',source=None):
    service,_,org,actor,project=fixture
    source=image_fixture() if source is None else source
    original=service.packages.upload(org,actor,project,'tender',None,prepare_files([(name,source)]),'upload-'+key)['package']
    response=service.start(org,actor,project,original['id'],'ocr-'+key,{})
    return original,response,source


def execute_ocr(fixture,response):
    service,engine,org,actor,project=fixture
    jobs=JobService(engine,org)
    claim=jobs.claim(response['job']['id'])
    service.handle(ExecutionContext(jobs,response['job']['id'],claim))
    jobs.finish(response['job']['id'],claim.token)
    return claim


@real_ocr
def test_actual_ocr_ciphertext_source_binding_cache_and_duplicate_delivery(actual_ocr_documents):
    service,engine,org,actor,project=actual_ocr_documents
    original,response,source=upload_ocr(actual_ocr_documents)
    execute_ocr(actual_ocr_documents,response)
    artifact=service.artifacts(org,actor,project,original['id'],response['job']['id'])['artifacts'][0]
    blocks=service.blocks(org,actor,project,original['id'],artifact['id'])
    assert blocks['schema_version']=='2'
    assert any(b['transcription_text']=='16' and 'numeric_ocr_unverified' in b['review_flags'] for b in blocks['blocks'])
    assert all('original_text' not in b for b in blocks['blocks'])
    assert blocks['document_id']==original['documents'][0]['id'] and not blocks['analysis_allowed']
    with tenant_transaction(engine,org) as db:
        row=db.get(NormalizedArtifact,artifact['id'])
        assert b'Memory' not in service.packages.store.get(row.object_key)
        assert db.get(ArtifactUploadAttempt,row.attempt_id).status=='committed'
    # A project-qualified cache rebinds to the new immutable original while
    # retaining identical frame pixels and profile identity.
    second,next_run,_=upload_ocr(actual_ocr_documents,'renamed','renamed.png',source)
    service.parser=lambda *a,**k: pytest.fail('healthy project-qualified OCR cache was not reused')
    execute_ocr(actual_ocr_documents,next_run)
    next_artifact=service.artifacts(org,actor,project,second['id'],next_run['job']['id'])['artifacts'][0]
    detail=service.blocks(org,actor,project,second['id'],next_artifact['id'])
    assert detail['document_id']==second['documents'][0]['id'] and detail['file_hash']==blocks['file_hash']
    with tenant_transaction(engine,org) as db:
        assert db.scalar(select(func.count()).select_from(NormalizedArtifact).where(NormalizedArtifact.job_id==next_run['job']['id']))==1
    jobs=JobService(engine,org)
    assert jobs.claim(next_run['job']['id']) is None


@real_ocr
@pytest.mark.parametrize('fault',['cancel','viewer'])
def test_actual_ocr_cancel_or_role_change_during_storage_prevents_fenced_publication(actual_ocr_documents,fault):
    from fastapi import HTTPException
    from app.jobs.service import LeaseLost
    service,engine,org,actor,project=actual_ocr_documents
    original,response,_=upload_ocr(actual_ocr_documents,fault)
    jobs=JobService(engine,org);claim=jobs.claim(response['job']['id'])
    put=service.packages.store.put
    def changed(key,data):
        put(key,data)
        if fault=='cancel':
            with tenant_transaction(engine,org) as db:
                jobs.cancel_in(db,response['job']['id'],project,actor,'cancel-ocr',{})
        else:
            with tenant_transaction(engine,org) as db:
                db.scalar(select(Membership).where(Membership.user_id==actor)).role='viewer'
    service.packages.store.put=changed
    try:
        with pytest.raises((LeaseLost,HTTPException)):
            service.handle(ExecutionContext(jobs,response['job']['id'],claim))
    finally:
        service.packages.store.put=put
    with tenant_transaction(engine,org) as db:
        assert db.scalar(select(func.count()).select_from(NormalizedArtifact))==0
        attempt=db.scalar(select(ArtifactUploadAttempt))
        assert attempt.status=='cleaned'
    assert service.packages.download(org,actor,project,original['id'],original['documents'][0]['id'])[0]


@real_ocr
def test_actual_ocr_retry_keeps_healthy_checkpoint_and_failed_attempt_history(actual_ocr_documents):
    from app.workspace.repository import ValidationError
    service,engine,org,actor,project=actual_ocr_documents
    original=service.packages.upload(org,actor,project,'tender',None,
        prepare_files([('a.png',image_fixture()),('b.jpeg',image_fixture('JPEG'))]),'retry-original')['package']
    response=service.start(org,actor,project,original['id'],'retry-ocr',{})
    real=service.parser;calls=[]
    def unstable(source,name,*args,**kwargs):
        calls.append(name)
        if name=='b.jpeg' and calls.count(name)==1:
            raise DocumentRunnerError('parser_failed','Synthetic process fault after healthy OCR checkpoint')
        return real(source,name,*args,**kwargs)
    service.parser=unstable
    jobs=JobService(engine,org);claim=jobs.claim(response['job']['id'])
    with pytest.raises(ValidationError):
        service.handle(ExecutionContext(jobs,response['job']['id'],claim))
    jobs.fail(response['job']['id'],claim.token,'handler_failed')
    with tenant_transaction(engine,org) as db:
        jobs.retry_in(db,response['job']['id'],project,actor,'retry-ocr',{})
    execute_ocr(actual_ocr_documents,response)
    assert calls==['a.png','b.jpeg','b.jpeg']
    artifacts=service.artifacts(org,actor,project,original['id'],response['job']['id'])['artifacts']
    assert len(artifacts)==3 and sum(a['format']=='unsupported' for a in artifacts)==1
    assert {a['attempt'] for a in artifacts}=={1,2}
