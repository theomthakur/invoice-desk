"""Negative tax cannot act as an implicit credit at the payment boundary."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from conftest import TODAY, make_draft
from invoice_flow.findings import FindingCode as C
from invoice_flow.parsers import load_source
from invoice_flow.rules import validate_invoice
from invoice_flow.schemas import Eligibility, Outcome
from invoice_flow.service import execute_run
from workflow_helpers import critique, lookup, make_app, payments, proposal, reply, scripted


def test_negative_json_rate_with_no_tax_amount_is_rejected_before_payment(tmp_path, policy):
    path = tmp_path / "negative-tax.json"
    path.write_text(
        json.dumps(
            {
                "invoice_number": "INV-TAX",
                "vendor": "Test Vendor Inc.",
                "date": "2026-11-01",
                "due_date": "2026-12-01",
                "currency": "USD",
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "100"}],
                "subtotal": "100",
                "tax_rate": "-0.2",
                "total": "80",
            }
        )
    )
    provider = scripted(
        lookup(["WidgetA"]),
        reply(proposal(rationale="Approve Test Vendor Inc. INV-TAX for 80.00 USD.")),
        reply(critique()),
    )
    app = make_app(tmp_path / "app", provider)
    bank_calls = []
    app.mock_payment = lambda vendor, amount: (
        bank_calls.append((vendor, amount)) or {"status": "success"}
    )
    result = execute_run(
        app, workspace_id="local", source=load_source(path, policy).source, mode="mock", today=TODAY
    )

    assert result.outcome is Outcome.REJECTED
    assert any(f.field == "tax_rate" and f.blocks_payment for f in result.findings)
    assert any(
        f.code is C.NEGATIVE_AMOUNT and f.field == "tax_amount" and f.observed == "-20"
        for f in result.findings
    )
    assert payments(app) == []
    assert bank_calls == []
    assert provider.requests == []


def test_negative_rate_is_invalid_even_when_stated_tax_is_zero(index, policy):
    draft = make_draft(
        lines=[("WidgetA", 1, "100")], tax_rate="-0.00001", tax_amount="0", total="100"
    )
    report = validate_invoice(draft, "", index, policy, TODAY)
    assert report.eligibility is Eligibility.REJECTED
    assert any(f.field == "tax_rate" and f.blocks_payment for f in report.findings)


def test_negative_derived_subtotal_and_tax_are_both_reported(index, policy):
    draft = make_draft(lines=[("WidgetA", 1, "-100")], tax_rate="0.2", total="-120")
    report = validate_invoice(draft, "", index, policy, TODAY)
    negative_fields = {f.field for f in report.findings if f.code is C.NEGATIVE_AMOUNT}
    assert {"unit_price", "subtotal", "tax_amount", "total"} <= negative_fields


@pytest.mark.parametrize(
    ("rate", "price", "tax", "total"),
    [("0", "100", "0", "100"), ("0.2", "100", "20", "120"), ("0.07", "33.33", "2.33", "35.66")],
)
def test_nonnegative_derived_tax_remains_eligible(index, policy, rate, price, tax, total):
    report = validate_invoice(
        make_draft(lines=[("WidgetA", 1, price)], tax_rate=rate, total=total),
        "",
        index,
        policy,
        TODAY,
    )
    assert report.eligibility is Eligibility.ELIGIBLE
    assert report.normalized.tax == Decimal(tax)
