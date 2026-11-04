"""Cancellation/deadline regressions at the final model and payment boundaries."""

from __future__ import annotations

import threading

import pytest

from invoice_flow.budget import BudgetExceeded, RunBudget
from invoice_flow.llm import ModelClient
from invoice_flow.providers import ProviderRequest, ScriptedProvider, reply
from invoice_flow.providers.simulated import SimulatedProvider
from invoice_flow.runtime import EventRecorder
from invoice_flow.schemas import Outcome, RunLifecycle
from workflow_helpers import custom_source, make_app, payments, run_source

TEXT = """Invoice Number: STOP-100
Vendor: Test Vendor
Invoice Date: 2026-11-01
Due Date: 2026-11-30
Currency: USD
WidgetA 1 $100 $100
Subtotal: $100
Total: $100
"""


def test_cancellation_during_final_critique_prevents_payment(tmp_path):
    cancelled = threading.Event()

    class CancellingProvider(SimulatedProvider):
        def complete(self, request):
            response = super().complete(request)
            if request.label == "critique":
                cancelled.set()
            return response

    app = make_app(tmp_path, CancellingProvider())
    result = run_source(app, custom_source(TEXT), cancel=cancelled)

    assert result.lifecycle is RunLifecycle.INTERRUPTED
    assert result.outcome is None
    assert result.receipt is None
    assert payments(app) == []


def test_cancellation_immediately_before_bank_call_prevents_payment(tmp_path, monkeypatch):
    cancelled = threading.Event()
    original = EventRecorder.__call__

    def emit_and_cancel(self, stage, type_, payload):
        original(self, stage, type_, payload)
        if type_ == "payment_attempt":
            cancelled.set()

    monkeypatch.setattr(EventRecorder, "__call__", emit_and_cancel)
    app = make_app(tmp_path, SimulatedProvider())
    result = run_source(app, custom_source(TEXT), cancel=cancelled)

    assert cancelled.is_set()
    assert result.lifecycle is RunLifecycle.INTERRUPTED
    assert result.receipt is None
    assert payments(app) == []


def test_a_response_returning_after_the_deadline_is_not_used(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("invoice_flow.budget.time.monotonic", lambda: now[0])
    budget = RunBudget(started=0.0, timeout_seconds=10.0)

    def late_response(request):
        now[0] = 11.0
        return reply({"approved": True})

    provider = ScriptedProvider([late_response])
    client = ModelClient(provider, budget, lambda *args: None)

    with pytest.raises(BudgetExceeded) as failure:
        client.call(ProviderRequest(messages=[], label="final_response"))

    assert failure.value.kind == "deadline"
    assert budget.snapshot()["model_calls"] == 1
    assert budget.snapshot()["prompt_tokens"] == 100


def test_an_expired_deadline_at_the_payment_boundary_prevents_payment(tmp_path, monkeypatch):
    expired = [False]
    original_emit = EventRecorder.__call__
    original_remaining = RunBudget.remaining_seconds

    def emit_and_expire(self, stage, type_, payload):
        original_emit(self, stage, type_, payload)
        if type_ == "payment_attempt":
            expired[0] = True

    def remaining(self):
        return -1.0 if expired[0] else original_remaining(self)

    monkeypatch.setattr(EventRecorder, "__call__", emit_and_expire)
    monkeypatch.setattr(RunBudget, "remaining_seconds", remaining)
    app = make_app(tmp_path, SimulatedProvider())
    result = run_source(app, custom_source(TEXT))

    assert expired[0]
    assert result.outcome is Outcome.NEEDS_REVIEW
    assert "Run time limit" in result.terminal_reason
    assert payments(app) == []


def test_cancellation_while_acquiring_the_payment_lock_prevents_bank_call(tmp_path, monkeypatch):
    from invoice_flow import workflow

    cancelled = threading.Event()
    bank_calls = []
    app = make_app(tmp_path, SimulatedProvider())
    app.mock_payment = lambda *args: bank_calls.append(args) or {"status": "success"}
    original_payment = workflow.execute_mock_payment
    original_connect = app.db.connect
    locked, release = threading.Event(), threading.Event()
    failures = []

    def hold_write_lock():
        try:
            with app.db.transaction():
                locked.set()
                if not release.wait(5):
                    raise AssertionError("Payment did not reach lock acquisition.")
        except Exception as error:
            failures.append(error)

    def contended_payment(*args, **kwargs):
        holder = threading.Thread(target=hold_write_lock)
        holder.start()
        assert locked.wait(5)

        def connect_with_trace():
            connection = original_connect()

            def before_statement(statement):
                if statement == "BEGIN IMMEDIATE":
                    cancelled.set()
                    release.set()

            connection.set_trace_callback(before_statement)
            return connection

        monkeypatch.setattr(app.db, "connect", connect_with_trace)
        try:
            return original_payment(*args, **kwargs)
        finally:
            release.set()
            holder.join(5)
            monkeypatch.setattr(app.db, "connect", original_connect)

    monkeypatch.setattr(workflow, "execute_mock_payment", contended_payment)
    result = run_source(app, custom_source(TEXT), cancel=cancelled)

    assert cancelled.is_set() and not failures
    assert result.lifecycle is RunLifecycle.INTERRUPTED
    assert bank_calls == []
    assert payments(app) == []
