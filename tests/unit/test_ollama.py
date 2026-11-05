"""Local inference transport and cloud-budget separation, without a model or network."""

import json

import httpx
import pytest

from invoice_flow.providers.base import ProviderError, ProviderRequest, ResponseSchema
from invoice_flow.providers.ollama import OllamaProvider
from invoice_flow.runtime import App, AppConfig, default_provider_factory


def local_transport(chat):
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "model_info": {"general.architecture": "qwen2"},
                    "capabilities": ["completion", "tools"],
                },
            )
        return chat(request)

    return httpx.MockTransport(handler)


def test_native_schema_and_tool_history_roundtrip():
    def respond(request):
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["format"] == {"type": "object"}
        assert body["messages"][0]["tool_calls"][0]["function"]["arguments"] == {"items": ["A"]}
        assert body["messages"][1]["tool_name"] == "lookup_inventory"
        assert "tool_call_id" not in body["messages"][1]
        assert body["options"]["temperature"] == 0
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "tool_calls": [
                        {"function": {"name": "lookup_inventory", "arguments": {"items": ["A"]}}}
                    ]
                },
                "prompt_eval_count": 20,
                "eval_count": 10,
            },
        )

    provider = OllamaProvider("qwen2.5:7b", transport=local_transport(respond))
    response = provider.complete(
        ProviderRequest(
            messages=[
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "lookup_inventory",
                                "arguments": '{"items":["A"]}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
            ],
            response_schema=ResponseSchema("answer", {"type": "object"}),
        )
    )
    assert response.tool_calls[0].name == "lookup_inventory"
    assert json.loads(response.tool_calls[0].arguments) == {"items": ["A"]}
    assert response.prompt_tokens == 20 and response.cost_usd == 0
    assert response.upstream == "local-ollama"
    provider.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com",
        "http://localhost/remote",
        "http://user:password@localhost",
        "http://localhost?remote=1",
    ],
)
def test_only_loopback_without_redirects_or_credentials(url):
    with pytest.raises(ValueError):
        OllamaProvider("qwen2.5:7b", base_url=url)


def test_cloud_models_are_rejected():
    with pytest.raises(ValueError):
        OllamaProvider("remote-cloud")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"done": True},
        {"done": True, "message": {"tool_calls": 3}},
        {"done": True, "message": {"tool_calls": [{"function": {}}]}},
    ],
)
def test_incomplete_or_malformed_response_stays_visible(payload):
    provider = OllamaProvider(
        "qwen2.5:7b", transport=local_transport(lambda _: httpx.Response(200, json=payload))
    )
    with pytest.raises(ProviderError, match="Ollama"):
        provider.complete(ProviderRequest(messages=[]))
    provider.close()


def test_unavailable_model_does_not_fall_back():
    provider = OllamaProvider(
        "missing",
        transport=httpx.MockTransport(lambda _: httpx.Response(404, text="private upstream error")),
    )
    with pytest.raises(ProviderError) as caught:
        provider.complete(ProviderRequest(messages=[]))
    assert caught.value.code == "model_unavailable"
    assert "private upstream" not in str(caught.value)
    provider.close()


def test_local_has_no_cloud_key_requirement_but_cloud_still_does(tmp_path):
    cfg = AppConfig(data_dir=tmp_path, llm_provider="ollama", llm_model="qwen2.5:7b")
    assert cfg.live_ready() == (True, "")
    app = App.create(cfg)
    app.check_live_allowed()
    provider = default_provider_factory(cfg)("live")
    assert provider.name == "ollama" and not provider.simulated
    provider.close()
    assert not AppConfig().live_ready()[0]
    assert not AppConfig(openrouter_api_key="test-key").live_ready()[0]
    assert AppConfig(openrouter_api_key="test-key", model_budget_usd=1).live_ready()[0]


def test_env_defaults_do_not_change_cloud_model(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert AppConfig.from_env().llm_model == "qwen2.5:7b"
    monkeypatch.setenv("LLM_PROVIDER", "invalid")
    with pytest.raises(ValueError):
        AppConfig.from_env()


@pytest.mark.parametrize(
    "metadata",
    [
        {"remote_host": "https://ollama.com", "model_info": {"architecture": "alias"}},
        {"remote_model": "hidden-remote", "model_info": {"architecture": "alias"}},
        {},
    ],
)
def test_alias_or_missing_local_weights_never_receives_invoice_text(metadata):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        assert "confidential invoice" not in request.content.decode()
        return httpx.Response(200, json=metadata)

    provider = OllamaProvider("innocent-alias", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError):
        provider.complete(
            ProviderRequest(messages=[{"role": "user", "content": "confidential invoice"}])
        )
    assert paths == ["/api/show"]
    provider.close()
