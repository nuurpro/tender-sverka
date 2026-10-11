from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from app.auth.dependencies import current_user
from app.workspace.database import tenant_transaction
from app.workspace.models import Membership, Organization
from app.workspace.repository import NotFound, Repository


def scoped_repository(organization_id: UUID, request: Request, user=Depends(current_user)):
    org_id = str(organization_id)
    with tenant_transaction(request.app.state.engine, org_id) as db:
        unsafe = request.method not in {"GET", "HEAD", "OPTIONS"}
        # Membership administration and every unsafe tenant request share this lock.
        # Authorization is therefore current when the tenant mutation commits.
        if unsafe:
            org = db.scalar(select(Organization).where(Organization.id == org_id).with_for_update())
            if not org:
                raise NotFound()
        statement = select(Membership).where(Membership.organization_id == org_id, Membership.user_id == user.id)
        if unsafe:
            statement = statement.with_for_update()
        membership = db.scalar(statement)
        if not membership:
            raise NotFound()
        if unsafe and membership.role == "viewer":
            raise HTTPException(403, "Недостаточно прав для изменения данных.")
        repo = Repository(db, org_id, user.id)
        repo.actor_role = membership.role
        yield repo


def admin_repository(repo=Depends(scoped_repository)):
    if repo.actor_role != "admin":
        raise HTTPException(403, "Требуются права администратора.")
    return repo


def nested_checklist(repo, project_id, checklist_id):
    repo.get_project(str(project_id))
    checklist = repo.get_checklist(str(checklist_id))
    if checklist.project_id != str(project_id):
        raise NotFound()
    return checklist
