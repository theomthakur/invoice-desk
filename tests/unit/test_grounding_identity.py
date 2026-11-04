"""Grounding of model-repaired values (invariant 10) and invoice identity."""

from __future__ import annotations

import pytest

from conftest import TODAY, make_draft
from fixtures.drafts import DRAFTS
from invoice_flow.findings import FindingCode as C
from invoice_flow.findings import Severity
from invoice_flow.identity import content_hash, payable_key, semantic_fingerprint
from invoice_flow.rules import validate_invoice
from invoice_flow.rules.grounding import verify_corrections
from invoice_flow.schemas import Correction, Eligibility, InvoiceDraft, LineItemDraft

SRC_1012 = "DATE: 26-Jan-2O26\nWidgetB 7 $500 $3,500.O0\nTOTAL: $9,975.00"


def correction(**overrides):
    base = dict(
        field="invoice_date",
        original="26-Jan-2O26",
        corrected="26-Jan-2026",
        evidence="DATE: 26-Jan-2O26",
    )
    base.update(overrides)
    return Correction(**base)


# ------------------------------------------------------------------ corrections


def test_plain_ocr_repair_is_accepted_with_evidence():
    findings, accepted = verify_corrections([correction()], SRC_1012)
    assert [f.code for f in findings] == [C.EXTRACTION_CORRECTION] and len(accepted) == 1
    assert findings[0].severity is Severity.INFO and findings[0].evidence == "DATE: 26-Jan-2O26"


def test_amount_repair_with_letter_o_is_accepted():
    c = correction(
        field="amount",
        item_ref="line 2",
        original="3,500.O0",
        corrected="3500.00",
        evidence="WidgetB 7 $500 $3,500.O0",
    )
    findings, accepted = verify_corrections([c], SRC_1012)
    assert findings[0].code is C.EXTRACTION_CORRECTION and accepted


@pytest.mark.parametrize(
    ("original", "corrected"),
    [
        ("3,500.O0", "350,000"),
        ("3,500.O0", "35.00"),
        ("3,500.O0", "3500.50"),
        ("3,500.O0", "5000.00"),
    ],
)
def test_a_repair_that_changes_the_amount_is_not_a_repair(original, corrected):
    # a model must not be able to move a decimal point and call it OCR cleanup
    c = correction(
        field="amount", original=original, corrected=corrected, evidence="WidgetB 7 $500 $3,500.O0"
    )
    findings, accepted = verify_corrections([c], SRC_1012)
    assert findings[0].code is C.SUBSTANTIVE_CORRECTION and not accepted
    assert findings[0].severity is Severity.REVIEW


def test_evidence_must_exist_in_the_source():
    findings, accepted = verify_corrections([correction(evidence="DATE: 26-Jan-2026")], SRC_1012)
    assert findings[0].code is C.CORRECTION_NOT_IN_SOURCE and not accepted


def test_original_must_be_inside_the_quoted_evidence():
    c = correction(original="31-Dec-2025")  # evidence is real, but not the thing being corrected
    findings, _ = verify_corrections([c], SRC_1012)
    assert findings[0].code is C.CORRECTION_NOT_IN_SOURCE


def test_evidence_matching_ignores_whitespace_and_case_only():
    c = correction(evidence="date:  26-jan-2o26")
    assert verify_corrections([c], SRC_1012)[0][0].code is C.EXTRACTION_CORRECTION


def test_a_repeated_model_answer_is_not_proof(index, policy):
    # the same invented amount twice is still absent from the source
    invented = InvoiceDraft(
        origin="model",
        invoice_number="INV-9",
        vendor_name="Test Vendor Inc.",
        due_date_raw="2026-12-01",
        invoice_date_raw="2026-11-01",
        line_items=[
            LineItemDraft(line_no=1, description="WidgetA", quantity="2", unit_price="777")
        ],
        total="1554",
    )
    source = "INV-9 Test Vendor Inc. WidgetA qty 2 price 250 total 500"
    for _ in range(2):
        report = validate_invoice(invented, source, index, policy, TODAY)
        flagged = {(f.field) for f in report.findings if f.code is C.VALUE_NOT_IN_SOURCE}
        assert {"unit_price", "total"} <= flagged
        assert report.eligibility is Eligibility.NEEDS_REVIEW


def test_every_value_in_a_grounded_draft_passes(sample, index, policy):
    for name in (
        "invoice_1001.txt",
        "invoice_1010.txt",
        "invoice_1012.txt",
        "invoice_1012.pdf",
        "invoice_1013.pdf",
        "invoice_1011.pdf",
    ):
        report = validate_invoice(DRAFTS[name], sample(name).source.text, index, policy, TODAY)
        assert C.VALUE_NOT_IN_SOURCE not in {f.code for f in report.findings}, name


def test_item_missing_from_source_is_flagged(index, policy):
    draft = make_draft(lines=[("WidgetA", 2, "250.00")])
    draft.origin = "model"
    report = validate_invoice(
        draft, "INV-T1 Test Vendor Inc. 2 250.00 500.00", index, policy, TODAY
    )
    assert "item" in {f.field for f in report.findings if f.code is C.VALUE_NOT_IN_SOURCE}


