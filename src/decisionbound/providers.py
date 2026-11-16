"""Opt-in provider adapters; network and credentials are disabled by default.

The same transport contract accepts fake HTTP exchanges for deterministic
integration tests. These are *JSON action* adapters, not Claude/Codex SDK agent
adapters. Provider support is only claimed after live conformance testing.
"""
from __future__ import annotations

import ipaddress
import json
import os
import ssl
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, HTTPSHandler

from .model_planner import JSONModelTransport, ModelCompletion, ProviderResponseError

Exchange = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


class _NoRedirect(HTTPRedirectHandler):
    """Disallow all redirect chains for secret-bearing provider requests."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        """Prevent credentials from being forwarded to a different origin."""
        return None


def _checked_endpoint(endpoint: str, allowed_hosts: frozenset[str], allow_loopback: bool) -> None:
    """Reject URL authorities that are unsafe for an API-key-bearing POST.

    A user-configured allowed host is an explicit trust decision; DNS pinning
    and egress policy remain the responsibility of production infrastructure.
    """
    url = urlsplit(endpoint)
    if url.username or url.password or url.query or url.fragment or not url.hostname or not url.path:
        raise ValueError("Invalid provider endpoint")
    host = url.hostname.lower()
    local = host in {"localhost", "127.0.0.1", "::1"}
    if url.scheme != "https" and not (allow_loopback and local and url.scheme == "http"):
        raise ValueError("Provider requests require HTTPS except explicit local loopback")
    if host not in allowed_hosts and not (allow_loopback and local):
        raise ValueError("Provider hostname has not been approved")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not local:
        raise ValueError("IP-address provider endpoints are not allowed")


def _default_exchange(endpoint: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Send exactly one HTTPS request, no retries, redirects, or proxy inheritance.

    HTTP errors contain private provider details; none are passed to agent logs.
    """
    body = json.dumps(payload).encode("utf-8")
    if len(body) > 1_000_000:
        raise ProviderResponseError("Provider request too large")
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
    request = Request(endpoint, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(1_000_001)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ProviderResponseError("Provider connection failed or request was rejected") from None
    if len(data) > 1_000_000:
        raise ProviderResponseError("Provider response too large")
    try:
        result = json.loads(data)
    except ValueError as exc:
        raise ProviderResponseError("Provider returned invalid JSON") from exc
    if type(result) is not dict:
        raise ProviderResponseError("Provider returned an invalid envelope")
    return result


class OpenAICompatibleJSONTransport(JSONModelTransport):
    """OpenAI Chat Completions style JSON-schema actions (incl. compatible gateways).

    Requires explicit ``enabled=True``, environment-sourced credentials and
    an approved HTTPS host. Usage and refusal handling are interpreted at the
    transport boundary, but the host must still validate action arguments.
    """

    def __init__(self, *, model: str, endpoint: str = "https://api.openai.com/v1/chat/completions",
                 api_key_env: str = "OPENAI_API_KEY", enabled: bool = False,
                 allowed_hosts: frozenset[str] = frozenset({"api.openai.com", "openrouter.ai", "dashscope.aliyuncs.com"}),
                 allow_local: bool = False, timeout: float = 30.0,
                 exchange: Exchange | None = None):
        _checked_endpoint(endpoint, allowed_hosts, allow_local)
        if not model or not (1 <= timeout <= 180):
            raise ValueError("Model and timeout must be explicitly configured")
        self.model, self.endpoint, self.api_key_env = model, endpoint, api_key_env
        self.enabled, self.timeout, self.exchange = enabled, timeout, exchange or _default_exchange

    def complete(self, *, messages: list[dict[str, str]], schema: dict[str, Any],
                 max_output_tokens: int) -> ModelCompletion:
        """Request one schema-constrained action; paid requests are never retried."""
        if not self.enabled:
            raise PermissionError("Remote model calls must be explicitly enabled")
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ValueError(f"Missing provider credential environment variable: {self.api_key_env}")
        payload = {"model": self.model, "messages": messages, "temperature": 0,
                   "max_completion_tokens": max_output_tokens,
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "decisionbound_action", "strict": True, "schema": schema}}}
        result = self.exchange(self.endpoint, {"Authorization": f"Bearer {key}"}, payload, self.timeout)
        try:
            choice = result["choices"][0]
            if choice.get("finish_reason") in {"length", "content_filter"} or choice["message"].get("refusal"):
                raise ProviderResponseError("Provider refused or truncated action")
            content = choice["message"]["content"]
            usage = result.get("usage", {})
            if not isinstance(content, str):
                raise TypeError
            return ModelCompletion(content, usage.get("prompt_tokens"), usage.get("completion_tokens"))
        except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
            raise ProviderResponseError("Provider response schema invalid") from None


class AnthropicJSONTransport(JSONModelTransport):
    """Anthropic Messages adapter for JSON action generation.

    The model instruction asks for JSON; this adapter does not claim native
    strict JSON-schema enforcement. Host validation remains mandatory.
    """

    def __init__(self, *, model: str, enabled: bool = False, api_key_env: str = "ANTHROPIC_API_KEY",
                 timeout: float = 30, exchange: Exchange | None = None):
        if not model or not (1 <= timeout <= 180):
            raise ValueError("Model and timeout must be configured")
        self.model, self.enabled, self.api_key_env = model, enabled, api_key_env
        self.timeout, self.exchange = timeout, exchange or _default_exchange

    def complete(self, *, messages: list[dict[str, str]], schema: dict[str, Any],
                 max_output_tokens: int) -> ModelCompletion:
        """Request one read-only planning action from Anthropic Messages."""
        if not self.enabled:
            raise PermissionError("Remote model calls must be explicitly enabled")
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ValueError(f"Missing provider credential environment variable: {self.api_key_env}")
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_messages = [m for m in messages if m["role"] != "system"]
        payload = {"model": self.model, "system": system + "\nReturn JSON matching: " + json.dumps(schema),
                   "messages": user_messages, "max_tokens": max_output_tokens}
        result = self.exchange("https://api.anthropic.com/v1/messages", {
            "x-api-key": key, "anthropic-version": "2023-06-01"}, payload, self.timeout)
        try:
            if result.get("stop_reason") == "max_tokens":
                raise ProviderResponseError("Provider truncated action")
            blocks = result["content"]
            if not blocks or blocks[0]["type"] != "text":
                raise TypeError
            usage = result.get("usage", {})
            return ModelCompletion(blocks[0]["text"], usage.get("input_tokens"), usage.get("output_tokens"))
        except (KeyError, IndexError, TypeError, AttributeError, ValueError):
            raise ProviderResponseError("Provider response schema invalid") from None
