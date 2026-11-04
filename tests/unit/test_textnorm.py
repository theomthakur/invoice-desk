from datetime import date

import pytest

from invoice_flow.config import DatePolicy
from invoice_flow.textnorm import (
    invoice_number_key,
    item_key,
    parse_date,
    revision_rank,
    terms_days,
    vendor_key,
)

MDY = DatePolicy(slash_date_order="mdy", terms_tolerance_days=3)
DMY = DatePolicy(slash_date_order="dmy", terms_tolerance_days=3)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-15", date(2026, 1, 15)),
        ("Jan 30 2026", date(2026, 1, 30)),
        ("January 27, 2026", date(2026, 1, 27)),
        ("26-Jan-2026", date(2026, 1, 26)),
        ("02/28/2026", date(2026, 2, 28)),
        ("1st Mar 2026", date(2026, 3, 1)),
    ],
)
def test_readable_dates(raw, expected):
    result = parse_date(raw, MDY)
    assert result.value == expected and not result.ambiguous


@pytest.mark.parametrize(
    "raw", ["yesterday", "26-Jan-2O26", "", None, "2026-02-30", "13/45/2026", "soon", "Janu 5 2026"]
)
def test_unreadable_dates_stay_unknown(raw):
    # 'yesterday' and the OCR typo '2O26' must never be silently turned into a date
    assert parse_date(raw, MDY).value is None


def test_slash_dates_are_flagged_when_order_matters():
    mdy = parse_date("03/04/2026", MDY)
    dmy = parse_date("03/04/2026", DMY)
    assert mdy.value == date(2026, 3, 4) and mdy.ambiguous
    assert dmy.value == date(2026, 4, 3) and dmy.ambiguous
    assert not parse_date("05/05/2026", MDY).ambiguous  # same either way
    assert not parse_date("02/28/2026", MDY).ambiguous  # only one valid reading


def test_vendor_key_ignores_case_spacing_punctuation_only():
    assert vendor_key("Widgets Inc.") == vendor_key("  widgets   INC ")
    assert vendor_key("Smith & Sons") == vendor_key("smith and sons")
    # broad similarity must NOT merge vendors
    assert vendor_key("Widgets Inc.") != vendor_key("Widgets LLC")
    assert vendor_key("Acme Industrial Supplies") != vendor_key("Acme Industrial Supply")


def test_invoice_number_key():
    assert invoice_number_key("INV-1012") == invoice_number_key("INV 1012") == "inv1012"
    assert invoice_number_key("1002") != invoice_number_key("INV-1002")
    assert invoice_number_key("INV-0042") != invoice_number_key("INV-42")  # leading zeros kept


def test_item_key():
    assert item_key("Widget A") == item_key("widget-a") == item_key("WidgetA") == "widgeta"
    assert item_key("WidgetA (rush order)") == "widgeta"
    assert item_key("WidgetC") != item_key("WidgetA")


@pytest.mark.parametrize(
    ("terms", "days"),
    [
        ("Net 30", 30),
        ("net30", 30),
        ("Immediate", 0),
        ("Due on receipt", 0),
        ("Whenever", None),
        (None, None),
        ("", None),
    ],
)
def test_terms_days(terms, days):
    assert terms_days(terms) == days


@pytest.mark.parametrize(
    ("label", "rank"),
    [
        (None, 0),
        ("", 0),
        ("R1", 1),
        ("r2", 2),
        ("Rev 3", 3),
        ("v4", 4),
        ("1", 1),
        ("Revision-5", 5),
        ("final", None),
        ("R0", None),
        ("amended", None),
    ],
)
def test_revision_rank(label, rank):
    assert revision_rank(label) == rank
