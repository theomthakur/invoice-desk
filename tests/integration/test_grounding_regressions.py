"""Source evidence must survive model normalization, repairs, and payment decisions."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import TODAY, make_draft
from invoice_flow.findings import FindingCode as C
from invoice_flow.rules import validate_invoice
from invoice_flow.rules.grounding import verify_corrections
from invoice_flow.schemas import Correction, Eligibility, Outcome, RunLifecycle
from workflow_helpers import (
    critique,
    custom_source,
    extraction,
    lookup,
    make_app,
    payments,
    proposal,
    reply,
    run_source,
    scripted,
)


def invoice_text(*, invoice_date="2026-11-01", due_date="2026-12-01", terms=None, revision=None):
    lines = [
        "Invoice Number: INV-DATE",
        "Vendor: Test Vendor Inc.",
        f"Invoice Date: {invoice_date}",
    ]
    if due_date is not None:
        lines.append(f"Due Date: {due_date}")
    if terms is not None:
        lines.append(f"Payment Terms: {terms}")
    if revision is not None:
        lines.append(f"Revision: {revision}")
    lines.extend(["WidgetA 1 $100.00", "Total: $100.00"])
    return "\n".join(lines)


def model_draft(**fields):
    draft = make_draft(number="INV-DATE", lines=[("WidgetA", 1, "100.00")], **fields)
    draft.origin = "model"
    return draft


def unsupported_fields(report):
    return {finding.field for finding in report.findings if finding.code is C.VALUE_NOT_IN_SOURCE}


@pytest.mark.parametrize("invented_due", ["2026-11-30", "2026-11-01"])
def test_model_invented_due_date_cannot_release_payment(tmp_path, invented_due):
    claimed = extraction(
        invoice_number="INV-DATE",
        vendor_name="Test Vendor Inc.",
        invoice_date="2026-11-01",
        due_date=invented_due,
        currency="USD",
        total="100.00",
        line_items=[{"description": "WidgetA", "quantity": "1", "unit_price": "100.00"}],
    )

    def answer(request):
        if request.label in {"extraction", "reflection"}:
            return reply(claimed)
        if request.label == "validation":
            return lookup(["WidgetA"])
        if request.label in {"approval", "revision"}:
            return reply(proposal(rationale="Approve Test Vendor Inc. INV-DATE for 100.00 USD."))
        assert request.label == "critique"
        return reply(critique())

    provider = scripted(*([answer] * 8))
    app = make_app(tmp_path, provider)
    bank_calls = []
    app.mock_payment = lambda vendor, amount: (
        bank_calls.append((vendor, amount)) or {"status": "success"}
    )

    result = run_source(app, custom_source(invoice_text(due_date=None)))

    assert result.lifecycle is RunLifecycle.COMPLETED
    assert result.outcome is Outcome.NEEDS_REVIEW
    assert any(f.code is C.VALUE_NOT_IN_SOURCE and f.field == "due_date" for f in result.findings)
    assert result.receipt is None
    assert payments(app) == []
    assert bank_calls == []


def test_invoice_date_requires_its_own_source_evidence(index, policy):
    report = validate_invoice(model_draft(date_="2026-11-02"), invoice_text(), index, policy, TODAY)
    assert "invoice_date" in unsupported_fields(report)
    assert report.eligibility is Eligibility.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("invoice_date", "due_date"),
    [
        ("November 1, 2026", "December 1, 2026"),
        ("1-Nov-2026", "1st December 2026"),
        ("11/01/2026", "12/01/2026"),
        ("01.11.2026", "01.12.2026"),
    ],
)
def test_normalized_supported_source_dates_remain_eligible(index, policy, invoice_date, due_date):
    if "." in invoice_date:
        policy = policy.model_copy(
            update={"dates": policy.dates.model_copy(update={"slash_date_order": "dmy"})}
        )
    report = validate_invoice(
        model_draft(),
        invoice_text(invoice_date=invoice_date, due_date=due_date),
        index,
        policy,
        TODAY,
    )
    assert not unsupported_fields(report)
    assert report.eligibility is Eligibility.ELIGIBLE
    assert report.normalized.invoice_date == date(2026, 11, 1)
    assert report.normalized.due_date == date(2026, 12, 1)


def test_normalized_date_must_preserve_configured_slash_order(index, policy):
    source = invoice_text(invoice_date="04/05/2026")
    report = validate_invoice(model_draft(date_="2026-05-04"), source, index, policy, TODAY)
    assert "invoice_date" in unsupported_fields(report)


@pytest.mark.parametrize("original", ["1-Nov-2O26", "1st November 2O26"])
def test_normalized_ocr_date_correction_is_grounded_in_the_same_field(index, policy, original):
    draft = model_draft(
        corrections=[
            Correction(
                field="invoice_date",
                original=original,
                corrected="2026-11-01",
                evidence=f"Invoice Date: {original}",
            )
        ]
    )
    report = validate_invoice(draft, invoice_text(invoice_date=original), index, policy, TODAY)
    assert report.eligibility is Eligibility.ELIGIBLE
    assert C.EXTRACTION_CORRECTION in {f.code for f in report.findings}
    assert not unsupported_fields(report)


def test_date_correction_cannot_change_the_calendar_date(index, policy):
    draft = model_draft(
        date_="2026-11-02",
        corrections=[
            Correction(
                field="invoice_date",
                original="1-Nov-2O26",
                corrected="2026-11-02",
                evidence="Invoice Date: 1-Nov-2O26",
            )
        ],
    )
    report = validate_invoice(draft, invoice_text(invoice_date="1-Nov-2O26"), index, policy, TODAY)
    assert C.SUBSTANTIVE_CORRECTION in {finding.code for finding in report.findings}
    assert "invoice_date" in unsupported_fields(report)
    assert report.eligibility is Eligibility.NEEDS_REVIEW


@pytest.mark.parametrize("field", ["invoice_date", "due_date"])
@pytest.mark.parametrize("original", ["2026-1-11", "2O26-1-11"])
def test_date_correction_cannot_move_component_boundaries(field, original, policy):
    correction = Correction(
        field=field,
        original=original,
        corrected="2026-11-1",
        evidence=f"Date: {original}",
    )
    findings, accepted = verify_corrections([correction], correction.evidence, policy)
    assert accepted == []
    assert [finding.code for finding in findings] == [C.SUBSTANTIVE_CORRECTION]


def test_moving_date_separators_cannot_release_payment(tmp_path):
    claimed = extraction(
        invoice_number="INV-DATE",
        vendor_name="Test Vendor Inc.",
        invoice_date="2026-11-01",
        due_date="2026-11-1",
        currency="USD",
        total="100.00",
        line_items=[{"description": "WidgetA", "quantity": "1", "unit_price": "100.00"}],
        corrections=[
            {
                "field": "due_date",
                "item_ref": None,
                "original": "2026-1-11",
                "corrected": "2026-11-1",
                "evidence": "Due Date: 2026-1-11",
            }
        ],
    )

    def answer(request):
        if request.label in {"extraction", "reflection"}:
            return reply(claimed)
        if request.label == "validation":
            return lookup(["WidgetA"])
        if request.label in {"approval", "revision"}:
            return reply(proposal(rationale="Approve Test Vendor Inc. INV-DATE for 100.00 USD."))
        assert request.label == "critique"
        return reply(critique())

    app = make_app(tmp_path, scripted(*([answer] * 8)))
    bank_calls = []
    app.mock_payment = lambda vendor, amount: (
        bank_calls.append((vendor, amount)) or {"status": "success"}
    )
    result = run_source(app, custom_source(invoice_text(due_date="2026-1-11")))

    assert result.lifecycle is RunLifecycle.COMPLETED
    assert result.outcome is Outcome.NEEDS_REVIEW
    due_findings = {finding.code for finding in result.findings if finding.field == "due_date"}
    assert {C.SUBSTANTIVE_CORRECTION, C.VALUE_NOT_IN_SOURCE} <= due_findings
    assert C.EXTRACTION_CORRECTION not in due_findings
    assert result.receipt is None
    assert bank_calls == []
    assert payments(app) == []


def test_date_correction_cannot_supply_a_different_field(index, policy):
    draft = model_draft(
        due="2026-11-01",
        corrections=[
            Correction(
                field="due_date",
                original="1-Nov-2O26",
                corrected="1-Nov-2026",
                evidence="Invoice Date: 1-Nov-2O26",
            )
        ],
    )
    report = validate_invoice(
        draft, invoice_text(invoice_date="1-Nov-2O26", due_date=None), index, policy, TODAY
    )
    assert "due_date" in unsupported_fields(report)


def test_due_date_may_be_derived_from_grounded_invoice_date_and_source_terms(index, policy):
    report = validate_invoice(
        model_draft(payment_terms="Net 30"),
        invoice_text(invoice_date="November 1, 2026", due_date=None, terms="Net 30"),
        index,
        policy,
        TODAY,
    )
    assert report.eligibility is Eligibility.ELIGIBLE
    assert not unsupported_fields(report)


@pytest.mark.parametrize(
    ("invoice_date", "source_terms"),
    [("2026-11-02", "Net 30"), ("2026-11-01", None), ("2026-11-01", "Net 15")],
)
def test_due_date_derivation_cannot_use_invented_date_or_terms(
    index, policy, invoice_date, source_terms
):
    report = validate_invoice(
        model_draft(payment_terms="Net 30"),
        invoice_text(invoice_date=invoice_date, due_date=None, terms=source_terms),
        index,
        policy,
        TODAY,
    )
    assert "due_date" in unsupported_fields(report)


def test_derivation_cannot_replace_an_explicit_unreadable_due_date(index, policy):
    report = validate_invoice(
        model_draft(payment_terms="Net 30"),
        invoice_text(due_date="yesterday", terms="Net 30"),
        index,
        policy,
        TODAY,
    )
    assert "due_date" in unsupported_fields(report)


@pytest.mark.parametrize("source_revision", [None, "20"])
def test_revision_claim_needs_labeled_source_evidence(index, policy, source_revision):
    report = validate_invoice(
        model_draft(revision="2"), invoice_text(revision=source_revision), index, policy, TODAY
    )
    assert "revision" in unsupported_fields(report)
    assert report.eligibility is Eligibility.NEEDS_REVIEW


@pytest.mark.parametrize("source_revision", ["2", "R02", "version 2"])
def test_equivalent_revision_labels_are_grounded(index, policy, source_revision):
    report = validate_invoice(
        model_draft(revision="r2"), invoice_text(revision=source_revision), index, policy, TODAY
    )
    assert not unsupported_fields(report)
    assert report.eligibility is Eligibility.ELIGIBLE


@pytest.mark.parametrize("original,corrected", [("R1.1", "R11"), ("R1-1", "R11")])
def test_revision_correction_cannot_remove_component_boundaries(original, corrected):
    source = f"Revision: {original}"
    findings, accepted = verify_corrections(
        [Correction(field="revision", original=original, corrected=corrected, evidence=source)],
        source,
    )
    assert accepted == []
    assert any(f.code is C.SUBSTANTIVE_CORRECTION for f in findings)


def test_revision_correction_cannot_quote_only_a_prefix_of_a_version(index, policy):
    draft = model_draft(revision="R1")
    draft.corrections = [
        Correction(field="revision", original="R1", corrected="R1", evidence="Revision: R1.1")
    ]
    report = validate_invoice(draft, invoice_text(revision="R1.1"), index, policy, TODAY)
    assert "revision" in unsupported_fields(report)
