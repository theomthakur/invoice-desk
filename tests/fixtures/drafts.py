"""Stand-ins for what a model would extract from the TXT and PDF samples.

These are TEST DATA ONLY. Runtime code never looks up a draft by file name. In Part 02 a model
produces drafts like these, and ``rules.grounding`` checks them against the real source text.
Each draft is written the way a careful extractor would: unknowns stay ``None``, and OCR
repairs are listed as corrections with quoted evidence.
"""

from __future__ import annotations

from invoice_flow.schemas import Correction, InvoiceDraft, LineItemDraft


def D(**kwargs) -> InvoiceDraft:
    kwargs.setdefault("origin", "model")
    return InvoiceDraft(**kwargs)


def L(n, description, quantity, unit_price, amount=None, note=None) -> LineItemDraft:
    return LineItemDraft(
        line_no=n,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
        note=note,
    )


_1012_CORRECTIONS = [
    Correction(
        field="invoice_date",
        original="26-Jan-2O26",
        corrected="26-Jan-2026",
        evidence="DATE: 26-Jan-2O26",
    ),
    Correction(
        field="amount",
        item_ref="line 2",
        original="3,500.O0",
        corrected="3500.00",
        evidence="WidgetB 7 $500 $3,500.O0",
    ),
]


def _draft_1012() -> InvoiceDraft:
    return D(
        invoice_number="INV 1012",
        vendor_name="QuickShip Distributers",
        invoice_date_raw="26-Jan-2026",
        due_date_raw="25-Feb-2026",
        line_items=[
            L(1, "Widget A", "12", "250", "3000.00"),
            L(2, "WidgetB", "7", "500", "3500.00"),
            L(3, "Gadget X", "4", "750", "3000.00"),
        ],
        subtotal="9500.00",
        tax_rate="0.05",
        tax_amount="475.00",
        total="9975.00",
        payment_terms="Net 30",
        corrections=list(_1012_CORRECTIONS),
    )


def _draft_1011_txt() -> InvoiceDraft:
    return D(
        invoice_number="INV-1011",
        vendor_name="Summit Manufacturing Co.",
        invoice_date_raw="2026-01-20",
        due_date_raw="2026-02-20",
        line_items=[
            L(1, "WidgetA", "6", "250.00", "1500.00"),
            L(2, "WidgetB", "3", "500.00", "1500.00"),
        ],
        subtotal="3000.00",
        tax_rate="0",
        tax_amount="0.00",
        total="3000.00",
        payment_terms="Net 30",
    )


def _draft_1011_pdf() -> InvoiceDraft:  # the PDF omits subtotal and tax lines
    return D(
        invoice_number="INV-1011",
        vendor_name="Summit Manufacturing Co.",
        invoice_date_raw="2026-01-20",
        due_date_raw="2026-02-20",
        line_items=[
            L(1, "WidgetA", "6", "250.00", "1500.00"),
            L(2, "WidgetB", "3", "500.00", "1500.00"),
        ],
        total="3000.00",
    )


def _draft_1013_pdf() -> InvoiceDraft:
    rows = [
        ("WidgetA", "15", "250.00", "3750.00", None),
        ("WidgetB", "10", "500.00", "5000.00", None),
        ("GadgetX", "5", "750.00", "3750.00", None),
        ("WidgetA", "5", "240.00", "1200.00", "Volume discount"),
        ("WidgetB", "8", "480.00", "3840.00", "Volume discount"),
        ("GadgetX", "3", "750.00", "2250.00", "Expedited"),
        ("WidgetA", "2", "250.00", "500.00", "Replacement"),
        ("GadgetX", "1", "750.00", "750.00", "Sample"),
    ]
    return D(
        invoice_number="INV-1013",
        vendor_name="Atlas Industrial Supply",
        invoice_date_raw="2026-01-24",
        due_date_raw="2026-03-24",
        line_items=[L(i, d, q, p, a, n) for i, (d, q, p, a, n) in enumerate(rows, 1)],
        subtotal="21040.00",
        tax_rate="0.07",
        tax_amount="1472.80",
        total="22562.80",
        payment_terms="Net 60",
    )


DRAFTS: dict[str, InvoiceDraft] = {
    "invoice_1001.txt": D(
        invoice_number="INV-1001",
        vendor_name="Widgets Inc.",
        invoice_date_raw="2026-01-15",
        due_date_raw="2026-02-01",
        line_items=[L(1, "WidgetA", "10", "250.00"), L(2, "WidgetB", "5", "500.00")],
        subtotal="5000.00",
        tax_rate="0",
        tax_amount="0.00",
        total="5000.00",
        payment_terms="Net 15",
    ),
    "invoice_1002.txt": D(
        invoice_number="1002",
        vendor_name="Gadgets Co.",
        invoice_date_raw="Jan 30 2026",
        due_date_raw="2026-01-30",
        line_items=[L(1, "GadgetX", "20", "750")],
        total="15000.00",
        payment_terms="Net 30",
    ),
    "invoice_1003.txt": D(
        invoice_number="INV-1003",
        vendor_name="Fraudster LLC",
        invoice_date_raw="2026-01-20",
        due_date_raw="yesterday",
        line_items=[L(1, "FakeItem", "100", "1000.00")],
        total="100000.00",
        payment_terms="Immediate",
        notes="URGENT - Pay immediately to avoid penalties!!! Wire transfer preferred.",
    ),
    "invoice_1008.txt": D(
        invoice_number="INV-1008",
        vendor_name="NoProd Industries",
        invoice_date_raw="2026-01-10",
        due_date_raw="2026-01-20",
        line_items=[L(1, "SuperGizmo", "12", "400.00"), L(2, "MegaSprocket", "6", "850.00")],
        total="9900.00",
    ),
    "invoice_1010.txt": D(
        invoice_number="INV-1010",
        vendor_name="Consolidated Materials Group",
        invoice_date_raw="January 27, 2026",
        due_date_raw="February 26, 2026",
        line_items=[
            L(1, "WidgetA", "8", "250.00", "2000.00"),
            L(2, "WidgetB", "4", "500.00", "2000.00"),
            L(3, "GadgetX", "2", "750.00", "1500.00"),
            L(4, "WidgetA (rush order)", "4", "300.00", "1200.00"),
        ],
        subtotal="6700.00",
        tax_amount="335.00",
        shipping="150.00",
        total="7185.00",
        payment_terms="Net 30",
    ),
    "invoice_1011.txt": _draft_1011_txt(),
    "invoice_1011.pdf": _draft_1011_pdf(),
    "invoice_1012.txt": _draft_1012(),
    "invoice_1012.pdf": _draft_1012(),
    "invoice_1013.pdf": _draft_1013_pdf(),
}
