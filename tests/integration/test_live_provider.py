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
from invoice_flow.schemas import ApprovalStage, Outcome, RunLifecycle
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
    with app.db.read() as conn:
        document = conn.execute(
            "SELECT byte_hash FROM documents WHERE workspace_id = ? AND document_id = ?",
            (result.workspace_id, result.document_id),
        ).fetchone()
    payload = {
        "model": result.model,
        "provider": result.provider,
        "lifecycle": result.lifecycle.value,
        "outcome": result.outcome.value if result.outcome else None,
        "usage": result.usage.model_dump(),
        "reason": result.terminal_reason,
        "input_sha256": document["byte_hash"] if document else None,
        "policy_version": result.policy_version,
        "decisions": [approval.model_dump(mode="json") for approval in result.approval],
        "events": events(app, result),
    }
    (EVIDENCE / f"{name}.json").write_text(json.dumps(payload, indent=2, default=str))


def test_genuine_tool_calling_and_structured_output(tmp_path):
    app = live_app(tmp_path)
    result = run(app, "invoice_1001.txt", mode="live")
    save_trace(app, result, "clean_text_invoice")
    assert result.lifecycle is RunLifecycle.COMPLETED, result.terminal_reason
    assert result.outcome is Outcome.PAID, result.terminal_reason
    assert result.usage.model_calls >= 1
    assert result.invoice is not None
    assert result.invoice.invoice_number == "INV-1001"
    assert result.invoice.vendor_name == "Widgets Inc."
    assert str(result.invoice.total) == "5000.00"
    tool_events = [e for e in events(app, result) if e["type"] == "tool_call"]
    assert tool_events, "The model must actually call the inventory tool."
    assert all(e["payload"]["tool"] == "lookup_inventory" for e in tool_events)
    assert any(e["payload"]["outcome"] == "ok" for e in tool_events)
    stages = {approval.stage for approval in result.approval}
    assert {ApprovalStage.APPROVAL, ApprovalStage.CRITIQUE, ApprovalStage.FINAL} <= stages
    assert result.receipt is not None and str(result.receipt.amount) == "5000.00"
    assert len(payments(app)) == 1


def test_messy_invoice_never_pays_without_a_person(tmp_path):
    app = live_app(tmp_path)
    result = run(app, "invoice_1012.txt", mode="live")
    save_trace(app, result, "messy_ocr_invoice")
    assert result.lifecycle is RunLifecycle.COMPLETED, result.terminal_reason
    assert result.outcome is Outcome.NEEDS_REVIEW
    assert payments(app) == []


def test_fraudulent_invoice_is_never_paid(tmp_path):
    app = live_app(tmp_path)
    result = run(app, "invoice_1003.txt", mode="live")
    save_trace(app, result, "fraudulent_invoice")
    assert result.lifecycle is RunLifecycle.COMPLETED, result.terminal_reason
    assert result.outcome is Outcome.REJECTED and payments(app) == []


def test_reported_usage_is_recorded_not_invented(tmp_path):
    app = live_app(tmp_path)
    result = run(app, "invoice_1002.txt", mode="live")
    save_trace(app, result, "usage_report")
    assert result.lifecycle is RunLifecycle.COMPLETED, result.terminal_reason
    assert result.usage.model_calls > 0
    # Either the provider reported usage, or we say it is incomplete. Never a silent zero.
    assert result.usage.usage_complete or result.usage.estimated_cost_usd is None
