"""Authenticated full API: only injected SMS, migrated schemas and real cookies."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

ORIGIN = "http://localhost:3000"


class RecordingSms:
    def __init__(self):
        self.messages = []
        self.fail = False

    def send(self, phone, code):
        if self.fail:
            raise RuntimeError("sensitive-provider-body")
        self.messages.append((phone, code))


@pytest.fixture
def full_api(tmp_path, monkeypatch):
    from app.config import Settings
    from app.workspace.database import create_engine
    try:
        from app import full_main
    except ImportError:
        pytest.fail("Authenticated full application entrypoint is missing")
    url = f"sqlite:///{(tmp_path / 'auth.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ALEMBIC_ALLOW_SQLITE_TESTS", "1")
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url, allow_sqlite_for_tests=True, connect_args={"check_same_thread": False})
    settings = Settings(environment="test", database_url=url, session_secret="x" * 48, public_origin=ORIGIN)
    sender = RecordingSms()
    instant = [datetime(2026, 10, 12, tzinfo=timezone.utc)]
    app = full_main.create_app(settings, engine=engine, sms_sender=sender, clock=lambda: instant[0])
    with TestClient(app) as client:
        yield client, engine, sender, instant, app
    engine.dispose()


def request_code(client, phone="+998 (90) 123-45-67"):
    return client.post("/api/auth/request", json={"phone": phone}, headers={"Origin": ORIGIN})


def login(api, phone="+998901234567"):
    client, _, sender, _, _ = api
    response = request_code(client, phone)
    assert response.status_code == 200, response.text
    return client.post("/api/auth/verify", json={"challenge_id": response.json()["challenge_id"],
        "code": sender.messages[-1][1]}, headers={"Origin": ORIGIN})


def auth_headers(client):
    return {"Origin": ORIGIN, "X-CSRF-Token": client.get("/api/auth/session").json()["csrf_token"]}


def test_full_entrypoint_has_no_anonymous_mvp_or_demo_identity(full_api):
    client, *_ = full_api
    assert client.get("/api/auth/session").status_code == 401
    assert client.get("/api/organizations").status_code == 401
    assert client.get("/checks").status_code == 404
    assert client.get("/api/organizations", headers={"Authorization": "Bearer demo", "X-User-Id": str(uuid4())}).status_code == 401


def test_phone_login_stores_hashes_and_uses_cookie_and_csrf(full_api):
    from app.workspace.models import User, OtpChallenge, AuthSession
    client, engine, sender, _, _ = full_api
    requested = request_code(client)
    assert requested.status_code == 200
    assert set(requested.json()) == {"challenge_id", "expires_in"}
    assert sender.messages[-1][0] == "+998901234567"
    code = sender.messages[-1][1]
    assert len(code) == 6 and code.isascii() and code.isdigit()
    with Session(engine) as db:
        challenge = db.get(OtpChallenge, requested.json()["challenge_id"])
        assert challenge.code_digest != code and len(challenge.code_digest) == 64
    verified = client.post("/api/auth/verify", json={"challenge_id": requested.json()["challenge_id"], "code": code}, headers={"Origin": ORIGIN})
    assert verified.status_code == 200
    assert "HttpOnly" in verified.headers["set-cookie"] and "SameSite=lax" in verified.headers["set-cookie"]
    assert verified.headers["cache-control"] == "no-store"
    assert "token" not in verified.json()
    assert client.get("/api/auth/session").json()["user"]["phone"] == "+998901234567"
    with Session(engine) as db:
        assert db.scalar(select(User)).email is None
        stored = db.scalar(select(AuthSession))
        assert stored.verifier != client.cookies.get("tender_session")
    assert client.post("/api/organizations", json={"name": "A"}, headers={"Origin": ORIGIN}).status_code == 403
    assert client.post("/api/organizations", json={"name": "A"}, headers=auth_headers(client)).status_code == 200
    assert client.post("/api/auth/logout", headers=auth_headers(client)).status_code == 200
    assert client.get("/api/auth/session").status_code == 401


def test_origin_phone_validation_and_no_input_reflection(full_api):
    client, *_ = full_api
    for bad in ["998901234567", "+99890letters", "+９９８９０１２３４５６７", "+0" + "1" * 8, "+" + "1" * 33]:
        response = request_code(client, bad)
        assert response.status_code == 422
        assert bad not in response.text
    assert client.post("/api/auth/request", json={"phone": "+998901234567"}).status_code == 403
    assert client.post("/api/auth/request", json={"phone": "+998901234567"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_attempts_are_durable_and_codes_single_use(full_api):
    from app.workspace.models import OtpChallenge
    client, engine, sender, _, _ = full_api
    challenge = request_code(client).json()["challenge_id"]
    code = sender.messages[-1][1]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert client.post("/api/auth/verify", json={"challenge_id": challenge, "code": wrong}, headers={"Origin": ORIGIN}).status_code == 401
    with Session(engine) as db:
        assert db.get(OtpChallenge, challenge).attempts == 5
    assert client.post("/api/auth/verify", json={"challenge_id": challenge, "code": code}, headers={"Origin": ORIGIN}).status_code == 401


def test_resend_cooldown_quota_delivery_failure_and_expiry(full_api):
    client, _, sender, instant, _ = full_api
    first = request_code(client).json()["challenge_id"]
    old_code = sender.messages[-1][1]
    assert request_code(client).status_code == 429
    instant[0] += timedelta(seconds=61)
    second = request_code(client).json()["challenge_id"]
    assert client.post("/api/auth/verify", json={"challenge_id": first, "code": old_code}, headers={"Origin": ORIGIN}).status_code == 401
    instant[0] += timedelta(seconds=301)
    assert client.post("/api/auth/verify", json={"challenge_id": second, "code": sender.messages[-1][1]}, headers={"Origin": ORIGIN}).status_code == 401
    sender.fail = True
    assert request_code(client).status_code == 503
    assert request_code(client).status_code == 429
    for _ in range(2):
        instant[0] += timedelta(seconds=61)
        response = request_code(client)
        assert response.status_code == 503 and "sensitive-provider-body" not in response.text
    instant[0] += timedelta(seconds=61)
    assert request_code(client).status_code == 429


def test_idle_expiry_absolute_expiry_and_disabled_user(full_api):
    from app.workspace.models import User
    client, engine, _, instant, _ = full_api
    assert login(full_api).status_code == 200
    instant[0] += timedelta(minutes=31)
    assert client.get("/api/auth/session").status_code == 401
    assert login(full_api).status_code == 200
    with Session(engine) as db, db.begin():
        db.scalar(select(User)).active = False
    assert client.get("/api/auth/session").status_code == 401


def test_sms_missing_returns_clear_service_unavailable(full_api):
    from app.full_main import create_app
    from app.config import Settings
    _, engine, _, _, _ = full_api
    settings = Settings(environment="test", database_url="sqlite://", session_secret="x" * 48)
    with TestClient(create_app(settings, engine=engine)) as client:
        response = request_code(client)
        assert response.status_code == 503
        assert "SMS" in response.json()["detail"]
