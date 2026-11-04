"""The only door to a model.

``ModelClient`` wraps a provider with the run budget, retries for retryable failures, event
emission and structured-output repair. Agents never call a provider directly, so no code path can
exceed the budget or hide a failure.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from .agent_models import OutputError, strict_schema, validate_output
from .budget import BudgetExceeded, RunBudget
from .providers.base import (
    Provider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ResponseSchema,
    redact,
)
from .tools.registry import TOOL_SPECS, ToolContext, ToolError, call_tool

T = TypeVar("T", bound=BaseModel)
Emit = Callable[[str, str, dict[str, Any]], None]  # (stage, type, payload)


class StructuredOutputError(Exception):
    """The model could not produce a valid structured answer, even after one repair request."""

    def __init__(self, label: str, problems: list[str]) -> None:
        super().__init__(f"{label}: {'; '.join(problems)}")
        self.label = label
        self.problems = problems


def tool_schemas(names: list[str]) -> list[dict[str, Any]]:
    schemas = []
    for name in names:
        spec = TOOL_SPECS[name]
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.json_schema(),
                },
            }
        )
    return schemas


class ModelClient:
    def __init__(
        self,
        provider: Provider,
        budget: RunBudget,
        emit: Emit,
        *,
        max_output_tokens: int = 1500,
        request_timeout: float = 30.0,
        max_retries: int = 1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.budget = budget
        self._emit = emit
        self.max_output_tokens = max_output_tokens
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self._sleep = sleep

    # ------------------------------------------------------------------ one request

    def call(self, request: ProviderRequest, *, stage: str = "approval") -> ProviderResponse:
        """One logical request, including retries of retryable failures. Every attempt counts."""
        request.max_tokens = min(request.max_tokens, self.max_output_tokens)
        retries = 0
        while True:
            allowed = self.budget.before_request()
            request.timeout = min(self.request_timeout, allowed)
            self._emit(
                stage,
                "model_request",
                {
                    "label": request.label,
                    "request_number": self.budget.requests,
                    "provider": self.provider.name,
                    "model": self.provider.model,
                    "simulated": self.provider.simulated,
                    "tools_offered": bool(request.tools),
                },
            )
            try:
                response = self.provider.complete(request)
            except ProviderError as exc:
                self._emit(
                    stage,
                    "model_error",
                    {
                        "label": request.label,
                        "code": exc.code,
                        "retryable": exc.retryable,
                        "message": redact(exc.message),
                    },
                )
                if not exc.retryable or retries >= self.max_retries:
                    raise
                wait = min(exc.retry_after if exc.retry_after is not None else 1.0, 10.0)
                if self.budget.remaining_requests() < 1 or self.budget.remaining_seconds() <= wait:
                    raise
                retries += 1
                self._emit(stage, "model_retry", {"label": request.label, "wait_seconds": wait})
                self._sleep(wait)
                self.budget.check_cancelled()
                continue
            self.budget.record_usage(
                response.prompt_tokens, response.completion_tokens, response.cost_usd
            )
            self._emit(
                stage,
                "model_response",
                {
                    "label": request.label,
                    "finish_reason": response.finish_reason,
                    "tool_calls": len(response.tool_calls),
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "served_by": response.upstream,
                    "usage_reported": response.prompt_tokens is not None,
                },
            )
            return response

    # ------------------------------------------------------------------ structured answers

    def structured(
        self,
        request: ProviderRequest,
        model_cls: type[T],
        *,
        stage: str,
        extra_check: Callable[[T], None] | None = None,
    ) -> tuple[T, ProviderResponse]:
        """Ask for ``model_cls``. On an unusable answer, ask once more with the problems listed.

        ``extra_check`` may raise :class:`OutputError` for rules the schema cannot express.
        Both attempts count against the budget.
        """
        request.response_schema = ResponseSchema(model_cls.__name__, strict_schema(model_cls))
        response = self.call(request, stage=stage)
        try:
            return self._parse(response, model_cls, extra_check), response
        except OutputError as first:
            self._emit(
                stage, "output_invalid", {"label": request.label, "problems": first.problems[:6]}
            )
            repair = dataclasses.replace(
                request,
                label=f"{request.label}_repair",
                messages=[
                    *request.messages,
                    {"role": "assistant", "content": (response.content or "")[:2000]},
                    {
                        "role": "user",
                        "content": "Your reply could not be used: "
                        + "; ".join(first.problems[:6])
                        + ". Reply again with ONLY the corrected JSON object.",
                    },
                ],
            )
            second_response = self.call(repair, stage=stage)
            try:
                return self._parse(second_response, model_cls, extra_check), second_response
            except OutputError as second:
                self._emit(
                    stage,
                    "output_invalid",
                    {
                        "label": repair.label,
                        "problems": second.problems[:6],
                        "final": True,
                    },
                )
                raise StructuredOutputError(request.label, second.problems) from second

    @staticmethod
    def _parse(
        response: ProviderResponse, model_cls: type[T], extra_check: Callable[[T], None] | None
    ) -> T:
        if response.finish_reason == "length":
            raise OutputError(["the reply was cut off (too long)"])
        if response.tool_calls:
            raise OutputError(["a tool call was made where a final answer was required"])
        parsed = validate_output(model_cls, response.content)
        if extra_check is not None:
            extra_check(parsed)
        return parsed

    # ------------------------------------------------------------------ tools

    def run_tool_round(
        self,
        request: ProviderRequest,
        tool_names: list[str],
        ctx: ToolContext,
        *,
        stage: str,
        require: bool = False,
    ) -> list[dict[str, Any]]:
        """Offer tools once. Execute whatever the model asks for, validated server-side.

        Returns the messages to append to the conversation (assistant tool request plus tool
        results), or ``[]`` when the model asked for nothing. Each tool execution is a real
        application event with the arguments and a result summary.
        """
        request.tools = tool_schemas(tool_names)
        request.tool_choice = "required" if require else "auto"
        response = self.call(request, stage=stage)
        request.tools, request.tool_choice = None, None
        if not response.tool_calls:
            if require:
                self._emit(stage, "tool_not_called", {"label": request.label})
            return []
        messages: list[dict[str, Any]] = [
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.arguments},
                    }
                    for c in response.tool_calls
                ],
            }
        ]
        for call in response.tool_calls:
            self.budget.before_tool()
            allowed = call.name in tool_names
            started = time.monotonic()
            try:
                if not allowed:
                    raise ToolError(
                        "unknown_tool", f"Tool '{call.name[:40]}' is not available here."
                    )
                result = call_tool(ctx, call.name, call.arguments)
                outcome, summary = "ok", _summarize(result)
            except ToolError as exc:
                result = {"error": exc.code, "message": exc.message}
                outcome, summary = "error", f"{exc.code}: {exc.message[:120]}"
            self._emit(
                stage,
                "tool_call",
                {
                    "tool": call.name[:60],
                    "arguments": _clip(call.arguments),
                    "outcome": outcome,
                    "result_summary": summary,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result)[:6000],
                }
            )
        return messages


def _clip(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _summarize(result: dict[str, Any]) -> str:
    return _clip(json.dumps(result, default=str), 200)


__all__ = ["BudgetExceeded", "ModelClient", "StructuredOutputError", "tool_schemas"]
