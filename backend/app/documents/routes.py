"""Authenticated nested normalization and immutable evidence reads."""
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import current_user
from app.packages.routes import acquire_admission, AdmittedResponse, guarded_document_thread
from .service import DocumentService, NormalizationOptions

router = APIRouter(prefix='/api/organizations/{organization_id}/projects/{project_id}/packages/{package_id}')


def service(request):
    injected = getattr(request.app.state,'documents',None)
    if injected is not None and request.app.state.settings.environment=='test':
        return injected
    return DocumentService(request.app.state.engine,request.app.state.settings,packages=request.app.state.packages)


@router.post('/normalize')
async def start(organization_id:UUID,project_id:UUID,package_id:UUID,body:NormalizationOptions,request:Request,user=Depends(current_user)):
    return await run_in_threadpool(service(request).start,str(organization_id),user.id,str(project_id),str(package_id),
        request.headers.get('idempotency-key'),body.model_dump(),refresh_auth=lambda:current_user(request))


@router.get('/normalizations')
def normalizations(organization_id:UUID,project_id:UUID,package_id:UUID,request:Request,user=Depends(current_user)):
    return service(request).normalizations(str(organization_id),user.id,str(project_id),str(package_id))


@router.get('/normalizations/{job_id}/artifacts')
def artifacts(organization_id:UUID,project_id:UUID,package_id:UUID,job_id:UUID,request:Request,user=Depends(current_user)):
    return service(request).artifacts(str(organization_id),user.id,str(project_id),str(package_id),str(job_id))


async def admitted_read(request, method, args, **kwargs):
    gate = acquire_admission(request)
    try:
        result = await guarded_document_thread(method,*args,refresh_auth=lambda:current_user(request),**kwargs)
        return AdmittedResponse(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode(),gate=gate,
            media_type='application/json',headers={'X-Content-Type-Options':'nosniff'})
    except BaseException:
        gate.release()
        raise


@router.get('/artifacts/{artifact_id}')
async def detail(organization_id:UUID,project_id:UUID,package_id:UUID,artifact_id:UUID,request:Request,user=Depends(current_user)):
    return await admitted_read(request,service(request).detail,
        (str(organization_id),user.id,str(project_id),str(package_id),str(artifact_id)))


@router.get('/artifacts/{artifact_id}/blocks')
async def blocks(organization_id:UUID,project_id:UUID,package_id:UUID,artifact_id:UUID,request:Request,
                 offset:int=Query(default=0,ge=0),limit:int=Query(default=100,ge=1,le=100),user=Depends(current_user)):
    return await admitted_read(request,service(request).blocks,
        (str(organization_id),user.id,str(project_id),str(package_id),str(artifact_id)),offset=offset,limit=limit)


@router.get('/artifacts/{artifact_id}/blocks/{block_id}')
async def block(organization_id:UUID,project_id:UUID,package_id:UUID,artifact_id:UUID,block_id:str,request:Request,user=Depends(current_user)):
    return await admitted_read(request,service(request).block,
        (str(organization_id),user.id,str(project_id),str(package_id),str(artifact_id),block_id))
