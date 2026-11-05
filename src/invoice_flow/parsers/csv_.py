"""CSV invoices in the two shapes seen in the supplied data.

``key_value``  ``field,value`` rows. ``item`` starts a new line item, and the ``quantity`` /
               ``unit_price`` rows that follow belong to it. Keys can repeat, so a plain dict
               would silently drop items. We never build one.
``tabular``    a header row, one row per item, then footer rows ("Subtotal:", "Tax (6%):",
               "Total:") whose label and value sit in the last two filled cells.
"""

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal

from ..schemas import InvoiceDraft, LineItemDraft
from .common import Reader, SourceError

_KEY_ALIASES = {
    "invoicenumber": "invoice_number",
    "invoiceno": "invoice_number",
    "invoice": "invoice_number",
    "number": "invoice_number",
    "revision": "revision",
    "rev": "revision",
    "vendor": "vendor",
    "vendorname": "vendor",
    "supplier": "vendor",
    "vendoraddress": "vendor_address",
    "address": "vendor_address",
    "date": "date",
    "invoicedate": "date",
    "duedate": "due_date",
    "due": "due_date",
    "item": "item",
    "description": "item",
    "sku": "item",
    "name": "item",
    "quantity": "quantity",
    "qty": "quantity",
    "unitprice": "unit_price",
    "price": "unit_price",
    "rate": "unit_price",
    "linetotal": "amount",
    "amount": "amount",
    "lineamount": "amount",
    "subtotal": "subtotal",
    "tax": "tax_amount",
    "taxamount": "tax_amount",
    "taxrate": "tax_rate",
    "shipping": "shipping",
    "shippingamount": "shipping",
    "total": "total",
    "totalamount": "total",
    "grandtotal": "total",
    "amountdue": "total",
    "currency": "currency",
    "paymentterms": "payment_terms",
    "terms": "payment_terms",
    "notes": "notes",
    "note": "note",
}
_ITEM_FIELDS = {"item", "quantity", "unit_price", "amount", "note"}
_FOOTER_KINDS = {
    "subtotal": "subtotal",
    "tax": "tax_amount",
    "salestax": "tax_amount",
    "vat": "tax_amount",
    "shipping": "shipping",
    "freight": "shipping",
    "total": "total",
    "grandtotal": "total",
    "amountdue": "total",
    "totaldue": "total",
}
_PERCENT = re.compile(r"\(?\s*(\d+(?:\.\d+)?)\s*%\s*\)?")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _key(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower())


def _delimiter(text: str) -> str:
    """Recognize a supported invoice header, not commas inside quoted money values."""
    for delimiter in (",", ";", "\t"):
        try:
            header = next(
                csv.reader(
                    io.StringIO(text.lstrip(), newline=""), delimiter=delimiter, strict=True
                ),
                [],
            )
        except csv.Error:
            continue
        keys = [_key(cell) for cell in header]
        if (len(keys) >= 2 and keys[:2] == ["field", "value"]) or any(
            _KEY_ALIASES.get(key) == "item" for key in keys
        ):
            return delimiter
    return ","  # The normal parser will report an unsupported shape or malformed quoting.


def _rows(text: str, max_rows: int) -> list[list[str]]:
    rows: list[list[str]] = []
    try:
        for row in csv.reader(
            io.StringIO(text, newline=""), delimiter=_delimiter(text), strict=True
        ):
            if any(cell.strip() for cell in row):
                rows.append([cell.strip() for cell in row])
            if len(rows) > max_rows:
                raise SourceError("too_many_rows", f"More than {max_rows} rows.")
    except csv.Error as exc:
        raise SourceError("malformed", f"Not valid CSV: {exc}") from exc
    return rows


def parse_csv_invoice(text: str, max_rows: int, max_lines: int) -> InvoiceDraft:
    rows = _rows(text, max_rows)
    if not rows:
        raise SourceError("empty", "CSV has no rows.")
    first = [_key(cell) for cell in rows[0]]
    if len(first) >= 2 and first[0] == "field" and first[1] == "value":
        return _key_value(rows[1:], max_lines)
    if any(_KEY_ALIASES.get(cell) == "item" for cell in first):
        return _tabular(rows, max_lines)
    raise SourceError("unsupported_shape", "CSV is neither a field/value list nor an item table.")


