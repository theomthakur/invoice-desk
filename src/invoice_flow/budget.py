"""One budget per run. Every model request, retry, repair and tool continuation passes through
here, so the limits cannot be bypassed by an agent that "just asks once more".

Counts are kept separately:
* ``requests``   provider round trips, including retries and tool continuations
* ``tool_calls`` tool executions the model asked for

Unknown usage is *unknown*, never zero: if a response carries no token counts we flag
``usage_complete = False`` instead of reporting a cost of nothing.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """A hard limit was reached. ``kind`` is ``requests``, ``deadline`` or ``tokens``."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class RunCancelled(Exception):
    """The run was cancelled (shutdown or user request) before it finished."""


@dataclass
class RunBudget:
    max_requests: int = 8
    timeout_seconds: float = 90.0
    max_total_tokens: int | None = None
    max_tool_calls: int = 6
    cancel_event: threading.Event = field(default_factory=threading.Event)

    requests: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = True
    usage_complete: bool = True
    started: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ---------------------------------------------------------------- limits

    def remaining_seconds(self) -> float:
        return self.timeout_seconds - (time.monotonic() - self.started)

    def remaining_requests(self) -> int:
        return max(0, self.max_requests - self.requests)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise RunCancelled("The run was cancelled.")

    def check_active(self) -> None:
        """Check boundaries without reserving another model or tool request."""
        self.check_cancelled()
        if self.remaining_seconds() <= 0:
            raise BudgetExceeded("deadline", f"Run time limit reached ({self.timeout_seconds:g}s).")

    def before_request(self) -> float:
        """Reserve one request. Returns the seconds this request may take at most."""
        with self._lock:
            self.check_active()
            if self.requests >= self.max_requests:
                raise BudgetExceeded(
                    "requests", f"Model request limit reached ({self.max_requests})."
                )
            remaining = self.remaining_seconds()
            total = self.prompt_tokens + self.completion_tokens
            if self.max_total_tokens is not None and total >= self.max_total_tokens:
                raise BudgetExceeded("tokens", f"Token limit reached ({self.max_total_tokens}).")
            self.requests += 1
            return remaining

    def before_tool(self) -> None:
        with self._lock:
            self.check_active()
            if self.tool_calls >= self.max_tool_calls:
                raise BudgetExceeded(
                    "requests", f"Tool call limit reached ({self.max_tool_calls})."
                )
            self.tool_calls += 1

    # ---------------------------------------------------------------- usage

    def record_usage(
        self, prompt: int | None, completion: int | None, cost_usd: float | None
    ) -> None:
        with self._lock:
            if prompt is None or completion is None:
                self.usage_complete = False
            self.prompt_tokens += prompt or 0
            self.completion_tokens += completion or 0
            if cost_usd is None:
                self.cost_known = False
            else:
                self.cost_usd += cost_usd

    def snapshot(self) -> dict[str, object]:
        """Plain dict for events and results. ``estimated_cost_usd`` is ``None`` when any
        request did not report a cost, so a missing number is never mistaken for zero."""
        return {
            "model_calls": self.requests,
            "tool_calls": self.tool_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "usage_complete": self.usage_complete or self.requests == 0,
            "estimated_cost_usd": (
                f"{self.cost_usd:.6f}" if self.cost_known and self.requests else None
            ),
        }
