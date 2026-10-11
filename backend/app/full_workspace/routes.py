from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.auth.dependencies import current_user, organizations
from app.workspace.database import tenant_transaction
from app.workspace.models import AuditEvent, Membership, Organization, User
from app.workspace.repository import Conflict, NotFound, snapshot
from .dependencies import admin_repository, nested_checklist, scoped_repository
from .schemas import (ChecklistCreate, ChecklistSave, Empty, MembershipChange,
    MembershipCreate, Named, ParticipantCreate, ProjectCreate, Reason, Revision)

router = APIRouter(prefix="/api/organizations")


@router.get("")
def list_organizations(request: Request, user=Depends(current_user)):
    return organizations(request.app.state.engine, user)


@router.post("")
def create_organization(body: Named, request: Request, user=Depends(current_user)):
    org_id = str(uuid4())
    with tenant_transaction(request.app.state.engine, org_id) as db:
        org = Organization(id=org_id, name=body.name.strip())
        db.add(org)
        db.flush()
        member = Membership(organization_id=org_id, user_id=user.id, role="admin")
        db.add(member)
        db.flush()
        db.add(AuditEvent(organization_id=org_id, actor_id=user.id, action="organization.created",
            entity_type="organizations", entity_id=org_id, after={"name": org.name}))
        return {"id": org.id, "name": org.name, "role": "admin"}


@router.get("/{organization_id}/projects")
def list_projects(repo=Depends(scoped_repository)):
    return [snapshot(row) for row in repo.list_projects()]


@router.post("/{organization_id}/projects")
def create_project(body: ProjectCreate, repo=Depends(scoped_repository)):
    return snapshot(repo.create_project(**body.model_dump()))


@router.get("/{organization_id}/projects/{project_id}")
def project_detail(project_id: UUID, repo=Depends(scoped_repository)):
    return snapshot(repo.get_project(str(project_id)))


@router.get("/{organization_id}/projects/{project_id}/participants")
def participants(project_id: UUID, repo=Depends(scoped_repository)):
    return [snapshot(row) for row in repo.list_participants(str(project_id))]


@router.post("/{organization_id}/projects/{project_id}/participants")
def add_participant(project_id: UUID, body: ParticipantCreate, repo=Depends(scoped_repository)):
    return snapshot(repo.create_participant(str(project_id), **body.model_dump()))


@router.get("/{organization_id}/projects/{project_id}/checklists")
def history(project_id: UUID, repo=Depends(scoped_repository)):
    return [snapshot(row) for row in repo.list_checklist_history(str(project_id))]


@router.post("/{organization_id}/projects/{project_id}/checklists")
def create_checklist(project_id: UUID, body: ChecklistCreate, repo=Depends(scoped_repository)):
    return repo._checklist_snapshot(repo.create_checklist_draft(str(project_id), body.values()))


@router.get("/{organization_id}/projects/{project_id}/checklists/{checklist_id}")
def checklist_detail(project_id: UUID, checklist_id: UUID, repo=Depends(scoped_repository)):
    return repo._checklist_snapshot(nested_checklist(repo, project_id, checklist_id))


@router.put("/{organization_id}/projects/{project_id}/checklists/{checklist_id}")
def save_checklist(project_id: UUID, checklist_id: UUID, body: ChecklistSave, repo=Depends(scoped_repository)):
    nested_checklist(repo, project_id, checklist_id)
    return repo._checklist_snapshot(repo.save_checklist_draft(str(checklist_id), body.values(), body.expected_revision, body.reason))


@router.post("/{organization_id}/projects/{project_id}/checklists/{checklist_id}/approve")
def approve_checklist(project_id: UUID, checklist_id: UUID, body: Revision, repo=Depends(scoped_repository)):
    nested_checklist(repo, project_id, checklist_id)
    return repo._checklist_snapshot(repo.approve_checklist(str(checklist_id), body.expected_revision))


@router.post("/{organization_id}/projects/{project_id}/checklists/{checklist_id}/fork")
def fork_checklist(project_id: UUID, checklist_id: UUID, body: Empty, repo=Depends(scoped_repository)):
    nested_checklist(repo, project_id, checklist_id)
    return repo._checklist_snapshot(repo.fork_checklist(str(checklist_id)))


@router.get("/{organization_id}/audit")
def org_audit(repo=Depends(scoped_repository)):
    return [snapshot(row) for row in repo.list_audit()]


@router.get("/{organization_id}/projects/{project_id}/audit")
def project_audit(project_id: UUID, repo=Depends(scoped_repository)):
    return [snapshot(row) for row in repo.list_audit(str(project_id))]


@router.get("/{organization_id}/memberships")
def memberships(repo=Depends(admin_repository)):
    return [snapshot(row) for row in repo.session.scalars(repo._query(Membership))]


@router.post("/{organization_id}/memberships")
def add_membership(body: MembershipCreate, repo=Depends(admin_repository)):
    user = repo.session.scalar(select(User).where(User.phone == body.phone, User.active.is_(True)))
    if not user:
        raise NotFound()
    if repo.session.scalar(repo._query(Membership).where(Membership.user_id == user.id)):
        raise Conflict()
    member = Membership(organization_id=repo.organization_id, user_id=user.id, role=body.role)
    repo.session.add(member)
    repo.session.flush()
    repo._audit("membership.added", member, after=snapshot(member))
    return snapshot(member)


def existing_member(repo, user_id):
    member = repo.session.scalar(repo._query(Membership).where(Membership.user_id == str(user_id)).with_for_update())
    if not member:
        raise NotFound()
    return member


def protect_last_admin(repo, member):
    if member.role == "admin":
        admins = list(repo.session.scalars(repo._query(Membership).where(Membership.role == "admin")))
        if len(admins) <= 1:
            raise Conflict()


@router.patch("/{organization_id}/memberships/{user_id}")
def change_membership(user_id: UUID, body: MembershipChange, repo=Depends(admin_repository)):
    member = existing_member(repo, user_id)
    if body.role != "admin":
        protect_last_admin(repo, member)
    before = snapshot(member)
    member.role = body.role
    repo.session.flush()
    repo._audit("membership.role_changed", member, before=before, after=snapshot(member), reason=body.reason)
    return snapshot(member)


@router.delete("/{organization_id}/memberships/{user_id}")
def remove_membership(user_id: UUID, body: Reason, repo=Depends(admin_repository)):
    member = existing_member(repo, user_id)
    protect_last_admin(repo, member)
    before = snapshot(member)
    repo._audit("membership.removed", member, before=before, reason=body.reason)
    repo.session.delete(member)
    repo.session.flush()
    return {"ok": True}
