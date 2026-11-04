"""Run every deterministic rule and produce one ``ValidationReport``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..amounts import effective_amounts, effective_currency, line_value
from ..config import Policy
from ..findings import Finding, Severity
from ..identity import semantic_fingerprint
from ..money import CURRENCY_EXPONENTS, MoneyError, round_to_currency, to_minor
from ..schemas import (
    Eligibility,
    InvoiceDraft,
    NormalizedInvoice,
    NormalizedLine,
    ValidationReport,
)
from .arithmetic import check_arithmetic
from .dates import check_dates
from .grounding import check_grounding
from .integrity import check_integrity
from .inventory import InventoryIndex, check_inventory, usable_quantity
from .signals import check_signals


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.code, finding.field, finding.item_ref, finding.observed, finding.message)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def eligibility_of(findings: list[Finding]) -> Eligibility:
    if any(f.severity == Severity.ERROR for f in findings):
        return Eligibility.REJECTED
    if any(f.severity == Severity.REVIEW for f in findings):
        return Eligibility.NEEDS_REVIEW
    return Eligibility.ELIGIBLE


def _normalize(
    draft: InvoiceDraft,
    currency: str | None,
    resolved: dict[int, str | None],
    invoice_date: date | None,
    due_date: date | None,
) -> NormalizedInvoice | None:
    """Build the checked invoice if every required value is present and exact.

    Stated amounts are kept as written. Only absent values are derived (a missing line amount
    is quantity x price, a missing subtotal is the sum of the lines).
    """
    if (
        not draft.invoice_number
        or not draft.vendor_name
        or due_date is None
        or currency is None
        or currency not in CURRENCY_EXPONENTS
        or draft.total is None
        or not draft.line_items
    ):
        return None
    lines: list[NormalizedLine] = []
    for line in draft.line_items:
        quantity = usable_quantity(line.quantity)
        if quantity is None or not line.description or line.unit_price is None:
            return None
        amount = (
            line.amount if line.amount is not None else line_value(line.quantity, line.unit_price)
        )
        lines.append(
            NormalizedLine(
                line_no=line.line_no,
                description=line.description,
                sku=resolved.get(line.line_no),
                quantity=quantity,
                unit_price=line.unit_price,
                amount=amount,
                note=line.note,
            )
        )
    subtotal, tax, shipping = effective_amounts(draft)
    # a tax derived from a rate may have extra decimals; round it to the currency
    if draft.tax_amount is None:
        tax = round_to_currency(tax, currency)
    invoice = NormalizedInvoice(
        invoice_number=draft.invoice_number.strip(),
        revision=draft.revision,
        vendor_name=draft.vendor_name.strip(),
        invoice_date=invoice_date,
        due_date=due_date,
        currency=currency,
        lines=lines,
        subtotal=subtotal,
        tax=tax,
        shipping=shipping,
        total=draft.total,
        payment_terms=draft.payment_terms,
    )
    try:  # every amount must fit integer minor units, or it cannot be stored or paid
        for value in (
            invoice.subtotal,
            invoice.tax,
            invoice.shipping,
            invoice.total,
            *(line.unit_price for line in lines),
            *(line.amount for line in lines),
        ):
            to_minor(value, currency)
    except MoneyError:
        return None
    return invoice


def requires_scrutiny(total: Decimal | None, currency: str | None, policy: Policy) -> bool:
    """Strictly above the threshold, in the threshold's currency. Equal stays in the lower tier."""
    if total is None or currency != policy.approval.scrutiny_currency:
        return False
    return total > policy.approval.scrutiny_threshold


def validate_invoice(
    draft: InvoiceDraft,
    source_text: str,
    index: InventoryIndex,
    policy: Policy,
    today: date,
) -> ValidationReport:
    """Pure function: same inputs always give the same report. ``today`` is injected."""
    currency = effective_currency(draft, policy)
    valid_currency = currency if currency and len(currency) == 3 else None

    findings = check_integrity(draft, policy)
    findings += check_grounding(draft, source_text)
    date_findings, invoice_date, due_date = check_dates(draft, policy, today)
    findings += date_findings
    inventory_findings, resolved = check_inventory(draft, index)
    findings += inventory_findings
    if valid_currency in CURRENCY_EXPONENTS:
        findings += check_arithmetic(draft, valid_currency, policy)
    findings += check_signals(source_text, policy)

    findings = _dedupe(findings)
    normalized = _normalize(draft, valid_currency, resolved, invoice_date, due_date)
    return ValidationReport(
        policy_version=policy.policy_version,
        policy_fingerprint=policy.fingerprint,
        findings=findings,
        eligibility=eligibility_of(findings),
        requires_scrutiny=requires_scrutiny(draft.total, valid_currency, policy),
        normalized=normalized,
        semantic_fingerprint=semantic_fingerprint(draft, policy),
    )
