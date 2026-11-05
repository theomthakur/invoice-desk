"""Local-only Ollama adapter. No cloud fallback, API key or hosted inference endpoint."""

from __future__ import annotations

import json
import time
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from .base import Provider, ProviderError, ProviderRequest, ProviderResponse, ToolCall
from .openrouter import _as_int

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


class OllamaProvider(Provider):
    mode = "live"  # Genuine inference, as opposed to deterministic mock responses.
    name = "ollama"
    simulated = False

    def __init__(self, model: str, *, base_url: str = DEFAULT_OLLAMA_URL, transport=None):
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "Ollama must use an HTTP loopback address without credentials or a path."
            )
        if not model.strip() or "cloud" in model.casefold():
            raise ValueError("Choose an installed local Ollama model, not a cloud model.")
        self.model = model.strip()
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(transport=transport, follow_redirects=False, trust_env=False)

    def close(self):
        self._client.close()

    def build_body(self, request: ProviderRequest) -> dict:
        messages = []
        names = {}
        for raw in request.messages:
            message = dict(raw)
            if message.get("tool_calls"):
                calls = []
                for call in message["tool_calls"]:
                    function = dict(call["function"])
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    function["arguments"] = arguments
                    names[call.get("id")] = function["name"]
                    calls.append({"type": "function", "function": function})
                message["tool_calls"] = calls
            if message.get("role") == "tool":
                message["tool_name"] = names.get(message.pop("tool_call_id", None), "")
            messages.append(message)
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0,
                "seed": 42,
                "num_predict": request.max_tokens,
                "num_ctx": 8192,
            },
        }
        if request.tools:
            body["tools"] = request.tools
        if request.response_schema is not None:
            body["format"] = request.response_schema.schema
        return body

    def _verify_local_model(self, timeout: float) -> None:
        # Model aliases can conceal remote inference. Inspect stored metadata before
        # sending any invoice text. The daemon must also run with OLLAMA_NO_CLOUD=1.
        response = self._client.post(
            f"{self._base}/api/show", json={"model": self.model}, timeout=timeout
        )
        if response.status_code != 200:
            raise ProviderError("model_unavailable", "Cannot verify the installed local model.")
        try:
            metadata = response.json()
        except ValueError as exc:
            raise ProviderError("malformed_response", "Cannot read local model metadata.") from exc
        if not isinstance(metadata, dict) or not metadata.get("model_info"):
            raise ProviderError("model_unavailable", "Local model weights could not be verified.")
        if metadata.get("remote_host") or metadata.get("remote_model"):
            raise ProviderError("remote_model_forbidden", "Remote Ollama models are not allowed.")
        if "tools" not in metadata.get("capabilities", []):
            raise ProviderError("model_unavailable", "The installed model needs tool support.")

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        try:
            started = time.monotonic()
            self._verify_local_model(min(5.0, request.timeout))
            remaining = request.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise ProviderError("timeout", "Local model metadata check timed out.")
            response = self._client.post(
                f"{self._base}/api/chat", json=self.build_body(request), timeout=remaining
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "timeout", "Local model request timed out.", retryable=True
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderError(
                "network", "Start the local Ollama server first.", retryable=True
            ) from exc
        if response.status_code >= 400:
            raise ProviderError(
                "model_unavailable" if response.status_code == 404 else "provider_error",
                "Local inference failed. Confirm the model is installed and supports this request.",
                retryable=response.status_code >= 500,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                "malformed_response", "Ollama returned an unreadable response."
            ) from exc
        if not isinstance(payload, dict) or payload.get("error") or not payload.get("done"):
            raise ProviderError("malformed_response", "Ollama did not complete the request.")
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ProviderError("malformed_response", "Ollama returned no assistant message.")
        calls = []
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ProviderError("malformed_response", "Ollama returned an invalid tool list.")
        for call in raw_calls:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise ProviderError("malformed_response", "Ollama returned an invalid tool call.")
            arguments = function.get("arguments", {})
            calls.append(
                ToolCall(
                    id=f"local_{uuid4().hex}",
                    name=function["name"],
                    arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
                )
            )
        return ProviderResponse(
            content=message.get("content") if isinstance(message.get("content"), str) else None,
            tool_calls=calls,
            finish_reason="tool_calls" if calls else str(payload.get("done_reason") or "stop"),
            prompt_tokens=_as_int(payload.get("prompt_eval_count")),
            completion_tokens=_as_int(payload.get("eval_count")),
            cost_usd=0.0,  # No API charge; local compute still has a hardware/energy cost.
            upstream="local-ollama",
            model=self.model,
        )
