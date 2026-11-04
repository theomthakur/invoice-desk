"""Offline checks that the genuine-model acceptance test rejects failed integrations."""

from __future__ import annotations

import pytest

from invoice_flow.providers import ProviderError, ScriptedProvider, reply
from invoice_flow.providers.simulated import SimulatedProvider
from tests.integration import test_live_provider as live_checks
from workflow_helpers import make_app


def use_provider(monkeypatch, tmp_path, provider):
    app = make_app(
        tmp_path,
        provider,
        openrouter_api_key="offline-test-placeholder",
        model_budget_usd=1.0,
    )
    monkeypatch.setattr(live_checks, "live_app", lambda _: app)
    monkeypatch.setattr(live_checks, "EVIDENCE", tmp_path / "traces")
    return app


def test_positive_live_check_rejects_complete_provider_failure(tmp_path, monkeypatch):
    use_provider(
        monkeypatch,
        tmp_path,
        ScriptedProvider([ProviderError("auth_failed", "Offline forced provider failure.")]),
    )

    with pytest.raises(AssertionError):
        live_checks.test_genuine_tool_calling_and_structured_output(tmp_path)


def test_positive_live_check_rejects_missing_inventory_tool_call(tmp_path, monkeypatch):
    class NoToolsProvider(SimulatedProvider):
        def complete(self, request):
            if request.label == "validation":
                return reply({})
            return super().complete(request)

    use_provider(monkeypatch, tmp_path, NoToolsProvider())

    with pytest.raises(AssertionError):
        live_checks.test_genuine_tool_calling_and_structured_output(tmp_path)


def test_positive_live_check_accepts_the_complete_workflow_contract(tmp_path, monkeypatch):
    # This tests the acceptance assertions offline; it is never evidence of a genuine LLM run.
    use_provider(monkeypatch, tmp_path, SimulatedProvider())
    live_checks.test_genuine_tool_calling_and_structured_output(tmp_path)
