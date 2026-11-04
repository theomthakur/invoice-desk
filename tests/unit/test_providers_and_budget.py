"""Budget, OpenRouter adapter (no network: httpx.MockTransport) and the model client."""

from __future__ import annotations

import json
import threading

import httpx
import pytest
from pydantic import BaseModel

from invoice_flow.agent_models import (
    ApprovalProposal,
    ExtractionOutput,
    OutputError,
    strict_schema,
    validate_output,
)
from invoice_flow.budget import BudgetExceeded, RunBudget, RunCancelled
from invoice_flow.llm import ModelClient, StructuredOutputError
from invoice_flow.providers import (
    OpenRouterProvider,
    ProviderError,
    ProviderRequest,
    ScriptedProvider,
    redact,
    reply,
)
from invoice_flow.providers.openrouter import _check_base_url

SECRET = "sk-or-v1-SUPERSECRETKEY1234567890"


# ------------------------------------------------------------------ budget


def test_request_limit_is_enforced_and_counted():
    budget = RunBudget(max_requests=2)
    budget.before_request()
    budget.before_request()
    with pytest.raises(BudgetExceeded) as caught:
        budget.before_request()
    assert caught.value.kind == "requests" and budget.requests == 2


def test_deadline_is_enforced():
    budget = RunBudget(timeout_seconds=0.0)
    with pytest.raises(BudgetExceeded) as caught:
        budget.before_request()
    assert caught.value.kind == "deadline"


def test_token_limit_is_enforced():
    budget = RunBudget(max_total_tokens=100)
    budget.before_request()
    budget.record_usage(90, 20, 0.0)
    with pytest.raises(BudgetExceeded) as caught:
        budget.before_request()
    assert caught.value.kind == "tokens"


def test_tool_calls_and_requests_are_separate_counters():
    budget = RunBudget(max_requests=5, max_tool_calls=2)
    budget.before_request()
    budget.before_tool()
    budget.before_tool()
    with pytest.raises(BudgetExceeded):
        budget.before_tool()
    assert (budget.requests, budget.tool_calls) == (1, 2)


def test_cancellation_stops_the_next_request():
    budget = RunBudget()
    budget.cancel_event.set()
    with pytest.raises(RunCancelled):
        budget.before_request()
    with pytest.raises(RunCancelled):
        budget.before_tool()


def test_unknown_usage_is_not_reported_as_zero():
    budget = RunBudget()
    budget.before_request()
    budget.record_usage(None, None, None)
    snap = budget.snapshot()
    assert snap["usage_complete"] is False and snap["estimated_cost_usd"] is None
    assert snap["model_calls"] == 1


def test_known_usage_sums_and_flags_complete():
    budget = RunBudget()
    for _ in range(2):
        budget.before_request()
        budget.record_usage(100, 50, 0.002)
    snap = budget.snapshot()
    assert (snap["prompt_tokens"], snap["completion_tokens"]) == (200, 100)
    assert snap["estimated_cost_usd"] == "0.004000" and snap["usage_complete"] is True


def test_one_missing_cost_makes_the_total_unknown():
    budget = RunBudget()
    budget.before_request()
    budget.record_usage(10, 10, 0.5)
    budget.before_request()
    budget.record_usage(10, 10, None)
    assert budget.snapshot()["estimated_cost_usd"] is None


def test_no_requests_means_no_usage_claims():
    snap = RunBudget().snapshot()
    assert snap["model_calls"] == 0 and snap["estimated_cost_usd"] is None


def test_budget_is_thread_safe():
    budget = RunBudget(max_requests=50)
    errors = []

    def go():
        for _ in range(20):
            try:
                budget.before_request()
            except BudgetExceeded:
                errors.append(1)

    threads = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert budget.requests == 50 and len(errors) == 50  # never over the limit


# ------------------------------------------------------------------ OpenRouter adapter


def provider_with(handler, **kw) -> OpenRouterProvider:
    return OpenRouterProvider(SECRET, "x-ai/grok-4.7", transport=httpx.MockTransport(handler), **kw)


def ok_body(**over):
    body = {
        "id": "gen-1",
        "model": "x-ai/grok-4.7",
        "provider": "xAI",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "cost": 0.0003},
    }
    body.update(over)
    return body


def request(**kw) -> ProviderRequest:
    return ProviderRequest(messages=[{"role": "user", "content": "hi"}], label="t", **kw)


