"""XML invoices, parsed with defusedxml so DTDs, entities and external references are refused."""

from __future__ import annotations

from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from ..schemas import InvoiceDraft, LineItemDraft
from .common import Reader, SourceError

_ITEM_TAGS = ("item", "line_item", "line")
_ITEM_CONTAINERS = ("line_items", "items", "lines")


def _texts(root: Element, *tags: str) -> list[str | None]:
    found: list[str | None] = []
    for element in root.iter():
        if element.tag in tags and len(element) == 0:
            found.append((element.text or "").strip() or None)
    return found


def _child_text(element: Element, *tags: str) -> str | None:
    for child in element:
        if child.tag in tags:
            return (child.text or "").strip() or None
    return None


def parse_xml_invoice(data: bytes, max_lines: int) -> InvoiceDraft:
    try:
        root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise SourceError(
            "unsafe_xml", "XML with DTDs, entities or external references is refused."
        ) from exc
    except ParseError as exc:
        raise SourceError("malformed", f"Not valid XML: {exc}") from exc

    if root.tag.lower() != "invoice":
        raise SourceError("unsupported_shape", f"Expected an <invoice> root, found <{root.tag}>.")

    reader = Reader()

    # Line items live under a container, or directly as <item> elements.
    item_elements: list[Element] = []
    for container in root.iter():
        if container.tag in _ITEM_CONTAINERS:
            item_elements.extend(child for child in container if child.tag in _ITEM_TAGS)
    if not item_elements:
        item_elements = [child for child in root if child.tag in _ITEM_TAGS]
    if len(item_elements) > max_lines:
        raise SourceError("too_many_lines", f"More than {max_lines} line items.")

    lines: list[LineItemDraft] = []
    for index, element in enumerate(item_elements, start=1):
        ref = f"line {index}"
        lines.append(
            LineItemDraft(
                line_no=index,
                description=reader.text(
                    _child_text(element, "name", "item", "description", "sku"), "item", ref
                ),
                quantity=reader.money(_child_text(element, "quantity", "qty"), "quantity", ref),
                unit_price=reader.money(
                    _child_text(element, "unit_price", "price", "rate"), "unit_price", ref
                ),
                amount=reader.money(
                    _child_text(element, "amount", "line_total", "total"), "amount", ref
                ),
                note=reader.text(_child_text(element, "note", "notes"), "note", ref),
            )
        )

    # Vendor is either text or an element with <name>/<address> children.
    vendor_name = vendor_address = None
    for element in root.iter():
        if element.tag in ("vendor", "supplier"):
            if len(element):
                vendor_name = _child_text(element, "name", "vendor_name")
                vendor_address = _child_text(element, "address")
            else:
                vendor_name = (element.text or "").strip() or None
            break

    def single(field: str, *tags: str) -> str | None:
        return reader.one_of(field, _texts(root, *tags))

    return InvoiceDraft(
        invoice_number=reader.text(
            single("invoice_number", "invoice_number", "invoice_no"), "invoice_number"
        ),
        revision=reader.text(single("revision", "revision", "rev"), "revision"),
        vendor_name=reader.text(vendor_name, "vendor_name"),
        vendor_address=reader.text(vendor_address, "vendor_address"),
        invoice_date_raw=reader.text(
            single("invoice_date", "date", "invoice_date"), "invoice_date"
        ),
        due_date_raw=reader.text(single("due_date", "due_date", "due"), "due_date"),
        currency=reader.currency(single("currency", "currency")),
        line_items=lines,
        subtotal=reader.money(single("subtotal", "subtotal"), "subtotal"),
        tax_rate=reader.money(single("tax_rate", "tax_rate"), "tax_rate"),
        tax_amount=reader.money(single("tax_amount", "tax_amount", "tax"), "tax_amount"),
        shipping=reader.money(single("shipping", "shipping", "shipping_amount"), "shipping"),
        total=reader.money(single("total", "total", "total_amount", "amount_due"), "total"),
        payment_terms=reader.text(
            single("payment_terms", "payment_terms", "terms"), "payment_terms"
        ),
        notes=reader.text(single("notes", "notes"), "notes"),
        parse_findings=reader.findings,
    )
