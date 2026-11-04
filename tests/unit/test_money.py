from decimal import Decimal

import pytest

from invoice_flow.money import (
    MoneyError,
    canonical,
    format_minor,
    from_minor,
    parse_decimal,
    round_to_currency,
    to_minor,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("250", "250"),
        ("$1,234.50", "1234.50"),
        ("  15,000.00 ", "15000.00"),
        (7, "7"),
        ("-5", "-5"),
        ("(250.00)", "-250.00"),
        ("$-5.00", "-5.00"),
        ("\u2212 3", "-3"),
        (Decimal("1.10"), "1.10"),
        ("0.1", "0.1"),
    ],
)
def test_parse_decimal_accepts(raw, expected):
    assert parse_decimal(raw) == Decimal(expected)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (True, "boolean_not_a_number"),
        (1.5, "float_not_allowed"),
        ("", "empty"),
        ("3,500.O0", "unparseable"),
        ("1e5", "unparseable"),
        ("1,23,4", "unparseable"),
        ("NaN", "unparseable"),
        ("Infinity", "unparseable"),
        (Decimal("NaN"), "not_finite"),
        (Decimal("Infinity"), "not_finite"),
        ("abc", "unparseable"),
        (None, "unsupported_type"),
        ([1], "unsupported_type"),
        (10**13, "too_large"),
        ("1,000,000,000,000", "too_large"),
    ],
)
def test_parse_decimal_rejects(raw, reason):
    with pytest.raises(MoneyError) as caught:
        parse_decimal(raw)
    assert caught.value.reason == reason


def test_float_is_never_converted():
    # 0.1 + 0.2 style surprises must not be able to enter the system at all
    with pytest.raises(MoneyError):
        parse_decimal(0.1)


@pytest.mark.parametrize(
    ("amount", "currency", "minor"),
    [("15000.00", "USD", 1500000), ("0.01", "USD", 1), ("19.99", "EUR", 1999), ("500", "JPY", 500)],
)
def test_minor_unit_round_trip(amount, currency, minor):
    assert to_minor(Decimal(amount), currency) == minor
    assert from_minor(minor, currency) == Decimal(amount)


def test_excess_precision_is_rejected_not_rounded():
    with pytest.raises(MoneyError) as caught:
        to_minor(Decimal("10.005"), "USD")
    assert caught.value.reason == "excess_precision"
    with pytest.raises(MoneyError):
        to_minor(Decimal("0.5"), "JPY")


def test_unknown_currency_has_no_minor_units():
    with pytest.raises(MoneyError) as caught:
        to_minor(Decimal("1"), "XYZ")
    assert caught.value.reason == "unsupported_currency"


def test_format_minor_is_a_string_with_currency_scale():
    assert format_minor(1500000, "USD") == "15000.00"
    assert format_minor(5, "USD") == "0.05"
    assert format_minor(500, "JPY") == "500"


def test_canonical_ignores_scale():
    assert canonical(Decimal("250")) == canonical(Decimal("250.00")) == "250"
    assert canonical(Decimal("0.00")) == "0"
    assert canonical(None) == "?"


def test_round_half_up_for_derived_tax():
    assert round_to_currency(Decimal("1.005"), "USD") == Decimal("1.01")
    assert round_to_currency(Decimal("1.004"), "USD") == Decimal("1.00")
