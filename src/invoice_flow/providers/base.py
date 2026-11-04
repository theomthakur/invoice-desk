"""Provider contract: one small interface for every model backend.

A provider turns a request into a typed response. It never decides what to do with the answer,
never retries (the :class:`~invoice_flow.llm.ModelClient` owns retries and the budget), and never
falls back to another model. A failure is raised as :class:`ProviderError` and stays visible.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """A model request failed. ``code`` is stable; ``message`` is safe to show (no secrets)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class ResponseSchema:
    name: str
    schema: dict[str, Any]


@dataclass
class ProviderRequest:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | None = None  # "auto" | "required" | "none"
    response_schema: ResponseSchema | None = None
    max_tokens: int = 1500
    timeout: float = 30.0
    label: str = ""  # for events and scripted tests, e.g. "extraction"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text exactly as the model produced it


@dataclass
class ProviderResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    # ``None`` means the provider did not report it. That is not the same as zero.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    upstream: str | None = None  # provider that actually served the request
    model: str | None = None
    response_id: str | None = None


class Provider(ABC):
    """Identity fields are shown to users, so they must be truthful."""

    mode: str  # "live" or "mock"
    name: str  # "openrouter", "simulated", "scripted"
    model: str
    simulated: bool = False

    @abstractmethod
    def complete(self, request: ProviderRequest) -> ProviderResponse: ...

    def close(self) -> None:  # pragma: no cover - default is nothing to release
        return None


_KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
]


def redact(text: str, *secrets: str | None) -> str:
    """Remove API keys from any text before it is logged, stored or returned."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    for pattern in _KEY_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text
