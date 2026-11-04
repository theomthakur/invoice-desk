"""Exact money handling.

Rules enforced here:
* Money is ``Decimal`` in Python and integer minor units in SQLite.
* Floats, booleans, NaN and Infinity never become money.
* A value with more precision than its currency allows is rejected, never rounded.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Number of minor-unit digits per currency. A currency absent from this table cannot be
# normalized to minor units, so it can never be paid automatically.
CURRENCY_EXPONENTS: dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CAD": 2,
    "AUD": 2,
    "CHF": 2,
    "MXN": 2,
    "INR": 2,
    "CNY": 2,
    "JPY": 0,
}

CURRENCY_SYMBOLS: dict[str, str] = {"$": "USD", "€": "EUR", "£": "GBP"}

# Largest absolute amount accepted anywhere (one trillion). Larger values are rejected.
MAX_ABS_AMOUNT = Decimal("1000000000000")

_PLAIN = re.compile(r"^-?\d+(\.\d+)?$")
_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_SYMBOLS = "".join(CURRENCY_SYMBOLS)


class MoneyError(ValueError):
    """A value cannot be used as an exact amount."""

    def __init__(self, reason: str, raw: object = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw = raw


def parse_decimal(value: object) -> Decimal:
    """Parse ``value`` into a finite ``Decimal`` or raise :class:`MoneyError`.

    Accepts ``int``, ``Decimal`` and ``str``. Rejects ``bool`` and ``float`` because both
    silently lose or invent precision. Strings may carry a currency symbol and thousands
    separators ("$1,234.50"); anything else, including exponent notation and letters that
    look like digits ("3,500.O0"), is rejected.
    """
    if isinstance(value, bool):
        raise MoneyError("boolean_not_a_number", value)
    if isinstance(value, float):
        raise MoneyError("float_not_allowed", value)
    if isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, Decimal):
        result = value
    elif isinstance(value, str):
        result = _parse_string(value)
    else:
        raise MoneyError("unsupported_type", value)
    if not result.is_finite():
        raise MoneyError("not_finite", value)
    if abs(result) >= MAX_ABS_AMOUNT:
        raise MoneyError("too_large", value)
    return result


def _parse_string(raw: str) -> Decimal:
    text = raw.strip()
    if not text:
        raise MoneyError("empty", raw)
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    text = text.replace("\u2212", "-")  # unicode minus
    sign = ""
    if text.startswith(("-", "+")):
        sign, text = text[0], text[1:].lstrip()
    text = text.lstrip(_SYMBOLS).strip()
    if text.startswith(("-", "+")) and not sign:  # "$-5.00"
        sign, text = text[0], text[1:]
    candidate = sign.replace("+", "") + text
    if not (_PLAIN.match(candidate) or _THOUSANDS.match(candidate)):
        raise MoneyError("unparseable", raw)
    try:
        result = Decimal(candidate.replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - guarded by the regexes above
        raise MoneyError("unparseable", raw) from exc
    return -result if negative else result


def exponent_for(currency: str) -> int | None:
    return CURRENCY_EXPONENTS.get(currency.upper())


def to_minor(amount: Decimal, currency: str) -> int:
    """Convert to integer minor units; refuse values finer than the currency allows."""
    exponent = exponent_for(currency)
    if exponent is None:
        raise MoneyError("unsupported_currency", currency)
    scaled = amount.scaleb(exponent)
    if scaled != scaled.to_integral_value():
        raise MoneyError("excess_precision", amount)
    return int(scaled)


def from_minor(minor: int, currency: str) -> Decimal:
    exponent = exponent_for(currency)
    if exponent is None:
        raise MoneyError("unsupported_currency", currency)
    return Decimal(minor).scaleb(-exponent)


def format_minor(minor: int, currency: str) -> str:
    """Decimal string for API output, e.g. ``15625`` USD -> ``"156.25"``."""
    exponent = exponent_for(currency)
    if exponent is None:
        raise MoneyError("unsupported_currency", currency)
    return f"{from_minor(minor, currency):.{exponent}f}"


def format_amount(amount: Decimal) -> str:
    """Plain, comma-free string used in findings; keeps the value's own scale."""
    return format(amount, "f")


def canonical(amount: Decimal | None) -> str:
    """Scale-independent text so 250, 250.0 and 250.00 compare equal."""
    if amount is None:
        return "?"
    return format(amount.normalize(), "f") if amount != 0 else "0"


def round_to_currency(amount: Decimal, currency: str) -> Decimal:
    exponent = exponent_for(currency) or 2
    return amount.quantize(Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_UP)


def tolerance(currency: str, minor_units: int) -> Decimal:
    exponent = exponent_for(currency) or 2
    return Decimal(minor_units).scaleb(-exponent)