def test_request_body_shape():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"], seen["auth"] = str(req.url), json.loads(req.content), req.headers
        return httpx.Response(200, json=ok_body())

    p = provider_with(handler)
    from invoice_flow.providers import ResponseSchema

    p.complete(
        request(
            tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
            tool_choice="auto",
            response_schema=ResponseSchema("Out", {"type": "object", "properties": {}}),
        )
    )
    body = seen["body"]
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert body["model"] == "x-ai/grok-4.7" and body["temperature"] == 0
    assert body["provider"] == {
        "require_parameters": True
    }  # never routed to a provider that ignores tools
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["tool_choice"] == "auto" and body["tools"][0]["type"] == "function"
    assert seen["auth"]["authorization"] == f"Bearer {SECRET}"


def test_successful_response_is_parsed_with_usage():
    p = provider_with(lambda r: httpx.Response(200, json=ok_body()))
    out = p.complete(request())
    assert (out.prompt_tokens, out.completion_tokens, out.cost_usd) == (12, 7, 0.0003)
    assert out.upstream == "xAI" and out.model == "x-ai/grok-4.7" and out.finish_reason == "stop"


def test_missing_usage_stays_unknown():
    body = ok_body()
    del body["usage"]
    out = provider_with(lambda r: httpx.Response(200, json=body)).complete(request())
    assert out.prompt_tokens is None and out.completion_tokens is None and out.cost_usd is None


@pytest.mark.parametrize("bad", [True, -1, "12", None, [1], {"a": 1}])
def test_garbage_usage_values_are_unknown_not_trusted(bad):
    body = ok_body(usage={"prompt_tokens": bad, "completion_tokens": bad, "cost": bad})
    out = provider_with(lambda r: httpx.Response(200, json=body)).complete(request())
    assert out.prompt_tokens is None and out.completion_tokens is None and out.cost_usd is None


def test_fractional_token_counts_are_not_trusted_but_a_real_cost_is_kept():
    body = ok_body(usage={"prompt_tokens": 1.5, "completion_tokens": 2.5, "cost": 1.5})
    out = provider_with(lambda r: httpx.Response(200, json=body)).complete(request())
    assert out.prompt_tokens is None and out.completion_tokens is None
    assert out.cost_usd == 1.5  # a non-negative number is a legitimate cost


def test_tool_calls_are_parsed():
    body = ok_body(
        choices=[
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "lookup_inventory",
                                "arguments": '{"items":["WidgetA"]}',
                            },
                        }
                    ],
                },
            }
        ]
    )
    out = provider_with(lambda r: httpx.Response(200, json=body)).complete(request())
    assert out.finish_reason == "tool_calls" and out.content is None
    assert (out.tool_calls[0].name, out.tool_calls[0].arguments) == (
        "lookup_inventory",
        '{"items":["WidgetA"]}',
    )


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "auth_failed", False),
        (402, "credits_exhausted", False),
        (403, "forbidden", False),
        (404, "model_unavailable", False),
        (408, "timeout", True),
        (429, "rate_limited", True),
        (502, "provider_error", True),
        (503, "provider_unavailable", True),
        (400, "bad_request", False),
    ],
)
def test_http_errors_are_classified(status, code, retryable):
    err = {"error": {"code": status, "message": f"upstream said no {SECRET}"}}
    with pytest.raises(ProviderError) as caught:
        provider_with(lambda r: httpx.Response(status, json=err)).complete(request())
    assert (caught.value.code, caught.value.retryable, caught.value.status) == (
        code,
        retryable,
        status,
    )
    assert SECRET not in caught.value.message  # a leaked key in an error body is redacted


def test_error_inside_http_200_is_still_an_error():
    body = {"error": {"code": 429, "message": "slow down"}}
    with pytest.raises(ProviderError) as caught:
        provider_with(lambda r: httpx.Response(200, json=body)).complete(request())
    assert caught.value.code == "rate_limited" and caught.value.retryable


def test_error_inside_a_choice_is_an_error():
    body = ok_body(choices=[{"error": {"code": 502, "message": "x"}, "message": {}}])
    with pytest.raises(ProviderError) as caught:
        provider_with(lambda r: httpx.Response(200, json=body)).complete(request())
    assert caught.value.code == "provider_error"


def test_retry_after_header_is_read():
    def handler(r):
        return httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"message": "x"}})

    with pytest.raises(ProviderError) as caught:
        provider_with(handler).complete(request())
    assert caught.value.retry_after == 7.0


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>gateway</html>"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": "text"}]}),
        httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"function": {}}]}}]}),
    ],
)
def test_malformed_bodies_are_reported(response):
    with pytest.raises(ProviderError) as caught:
        provider_with(lambda r: response).complete(request())
    assert caught.value.code == "malformed_response"


