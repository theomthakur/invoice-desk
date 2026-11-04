"""OpenRouter adapter (Chat Completions, OpenAI-compatible).

Verified against the official docs on 3 Nov 2026:
* endpoint ``POST {base}/chat/completions``; ``tools`` / ``tool_calls``; ``response_format``
  with ``json_schema``; normalized ``finish_reason``;
* ``usage`` is always returned and may include ``cost``;
* ``provider.require_parameters`` keeps a request off providers that would ignore tools or the
  schema;
* an error can arrive inside an HTTP 200 body, so the body is always checked.

Not verified (no key or spending cap was supplied): that the configured model slug exists and
supports tools and strict schemas on this account. That needs a live smoke test.

The API key is read from the server environment only. It is never logged, never put in an
exception message, and never returned to a client.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

from .base import Provider, ProviderError, ProviderRequest, ProviderResponse, ToolCall, redact

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def _check_base_url(url: str) -> str:
    parsed = urlparse(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("The model base URL must use https.")
    if not parsed.hostname:
        raise ValueError("The model base URL is not valid.")
    return url.rstrip("/")


_STATUS_MAP: dict[int, tuple[str, bool]] = {
    400: ("bad_request", False),
    401: ("auth_failed", False),
    402: ("credits_exhausted", False),
    403: ("forbidden", False),
    404: ("model_unavailable", False),
    408: ("timeout", True),
    429: ("rate_limited", True),
    500: ("provider_error", True),
    502: ("provider_error", True),
    503: ("provider_unavailable", True),
    504: ("timeout", True),
}

_FRIENDLY = {
    "auth_failed": "The model API rejected the credentials.",
    "credits_exhausted": "The model account has insufficient credits.",
    "forbidden": "The model API refused the request (permissions, budget or content rules).",
    "model_unavailable": "The configured model is not available.",
    "rate_limited": "The model API is rate limiting requests.",
    "timeout": "The model request timed out.",
    "provider_error": "The model provider returned an error.",
    "provider_unavailable": "No model provider is available right now.",
    "bad_request": "The model API rejected the request.",
}


class OpenRouterProvider(Provider):
    mode = "live"
    name = "openrouter"
    simulated = False

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        app_title: str = "invoice-flow",
        require_parameters: bool = True,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ProviderError("no_api_key", "OPENROUTER_API_KEY is not configured.")
        if not model or not model.strip():
            raise ProviderError("no_model", "LLM_MODEL is not configured.")
        self.model = model.strip()
        self._key = api_key.strip()
        self._base = _check_base_url(base_url)
        self._require_parameters = require_parameters
        self._client = httpx.Client(transport=transport, follow_redirects=False)
        self._headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "X-Title": app_title,
        }

    def __repr__(self) -> str:  # never show the key
        return f"OpenRouterProvider(model={self.model!r})"

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ request

    def build_body(self, request: ProviderRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": 0,
            # Never silently route to a provider that would ignore tools or the schema.
            "provider": {"require_parameters": self._require_parameters},
        }
        if request.tools:
            body["tools"] = request.tools
            if request.tool_choice:
                body["tool_choice"] = request.tool_choice
        if request.response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema.name,
                    "strict": True,
                    "schema": request.response_schema.schema,
                },
            }
        return body

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        body = self.build_body(request)
        try:
            response = self._client.post(
                f"{self._base}/chat/completions",
                headers=self._headers,
                json=body,
                timeout=max(1.0, request.timeout),
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "The model request timed out.", retryable=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError(
                "network", "Could not reach the model API.", retryable=True
            ) from exc

        retry_after = _retry_after(response)
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.status_code >= 400:
            raise self._error(response.status_code, payload, retry_after)
        if not isinstance(payload, dict):
            raise ProviderError("malformed_response", "The model API returned a non-JSON body.")
        if isinstance(payload.get("error"), dict):  # error inside an HTTP 200
            code = payload["error"].get("code")
            status = code if isinstance(code, int) else 502
            raise self._error(status, payload, retry_after)
        return self._parse(payload)

    # ------------------------------------------------------------------ response

    def _error(self, status: int, payload: object, retry_after: float | None) -> ProviderError:
        code, retryable = _STATUS_MAP.get(status, ("provider_error", status >= 500))
        detail = ""
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            raw = payload["error"].get("message")
            if isinstance(raw, str):
                detail = redact(raw, self._key)[:200]
        message = _FRIENDLY.get(code, "The model request failed.")
        if detail:
            message = f"{message} ({detail})"
        return ProviderError(
            code, message, retryable=retryable, status=status, retry_after=retry_after
        )

    def _parse(self, payload: dict[str, Any]) -> ProviderResponse:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError("malformed_response", "The model API returned no choices.")
        choice = choices[0]
        if isinstance(choice.get("error"), dict):
            raise ProviderError(
                "provider_error", "The model provider reported an error.", retryable=True
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError("malformed_response", "The model API returned no message.")

        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") if isinstance(raw, dict) else None
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise ProviderError("malformed_response", "A tool call was malformed.")
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments if arguments is not None else {})
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{len(calls)}"),
                    name=function["name"],
                    arguments=arguments,
                )
            )

        content = message.get("content")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return ProviderResponse(
            content=content if isinstance(content, str) else None,
            tool_calls=calls,
            finish_reason=str(choice.get("finish_reason") or "stop"),
            prompt_tokens=_as_int(usage.get("prompt_tokens")),
            completion_tokens=_as_int(usage.get("completion_tokens")),
            cost_usd=_as_float(usage.get("cost")),
            upstream=payload.get("provider") if isinstance(payload.get("provider"), str) else None,
            model=payload.get("model") if isinstance(payload.get("model"), str) else self.model,
            response_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
        )


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None
