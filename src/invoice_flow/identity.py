"""Invoice identity: which payable a document belongs to, and whether two documents say the
same thing.

* ``payable_key``          stable across revisions. One payment per payable, ever.
* ``byte_hash``            catches the identical file.
* ``semantic_fingerprint`` catches the same invoice in another format (PDF vs TXT) by hashing
                           the *material* values, not the layout.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .amounts import effective_amounts, effective_currency, line_value
from .config import Policy
from .money import canonical
from .schemas import InvoiceDraft, NormalizedInvoice
from .textnorm import collapse_ws, invoice_number_key, item_key, parse_date, vendor_key


@dataclass(frozen=True)
class PayableKey:
    vendor_key: str
    number_key: str

    @property
    def payable_id(self) -> str:
        digest = hashlib.sha256(f"{self.vendor_key}|{self.number_key}".encode()).hexdigest()
        return f"pay_{digest[:16]}"


def payable_key(draft: InvoiceDraft) -> PayableKey | None:
    """``None`` when vendor or invoice number is missing. Identity is never invented."""
    vendor = vendor_key(draft.vendor_name or "")
    number = invoice_number_key(draft.invoice_number or "")
    if not vendor or not number:
        return None
    return PayableKey(vendor, number)


def _date_token(raw: str | None, policy: Policy) -> str:
    if not raw or not raw.strip():
        return ""
    parsed = parse_date(raw, policy.dates)
    return parsed.value.isoformat() if parsed.value else "raw:" + collapse_ws(raw).casefold()


def semantic_fingerprint(draft: InvoiceDraft, policy: Policy) -> str:
    """Hash of material values. Layout, case, spacing and representation are ignored; amounts,
    items, dates, currency and identity are not."""
    subtotal, tax, shipping = effective_amounts(draft)
    lines = sorted(
        (
            item_key(line.description or ""),
            canonical(line.quantity),
            canonical(line.unit_price),
            canonical(
                line.amount
                if line.amount is not None
                else line_value(line.quantity, line.unit_price)
            ),
        )
        for line in draft.line_items
    )
    material = {
        "vendor": vendor_key(draft.vendor_name or ""),
        "number": invoice_number_key(draft.invoice_number or ""),
        "currency": effective_currency(draft, policy) or "",
        "invoice_date": _date_token(draft.invoice_date_raw, policy),
        "due_date": _date_token(draft.due_date_raw, policy),
        "lines": lines,
        "subtotal": canonical(subtotal),
        "tax": canonical(tax),
        "shipping": canonical(shipping),
        "total": canonical(draft.total),
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def content_hash(invoice: NormalizedInvoice) -> str:
    """Hash of the checked invoice. Approvals bind to this; any change voids them."""
    blob = json.dumps(invoice.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()
