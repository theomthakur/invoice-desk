"""JSON invoices. Money stays exact: floats are read as Decimal, never as binary floats."""

from __future__ import annotations

import json
from decimal import Decimal

from ..schemas import InvoiceDraft, LineItemDraft
from .common import Reader, SourceError


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise SourceError("duplicate_key", f"JSON key '{key}' appears more than once.")
        seen[key] = value
    return seen


def _reject_constant(name: str) -> object:
    raise SourceError("non_finite_number", f"JSON contains {name}, which is not a valid amount.")


def loads_exact(text: str) -> object:
    try:
        return json.loads(
            text,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_no_duplicates,
        )
    except SourceError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise SourceError("malformed", f"Not valid JSON: {exc}") from exc


def _first(mapping: dict, *keys: str) -> object:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def parse_json_invoice(text: str, max_lines: int) -> InvoiceDraft:
    data = loads_exact(text)
    if not isinstance(data, dict):
        raise SourceError("unsupported_shape", "JSON invoice must be an object.")
    reader = Reader()

    vendor_raw = _first(data, "vendor", "vendor_name", "supplier")
    vendor_name = vendor_address = None
    if isinstance(vendor_raw, dict):
        vendor_name = reader.text(_first(vendor_raw, "name", "vendor_name"), "vendor_name")
        vendor_address = reader.text(vendor_raw.get("address"), "vendor_address")
    else:
        vendor_name = reader.text(vendor_raw, "vendor_name")
        vendor_address = reader.text(data.get("vendor_address"), "vendor_address")

    items_raw = _first(data, "line_items", "items", "lines")
    lines: list[LineItemDraft] = []
    if items_raw is not None and not isinstance(items_raw, list):
        reader.invalid("line_items", items_raw, "not_a_list")
    elif isinstance(items_raw, list):
        if len(items_raw) > max_lines:
            raise SourceError("too_many_lines", f"More than {max_lines} line items.")
        for index, entry in enumerate(items_raw, start=1):
            ref = f"line {index}"
            if not isinstance(entry, dict):
                reader.invalid("line_item", entry, "not_an_object", ref)
                lines.append(LineItemDraft(line_no=index))
                continue
            lines.append(
                LineItemDraft(
                    line_no=index,
                    description=reader.text(
                        _first(entry, "item", "name", "description", "sku"), "item", ref
                    ),
                    quantity=reader.money(_first(entry, "quantity", "qty"), "quantity", ref),
                    unit_price=reader.money(
                        _first(entry, "unit_price", "price", "rate"), "unit_price", ref
                    ),
                    amount=reader.money(
                        _first(entry, "amount", "line_total", "total"), "amount", ref
                    ),
                    note=reader.text(_first(entry, "note", "notes"), "note", ref),
                )
            )

    return InvoiceDraft(
        invoice_number=reader.text(
            _first(data, "invoice_number", "invoice_no", "number"), "invoice_number"
        ),
        revision=reader.text(_first(data, "revision", "rev"), "revision"),
        vendor_name=vendor_name,
        vendor_address=vendor_address,
        invoice_date_raw=reader.text(_first(data, "date", "invoice_date"), "invoice_date"),
        due_date_raw=reader.text(_first(data, "due_date", "due"), "due_date"),
        currency=reader.currency(data.get("currency")),
        line_items=lines,
        subtotal=reader.money(data.get("subtotal"), "subtotal"),
        tax_rate=reader.money(data.get("tax_rate"), "tax_rate"),
        tax_amount=reader.money(_first(data, "tax_amount", "tax"), "tax_amount"),
        shipping=reader.money(_first(data, "shipping", "shipping_amount"), "shipping"),
        total=reader.money(_first(data, "total", "total_amount", "amount_due"), "total"),
        payment_terms=reader.text(_first(data, "payment_terms", "terms"), "payment_terms"),
        notes=reader.text(data.get("notes"), "notes"),
        parse_findings=reader.findings,
    )
