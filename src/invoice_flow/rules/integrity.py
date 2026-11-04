"""Required fields, numeric sanity and currency."""

from __future__ import annotations

import re
from decimal import Decimal

from ..amounts import effective_currency
from ..config import Policy
from ..findings import Finding, FindingCode, Severity, make_finding
from ..money import CURRENCY_EXPONENTS, canonical, exponent_for
from ..schemas import InvoiceDraft
from .inventory import usable_quantity

_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")


def _has_precision(amount: Decimal, currency: str) -> bool:
    """True if ``amount`` fits the currency's minor units."""
    exponent = exponent_for(currency)
    if exponent is None:
        return True
    scaled = amount.scaleb(exponent)
    return scaled == scaled.to_integral_value()


def check_integrity(draft: InvoiceDraft, policy: Policy) -> list[Finding]:
    findings: list[Finding] = list(draft.parse_findings)

    def missing(field: str, label: str, severity: Severity | None = None) -> None:
        findings.append(
            make_finding(
                FindingCode.MISSING_FIELD, f"{label} is missing.", field=field, severity=severity
            )
        )

    if not (draft.invoice_number or "").strip():
        missing("invoice_number", "Invoice number")
    if not (draft.vendor_name or "").strip():
        missing("vendor_name", "Vendor name")
    if not (draft.due_date_raw or "").strip():
        missing("due_date", "Due date")
    if not (draft.invoice_date_raw or "").strip():
        missing("invoice_date", "Invoice date", Severity.WARNING)
    if not draft.line_items:
        missing("line_items", "Line items")
    if draft.total is None:
        missing("total", "Invoice total")

    # currency
    currency = effective_currency(draft, policy)
    if draft.currency is None:
        if currency is None:
            missing("currency", "Currency")
        else:
            findings.append(
                make_finding(
                    FindingCode.CURRENCY_ASSUMED,
                    f"No currency stated; policy default {currency} applied.",
                    field="currency",
                    observed=None,
                    expected=currency,
                )
            )
    elif not _CURRENCY_CODE.match(draft.currency):
        findings.append(
            make_finding(
                FindingCode.SOURCE_FIELD_INVALID,
                f"'{draft.currency}' is not a three-letter currency code.",
                field="currency",
                observed=draft.currency,
                evidence=draft.currency,
            )
        )
        currency = None
    if currency is not None and (
        currency not in policy.payment.auto_pay_currencies or currency not in CURRENCY_EXPONENTS
    ):
        findings.append(
            make_finding(
                FindingCode.UNSUPPORTED_CURRENCY,
                f"{currency} invoices cannot be paid automatically (no exchange rate supplied).",
                field="currency",
                observed=currency,
                expected=", ".join(policy.payment.auto_pay_currencies),
            )
        )

    # lines
    for line in draft.line_items:
        ref = f"line {line.line_no}"
        if not (line.description or "").strip():
            findings.append(
                make_finding(
                    FindingCode.MISSING_FIELD,
                    "Item description is missing.",
                    field="item",
                    item_ref=ref,
                )
            )
        if line.quantity is None:
            findings.append(
                make_finding(
                    FindingCode.MISSING_FIELD,
                    "Quantity is missing.",
                    field="quantity",
                    item_ref=ref,
                )
            )
        elif usable_quantity(line.quantity) is None:
            findings.append(
                make_finding(
                    FindingCode.INVALID_QUANTITY,
                    f"Quantity {canonical(line.quantity)} must be a positive whole number.",
                    field="quantity",
                    item_ref=ref,
                    observed=canonical(line.quantity),
                    expected="positive integer",
                )
            )
        if line.unit_price is None:
            findings.append(
                make_finding(
                    FindingCode.MISSING_FIELD,
                    "Unit price is missing.",
                    field="unit_price",
                    item_ref=ref,
                )
            )
        elif line.unit_price < 0:
            findings.append(
                make_finding(
                    FindingCode.NEGATIVE_AMOUNT,
                    f"Unit price {canonical(line.unit_price)} is negative.",
                    field="unit_price",
                    item_ref=ref,
                    observed=canonical(line.unit_price),
                )
            )
        if line.amount is not None and line.amount < 0:
            findings.append(
                make_finding(
                    FindingCode.NEGATIVE_AMOUNT,
                    f"Line amount {canonical(line.amount)} is negative.",
                    field="amount",
                    item_ref=ref,
                    observed=canonical(line.amount),
                )
            )

    # header amounts
    for name, label, value in (
        ("subtotal", "Subtotal", draft.subtotal),
        ("tax_amount", "Tax", draft.tax_amount),
        ("shipping", "Shipping", draft.shipping),
        ("total", "Total", draft.total),
    ):
        if value is None:
            continue
        if value < 0:
            findings.append(
                make_finding(
                    FindingCode.NEGATIVE_AMOUNT,
                    f"{label} {canonical(value)} is negative.",
                    field=name,
                    observed=canonical(value),
                )
            )
        if currency and not _has_precision(value, currency):
            findings.append(
                make_finding(
                    FindingCode.INVALID_AMOUNT,
                    f"{label} {canonical(value)} has more decimals than {currency} allows.",
                    field=name,
                    observed=canonical(value),
                )
            )
    if draft.total is not None and draft.total == 0:
        findings.append(
            make_finding(
                FindingCode.INVALID_AMOUNT, "Invoice total is zero.", field="total", observed="0"
            )
        )
    return findings
