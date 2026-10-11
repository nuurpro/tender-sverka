from datetime import timedelta
import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from test_full_auth_api import full_api, login, auth_headers, request_code, ORIGIN, RecordingSms


def test_sms_wire_contract_and_sanitized_failures():
    from app.auth.sms import HttpSmsSender, SmsUnavailable
    seen = []
    def provider(request):
        seen.append(request)
        return httpx.Response(200)
    sender = HttpSmsSender("https://sms.example/messages", SecretStr("private-token"), transport=httpx.MockTransport(provider))
    sender.send("+998901234567", "123456")
    assert seen[0].headers["authorization"] == "Bearer private-token"
    payload = json.loads(seen[0].content)
    assert set(payload) == {"phone", "message"} and payload["phone"] == "+998901234567"
    assert "123456" in payload["message"]
    assert seen[0].extensions["timeout"]["read"] == 10.0
    for status in [301, 400, 429, 500]:
        bad = HttpSmsSender("https://sms.example/messages", SecretStr("private-token"),
            transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private-token code:123456 private-provider-body")))
        with pytest.raises(SmsUnavailable) as error:
            bad.send("+998901234567", "123456")
        assert not any(secret in str(error.value) for secret in ["private-token", "123456", "private-provider-body"])
    def timeout(_):
        raise httpx.ReadTimeout("token=private-token code=123456")
    with pytest.raises(SmsUnavailable) as error:
        HttpSmsSender("https://sms.example/messages", SecretStr("private-token"), transport=httpx.MockTransport(timeout)).send("+998901234567", "123456")
    assert "private-token" not in str(error.value)


def test_sms_settings_require_https_and_hide_tokens():
    from app.config import Settings
    values = dict(environment="test", database_url="sqlite://", session_secret="x" * 48, sms_token="private-token")
    assert "private-token" not in repr(Settings(**values, sms_endpoint="https://sms.example/api"))
    for endpoint in ["http://sms.example/api", "https://user:password@sms.example/api", "https://sms.example/api?token=secret"]:
        with pytest.raises(ValidationError):
            Settings(**values, sms_endpoint=endpoint)


def test_dependency_injection_is_only_available_in_explicit_test_environment():
    from app.config import Settings
    from app.full_main import create_app
    settings = Settings(environment="development", database_url="postgresql://app:test@db/tender", session_secret="x" * 48)
    with pytest.raises(ValueError, match="test"):
        create_app(settings, sms_sender=RecordingSms())


def test_migration_refuses_phone_account_downgrade_before_removing_auth_tables(full_api):
    from alembic import command
    from alembic.config import Config
    _, engine, *_ = full_api
    assert login(full_api).status_code == 200
    before = set(inspect(engine).get_table_names())
    with pytest.raises(RuntimeError, match="phone-only"):
        command.downgrade(Config("alembic.ini"), "0003_source_provenance")
    assert set(inspect(engine).get_table_names()) == before


def test_valid_code_is_one_time_and_logout_revokes_captured_cookie(full_api):
    client, _, sender, *_ = full_api
    requested = request_code(client).json()
    payload = {"challenge_id": requested["challenge_id"], "code": sender.messages[-1][1]}
    assert client.post("/api/auth/verify", json=payload, headers={"Origin": ORIGIN}).status_code == 200
    assert client.post("/api/auth/verify", json=payload, headers={"Origin": ORIGIN}).status_code == 401
    cookie = client.cookies.get("tender_session")
    assert client.post("/api/auth/logout", headers=auth_headers(client)).status_code == 200
    client.cookies.set("tender_session", cookie)
    assert client.get("/api/auth/session").status_code == 401


def test_absolute_expiry_even_with_activity_and_session_bound_csrf(full_api):
    client, _, _, instant, _ = full_api
    assert login(full_api).status_code == 200
    old_csrf = auth_headers(client)["X-CSRF-Token"]
    instant[0] += timedelta(seconds=61)
    assert login(full_api).status_code == 200
    assert client.post("/api/organizations", json={"name": "X"}, headers={"Origin": ORIGIN, "X-CSRF-Token": old_csrf}).status_code == 403
    for _ in range(49):
        instant[0] += timedelta(minutes=29)
        assert client.get("/api/auth/session").status_code == 200
    instant[0] += timedelta(minutes=19)
    assert client.get("/api/auth/session").status_code == 401


def test_client_ip_hourly_budget_ignores_forwarded_headers(full_api):
    client, _, sender, *_ = full_api
    sender.fail = True
    for number in range(20):
        response = client.post("/api/auth/request", json={"phone": f"+99890000{number:04d}"},
            headers={"Origin": ORIGIN, "X-Forwarded-For": f"192.0.2.{number}"})
        assert response.status_code == 503
    response = request_code(client, "+998900009999")
    assert response.status_code == 429


def test_restarted_application_retains_quota_and_attempt_count(full_api):
    from fastapi.testclient import TestClient
    from app.full_main import create_app
    client, engine, sender, instant, app = full_api
    challenge = request_code(client).json()["challenge_id"]
    wrong = "000000" if sender.messages[-1][1] != "000000" else "111111"
    for _ in range(4):
        assert client.post("/api/auth/verify", json={"challenge_id": challenge, "code": wrong}, headers={"Origin": ORIGIN}).status_code == 401
    with TestClient(create_app(app.state.settings, engine=engine, sms_sender=sender, clock=lambda: instant[0])) as restarted:
        assert request_code(restarted).status_code == 429
        assert restarted.post("/api/auth/verify", json={"challenge_id": challenge, "code": wrong}, headers={"Origin": ORIGIN}).status_code == 401
        assert restarted.post("/api/auth/verify", json={"challenge_id": challenge, "code": sender.messages[-1][1]}, headers={"Origin": ORIGIN}).status_code == 401


def test_production_session_cookie_is_host_secure_http_only(full_api):
    from app.config import Settings
    from app.auth.routes import router
    from app.auth.service import AuthService
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    # The production startup gate separately verifies a restricted PostgreSQL role;
    # this isolated HTTP boundary check uses the already-migrated test adapter.
    _, engine, sender, instant, _ = full_api
    settings = Settings(environment="production", database_url="postgresql://app:test@db/tender",
        session_secret="x" * 48, public_origin="https://tender.example")
    app = FastAPI()
    app.state.settings, app.state.engine = settings, engine
    app.state.cookie_name, app.state.cookie_secure = "__Host-tender_session", True
    app.state.auth = AuthService(engine, settings, sender, lambda: instant[0])
    app.include_router(router)
    with TestClient(app, base_url="https://tender.example") as client:
        requested = client.post("/api/auth/request", json={"phone": "+998901234567"}, headers={"Origin": settings.public_origin})
        verified = client.post("/api/auth/verify", json={"challenge_id": requested.json()["challenge_id"], "code": sender.messages[-1][1]}, headers={"Origin": settings.public_origin})
        cookie = verified.headers["set-cookie"]
        assert "__Host-tender_session=" in cookie and "Secure" in cookie and "HttpOnly" in cookie
        assert "Path=/" in cookie and "Domain" not in cookie and "SameSite=lax" in cookie
