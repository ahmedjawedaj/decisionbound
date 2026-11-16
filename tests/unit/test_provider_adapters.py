"""Mocked HTTP transport and SSRF / credential gating tests."""
import json
import os
import pytest
from decisionbound.model_planner import ACTION_SCHEMA, ProviderResponseError
from decisionbound.providers import OpenAICompatibleJSONTransport, AnthropicJSONTransport


def test_remote_requires_explicit_opt_in_and_key(monkeypatch):
    t = OpenAICompatibleJSONTransport(model="mock", enabled=False)
    with pytest.raises(PermissionError):
        t.complete(messages=[], schema=ACTION_SCHEMA, max_output_tokens=100)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    t = OpenAICompatibleJSONTransport(model="mock", enabled=True)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        t.complete(messages=[], schema=ACTION_SCHEMA, max_output_tokens=100)


def test_mocked_openai_json_schema_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "SECRET_SHOULD_NEVER_LEAK")
    witnessed = {}
    def exchange(endpoint, headers, payload, timeout):
        witnessed.update({"endpoint": endpoint, "headers": headers, "payload": payload, "timeout": timeout})
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "kind": "finish", "tool": None, "arguments_json": "{}", "message": "done"})}}],
                "usage": {"prompt_tokens": 88, "completion_tokens": 17}}
    t = OpenAICompatibleJSONTransport(model="test-model", enabled=True, exchange=exchange)
    response = t.complete(messages=[{"role": "user", "content": "test"}], schema=ACTION_SCHEMA,
                          max_output_tokens=150)
    assert response.input_tokens == 88 and response.output_tokens == 17
    assert witnessed["payload"]["response_format"]["json_schema"]["strict"] is True
    assert witnessed["payload"]["max_completion_tokens"] == 150
    assert witnessed["headers"]["Authorization"].startswith("Bearer ")


def test_endpoint_host_and_redirect_defenses():
    for address in ("http://api.openai.com/v1/chat/completions", "https://localhost/internal",
                    "https://evil.com/steal", "https://api.openai.com/v1/ok?secret=hi",
                    "https://user:pass@api.openai.com/v1/chat", "https://127.0.0.1/v1/chat"):
        with pytest.raises(ValueError):
            OpenAICompatibleJSONTransport(model="m", endpoint=address)
    OpenAICompatibleJSONTransport(model="m", endpoint="http://localhost:11434/v1/chat/completions",
                                  allow_local=True)


def test_provider_refusal_and_incomplete_messages(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "KEY")
    t = OpenAICompatibleJSONTransport(model="m", enabled=True,
        exchange=lambda *args: {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]})
    with pytest.raises(ProviderResponseError, match="truncated"):
        t.complete(messages=[], schema=ACTION_SCHEMA, max_output_tokens=100)
    t = OpenAICompatibleJSONTransport(model="m", enabled=True, exchange=lambda *a: {"error": "internal"})
    with pytest.raises(ProviderResponseError):
        t.complete(messages=[], schema=ACTION_SCHEMA, max_output_tokens=100)


def test_anthropic_mock_and_token_usage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SECRET")
    calls = []
    def exchange(url, headers, payload, timeout):
        calls.append((url, headers, payload))
        return {"content": [{"type": "text", "text": '{"kind":"finish","tool":null,"arguments_json":"{}","message":"ok"}'}],
                "usage": {"input_tokens": 31, "output_tokens": 12}, "stop_reason": "end_turn"}
    response = AnthropicJSONTransport(model="test", enabled=True, exchange=exchange).complete(
        messages=[{"role": "system", "content": "instructions"}, {"role": "user", "content": "task"}],
        schema=ACTION_SCHEMA, max_output_tokens=80)
    assert response.input_tokens == 31 and response.output_tokens == 12
    assert calls[0][0] == "https://api.anthropic.com/v1/messages"
    assert calls[0][2]["max_tokens"] == 80
    assert "instructions" in calls[0][2]["system"]
