"""Source grounding for model-produced drafts (shared invariant 10).

A model may repair what it read, but a repeated answer is not proof. These checks compare the
draft against the source text itself:

* every correction must quote evidence that really appears in the source, and its original
  value must be inside that evidence;
* a correction is only accepted as a plain repair when it differs by look-alike characters,
  spacing or punctuation (``2O26`` -> ``2026``). Anything else needs a person;
* every amount, quantity, identity and item name must appear in the source (or be a
  verified correction);
* dates and revisions must have evidence for the corresponding source field. A due date
  may also follow from a grounded invoice date and stated payment terms.

Structured drafts (``origin == "structured"``) came from code, not a model, and skip this.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from ..config import DatePolicy, Policy
from ..findings import Finding, FindingCode, make_finding
from ..schemas import Correction, InvoiceDraft
from ..textnorm import (
    collapse_ws,
    invoice_number_key,
    item_key,
    ocr_skeleton,
    parse_date,
    revision_rank,
    terms_days,
    vendor_key,
)

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DEFAULT_DATES = DatePolicy(slash_date_order="mdy", terms_tolerance_days=3)
_SOURCE_FIELDS = re.compile(
    r"(?ix)\b(?:"
    r"(?P<due_date>due(?:[\s_-]*(?:date|dt))?|payment[\s_-]*due|payable[\s_-]*by)"
    r"|(?P<invoice_date>(?:invoice[\s_-]*)?(?:date|dt))"
    r"|(?P<payment_terms>(?:payment[\s_-]*|pymnt[\s_-]*)?terms)"
    r"|(?P<revision>revision|rev\.?|version)"
    r")\b\s*(?:[:=\#]\s*|\s+)"
)
_DATE_LITERAL = re.compile(
    r"(?ix)^(?:"
    r"\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/.]\d{1,2}[/.]\d{4}"
    r"|\d{1,2}(?:st|nd|rd|th)?[\s-]+[a-z]{3,9}\.?,?[\s-]+\d{4}"
    r"|[a-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r")(?![\w/.-])"
)


def _source_fields(text: str) -> dict[str, list[str]]:
    """Keep each header's value in its own role, including adjacent PDF header fields."""
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        labels = list(_SOURCE_FIELDS.finditer(line))
        for index, label in enumerate(labels):
            end = labels[index + 1].start() if index + 1 < len(labels) else len(line)
            value = line[label.end() : end].strip(" \t\"'")
            fields.setdefault(label.lastgroup, []).append(value)
    return fields


def _starts_with(value: str, raw: str) -> bool:
    value, raw = _fold(value), _fold(raw)
    return (
        bool(raw)
        and value.startswith(raw)
        and (len(value) == len(raw) or not value[len(raw)].isalnum())
    )


def _repaired_date(raw: str) -> str:
    # Repair numeric components only. Translating the entire string would corrupt months
    # such as April. This is used only for an explicitly quoted correction.
    return re.sub(
        r"(?<![a-z0-9])([0-9oils]+)(st|nd|rd|th)?(?![a-z0-9])",
        lambda match: match[1].translate(_LOOKALIKE) + (match[2] or ""),
        raw.casefold(),
    )


def _same_date(first: str, second: str, dates: DatePolicy, *, repair: bool = False) -> bool:
    parsed = parse_date(_repaired_date(first) if repair else first, dates).value
    return parsed is not None and parsed == parse_date(second, dates).value


def _date_in_field(raw: str, values: list[str], dates: DatePolicy) -> bool:
    for value in values:
        if _starts_with(value, raw):
            return True
        literal = _DATE_LITERAL.match(value)
        if literal and _same_date(literal[0], raw, dates):
            return True
    return False


def _same_revision(first: str, second: str) -> bool:
    first_rank, second_rank = revision_rank(first), revision_rank(second)
    if first_rank is not None and second_rank is not None:
        return first_rank == second_rank
    return _fold(first) == _fold(second)


def _fold(text: str) -> str:
    return collapse_ws(text).casefold()


