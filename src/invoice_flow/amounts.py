"""Amount helpers shared by the rules and by identity hashing.

Kept apart from ``rules`` so ``identity`` can use them without importing the rules package.
"""

from __future__ import annotations

from decimal import Decimal

from .config import Policy
from .schemas import InvoiceDraft


def line_value(quantity: Decimal | None, unit_price: Decimal | None) -> Decimal | None:
    if quantity is None or unit_price is None:
        return None
    return quantity * unit_price


def effective_currency(draft: InvoiceDraft, policy: Policy) -> str | None:
    return draft.currency or policy.payment.assume_currency_when_missing


def effective_amounts(draft: InvoiceDraft) -> tuple[Decimal, Decimal, Decimal]:
    """(subtotal, tax, shipping) as the invoice states them, with derived fallbacks."""
    lines = Decimal(0)
    for line in draft.line_items:
        value = line_value(line.quantity, line.unit_price)
        lines += value if value is not None else (line.amount or Decimal(0))
    subtotal = draft.subtotal if draft.subtotal is not None else lines
    if draft.tax_amount is not None:
        tax = draft.tax_amount
    elif draft.tax_rate is not None:
        tax = subtotal * draft.tax_rate
    else:
        tax = Decimal(0)
    return subtotal, tax, draft.shipping if draft.shipping is not None else Decimal(0)