def test_timeout_and_network_failures_are_retryable():
    def timeout(r):
        raise httpx.ReadTimeout("slow", request=r)

    def down(r):
        raise httpx.ConnectError("no route", request=r)

    for handler, code in ((timeout, "timeout"), (down, "network")):
        with pytest.raises(ProviderError) as caught:
            provider_with(handler).complete(request())
        assert caught.value.code == code and caught.value.retryable


def test_missing_key_or_model_is_reported_before_any_request():
    for key, model, code in (
        ("", "m", "no_api_key"),
        ("   ", "m", "no_api_key"),
        ("k", "", "no_model"),
    ):
        with pytest.raises(ProviderError) as caught:
            OpenRouterProvider(key, model)
        assert caught.value.code == code


def test_the_key_never_appears_in_repr_or_headers_sent_to_events():
    p = provider_with(lambda r: httpx.Response(200, json=ok_body()))
    assert SECRET not in repr(p) and SECRET not in str(p.__dict__.get("model"))


@pytest.mark.parametrize("url", ["http://evil.example/api", "ftp://x", "https://", "not a url", ""])
def test_base_url_must_be_https(url):
    with pytest.raises(ValueError):
        _check_base_url(url)


def test_local_http_is_allowed_for_tests_only_hosts():
    assert _check_base_url("http://localhost:8080/v1/") == "http://localhost:8080/v1"