def _build(
    scalars: dict[str, list[str | None]],
    lines: list[LineItemDraft],
    reader: Reader,
    *,
    tax_rate: Decimal | None = None,
    footer: dict[str, Decimal | None] | None = None,
) -> InvoiceDraft:
    footer = footer or {}

    def one(field: str) -> str | None:
        return reader.one_of(field, scalars.get(field, []))

    def money(field: str, name: str) -> Decimal | None:
        if field in footer:
            return footer[field]
        return reader.money(one(field), name)

    explicit_rate = reader.money(one("tax_rate"), "tax_rate")
    return InvoiceDraft(
        invoice_number=one("invoice_number"),
        revision=one("revision"),
        vendor_name=one("vendor"),
        vendor_address=one("vendor_address"),
        invoice_date_raw=one("date"),
        due_date_raw=one("due_date"),
        currency=reader.currency(one("currency")),
        line_items=lines,
        subtotal=money("subtotal", "subtotal"),
        tax_rate=explicit_rate if explicit_rate is not None else tax_rate,
        tax_amount=money("tax_amount", "tax_amount"),
        shipping=money("shipping", "shipping"),
        total=money("total", "total"),
        payment_terms=one("payment_terms"),
        notes=one("notes"),
        parse_findings=reader.findings,
    )


def _key_value(rows: list[list[str]], max_lines: int) -> InvoiceDraft:
    reader = Reader()
    scalars: dict[str, list[str | None]] = {}
    lines: list[LineItemDraft] = []
    current: dict[str, str | None] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        ref = f"line {len(lines) + 1}"
        lines.append(
            LineItemDraft(
                line_no=len(lines) + 1,
                description=reader.text(current.get("item"), "item", ref),
                quantity=reader.money(current.get("quantity"), "quantity", ref),
                unit_price=reader.money(current.get("unit_price"), "unit_price", ref),
                amount=reader.money(current.get("amount"), "amount", ref),
                note=reader.text(current.get("note"), "note", ref),
            )
        )
        current = None

    for row in rows:
        if len(row) < 2:
            reader.invalid("row", ",".join(row), "expected_field_and_value")
            continue
        if len(row) > 2 and any(row[2:]):
            reader.invalid("row", ",".join(row), "extra_columns")
            continue
        field = _KEY_ALIASES.get(_key(row[0]))
        value = row[1] or None
        if field is None:
            reader.invalid(row[0], value, "unknown_field")
            continue
        if field == "item":
            flush()
            if len(lines) >= max_lines:
                raise SourceError("too_many_lines", f"More than {max_lines} line items.")
            current = {"item": value}
        elif field in _ITEM_FIELDS:
            if current is None:
                reader.invalid(field, value, "item_field_before_any_item")
            elif field in current:
                reader.invalid(field, value, "repeated_within_one_item", f"line {len(lines) + 1}")
            else:
                current[field] = value
        else:
            scalars.setdefault(field, []).append(value)
    flush()
    return _build(scalars, lines, reader)


def _tabular(rows: list[list[str]], max_lines: int) -> InvoiceDraft:
    reader = Reader()
    header = [_KEY_ALIASES.get(_key(cell)) for cell in rows[0]]
    known = [name for name in header if name is not None]
    if len(known) != len(set(known)):
        raise SourceError(
            "ambiguous_columns", "CSV repeats a column or its alias. Use one column per field."
        )
    item_col = header.index("item")
    scalars: dict[str, list[str | None]] = {}
    lines: list[LineItemDraft] = []
    footer: dict[str, Decimal | None] = {}
    tax_rate: Decimal | None = None

    for row in rows[1:]:
        if len(row) > len(header) and any(row[len(header) :]):
            reader.invalid("row", ",".join(row), "extra_columns")
        cells = row + [""] * (len(header) - len(row))
        if cells[item_col]:
            if len(lines) >= max_lines:
                raise SourceError("too_many_lines", f"More than {max_lines} line items.")
            record: dict[str, str | None] = {}
            for name, cell in zip(header, cells, strict=False):
                if name is not None:
                    record[name] = cell or None
            for field, value in record.items():
                if field not in _ITEM_FIELDS:
                    scalars.setdefault(field, []).append(value)
            ref = f"line {len(lines) + 1}"
            lines.append(
                LineItemDraft(
                    line_no=len(lines) + 1,
                    description=reader.text(record.get("item"), "item", ref),
                    quantity=reader.money(record.get("quantity"), "quantity", ref),
                    unit_price=reader.money(record.get("unit_price"), "unit_price", ref),
                    amount=reader.money(record.get("amount"), "amount", ref),
                    note=reader.text(record.get("note"), "note", ref),
                )
            )
            continue
        # footer row: the last filled cell is the value, the one before it is the label
        filled = [(i, c) for i, c in enumerate(cells) if c]
        if len(filled) < 2:
            reader.invalid("row", ",".join(row), "unrecognized_footer_row")
            continue
        label, value = filled[-2][1], filled[-1][1]
        kind = _FOOTER_KINDS.get(_key(_PERCENT.sub("", label)))
        if kind is None:
            reader.invalid("footer", label, "unknown_footer_label")
            continue
        if kind in footer:
            reader.invalid(kind, value, "repeated_footer_row")
            continue
        footer[kind] = reader.money(value, kind)
        if kind == "tax_amount" and (match := _PERCENT.search(label)):
            tax_rate = Decimal(match[1]) / Decimal(100)

    return _build(scalars, lines, reader, tax_rate=tax_rate, footer=footer)
