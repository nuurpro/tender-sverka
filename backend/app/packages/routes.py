import asyncio
from contextlib import contextmanager
import threading
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import current_user
from app.workspace.repository import ValidationError
from .inputs import prepare_files,receive_multipart

router=APIRouter(prefix='/api/organizations/{organization_id}/projects/{project_id}')


def acquire_admission(request):
    gate=request.app.state.document_admission
    if not gate.acquire(blocking=False):
        raise HTTPException(503,'Обработка документов занята. Повторите позже.')
    return gate


class AdmittedResponse(Response):
    def __init__(self,*args,gate,**kwargs):
        super().__init__(*args,**kwargs)
        self.gate=gate

    async def __call__(self,scope,receive,send):
        try:
            await super().__call__(scope,receive,send)
        finally:
            self.body=b''
            self.gate.release()


@contextmanager
def admission(request):
    # One bounded gate covers receiving, expansion, encryption and download;
    # admission is immediate, so waiting requests cannot accumulate RAM bodies.
    gate=acquire_admission(request)
    try:
        yield
    finally:
        gate.release()


def identifiers(organization_id,project_id):
    return str(organization_id),str(project_id)


async def guarded_document_thread(function,*args,cancellation=None,**kwargs):
    # asyncio cancellation cannot stop a Python I/O thread. Keep its admission
    # slot until it finishes, signaling upload cleanup before propagating cancel.
    operation=asyncio.create_task(run_in_threadpool(function,*args,**kwargs))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        if cancellation is not None:
            cancellation.set()
        while not operation.done():
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            operation.result()
        except BaseException:
            pass
        raise


@router.post('/packages')
async def upload(organization_id:UUID,project_id:UUID,request:Request,user=Depends(current_user)):
    service=request.app.state.packages
    org,project=identifiers(organization_id,project_id)
    await run_in_threadpool(service.authorize,org,user.id,project,mutate=True)
    with admission(request):
        try:
            async with asyncio.timeout(120):
                fields,files=await receive_multipart(request.stream(),request.headers.get('content-type',''),
                    max_files=request.app.state.settings.max_package_files,max_bytes=request.app.state.settings.max_package_bytes)
        except TimeoutError:
            raise ValidationError('Upload timed out') from None
        batch=await guarded_document_thread(prepare_files,files,max_files=request.app.state.settings.max_package_files,
            max_bytes=request.app.state.settings.max_package_bytes)
        # No SQL transaction survives body/network I/O; session is checked again
        # after storage and current membership is locked at final commit.
        cancelled=threading.Event()
        return await guarded_document_thread(service.upload,org,user.id,project,fields.get('kind'),fields.get('participant_id') or None,
            batch,request.headers.get('idempotency-key'),cancellation=cancelled,
            cancelled=cancelled,refresh_auth=lambda:current_user(request))


@router.get('/packages')
def history(organization_id:UUID,project_id:UUID,request:Request,user=Depends(current_user)):
    return request.app.state.packages.history(str(organization_id),user.id,str(project_id))


@router.get('/packages/{package_id}')
def detail(organization_id:UUID,project_id:UUID,package_id:UUID,request:Request,user=Depends(current_user)):
    return request.app.state.packages.detail(str(organization_id),user.id,str(project_id),str(package_id))


@router.get('/packages/{package_id}/preflight')
def preflight(organization_id:UUID,project_id:UUID,package_id:UUID,request:Request,user=Depends(current_user)):
    return request.app.state.packages.preflight(str(organization_id),user.id,str(project_id),str(package_id))


@router.get('/packages/{package_id}/documents/{document_id}/download')
async def download(organization_id:UUID,project_id:UUID,package_id:UUID,document_id:UUID,request:Request,user=Depends(current_user)):
    gate=acquire_admission(request)
    try:
        data,metadata=await guarded_document_thread(request.app.state.packages.download,str(organization_id),user.id,str(project_id),str(package_id),str(document_id),
                                                        refresh_auth=lambda:current_user(request))
        name=metadata['name'].replace('\\','/').split('/')[-1]
        return AdmittedResponse(data,gate=gate,media_type='application/octet-stream',headers={'Cache-Control':'no-store',
            'X-Content-Type-Options':'nosniff','Content-Disposition':"attachment; filename=\"document\"; filename*=UTF-8''"+quote(name,safe='')})
    except BaseException:
        gate.release()
        raise