def _numbers(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER.findall(text):
        try:
            found.add(Decimal(token.replace(",", "")).normalize())
        except InvalidOperation:  # pragma: no cover - the regex only yields digits
            continue
    return found


def _alnum(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.casefold())


_NUMERIC_FIELDS = {
    "quantity",
    "unit_price",
    "amount",
    "subtotal",
    "tax_amount",
    "tax_rate",
    "shipping",
    "total",
}
_LOOKALIKE = str.maketrans({"o": "0", "l": "1", "i": "1", "s": "5"})


def _numeric_skeleton(text: str) -> str:
    """For amounts the decimal point *is* part of the value, so it is kept. Only currency
    symbols, thousands commas, spaces and look-alike letters are ignored. '3,500.O0' and
    '3500.00' match; '3,500.O0' and '350,000' do not."""
    cleaned = re.sub(r"[\s,$€£]", "", text.casefold())
    return cleaned.translate(_LOOKALIKE)


def _skeleton(correction: Correction, *, corrected: bool = False) -> str:
    text = correction.corrected if corrected else correction.original
    if correction.field in _NUMERIC_FIELDS:
        return _numeric_skeleton(text)
    return ocr_skeleton(text)


def verify_corrections(
    corrections: list[Correction], source_text: str, policy: Policy | None = None
) -> tuple[list[Finding], list[Correction]]:
    """Return findings and the corrections that are verified plain repairs."""
    findings: list[Finding] = []
    accepted: list[Correction] = []
    folded_source = _fold(source_text)
    for correction in corrections:
        ref = correction.item_ref
        evidence = _fold(correction.evidence)
        if not evidence or evidence not in folded_source:
            findings.append(
                make_finding(
                    FindingCode.CORRECTION_NOT_IN_SOURCE,
                    f"Correction of {correction.field} cites text that is not in the source.",
                    field=correction.field,
                    item_ref=ref,
                    observed=correction.original,
                    expected=correction.corrected,
                    evidence=correction.evidence,
                )
            )
            continue
        if _fold(correction.original) not in evidence:
            findings.append(
                make_finding(
                    FindingCode.CORRECTION_NOT_IN_SOURCE,
                    f"The original value '{correction.original}' is not inside the quoted "
                    "evidence.",
                    field=correction.field,
                    item_ref=ref,
                    observed=correction.original,
                    expected=correction.corrected,
                    evidence=correction.evidence,
                )
            )
            continue
        if correction.field in {"invoice_date", "due_date"}:
            # Removing punctuation can move digits between date components: 2026-1-11
            # and 2026-11-1 are different dates despite the same generic OCR skeleton.
            plain_repair = _same_date(
                correction.original,
                correction.corrected,
                policy.dates if policy else _DEFAULT_DATES,
                repair=True,
            )
        elif correction.field == "revision":
            # Keep component separators: R1.1 is not the single revision R11.
            label = _fold(correction.original)
            numeric = re.fullmatch(
                r"((?:revision|version|rev|r|v)?[\s_.-]*)([0-9oils]{1,3})", label
            )
            if numeric:
                label = numeric[1] + numeric[2].translate(_LOOKALIKE)
            plain_repair = _same_revision(label, correction.corrected)
        else:
            plain_repair = _skeleton(correction) == _skeleton(correction, corrected=True)
        if not plain_repair:
            findings.append(
                make_finding(
                    FindingCode.SUBSTANTIVE_CORRECTION,
                    f"{correction.field}: '{correction.original}' -> '{correction.corrected}' is "
                    "more than a character-level repair.",
                    field=correction.field,
                    item_ref=ref,
                    observed=correction.original,
                    expected=correction.corrected,
                    evidence=correction.evidence,
                )
            )
            continue
        findings.append(
            make_finding(
                FindingCode.EXTRACTION_CORRECTION,
                f"{correction.field}: '{correction.original}' read as '{correction.corrected}'.",
                field=correction.field,
                item_ref=ref,
                observed=correction.original,
                expected=correction.corrected,
                evidence=correction.evidence,
            )
        )
        accepted.append(correction)
    return findings, accepted


def verify_values(
    draft: InvoiceDraft,
    source_text: str,
    accepted: list[Correction],
    policy: Policy | None = None,
) -> list[Finding]:
    """Every extracted number and identity string must be traceable to the source."""
    findings: list[Finding] = []
    numbers = _numbers(source_text)
    names = _alnum(source_text)
    for correction in accepted:
        numbers |= _numbers(correction.corrected)
        names += " " + _alnum(correction.corrected)

    def check_number(
        field: str, value: Decimal | None, ref: str | None = None, *, rate: bool = False
    ):
        if value is None:
            return
        candidates = {value.normalize()}
        if rate:  # "5%" is stated as 5 but stored as 0.05
            candidates.add((value * 100).normalize())
        if numbers.isdisjoint(candidates):
            findings.append(
                make_finding(
                    FindingCode.VALUE_NOT_IN_SOURCE,
                    f"{field} {value.normalize():f} does not appear in the source.",
                    field=field,
                    item_ref=ref,
                    observed=f"{value.normalize():f}",
                )
            )

    for line in draft.line_items:
        ref = f"line {line.line_no}"
        check_number("quantity", line.quantity, ref)
        check_number("unit_price", line.unit_price, ref)
        check_number("amount", line.amount, ref)
        if line.description and item_key(line.description) not in names:
            findings.append(
                make_finding(
                    FindingCode.VALUE_NOT_IN_SOURCE,
                    f"Item '{line.description}' does not appear in the source.",
                    field="item",
                    item_ref=ref,
                    observed=line.description,
                )
            )
    check_number("subtotal", draft.subtotal)
    check_number("tax_amount", draft.tax_amount)
    check_number("tax_rate", draft.tax_rate, rate=True)
    check_number("shipping", draft.shipping)
    check_number("total", draft.total)

    if draft.invoice_number and invoice_number_key(draft.invoice_number) not in names:
        findings.append(
            make_finding(
                FindingCode.VALUE_NOT_IN_SOURCE,
                f"Invoice number '{draft.invoice_number}' does not appear in the source.",
                field="invoice_number",
                observed=draft.invoice_number,
            )
        )
    if draft.vendor_name and _alnum(vendor_key(draft.vendor_name)) not in names:
        findings.append(
            make_finding(
                FindingCode.VALUE_NOT_IN_SOURCE,
                f"Vendor '{draft.vendor_name}' does not appear in the source.",
                field="vendor_name",
                observed=draft.vendor_name,
            )
        )

    fields = _source_fields(source_text)
    dates = policy.dates if policy else _DEFAULT_DATES

    def date_grounded(field: str, raw: str | None) -> bool:
        if not raw or not raw.strip():
            return False
        values = fields.get(field, [])
        if _date_in_field(raw, values, dates):
            return True
        return any(
            correction.field == field
            and _date_in_field(correction.original, values, dates)
            and _same_date(correction.corrected, raw, dates)
            for correction in accepted
        )

    invoice_date_grounded = date_grounded("invoice_date", draft.invoice_date_raw)
    for field, raw, grounded in (
        ("invoice_date", draft.invoice_date_raw, invoice_date_grounded),
        ("due_date", draft.due_date_raw, date_grounded("due_date", draft.due_date_raw)),
    ):
        if not raw or not raw.strip() or grounded:
            continue
        if field == "due_date" and not fields.get("due_date") and invoice_date_grounded:
            invoice_date = parse_date(draft.invoice_date_raw, dates).value
            due_date = parse_date(raw, dates).value
            days = terms_days(draft.payment_terms)
            if invoice_date and due_date and days is not None:
                source_days = {terms_days(value) for value in fields.get("payment_terms", [])}
                if days in source_days and (due_date - invoice_date) == timedelta(days=days):
                    continue
        findings.append(
            make_finding(
                FindingCode.VALUE_NOT_IN_SOURCE,
                f"{field} '{raw}' has no supporting source field or verified derivation.",
                field=field,
                observed=raw,
            )
        )

    if draft.revision and draft.revision.strip():
        revisions = fields.get("revision", [])
        grounded = any(_same_revision(value, draft.revision) for value in revisions)
        grounded = grounded or any(
            correction.field == "revision"
            and any(_same_revision(value, correction.original) for value in revisions)
            and _same_revision(correction.corrected, draft.revision)
            for correction in accepted
        )
        if not grounded:
            findings.append(
                make_finding(
                    FindingCode.VALUE_NOT_IN_SOURCE,
                    f"Revision '{draft.revision}' has no supporting revision label in the source.",
                    field="revision",
                    observed=draft.revision,
                )
            )
    return findings


def check_grounding(
    draft: InvoiceDraft, source_text: str, policy: Policy | None = None
) -> list[Finding]:
    if draft.origin != "model":
        return []
    findings, accepted = verify_corrections(draft.corrections, source_text, policy)
    findings.extend(verify_values(draft, source_text, accepted, policy))
    return findings
