"""Direct xAI Chat Completions using the shared validated response/error handling.

No OpenRouter routing parameters or credentials are sent to xAI. Reported USD ticks
are converted using xAI's documented 10^10 ticks per dollar. Missing cost stays unknown.
This adapter has offline contract tests; a funded direct xAI run is not yet recorded.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import httpx

from .base import ProviderError, ProviderRequest, ProviderResponse
from .openrouter import OpenRouterProvider, _as_int

DEFAULT_XAI_URL = "https://api.x.ai/v1"
DEFAULT_XAI_MODEL = "grok-4.7"


class XAIProvider(OpenRouterProvider):
    name = "xai"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_XAI_MODEL,
        *,
        base_url: str = DEFAULT_XAI_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ProviderError("no_api_key", "XAI_API_KEY is not configured.")
        super().__init__(api_key, model, base_url=base_url, transport=transport)
        self._headers.pop("X-Title", None)

    def __repr__(self) -> str:
        return f"XAIProvider(model={self.model!r})"

    def build_body(self, request: ProviderRequest) -> dict[str, Any]:
        body = super().build_body(request)
        body.pop("provider")
        body["max_completion_tokens"] = body.pop("max_tokens")
        return body

    def _parse(self, payload: dict[str, Any]) -> ProviderResponse:
        response = super()._parse(payload)
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        ticks = _as_int(usage.get("cost_in_usd_ticks"))
        return replace(
            response,
            cost_usd=ticks / 10_000_000_000 if ticks is not None else None,
            upstream="xai",
        )
