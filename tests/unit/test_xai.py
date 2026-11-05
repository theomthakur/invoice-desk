"""Direct xAI contracts. All HTTP responses are fixtures; no API credit is used."""

import json

import httpx
import pytest

from invoice_flow.providers import ProviderError, ProviderRequest, ResponseSchema, redact
from invoice_flow.providers.xai import XAIProvider
from invoice_flow.runtime import AppConfig, default_provider_factory

KEY = "xai-contract-placeholder-123456789"


def completion(usage=None, **message):
    return {
        "id": "xai-test",
        "model": "grok-4.7",
        "choices": [{"finish_reason": "stop", "message": {"content": "{}", **message}}],
        "usage": usage,
    }


def test_direct_request_preserves_schema_tools_and_uses_only_xai_credentials():
    def handler(request):
        assert str(request.url) == "https://api.x.ai/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert "x-title" not in request.headers
        body = json.loads(request.content)
        assert "provider" not in body and "max_tokens" not in body
        assert body["model"] == "grok-4.7" and body["max_completion_tokens"] == 123
        assert body["tools"][0]["function"]["name"] == "lookup_inventory"
        assert body["tool_choice"] == "required"
        assert body["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200,
            json=completion(
                {"prompt_tokens": 12, "completion_tokens": 7, "cost_in_usd_ticks": 3000000},
                tool_calls=[
                    {
                        "id": "call-1",
                        "function": {
                            "name": "lookup_inventory",
                            "arguments": '{"item_name":"WidgetA"}',
                        },
                    }
                ],
            ),
        )

    provider = XAIProvider(KEY, transport=httpx.MockTransport(handler))
    try:
        response = provider.complete(
            ProviderRequest(
                messages=[{"role": "user", "content": "Check this invoice"}],
                tools=[{"type": "function", "function": {"name": "lookup_inventory"}}],
                tool_choice="required",
                max_tokens=123,
                response_schema=ResponseSchema("Result", {"type": "object"}),
            )
        )
        assert response.cost_usd == 0.0003
        assert (response.prompt_tokens, response.completion_tokens) == (12, 7)
        assert response.upstream == "xai" and not provider.simulated
        assert response.tool_calls[0].id == "call-1"
    finally:
        provider.close()


@pytest.mark.parametrize(
    "ticks, expected",
    [(0, 0), (10000000000, 1), (None, None), (-1, None), (True, None), ("5000", None)],
)
def test_direct_cost_uses_only_valid_reported_ticks(ticks, expected):
    provider = XAIProvider(
        KEY,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=completion({"cost_in_usd_ticks": ticks, "cost": 99}))
        ),
    )
    try:
        assert provider.complete(ProviderRequest(messages=[])).cost_usd == expected
    finally:
        provider.close()


def test_auth_failure_redacts_key_and_does_not_fall_back():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": f"Bad key: {KEY}"}})

    provider = XAIProvider(KEY, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ProviderError) as error:
            provider.complete(ProviderRequest(messages=[]))
        assert error.value.code == "auth_failed" and not error.value.retryable
        assert KEY not in str(error.value) and KEY not in repr(provider)
        assert len(requests) == 1
        assert KEY not in redact(KEY)
    finally:
        provider.close()


@pytest.mark.parametrize(
    "provider, model, url",
    [
        ("openrouter", "x-ai/grok-4.7", "https://openrouter.ai/api/v1"),
        ("xai", "grok-4.7", "https://api.x.ai/v1"),
    ],
)
def test_provider_specific_defaults_and_secret_isolation(monkeypatch, provider, model, url):
    for name in ("LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("XAI_API_KEY", KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-placeholder")
    monkeypatch.setenv("MODEL_BUDGET_USD", "1")
    config = AppConfig.from_env()
    assert config.llm_model == model and config.llm_base_url == url
    assert KEY not in repr(config) and KEY not in str(config.public())
    instance = default_provider_factory(config)("live")
    try:
        assert instance.name == provider and instance.model == model
        assert instance._key == (KEY if provider == "xai" else "openrouter-placeholder")
    finally:
        instance.close()


def test_direct_constructor_and_missing_key_never_use_openrouter():
    config = AppConfig(
        llm_provider="xai", openrouter_api_key="other-provider-key", model_budget_usd=1
    )
    assert config.llm_model == "grok-4.7" and config.llm_base_url == "https://api.x.ai/v1"
    assert config.live_ready() == (False, "XAI_API_KEY is not configured.")
    with pytest.raises(ProviderError, match="XAI_API_KEY"):
        default_provider_factory(config)("live")
    assert not AppConfig(llm_provider="xai", xai_api_key=KEY).live_ready()[0]
    assert AppConfig(llm_provider="xai", xai_api_key=KEY, model_budget_usd=1).live_ready()[0]
    mock = default_provider_factory(config)("mock")
    assert mock.simulated and mock.name == "simulated"
