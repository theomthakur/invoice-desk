from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))  # lets tests import ``fixtures.drafts``

from invoice_flow.config import Policy, load_policy  # noqa: E402
from invoice_flow.parsers import ParsedSource, load_source  # noqa: E402
from invoice_flow.rules import InventoryIndex  # noqa: E402
from invoice_flow.schemas import InvoiceDraft, LineItemDraft  # noqa: E402
from invoice_flow.storage import STARTER_INVENTORY, Database, init_database  # noqa: E402

SAMPLES = ROOT / "data" / "invoices"
TODAY = date(2026, 11, 3)


@pytest.fixture(scope="session")
def policy() -> Policy:
    return load_policy()


@pytest.fixture
def index() -> InventoryIndex:
    return InventoryIndex.build(STARTER_INVENTORY)


@pytest.fixture
def db(tmp_path) -> Database:
    """A fresh database with a frozen clock for every test."""
    database = Database(
        tmp_path / "test.db", clock=lambda: datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    )
    init_database(database)
    return database


@pytest.fixture
def sample() -> Callable[[str], ParsedSource]:
    def _load(name: str) -> ParsedSource:
        return load_source(SAMPLES / name, load_policy())

    return _load


def make_draft(
    *,
    number="INV-T1",
    vendor="Test Vendor Inc.",
    lines=None,
    total=None,
    due="2026-12-01",
    date_="2026-11-01",
    currency="USD",
    **extra,
) -> InvoiceDraft:
    """A small valid structured draft; override anything via keyword arguments."""
    lines = lines or [("WidgetA", 2, "250.00")]
    items = [
        LineItemDraft(line_no=i, description=d, quantity=q, unit_price=p)
        for i, (d, q, p) in enumerate(lines, 1)
    ]
    if total is None:
        from decimal import Decimal

        total = str(sum(Decimal(str(q)) * Decimal(p) for _, q, p in lines))
    return InvoiceDraft(
        origin="structured",
        invoice_number=number,
        vendor_name=vendor,
        invoice_date_raw=date_,
        due_date_raw=due,
        currency=currency,
        line_items=items,
        total=total,
        payment_terms=extra.pop("payment_terms", None),
        **extra,
    )


@pytest.fixture
def draft_factory():
    return make_draft
