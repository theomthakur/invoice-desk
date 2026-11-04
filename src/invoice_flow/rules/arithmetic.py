"""Arithmetic checks. Source totals are never rewritten, even when they are wrong."""

from __future__ import annotations

from decimal import Decimal

from ..amounts import line_value
from ..config import Policy
from ..findings import Finding, FindingCode, make_finding
from ..money import canonical, tolerance
from ..schemas import InvoiceDraft


def check_arithmetic(draft: InvoiceDraft, currency: str, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    tol = tolerance(currency, policy.arithmetic.tolerance_minor_units)

    def differs(a: Decimal, b: Decimal) -> bool:
        return abs(a - b) > tol

    computed_sum = Decimal(0)
    sum_is_complete = bool(draft.line_items)
    for line in draft.line_items:
        value = line_value(line.quantity, line.unit_price)
        if value is None:
            sum_is_complete = False
            continue
        computed_sum += value
        if line.amount is not None and differs(value, line.amount):
            findings.append(
                make_finding(
                    FindingCode.LINE_TOTAL_MISMATCH,
                    f"Line {line.line_no}: {canonical(line.quantity)} x "
                    f"{canonical(line.unit_price)} = {canonical(value)}, "
                    f"but the line says {canonical(line.amount)}.",
                    field="amount",
                    item_ref=f"line {line.line_no}",
                    observed=canonical(line.amount),
                    expected=canonical(value),
                )
            )
    subtotal_basis: Decimal | None = None
    if sum_is_complete:
        if draft.subtotal is not None and differs(computed_sum, draft.subtotal):
            findings.append(
                make_finding(
                    FindingCode.SUBTOTAL_MISMATCH,
                    f"Lines add up to {canonical(computed_sum)}, but the subtotal says "
                    f"{canonical(draft.subtotal)}.",
                    field="subtotal",
                    observed=canonical(draft.subtotal),
                    expected=canonical(computed_sum),
                )
            )
        subtotal_basis = draft.subtotal if draft.subtotal is not None else computed_sum
    elif draft.subtotal is not None:
        subtotal_basis = draft.subtotal

    # tax: stated amount wins; otherwise the stated rate; otherwise none
    if draft.tax_amount is not None:
        tax = draft.tax_amount
        if subtotal_basis is not None and draft.tax_rate is not None:
            expected_tax = subtotal_basis * draft.tax_rate
            if differs(expected_tax, tax):
                findings.append(
                    make_finding(
                        FindingCode.TAX_MISMATCH,
                        f"Tax {canonical(tax)} differs from {canonical(draft.tax_rate * 100)}% "
                        f"of {canonical(subtotal_basis)} ({canonical(expected_tax)}).",
                        field="tax_amount",
                        observed=canonical(tax),
                        expected=canonical(expected_tax),
                    )
                )
    elif draft.tax_rate is not None and subtotal_basis is not None:
        tax = subtotal_basis * draft.tax_rate
    else:
        tax = Decimal(0)
    shipping = draft.shipping if draft.shipping is not None else Decimal(0)

    if subtotal_basis is not None and draft.total is not None:
        expected_total = subtotal_basis + tax + shipping
        if differs(expected_total, draft.total):
            parts = f"{canonical(subtotal_basis)} + {canonical(tax)} tax"
            if shipping:
                parts += f" + {canonical(shipping)} shipping"
            findings.append(
                make_finding(
                    FindingCode.TOTAL_MISMATCH,
                    f"Subtotal and charges add up to {canonical(expected_total)} ({parts}), "
                    f"but the total says {canonical(draft.total)}.",
                    field="total",
                    observed=canonical(draft.total),
                    expected=canonical(expected_total),
                )
            )
    return findings