def test_structured_drafts_skip_grounding(index, policy):
    report = validate_invoice(make_draft(), "unrelated text", index, policy, TODAY)
    assert C.VALUE_NOT_IN_SOURCE not in {f.code for f in report.findings}


def test_tax_rate_percent_in_source_matches_fractional_rate(index, policy):
    draft = DRAFTS["invoice_1012.txt"]  # source says "TAX (5%)", draft stores 0.05
    text = (
        "INV 1012 QuickShip Distributers Widget A 12 $250 $3,000.00 WidgetB 7 $500 3500.00 "
        "Gadget X 4 $750 3,000.00 SUBTOTAL 9,500.00 TAX (5%): 475.00 TOTAL 9,975.00"
    )
    report = validate_invoice(draft, text, index, policy, TODAY)
    assert "tax_rate" not in {f.field for f in report.findings if f.code is C.VALUE_NOT_IN_SOURCE}


# ------------------------------------------------------------------ identity


def test_payable_key_is_stable_across_formatting():
    a = make_draft(number="INV-1012", vendor="QuickShip Distributers")
    b = make_draft(number="INV 1012", vendor="  quickship  DISTRIBUTERS ")
    assert (
        payable_key(a) == payable_key(b) and payable_key(a).payable_id == payable_key(b).payable_id
    )


def test_payable_key_separates_different_vendors_and_numbers():
    base = make_draft(number="INV-1", vendor="Widgets Inc.")
    assert payable_key(base) != payable_key(make_draft(number="INV-1", vendor="Widgets LLC"))
    assert payable_key(base) != payable_key(make_draft(number="INV-2", vendor="Widgets Inc."))
    assert payable_key(base) != payable_key(make_draft(number="INV-01", vendor="Widgets Inc."))


def test_missing_identity_has_no_payable():
    assert payable_key(make_draft(vendor="")) is None
    no_number = make_draft()
    no_number.invoice_number = None
    assert payable_key(no_number) is None


def test_same_invoice_in_two_formats_shares_one_fingerprint(policy):
    assert semantic_fingerprint(DRAFTS["invoice_1011.txt"], policy) == semantic_fingerprint(
        DRAFTS["invoice_1011.pdf"], policy
    )  # PDF omits subtotal/tax lines
    assert semantic_fingerprint(DRAFTS["invoice_1012.txt"], policy) == semantic_fingerprint(
        DRAFTS["invoice_1012.pdf"], policy
    )


def test_pdf_and_json_of_the_same_invoice_match(sample, policy):
    json_draft = sample("invoice_1013.json").draft
    assert semantic_fingerprint(json_draft, policy) == semantic_fingerprint(
        DRAFTS["invoice_1013.pdf"], policy
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda d: setattr(d, "total", "501.00"),
        lambda d: setattr(d, "subtotal", "999.00"),
        lambda d: setattr(d.line_items[0], "amount", "999.00"),
        lambda d: setattr(d, "due_date_raw", "2026-12-02"),
        lambda d: setattr(d, "currency", "EUR"),
        lambda d: setattr(d.line_items[0], "quantity", 3),
        lambda d: setattr(d.line_items[0], "unit_price", 251),
        lambda d: setattr(d.line_items[0], "description", "WidgetB"),
        lambda d: setattr(d, "shipping", "5.00"),
        lambda d: setattr(d, "vendor_name", "Other Vendor"),
    ],
)
def test_any_material_change_changes_the_fingerprint(policy, change):
    original, changed = make_draft(), make_draft()
    change(changed)
    assert semantic_fingerprint(original, policy) != semantic_fingerprint(changed, policy)


def test_layout_only_differences_do_not_change_the_fingerprint(policy):
    a = make_draft(vendor="Widgets Inc.", lines=[("WidgetA", 2, "250.00")])
    b = make_draft(vendor="WIDGETS  INC", lines=[("widget a", 2, "250")], total="500")
    assert semantic_fingerprint(a, policy) == semantic_fingerprint(b, policy)


def test_line_order_does_not_matter(policy):
    a = make_draft(lines=[("WidgetA", 1, "10"), ("WidgetB", 2, "20")])
    b = make_draft(lines=[("WidgetB", 2, "20"), ("WidgetA", 1, "10")])
    assert semantic_fingerprint(a, policy) == semantic_fingerprint(b, policy)


def test_content_hash_binds_to_normalized_values(index, policy):
    one = validate_invoice(make_draft(), "", index, policy, TODAY).normalized
    same = validate_invoice(make_draft(), "", index, policy, TODAY).normalized
    other = validate_invoice(
        make_draft(lines=[("WidgetA", 3, "250.00")]), "", index, policy, TODAY
    ).normalized
    assert content_hash(one) == content_hash(same) != content_hash(other)


def test_fabricated_quote_containing_original_is_not_source_evidence():
    forged = correction(evidence="Forged document DATE: 26-Jan-2O26")
    findings, accepted = verify_corrections([forged], SRC_1012)
    assert [f.code for f in findings] == [C.CORRECTION_NOT_IN_SOURCE]
    assert accepted == []
