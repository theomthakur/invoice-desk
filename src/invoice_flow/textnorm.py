"""Small, conservative text normalizers shared by identity, inventory and grounding checks.

Nothing here does fuzzy matching. Two strings match only when they differ in case, spacing or
punctuation, never because they look similar.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from .config import DatePolicy

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_PAREN = re.compile(r"\([^)]*\)")
_WS = re.compile(r"\s+")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def collapse_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


def vendor_key(name: str) -> str:
    """Case, spacing and punctuation are ignored. Corporate suffixes are *kept*."""
    return collapse_ws(_NON_ALNUM.sub(" ", _fold(name).replace("&", " and ")))


def invoice_number_key(number: str) -> str:
    """Alphanumerics only, so 'INV-1012' equals 'INV 1012'. Leading zeros are kept."""
    return _NON_ALNUM.sub("", _fold(number))


def item_key(description: str) -> str:
    """Key for catalog lookup. A trailing '(qualifier)' such as '(rush order)' is ignored."""
    return _NON_ALNUM.sub("", _fold(_PAREN.sub(" ", description)))


# Characters that OCR commonly swaps. Used only to judge whether a *correction* is a plain
# OCR repair; never to decide what an invoice says.
_CONFUSABLE = str.maketrans({"o": "0", "l": "1", "i": "1", "s": "5"})


def ocr_skeleton(text: str) -> str:
    return _NON_ALNUM.sub("", _fold(text)).translate(_CONFUSABLE)


# ---------------------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------------------

_MONTHS = {
    name: index
    for index, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_SLASH = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$")
_DAY_MON_YEAR = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([a-z]{3,9})\.?,?[\s\-]+(\d{4})$")
_MON_DAY_YEAR = re.compile(r"^([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$")


class DateParse:
    __slots__ = ("ambiguous", "reason", "value")

    def __init__(self, value: date | None, ambiguous: bool = False, reason: str = "") -> None:
        self.value = value
        self.ambiguous = ambiguous
        self.reason = reason


def _make(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(raw: str | None, policy: DatePolicy) -> DateParse:
    """Read a date or say why not. Relative words ("yesterday") never resolve."""
    if raw is None or not raw.strip():
        return DateParse(None, reason="empty")
    text = collapse_ws(_fold(raw))

    if match := _ISO.match(text):
        value = _make(int(match[1]), int(match[2]), int(match[3]))
        return DateParse(value, reason="" if value else "not_a_calendar_date")

    if match := _SLASH.match(text):
        first, second, year = int(match[1]), int(match[2]), int(match[3])
        month_first = _make(year, first, second)
        day_first = _make(year, second, first)
        preferred, other = (
            (month_first, day_first)
            if policy.slash_date_order == "mdy"
            else (day_first, month_first)
        )
        if preferred and other and first != second:
            return DateParse(preferred, ambiguous=True, reason="day_month_order_ambiguous")
        value = preferred or other
        return DateParse(value, reason="" if value else "not_a_calendar_date")

    if match := _DAY_MON_YEAR.match(text):
        month = _MONTHS.get(match[2])
        value = _make(int(match[3]), month, int(match[1])) if month else None
        return DateParse(value, reason="" if value else "unknown_month_or_day")

    if match := _MON_DAY_YEAR.match(text):
        month = _MONTHS.get(match[1])
        value = _make(int(match[3]), month, int(match[2])) if month else None
        return DateParse(value, reason="" if value else "unknown_month_or_day")

    return DateParse(None, reason="unrecognized_format")


_NET = re.compile(r"\bnet\s*(\d{1,3})\b")
_IMMEDIATE = re.compile(
    r"\b(?:immediate(?:ly)?|due\s+(?:on|upon)\s+receipt|cod|cash\s+on\s+delivery)\b"
)


def terms_days(terms: str | None) -> int | None:
    """Days implied by 'Net 30'-style terms. Unknown wording returns ``None``."""
    if not terms:
        return None
    text = _fold(terms)
    if match := _NET.search(text):
        return int(match[1])
    if _IMMEDIATE.search(text):
        return 0
    return None


# ---------------------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------------------

_REV_NUMBER = re.compile(r"^(?:r|rev|revision|v|version)?[\s\-_.]*0*(\d{1,3})$")


def revision_rank(label: str | None) -> int | None:
    """Order of an explicit revision label. ``None`` label is the original (rank 0).

    Returns ``None`` when a label exists but its order cannot be determined, which the
    caller must treat as a conflict instead of guessing.
    """
    if label is None or not label.strip():
        return 0
    match = _REV_NUMBER.match(_fold(label).strip())
    if not match:
        return None
    rank = int(match[1])
    return rank if rank > 0 else None
