from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from test_full_auth_api import full_api, login, auth_headers, ORIGIN


def create_org(client, name="Company"):
    response = client.post("/api/organizations", json={"name": name}, headers=auth_headers(client))
    assert response.status_code == 200, response.text
    return response.json()["id"]


def project(client, org, mode="buyer"):
    response = client.post(f"/api/organizations/{org}/projects", json={"mode": mode, "lot_name": "Tender"}, headers=auth_headers(client))
    assert response.status_code == 200, response.text
    return response.json()["id"]


def requirement(**extra):
    return {"display_id": "T-1", "scope": "product", "category": "technical", "importance": "mandatory",
            "parameter": "Quantity", "condition": "At least one", **extra}


def test_projects_participants_and_nested_checklist_history(full_api):
    client, *_ = full_api
    assert login(full_api).status_code == 200
    org = create_org(client)
    pid, other = project(client, org, "supplier"), project(client, org)
    base = f"/api/organizations/{org}/projects/{pid}"
    headers = auth_headers(client)
    assert len(client.get(f"/api/organizations/{org}/projects").json()) == 2
    assert client.get(base).json()["mode"] == "supplier"
    assert client.post(base + "/participants", json={"name": "Us", "slot": 1}, headers=headers).status_code == 200
    assert client.post(base + "/participants", json={"name": "Other", "slot": 2}, headers=headers).status_code == 422
    assert len(client.get(base + "/participants").json()) == 1
    response = client.post(base + "/checklists", json={"requirements": [requirement()]}, headers=headers)
    assert response.status_code == 200, response.text
    initial = response.json()
    assert initial["status"] == "draft" and initial["requirements"][0]["logical_id"]
    path = base + "/checklists/" + initial["id"]
    wrong = f"/api/organizations/{org}/projects/{other}/checklists/{initial['id']}"
    assert client.get(wrong).status_code == 404
    assert client.post(wrong + "/approve", json={"expected_revision": 1}, headers=headers).status_code == 404
    assert client.put(path, json={"requirements": [], "expected_revision": 1}, headers=headers).status_code == 422
    payload = {"requirements": [requirement(item="Edited", logical_id=initial["requirements"][0]["logical_id"])], "expected_revision": 1, "reason": "Clarification"}
    saved = client.put(path, json=payload, headers=headers)
    assert saved.status_code == 200 and saved.json()["revision"] == 2
    assert client.put(path, json=payload, headers=headers).status_code == 409
    approved = client.post(path + "/approve", json={"expected_revision": 2}, headers=headers)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert client.put(path, json={**payload, "expected_revision": 3}, headers=headers).status_code == 409
    forked = client.post(path + "/fork", json={}, headers=headers)
    assert forked.status_code == 200 and forked.json()["parent_version_id"] == initial["id"]
    assert len(client.get(base + "/checklists").json()) == 2
    assert client.get(path).json()["requirements"][0]["item"] == "Edited"
    assert any(row["reason"] == "Clarification" for row in client.get(base + "/audit").json())


def test_membership_role_revocation_and_foreign_resources(full_api):
    from app.workspace.models import Membership
    client, engine, *_ = full_api
    assert login(full_api).status_code == 200
    user_id = client.get("/api/auth/session").json()["user"]["id"]
    org = create_org(client)
    pid = project(client, org)
    base = f"/api/organizations/{org}"
    assert client.get(f"/api/organizations/{uuid4()}/projects").status_code == 404
    with Session(engine) as db, db.begin():
        db.scalar(select(Membership).where(Membership.user_id == user_id)).role = "viewer"
    assert client.get(base + "/projects").status_code == 200
    assert client.post(base + "/projects", json={"mode": "buyer", "lot_name": "No"}, headers=auth_headers(client)).status_code == 403
    assert client.post(base + f"/projects/{pid}/checklists", json={"requirements": []}, headers=auth_headers(client)).status_code == 403
    assert client.patch(base + f"/memberships/{user_id}", json={"role": "admin", "reason": "Escalation"}, headers=auth_headers(client)).status_code == 403
    with Session(engine) as db, db.begin():
        db.delete(db.scalar(select(Membership).where(Membership.user_id == user_id)))
    assert client.get(base + "/projects").status_code == 404
    assert client.get("/api/organizations").json() == []


def test_admin_membership_changes_are_audited_and_last_admin_protected(full_api):
    client, *_ = full_api
    login(full_api)
    own_id = client.get("/api/auth/session").json()["user"]["id"]
    org = create_org(client)
    base = f"/api/organizations/{org}/memberships"
    headers = auth_headers(client)
    assert client.patch(base + f"/{own_id}", json={"role": "viewer", "reason": "Change"}, headers=headers).status_code == 409
    assert client.request("DELETE", base + f"/{own_id}", json={"reason": "Remove"}, headers=headers).status_code == 409
    assert client.post(base, json={"phone": "+998909999999", "role": "admin"}, headers=headers).status_code == 404
    first_cookie = client.cookies.get("tender_session")
    login(full_api, "+998909999999")
    other_id = client.get("/api/auth/session").json()["user"]["id"]
    assert client.get("/api/organizations").json() == []
    assert client.get(f"/api/organizations/{org}/projects").status_code == 404
    client.cookies.set("tender_session", first_cookie)
    headers = auth_headers(client)
    added = client.post(base, json={"phone": "+998909999999", "role": "admin"}, headers=headers)
    assert added.status_code == 200, added.text
    assert client.patch(base + f"/{own_id}", json={"role": "specialist", "reason": "Delegate"}, headers=headers).status_code == 200
    assert client.post(base, json={"phone": "+998901234567", "role": "admin"}, headers=headers).status_code == 403
    assert client.post(f"/api/organizations/{org}/projects", json={"mode": "buyer", "lot_name": "Specialist works"}, headers=headers).status_code == 200
    audit = client.get(f"/api/organizations/{org}/audit").json()
    assert any(row["action"] == "membership.role_changed" and row["reason"] == "Delegate" for row in audit)


def test_typed_validation_limits_and_safe_errors(full_api):
    client, *_ = full_api
    login(full_api)
    headers = auth_headers(client)
    org = create_org(client)
    endpoint = f"/api/organizations/{org}/projects"
    for payload in [dict(mode="buyer", lot_name=" "), dict(mode="buyer", lot_name="X", deadline="2026-10-12T12:00:00"),
                    dict(mode="buyer", lot_name="X", report_language="de"), dict(mode="buyer", lot_name="X", secret="never-echo-this")]:
        response = client.post(endpoint, json=payload, headers=headers)
        assert response.status_code == 422 and "never-echo-this" not in response.text
    assert client.post(endpoint, json={"mode": "buyer", "lot_name": "X"}, headers={**headers, "Origin": "https://evil.example"}).status_code == 403
