"""Source grounding for model-produced drafts (shared invariant 10).

A model may repair what it read, but a repeated answer is not proof. These checks compare the
draft against the source text itself:

* every correction must quote evidence that really appears in the source, and its original
  value must be inside that evidence;
* a correction is only accepted as a plain repair when it differs by look-alike characters,
  spacing or punctuation (``2O26`` -> ``2026``). Anything else needs a person;
* every amount, quantity, identity and item name must appear in the source (or be a
  verified correction).

Structured drafts (``origin == "structured"``) came from code, not a model, and skip this.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ..findings import Finding, FindingCode, make_finding
from ..schemas import Correction, InvoiceDraft
from ..textnorm import collapse_ws, invoice_number_key, item_key, ocr_skeleton, vendor_key

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _fold(text: str) -> str:
    return collapse_ws(text).casefold()


def _numbers(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER.findall(text):
        try:
            found.add(Decimal(token.replace(",", "")).normalize())
        except InvalidOperation:  # pragma: no cover - the regex only yields digits
            continue
    return found


def _alnum(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.casefold())


_NUMERIC_FIELDS = {
    "quantity",
    "unit_price",
    "amount",
    "subtotal",
    "tax_amount",
    "tax_rate",
    "shipping",
    "total",
}
_LOOKALIKE = str.maketrans({"o": "0", "l": "1", "i": "1", "s": "5"})


def _numeric_skeleton(text: str) -> str:
    """For amounts the decimal point *is* part of the value, so it is kept. Only currency
    symbols, thousands commas, spaces and look-alike letters are ignored. '3,500.O0' and
    '3500.00' match; '3,500.O0' and '350,000' do not."""
    cleaned = re.sub(r"[\s,$€£]", "", text.casefold())
    return cleaned.translate(_LOOKALIKE)


def _skeleton(correction: Correction, *, corrected: bool = False) -> str:
    text = correction.corrected if corrected else correction.original
    if correction.field in _NUMERIC_FIELDS:
        return _numeric_skeleton(text)
    return ocr_skeleton(text)


def verify_corrections(
    corrections: list[Correction], source_text: str
) -> tuple[list[Finding], list[Correction]]:
    """Return findings and the corrections that are verified plain repairs."""
    findings: list[Finding] = []
    accepted: list[Correction] = []
    folded_source = _fold(source_text)
    for correction in corrections:
        ref = correction.item_ref
        evidence = _fold(correction.evidence)
        if not evidence or evidence not in folded_source:
            findings.append(
                make_finding(
                    FindingCode.CORRECTION_NOT_IN_SOURCE,
                    f"Correction of {correction.field} cites text that is not in the source.",
                    field=correction.field,
                    item_ref=ref,
                    observed=correction.original,
                    expected=correction.corrected,
                    evidence=correction.evidence,
                )
            )
            continue
        if _fold(correction.original) not in evidence:
            findings.append(
                make_finding(
                    FindingCode.CORRECTION_NOT_IN_SOURCE,
                    f"The original value '{correction.original}' is not inside the quoted "
                    "evidence.",
                    field=correction.field,
                    item_ref=ref,
                    observed=correction.original,
                    expected=correction.corrected,
                    evidence=correction.evidence,
                )
            )
            continue
        if _skeleton(correction) != _skeleton(correction, corrected=True):
            findings.append(
                make_finding(
                    FindingCode.SUBSTANTIVE_CORRECTION,
                    f"{correction.field}: '{correction.original}' -> '{correction.corrected}' is "
                    "more than a character-level repair.",
                    field=correction.field,
                    item_ref=ref,
                    observed=correction.original,
                    expected=correction.corrected,
                    evidence=correction.evidence,
                )
            )
            continue
        findings.append(
            make_finding(
                FindingCode.EXTRACTION_CORRECTION,
                f"{correction.field}: '{correction.original}' read as '{correction.corrected}'.",
                field=correction.field,
                item_ref=ref,
                observed=correction.original,
                expected=correction.corrected,
                evidence=correction.evidence,
            )
        )
        accepted.append(correction)
    return findings, accepted


def verify_values(
    draft: InvoiceDraft, source_text: str, accepted: list[Correction]
) -> list[Finding]:
    """Every extracted number and identity string must be traceable to the source."""
    findings: list[Finding] = []
    numbers = _numbers(source_text)
    names = _alnum(source_text)
    for correction in accepted:
        numbers |= _numbers(correction.corrected)
        names += " " + _alnum(correction.corrected)

    def check_number(
        field: str, value: Decimal | None, ref: str | None = None, *, rate: bool = False
    ):
        if value is None:
            return
        candidates = {value.normalize()}
        if rate:  # "5%" is stated as 5 but stored as 0.05
            candidates.add((value * 100).normalize())
        if numbers.isdisjoint(candidates):
            findings.append(
                make_finding(
                    FindingCode.VALUE_NOT_IN_SOURCE,
                    f"{field} {value.normalize():f} does not appear in the source.",
                    field=field,
                    item_ref=ref,
                    observed=f"{value.normalize():f}",
                )
            )

    for line in draft.line_items:
        ref = f"line {line.line_no}"
        check_number("quantity", line.quantity, ref)
        check_number("unit_price", line.unit_price, ref)
        check_number("amount", line.amount, ref)
        if line.description and item_key(line.description) not in names:
            findings.append(
                make_finding(
                    FindingCode.VALUE_NOT_IN_SOURCE,
                    f"Item '{line.description}' does not appear in the source.",
                    field="item",
                    item_ref=ref,
                    observed=line.description,
                )
            )
    check_number("subtotal", draft.subtotal)
    check_number("tax_amount", draft.tax_amount)
    check_number("tax_rate", draft.tax_rate, rate=True)
    check_number("shipping", draft.shipping)
    check_number("total", draft.total)

    if draft.invoice_number and invoice_number_key(draft.invoice_number) not in names:
        findings.append(
            make_finding(
                FindingCode.VALUE_NOT_IN_SOURCE,
                f"Invoice number '{draft.invoice_number}' does not appear in the source.",
                field="invoice_number",
                observed=draft.invoice_number,
            )
        )
    if draft.vendor_name and _alnum(vendor_key(draft.vendor_name)) not in names:
        findings.append(
            make_finding(
                FindingCode.VALUE_NOT_IN_SOURCE,
                f"Vendor '{draft.vendor_name}' does not appear in the source.",
                field="vendor_name",
                observed=draft.vendor_name,
            )
        )
    return findings


def check_grounding(draft: InvoiceDraft, source_text: str) -> list[Finding]:
    if draft.origin != "model":
        return []
    findings, accepted = verify_corrections(draft.corrections, source_text)
    findings.extend(verify_values(draft, source_text, accepted))
    return findings