def test_redirects_are_not_followed():
    calls = []

    def handler(r):
        calls.append(str(r.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    with pytest.raises(ProviderError):
        provider_with(handler).complete(request())
    assert calls == ["https://openrouter.ai/api/v1/chat/completions"]  # the key was not forwarded


def test_redact_removes_keys():
    assert SECRET not in redact(f"token {SECRET} failed", SECRET)
    assert "sk-abcdefgh12345" not in redact("key sk-abcdefgh12345 end")
    assert "Bearer abcdefghijkl" not in redact("Authorization: Bearer abcdefghijkl")


# ------------------------------------------------------------------ model client


def client_for(steps, **kw):
    events = []
    provider = ScriptedProvider(steps)
    budget = kw.pop("budget", RunBudget())
    client = ModelClient(
        provider, budget, lambda s, t, p: events.append((s, t, p)), sleep=lambda s: None, **kw
    )
    return client, provider, budget, events


class Answer(BaseModel):
    """Same strictness as the production output models (they all forbid extra fields)."""

    model_config = {"extra": "forbid"}
    value: int


def req(label="t"):
    return ProviderRequest(messages=[{"role": "user", "content": "q"}], label=label)


def test_valid_structured_answer_costs_one_request():
    client, provider, budget, _ = client_for([reply({"value": 3})])
    out, _resp = client.structured(req(), Answer, stage="approval")
    assert out.value == 3 and budget.requests == 1
    assert provider.requests[0].response_schema.name == "Answer"


def test_unusable_answer_gets_exactly_one_repair_attempt():
    client, provider, budget, events = client_for([reply("not json"), reply({"value": 4})])
    out, _ = client.structured(req("x"), Answer, stage="approval")
    assert out.value == 4 and budget.requests == 2
    assert provider.labels == ["x", "x_repair"]
    repair_text = provider.requests[1].messages[-1]["content"]
    assert "could not be used" in repair_text and "not valid JSON" in repair_text
    assert any(t == "output_invalid" for _, t, _ in events)


def test_a_second_bad_answer_fails_visibly_not_a_third_try():
    client, provider, budget, _ = client_for(
        [reply("nope"), reply({"value": "x"}), reply({"value": 1})]
    )
    with pytest.raises(StructuredOutputError):
        client.structured(req(), Answer, stage="approval")
    assert (
        budget.requests == 2 and provider.remaining == 1
    )  # the third scripted answer is never used


def test_extra_fields_and_tool_calls_and_truncation_are_rejected():
    for bad in (
        reply({"value": 1, "approved": True}),
        reply(None, tool_calls=[("lookup_inventory", {})]),
        reply({"value": 1}, finish_reason="length"),
    ):
        client, _, _, _ = client_for([bad, reply({"value": 9})])
        out, _ = client.structured(req(), Answer, stage="approval")
        assert out.value == 9  # the first was refused, the repair accepted


def test_fenced_json_is_accepted():
    client, _, budget, _ = client_for([reply('```json\n{"value": 5}\n```')])
    assert client.structured(req(), Answer, stage="approval")[0].value == 5 and budget.requests == 1


def test_extra_check_failure_triggers_repair():
    def check(answer: Answer):
        if answer.value < 10:
            raise OutputError(["value must be at least 10"])

    client, provider, _, _ = client_for([reply({"value": 1}), reply({"value": 11})])
    out, _ = client.structured(req(), Answer, stage="approval", extra_check=check)
    assert out.value == 11 and "at least 10" in provider.requests[1].messages[-1]["content"]


def test_retryable_error_is_retried_once_and_counted():
    err = ProviderError("rate_limited", "slow", retryable=True, retry_after=0.0)
    client, _, budget, events = client_for([err, reply({"value": 2})])
    assert client.structured(req(), Answer, stage="approval")[0].value == 2
    assert budget.requests == 2  # the failed attempt counted
    assert [t for _, t, _ in events].count("model_retry") == 1


def test_retries_stop_after_the_configured_limit():
    err = ProviderError("provider_error", "boom", retryable=True, retry_after=0.0)
    client, _, budget, _ = client_for([err, err, reply({"value": 1})], max_retries=1)
    with pytest.raises(ProviderError):
        client.structured(req(), Answer, stage="approval")
    assert budget.requests == 2


def test_non_retryable_errors_are_not_retried():
    for code in ("auth_failed", "credits_exhausted", "model_unavailable"):
        err = ProviderError(code, "no", retryable=False)
        client, _, budget, _ = client_for([err, reply({"value": 1})])
        with pytest.raises(ProviderError) as caught:
            client.structured(req(), Answer, stage="approval")
        assert caught.value.code == code and budget.requests == 1


def test_retry_cannot_exceed_the_request_budget():
    err = ProviderError("rate_limited", "x", retryable=True, retry_after=0.0)
    client, _, budget, _ = client_for([err, reply({"value": 1})], budget=RunBudget(max_requests=1))
    with pytest.raises(ProviderError):
        client.structured(req(), Answer, stage="approval")
    assert budget.requests == 1


def test_repair_cannot_exceed_the_request_budget():
    client, _, _, _ = client_for(
        [reply("bad"), reply({"value": 1})], budget=RunBudget(max_requests=1)
    )
    with pytest.raises(BudgetExceeded):
        client.structured(req(), Answer, stage="approval")


def test_every_request_and_response_is_an_event():
    client, _, _, events = client_for([reply({"value": 1})])
    client.structured(req("lbl"), Answer, stage="critique")
    types = [(s, t) for s, t, _ in events]
    assert types == [("critique", "model_request"), ("critique", "model_response")]
    assert events[0][2]["simulated"] is True and events[1][2]["usage_reported"] is True


def test_output_tokens_are_capped():
    client, provider, _, _ = client_for([reply({"value": 1})], max_output_tokens=200)
    client.call(ProviderRequest(messages=[], max_tokens=9999, label="x"))
    assert provider.requests[0].max_tokens == 200


def test_timeout_is_limited_by_the_remaining_run_time():
    client, provider, _, _ = client_for(
        [reply({"value": 1})], budget=RunBudget(timeout_seconds=5), request_timeout=30
    )
    client.call(req())
    assert provider.requests[0].timeout <= 5


# ------------------------------------------------------------------ schemas for strict mode


def test_strict_schema_requires_every_property_and_forbids_extras():
    schema = strict_schema(ApprovalProposal)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    text = json.dumps(schema)
    for banned in ("maxItems", "minLength", "maxLength", "default", "title"):
        assert f'"{banned}"' not in text


def test_extraction_schema_is_strict_everywhere():
    schema = strict_schema(ExtractionOutput)
    line = schema["$defs"]["ExtractedLine"]
    assert line["additionalProperties"] is False and set(line["required"]) == set(
        line["properties"]
    )


def test_local_validation_still_enforces_what_the_schema_dropped():
    long_text = "x" * 2000
    bad = {
        "action": "approve",
        "rationale": long_text,
        "rules_applied": [],
        "addressed_codes": [],
        "scrutiny_checks": [],
    }
    with pytest.raises(OutputError):
        validate_output(ApprovalProposal, json.dumps(bad))
    too_many = {**bad, "rationale": "ok", "rules_applied": ["r"] * 13}
    with pytest.raises(OutputError):
        validate_output(ApprovalProposal, json.dumps(too_many))
