import hmac
from contextlib import contextmanager

from fastapi import HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.workspace.models import Membership, Organization


def require_origin(request: Request):
    if request.headers.get("origin") != request.app.state.settings.public_origin:
        raise HTTPException(403, "Недопустимый источник запроса.")


def current_user(request: Request):
    auth = request.app.state.auth
    raw = request.cookies.get(request.app.state.cookie_name)
    user = auth.authenticate(raw)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(supplied.encode(), auth.csrf(raw).encode()):
            raise HTTPException(403, "Не удалось проверить защиту запроса.")
    return user


@contextmanager
def identity_transaction(engine, user_id):
    with Session(engine, expire_on_commit=False) as db, db.begin():
        if engine.dialect.name == "postgresql":
            db.execute(text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": user_id})
        yield db


def organizations(engine, user):
    with identity_transaction(engine, user.id) as db:
        rows = db.execute(select(Organization, Membership.role).join(Membership,
            Membership.organization_id == Organization.id).where(Membership.user_id == user.id)
            .order_by(Organization.created_at, Organization.id)).all()
        return [{"id": org.id, "name": org.name, "role": role} for org, role in rows]


def session_payload(request, user, raw):
    return {"user": {"id": user.id, "phone": user.phone, "display_name": user.display_name},
        "csrf_token": request.app.state.auth.csrf(raw),
        "organizations": organizations(request.app.state.engine, user)}
