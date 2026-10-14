"""Authenticated, nested extraction and versioned admin consent."""
from uuid import UUID
from fastapi import APIRouter,Depends,Request
from app.auth.dependencies import current_user
from app.full_workspace.dependencies import scoped_repository,admin_repository
from .schemas import AnalysisOptions,Strict
from .service import AnalysisService,current_policy,update_policy
from pydantic import Field

router=APIRouter(prefix='/api/organizations')


class PolicyChange(Strict):
    allowed: bool
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1,max_length=2000)


def service(request):
    injected=getattr(request.app.state,'analysis',None)
    if injected is not None and request.app.state.settings.environment=='test':return injected
    return AnalysisService(request.app.state.engine,request.app.state.settings)


@router.get('/{organization_id}/ai-policy')
def policy(repo=Depends(scoped_repository)):
    return current_policy(repo)


@router.put('/{organization_id}/ai-policy')
def change_policy(body:PolicyChange,repo=Depends(admin_repository)):
    return update_policy(repo,body.allowed,body.expected_version,body.reason)


@router.post('/{organization_id}/projects/{project_id}/packages/{package_id}/analysis/preflight')
def preflight(organization_id:UUID,project_id:UUID,package_id:UUID,body:AnalysisOptions,request:Request,user=Depends(current_user)):
    return service(request).preflight(str(organization_id),user.id,str(project_id),str(package_id),body.model_dump(mode='json'))


@router.post('/{organization_id}/projects/{project_id}/packages/{package_id}/analysis')
def start(organization_id:UUID,project_id:UUID,package_id:UUID,body:AnalysisOptions,request:Request,user=Depends(current_user)):
    return service(request).start(str(organization_id),user.id,str(project_id),str(package_id),request.headers.get('idempotency-key'),body.model_dump(mode='json'),refresh_auth=lambda:current_user(request))


@router.get('/{organization_id}/projects/{project_id}/packages/{package_id}/analysis')
def history(organization_id:UUID,project_id:UUID,package_id:UUID,request:Request,user=Depends(current_user)):
    return service(request).history(str(organization_id),user.id,str(project_id),str(package_id))


@router.get('/{organization_id}/projects/{project_id}/analysis/{run_id}')
def detail(organization_id:UUID,project_id:UUID,run_id:UUID,request:Request,user=Depends(current_user)):
    return service(request).detail(str(organization_id),user.id,str(project_id),str(run_id))
