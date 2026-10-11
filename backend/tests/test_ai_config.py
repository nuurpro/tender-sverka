import json

import httpx
import pytest
from openai import APIStatusError, OpenAI

from app import ai
from app.schemas import ChecklistExtraction


@pytest.fixture
def chat_reply():
    return {"content": None, "finish_reason": "stop", "refusal": None, "empty_choices": False}


@pytest.fixture
def captured_requests(monkeypatch, chat_reply):
    for key in ("OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_BASE_URL", "CODEX_GATEWAY_API_KEY", "OPENAI_MODEL", "OPENAI_API_STYLE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AI_MODE", "openai")
    requests = []

    def respond(request):
        requests.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("/chat/completions"):
            default_content = '{"rows":[]}' if "BidComparison" in body["messages"][0]["content"] else '{"requirements":[]}'
            content = default_content if chat_reply["content"] is None else chat_reply["content"]
            choices = [] if chat_reply["empty_choices"] else [{
                "index": 0,
                "message": {"role": "assistant", "content": content, "refusal": chat_reply["refusal"]},
                "finish_reason": chat_reply["finish_reason"],
            }]
            return httpx.Response(200, json={
                "id": "chatcmpl_test", "object": "chat.completion", "created": 0,
                "model": body["model"], "choices": choices,
            })
        text = '{"rows":[]}' if body["text"]["format"]["name"] == "BidComparison" else '{"requirements":[]}'
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 0,
            "model": json.loads(request.content)["model"], "status": "completed",
            "output": [{
                "id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }],
        })

    def client(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond)))

    monkeypatch.setattr(ai, "OpenAI", client)
    return requests


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "gpt-6-astra", "claude-opus-5", "claude-opus-4-8"])
def test_gateway_uses_chat_json_and_gateway_key(captured_requests, monkeypatch, model):
    monkeypatch.setenv("OPENAI_API_BASE", "https://agentrouter.org/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "official-key-not-for-gateway")
    monkeypatch.setenv("CODEX_GATEWAY_API_KEY", "test-gateway-key")
    monkeypatch.setenv("OPENAI_MODEL", model)
    result = ai.extract_requirements([])
    assert isinstance(result, ChecklistExtraction)
    request = captured_requests[0]
    assert str(request.url) == "https://agentrouter.org/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-gateway-key"
    body = json.loads(request.content)
    assert body["model"] == model
    assert body["response_format"] == {"type": "json_object"}
    assert "text" not in body and "store" not in body
    assert "JSON" in body["messages"][0]["content"]
    assert '"additionalProperties": false' in body["messages"][0]["content"]
    assert '"requirements"' in body["messages"][0]["content"]
    assert json.loads(body["messages"][1]["content"]) == []


def test_gateway_accepts_explicit_openai_key(captured_requests, monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://agentrouter.org/v1/")
    monkeypatch.setenv("OPENAI_API_KEY", "test-alternative-gateway-key")
    ai.extract_requirements([])
    assert str(captured_requests[0].url) == "https://agentrouter.org/v1/chat/completions"
    assert captured_requests[0].headers["authorization"] == "Bearer test-alternative-gateway-key"


def test_comparison_works_with_gateway_key_only(captured_requests, monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://agentrouter.org/v1")
    monkeypatch.setenv("CODEX_GATEWAY_API_KEY", "test-gateway-key")
    result = ai.compare_bid([], [])
    assert result.rows == []
    assert str(captured_requests[0].url) == "https://agentrouter.org/v1/chat/completions"
    assert captured_requests[0].headers["authorization"] == "Bearer test-gateway-key"
    assert "BidComparison" in json.loads(captured_requests[0].content)["messages"][0]["content"]


@pytest.mark.parametrize("base", [None, "", "https://api.openai.com/v1", "https://other-gateway.example/v1"])
def test_other_endpoints_use_only_openai_key(captured_requests, monkeypatch, base):
    if base is not None:
        monkeypatch.setenv("OPENAI_API_BASE", base)
    monkeypatch.setenv("OPENAI_API_KEY", "test-official-key")
    monkeypatch.setenv("CODEX_GATEWAY_API_KEY", "gateway-key-must-not-leak")
    ai.extract_requirements([])
    assert str(captured_requests[0].url) == (base or "https://api.openai.com/v1").rstrip("/") + "/responses"
    assert captured_requests[0].headers["authorization"] == "Bearer test-official-key"
    body = json.loads(captured_requests[0].content)
    assert body["store"] is False
    assert body["text"]["format"]["strict"] is True


def test_gateway_key_is_not_used_for_official_endpoint(captured_requests, monkeypatch):
    monkeypatch.setenv("CODEX_GATEWAY_API_KEY", "gateway-key-must-not-leak")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        ai.extract_requirements([])
    assert captured_requests == []


def test_missing_gateway_key_has_actionable_error(captured_requests, monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://agentrouter.org/v1")
    with pytest.raises(ValueError, match="CODEX_GATEWAY_API_KEY"):
        ai.extract_requirements([])
    assert captured_requests == []


def test_explicit_responses_override_for_gateway(captured_requests, monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://agentrouter.org/v1")
    monkeypatch.setenv("CODEX_GATEWAY_API_KEY", "test-gateway-key")
    monkeypatch.setenv("OPENAI_API_STYLE", "responses")
    ai.extract_requirements([])
    assert str(captured_requests[0].url) == "https://agentrouter.org/v1/responses"


def test_explicit_chat_override_for_other_gateway(captured_requests, monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://other-gateway.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-other-key")
    monkeypatch.setenv("OPENAI_API_STYLE", "chat")
    ai.extract_requirements([])
    assert str(captured_requests[0].url) == "https://other-gateway.example/v1/chat/completions"


@pytest.mark.parametrize("content", [
    "not JSON", '{"requirements":"wrong type"}', '{"requirements":[],"extra":true}',
    '{"requirements":[{"scope":"product"}]}', "",
])
def test_chat_rejects_invalid_json_and_schema(captured_requests, monkeypatch, chat_reply, content):
    monkeypatch.setenv("OPENAI_API_BASE", "https://agentrouter.org/v1")
    monkeypatch.setenv("CODEX_GATEWAY_API_KEY", "test-gateway-key")
    chat_reply["content"] = content
    with pytest.raises(ValueError, match="JSON"):
        ai.extract_requirements([])
    assert len(captured_requests) == 1  # No paid repair or Responses fallback.


@pytest.mark.parametrize("failure", ["length", "refusal", "empty_choices"])
def test_chat_rejects_incomplete_or_refused_output(captured_requests, monkeypatch, chat_reply, failure):
    monkeypatch.setenv("OPENAI_API_BASE", "https://agentrouter.org/v1")
    monkeypatch.setenv("CODEX_GATEWAY_API_KEY", "test-gateway-key")
    if failure == "length":
        chat_reply["finish_reason"] = "length"
    elif failure == "refusal":
        chat_reply["refusal"] = "Refused"
    else:
        chat_reply["empty_choices"] = True
    with pytest.raises(ValueError):
        ai.extract_requirements([])
    assert len(captured_requests) == 1


def test_invalid_api_style_fails_before_request(captured_requests, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_STYLE", "typo")
    with pytest.raises(ValueError, match="OPENAI_API_STYLE"):
        ai.extract_requirements([])
    assert captured_requests == []


@pytest.mark.parametrize("nested", [False, True])
def test_gateway_content_block_has_specific_error(monkeypatch, nested):
    from app.tasks import error_message
    monkeypatch.setenv("OPENAI_API_BASE", "https://agentrouter.org/v1")
    detail = {"code": "content-blocked", "message": "content-blocked"}
    response = httpx.Response(400, request=httpx.Request("POST", "https://agentrouter.org/v1/chat/completions"))
    exc = APIStatusError("Gateway blocked request", response=response, body={"error": detail} if nested else detail)
    message = error_message(exc)
    assert "content-blocked" in message and "AgentRouter" in message
    assert "Structured Outputs" not in message


def test_gateway_client_rejection_does_not_misdiagnose_key(monkeypatch):
    from app.tasks import error_message
    monkeypatch.setenv("OPENAI_API_BASE", "https://agentrouter.org/v1")
    response = httpx.Response(401, request=httpx.Request("POST", "https://agentrouter.org/v1/chat/completions"))
    exc = APIStatusError("Rejected", response=response, body={"message": "unauthorized client detected"})
    assert "API-клиент" in error_message(exc)
