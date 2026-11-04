"""Additional authored invoices with manually calculated fields and expected decisions.

These are development regressions, not a blind or genuine-model accuracy benchmark.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_flow.providers.simulated import SimulatedProvider
from invoice_flow.schemas import Outcome, RunLifecycle
from invoice_flow.service import process_invoice
from workflow_helpers import make_app, payments

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/independent_invoices"


@pytest.mark.parametrize(
    ("filename", "number", "vendor", "total", "outcome"),
    [
        ("harbor.txt", "HARBOR-4721", "Harbor Components", "33.00", Outcome.PAID),
        ("unknown.csv", "ORCHARD-19", "Orchard Fabrication", "10.00", Outcome.REJECTED),
        ("scrutiny.json", "FOUNDRY-502", "North Foundry", "12500.00", Outcome.PAID),
        ("bad_total.json", "CIRCUIT-88", "Circuit Supply", "111.00", Outcome.REJECTED),
    ],
)
def test_independent_fields_and_outcomes(tmp_path, filename, number, vendor, total, outcome):
    app = make_app(tmp_path, SimulatedProvider())
    result = process_invoice(FIXTURES / filename, app=app, mode="mock")

    assert result.lifecycle is RunLifecycle.COMPLETED
    assert result.outcome is outcome
    assert result.draft is not None
    assert result.draft.invoice_number == number
    assert result.draft.vendor_name == vendor
    assert str(result.draft.total) == total
    assert result.draft.due_date_raw == "2026-12-01"
    assert len(payments(app)) == (1 if outcome is Outcome.PAID else 0)


def test_above_threshold_invoice_records_completed_scrutiny(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    result = process_invoice(FIXTURES / "scrutiny.json", app=app, mode="mock")

    assert result.outcome is Outcome.PAID
    assert result.requires_scrutiny
    assert any(approval.scrutiny_completed for approval in result.approval)
    assert result.receipt is not None and str(result.receipt.amount) == "12500.00"
