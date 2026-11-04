"""Scripted provider for tests: replays a fixed sequence of responses or errors.

This is test infrastructure. It is never selected silently at runtime and has no knowledge of
any invoice. It records every request so tests can assert exactly what the agents sent.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from typing import Any

from .base import Provider, ProviderError, ProviderRequest, ProviderResponse, ToolCall

Step = ProviderResponse | ProviderError | Callable[[ProviderRequest], ProviderResponse]


def reply(
    content: Any = None,
    *,
    tool_calls: list[tuple[str, dict[str, Any] | str]] | None = None,
    prompt_tokens: int | None = 100,
    completion_tokens: int | None = 50,
    cost_usd: float | None = 0.001,
    finish_reason: str | None = None,
) -> ProviderResponse:
    """Build a response. ``content`` may be a dict (sent as JSON) or text."""
    calls = [
        ToolCall(
            id=f"call_{i}", name=name, arguments=args if isinstance(args, str) else json.dumps(args)
        )
        for i, (name, args) in enumerate(tool_calls or [])
    ]
    text = content if content is None or isinstance(content, str) else json.dumps(content)
    return ProviderResponse(
        content=text,
        tool_calls=calls,
        finish_reason=finish_reason or ("tool_calls" if calls else "stop"),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        upstream="scripted",
        model="scripted-model",
    )


class ScriptedProvider(Provider):
    mode = "mock"
    name = "scripted"
    model = "scripted-model"
    simulated = True

    def __init__(self, steps: list[Step]) -> None:
        self._steps = list(steps)
        self.requests: list[ProviderRequest] = []

    @property
    def labels(self) -> list[str]:
        return [r.label for r in self.requests]

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        # snapshot: callers reuse and mutate the request object after the call
        self.requests.append(dataclasses.replace(request, messages=list(request.messages)))
        if not self._steps:
            raise ProviderError("script_exhausted", "The scripted provider has no more responses.")
        step = self._steps.pop(0)
        if isinstance(step, ProviderError):
            raise step
        if callable(step):
            return step(request)
        return step

    @property
    def remaining(self) -> int:
        return len(self._steps)
