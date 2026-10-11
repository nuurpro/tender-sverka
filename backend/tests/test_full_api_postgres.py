"""Restricted-runtime PostgreSQL proof; explicit disposable integration URLs only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import secrets
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from test_full_auth_api import RecordingSms, ORIGIN, auth_headers
from test_full_workspace_api import create_org, project, requirement


def new_phone():
    return "+998" + f"{secrets.randbelow(10**9):09d}"


@pytest.fixture
def pg_api():
    from app.config import Settings
    from app.full_main import create_app
    from app.workspace.database import create_engine
    runtime_url = os.environ.get("WORKSPACE_TEST_RUNTIME_DATABASE_URL")
    if not runtime_url or not os.environ.get("WORKSPACE_TEST_DATABASE_URL"):
        pytest.skip("Explicit PostgreSQL owner/runtime test URLs not configured")
    runtime = create_engine(runtime_url)
    settings = Settings(environment="test", database_url=runtime_url, session_secret="x" * 48, public_origin=ORIGIN)
    sender = RecordingSms()
    instant = [datetime.now(timezone.utc)]
    app = create_app(settings, engine=runtime, sms_sender=sender, clock=lambda: instant[0])
    with TestClient(app, client=(f"pg-test-{uuid4()}", 50000)) as client:
        yield client, runtime, sender, instant, app
    runtime.dispose()


def pg_login(api, phone=None):
    client, _, sender, *_ = api
    phone = phone or new_phone()
    requested = client.post("/api/auth/request", json={"phone": phone}, headers={"Origin": ORIGIN})
    assert requested.status_code == 200, requested.text
    response = client.post("/api/auth/verify", json={"challenge_id": requested.json()["challenge_id"], "code": sender.messages[-1][1]}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()


def test_postgres_sessions_attempts_roles_tenants_and_nested_resources(pg_api):
    from app.workspace.database import tenant_transaction
    from app.workspace.models import AuthSession, Membership, OtpChallenge
    client, runtime, sender, *_ = pg_api
    actor = pg_login(pg_api)["user"]["id"]
    org_a, org_b = create_org(client, "PG A"), create_org(client, "PG B")
    a, b = project(client, org_a), project(client, org_b)
    base_a = f"/api/organizations/{org_a}/projects/{a}"
    base_b = f"/api/organizations/{org_b}/projects/{b}"
    checklist = client.post(base_b + "/checklists", json={"requirements": [requirement()]}, headers=auth_headers(client)).json()
    assert client.get(base_a + "/checklists/" + checklist["id"]).status_code == 404
    assert client.get(f"/api/organizations/{org_a}/projects/{b}").status_code == 404
    with runtime.connect() as connection:
        assert connection.scalar(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")) is False
        assert connection.scalar(text("SELECT count(*) FROM memberships")) == 0
        assert connection.scalar(text("SELECT current_setting('app.user_id', true)")) in (None, "")
    with tenant_transaction(runtime, org_a) as db:
        member = db.scalar(select(Membership).where(Membership.organization_id == org_a, Membership.user_id == actor))
        member.role = "viewer"
    assert client.get(base_a).status_code == 200
    assert client.post(base_a + "/checklists", json={"requirements": []}, headers=auth_headers(client)).status_code == 403
    assert client.get("/api/organizations").json()[0]["role"] == "viewer"
    with tenant_transaction(runtime, org_a) as db:
        db.delete(db.scalar(select(Membership).where(Membership.organization_id == org_a, Membership.user_id == actor)))
    assert client.get(base_a).status_code == 404
    assert [row["id"] for row in client.get("/api/organizations").json()] == [org_b]
    phone = new_phone()
    challenge = client.post("/api/auth/request", json={"phone": phone}, headers={"Origin": ORIGIN}).json()["challenge_id"]
    correct = sender.messages[-1][1]
    wrong = "000000" if correct != "000000" else "111111"
    for _ in range(5):
        assert client.post("/api/auth/verify", json={"challenge_id": challenge, "code": wrong}, headers={"Origin": ORIGIN}).status_code == 401
    with Session(runtime) as db:
        assert db.get(OtpChallenge, challenge).attempts == 5
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == actor)) == 1
    assert client.post("/api/auth/verify", json={"challenge_id": challenge, "code": correct}, headers={"Origin": ORIGIN}).status_code == 401


def test_postgres_concurrent_otp_consumption_creates_one_session(pg_api):
    from app.workspace.models import AuthSession, User
    client, runtime, sender, _, app = pg_api
    phone = new_phone()
    challenge = client.post("/api/auth/request", json={"phone": phone}, headers={"Origin": ORIGIN}).json()["challenge_id"]
    code = sender.messages[-1][1]
    def verify(_):
        with TestClient(app, client=(f"verify-{uuid4()}", 50000)) as worker:
            return worker.post("/api/auth/verify", json={"challenge_id": challenge, "code": code}, headers={"Origin": ORIGIN}).status_code
    with ThreadPoolExecutor(max_workers=6) as executor:
        statuses = list(executor.map(verify, range(6)))
    assert statuses.count(200) == 1 and statuses.count(401) == 5
    with Session(runtime) as db:
        actor = db.scalar(select(User).where(User.phone == phone))
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == actor.id)) == 1


def test_postgres_concurrent_resend_and_failed_delivery_budget(pg_api):
    from app.workspace.models import AuthBudget, OtpChallenge
    _, runtime, sender, _, app = pg_api
    phone = new_phone()
    def request(_):
        with TestClient(app, client=("quota-" + phone, 50000)) as worker:
            return worker.post("/api/auth/request", json={"phone": phone}, headers={"Origin": ORIGIN}).status_code
    with ThreadPoolExecutor(max_workers=6) as executor:
        statuses = list(executor.map(request, range(6)))
    assert statuses.count(200) == 1 and statuses.count(429) == 5
    with Session(runtime) as db:
        assert len(db.scalar(select(AuthBudget).where(AuthBudget.key == "phone:" + phone)).events) == 1
        assert db.scalar(select(func.count()).select_from(OtpChallenge).where(OtpChallenge.phone == phone)) == 1
    sender.fail = True
    other_phone = new_phone()
    with TestClient(app, client=("failed-" + other_phone, 50000)) as worker:
        assert worker.post("/api/auth/request", json={"phone": other_phone}, headers={"Origin": ORIGIN}).status_code == 503
        assert worker.post("/api/auth/request", json={"phone": other_phone}, headers={"Origin": ORIGIN}).status_code == 429
    with Session(runtime) as db:
        assert db.scalar(select(OtpChallenge).where(OtpChallenge.phone == other_phone)).delivered is False


def test_postgres_concurrent_admin_demotion_preserves_last_admin(pg_api):
    from app.workspace.database import tenant_transaction
    from app.workspace.models import Membership
    client, runtime, _, _, app = pg_api
    first = pg_login(pg_api)
    first_cookie = client.cookies.get("tender_session")
    org = create_org(client)
    second_phone = new_phone()
    second = pg_login(pg_api, second_phone)
    second_cookie = client.cookies.get("tender_session")
    client.cookies.clear()
    client.cookies.set("tender_session", first_cookie)
    base = f"/api/organizations/{org}/memberships"
    assert client.post(base, json={"phone": second_phone, "role": "admin"}, headers=auth_headers(client)).status_code == 200
    def demote(pair):
        user, cookie = pair
        with TestClient(app, client=("admin-" + user["user"]["id"], 50000)) as worker:
            worker.cookies.set("tender_session", cookie)
            return worker.patch(base + "/" + user["user"]["id"], json={"role": "viewer", "reason": "Concurrent demotion"}, headers=auth_headers(worker)).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(demote, [(first, first_cookie), (second, second_cookie)]))
    assert sorted(statuses) == [200, 409]
    with tenant_transaction(runtime, org) as db:
        assert db.scalar(select(func.count()).select_from(Membership).where(Membership.organization_id == org, Membership.role == "admin")) == 1
