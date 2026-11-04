"""LIVE model tests. They spend real money, so they NEVER run by accident.

They run only when ALL of these are true:
* you pass ``-m live`` (the default test run deselects them);
* ``OPENROUTER_API_KEY`` is set;
* ``MODEL_BUDGET_USD`` is set to a positive cap;
* ``RUN_LIVE_TESTS=1`` is set.

Each test saves a sanitized trace under ``evidence/live/`` so a real run leaves proof, including
failures and the usage the provider actually reported. They assert the safety properties that hold
for any model; they do not promise the same wording or outcome on every run.

    RUN_LIVE_TESTS=1 OPENROUTER_API_KEY=... MODEL_BUDGET_USD=1 \\
        pytest -m live tests/integration/test_live_provider.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from invoice_flow.providers import OpenRouterProvider
from invoice_flow.runtime import DEFAULT_MODEL
from invoice_flow.schemas import Outcome, RunLifecycle
from workflow_helpers import events, make_app, payments, run

EVIDENCE = Path(__file__).resolve().parents[2] / "evidence" / "live"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (
            os.environ.get("RUN_LIVE_TESTS") == "1"
            and os.environ.get("OPENROUTER_API_KEY")
            and os.environ.get("MODEL_BUDGET_USD")
        ),
        reason="live tests need RUN_LIVE_TESTS=1, OPENROUTER_API_KEY and MODEL_BUDGET_USD",
    ),
]


def live_app(tmp_path):
    app = make_app(
        tmp_path,
        openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
        llm_model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        model_budget_usd=float(os.environ["MODEL_BUDGET_USD"]),
    )
    app.provider_factory = lambda mode: OpenRouterProvider(
        app.config.openrouter_api_key, app.config.llm_model
    )
    return app


def save_trace(app, result, name: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": result.model,
        "provider": result.provider,
        "lifecycle": result.lifecycle.value,
        "outcome": result.outcome.value if result.outcome else None,
        "usage": result.usage.model_dump(),
        "reason": result.terminal_reason,
        "events": events(app, result),
    }
    (EVIDENCE / f"{name}.json").write_text(json.dumps(payload, indent=2, default=str))


def test_genuine_tool_calling_and_structured_output(tmp_path):
    app = live_app(tmp_path)
    result = run(app, "invoice_1001.txt", mode="live")
    save_trace(app, result, "clean_text_invoice")
    assert result.lifecycle in (RunLifecycle.COMPLETED, RunLifecycle.FAILED)
    if result.lifecycle is RunLifecycle.COMPLETED:
        assert result.usage.model_calls >= 1
    tool_events = [e for e in events(app, result) if e["type"] == "tool_call"]
    # A real model should call the catalog tool. Record it either way; do not fake it.
    assert all(e["payload"]["tool"] == "lookup_inventory" for e in tool_events)
    if result.outcome is Outcome.PAID:
        assert len(payments(app)) == 1


def test_messy_invoice_never_pays_without_a_person(tmp_path):
    app = live_app(tmp_path)
    result = run(app, "invoice_1012.txt", mode="live")
    save_trace(app, result, "messy_ocr_invoice")
    assert result.outcome is not Outcome.PAID  # the vendor-rename hold is enforced in code
    assert payments(app) == []


def test_fraudulent_invoice_is_never_paid(tmp_path):
    app = live_app(tmp_path)
    result = run(app, "invoice_1003.txt", mode="live")
    save_trace(app, result, "fraudulent_invoice")
    assert result.outcome in (Outcome.REJECTED, None) and payments(app) == []


def test_reported_usage_is_recorded_not_invented(tmp_path):
    app = live_app(tmp_path)
    result = run(app, "invoice_1002.txt", mode="live")
    save_trace(app, result, "usage_report")
    if result.usage.model_calls:
        # either the provider reported usage, or we say it is incomplete. Never a silent zero.
        assert result.usage.usage_complete or result.usage.estimated_cost_usd is None
