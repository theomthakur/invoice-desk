"""Simulated provider: the explicitly labeled "mock" mode for development and demos.

It is NOT a language model. It is a small deterministic reader that answers the same JSON
requests a model would, so the whole workflow can run with no key and no network. Every event
and result says ``simulated: true``. It is never chosen silently after a live failure.

It has no knowledge of any invoice: no file names, no invoice numbers, no expected outcomes.
* extraction reads labeled fields and item rows from whatever text it is given;
* validation asks for the catalog tool with the item names it is shown;
* approval and critique reason only from the facts in their context.

Limits (stated, not hidden): it handles common invoice layouts with ``$`` amounts. A layout it
cannot read produces nulls, and the rules then report missing fields. It cannot read anything
that a real model could read but this parser cannot.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..agents.prompts import CONTEXT_MARK, DOC_CLOSE, DOC_OPEN
from .base import Provider, ProviderError, ProviderRequest, ProviderResponse
from .scripted import reply

# ---------------------------------------------------------------------------- text reading

_LABEL = re.compile(
    r"""(?ix)\b(?:
        (?P<number>invoice\s*(?:number|no\.?)\s*[:\#]?|invoice\s*[:\#]|inv\s*(?:number|no\.?|\#)\s*[:\#]?)
      | (?P<vendor>vendor(?:\s*name)?\s*:|vndr\s*:|supplier\s*:)
      | (?P<from>from\s*:)
      | (?P<due>due\s*date\s*:|due\s*dt\s*:|due\s*:)
      | (?P<date>invoice\s*date\s*:|date\s*:|dt\s*:)
      | (?P<terms>payment\s*terms\s*:|pymnt\s*terms\s*:|terms\s*:)
      | (?P<notes>notes?\s*:)
      | (?P<currency>currency\s*:)
    )"""
)
_TOTAL = re.compile(
    r"(?im)^\s*(?:grand\s+total|total\s+amount|total\s+due|amount\s+due|total|amt)\s*:?\s*\$?\s*(?P<v>[\dOo][\dOo,]*(?:\.[\dOo]+)?)\s*$"
)
_SUBTOTAL = re.compile(r"(?im)^\s*sub\s*total\s*:?\s*\$?\s*(?P<v>[\dOo][\dOo,]*(?:\.[\dOo]+)?)\s*$")
_TAX = re.compile(
    r"(?im)^\s*(?:sales\s+)?(?:tax|vat)\s*(?:\(\s*(?P<rate>\d+(?:\.\d+)?)\s*%\s*\))?\s*:?\s*\$?\s*(?P<v>[\dOo][\dOo,]*(?:\.[\dOo]+)?)\s*$"
)
_SHIPPING = re.compile(
    r"(?im)^\s*(?:shipping|freight|delivery)\s*:?\s*\$?\s*(?P<v>[\dOo][\dOo,]*(?:\.[\dOo]+)?)\s*$"
)
_REVISION = re.compile(r"(?im)^\s*(?:revision|rev)\.?\s*[:\#]?\s*(?P<v>[A-Za-z]?\d{1,3})\s*$")
_ITEM = re.compile(
    r"""(?ix)^\s*(?:[-*\u2022]\s*)?
        (?P<name>[A-Za-z][A-Za-z0-9 ()\-/.]*?)
        \s+(?:(?:qty\s*:?|quantity\s*:?)\s*|x(?=\d))?(?P<qty>\d+(?:\.\d+)?)
        \s*(?:@|unit\s*price\s*:?)?\s*\$\s*(?P<price>[\d,]+(?:\.\d+)?)
        (?:\s*(?:each|ea\.?)\b)?
        (?:\s*\$\s*(?P<amount>[\dOo][\dOo,]*(?:\.[\dOo]+)?))?
        (?P<rest>.*)$"""
)
_LOOKALIKE_O = re.compile(r"(?<=\d)[Oo](?=\d)|(?<=\d)[Oo]\b|\b[Oo](?=\d)|(?<=[.,])[Oo](?=\d)")


def _repair(token: str) -> str:
    return _LOOKALIKE_O.sub("0", token)


def _clean_money(token: str) -> str:
    return _repair(token).replace(",", "")


def _document(messages: list[dict[str, Any]]) -> tuple[str, str]:
    for message in reversed(messages):
        content = message.get("content") or ""
        if message.get("role") == "user" and DOC_OPEN in content:
            fmt = re.search(r"Document format:\s*(\w+)", content)
            body = content.split(DOC_OPEN, 1)[1].split(DOC_CLOSE, 1)[0].strip("\n")
            return body, fmt.group(1) if fmt else "txt"
    return "", "txt"


def read_invoice_text(text: str) -> dict[str, Any]:
    """Return an ``ExtractionOutput``-shaped dict. Pure function of the text."""
    corrections: list[dict[str, Any]] = []

    def fix(field: str, token: str, line: str, ref: str | None = None) -> str:
        repaired = _repair(token)
        if repaired != token:
            corrections.append(
                {
                    "field": field,
                    "item_ref": ref,
                    "original": token,
                    "corrected": repaired,
                    "evidence": line.strip(),
                }
            )
        return repaired

    values: dict[str, str] = {}
    froms: list[str] = []
    for line in text.splitlines():
        matches = list(_LABEL.finditer(line))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            value = line[match.end() : end].strip().rstrip(",;")
            kind = match.lastgroup
            if not value or kind is None:
                continue
            if kind == "from":
                froms.append(value)
            elif kind not in values:
                values[kind] = (value, line)  # type: ignore[assignment]

    def got(kind: str) -> tuple[str, str] | None:
        return values.get(kind)  # type: ignore[return-value]

    vendor = got("vendor")
    vendor_name = vendor[0] if vendor else next((f for f in froms if "@" not in f), None)
    number = got("number")
    date = got("date")
    due = got("due")

    def date_value(entry: tuple[str, str] | None, field: str) -> str | None:
        if entry is None:
            return None
        return fix(field, entry[0], entry[1])

    def find_money(pattern: re.Pattern[str], field: str) -> tuple[str | None, str | None]:
        match = pattern.search(text)
        if not match:
            return None, None
        line = text[text.rfind("\n", 0, match.start("v")) + 1 : text.find("\n", match.end("v"))]
        raw = match["v"]
        fix(field, raw, line if line.strip() else match[0])
        return _clean_money(raw), (match.groupdict().get("rate"))

    total, _ = find_money(_TOTAL, "total")
    subtotal, _ = find_money(_SUBTOTAL, "subtotal")
    tax, rate = find_money(_TAX, "tax_amount")
    shipping, _ = find_money(_SHIPPING, "shipping")

    items: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        match = _ITEM.match(raw_line)
        if not match:
            continue
        line_no = len(items) + 1
        ref = f"line {line_no}"
        amount = match["amount"]
        if amount:
            fix("amount", amount, raw_line, ref)
        rest = (match["rest"] or "").strip(" -:,")
        items.append(
            {
                "description": re.sub(r"\s+", " ", match["name"]).strip(),
                "quantity": match["qty"],
                "unit_price": match["price"].replace(",", ""),
                "amount": _clean_money(amount) if amount else None,
                "note": rest or None,
            }
        )

    notes_entry = got("notes")
    revision = _REVISION.search(text)
    currency = got("currency")
    if currency is None and "\u20ac" in text:
        currency = ("EUR", "")
    terms = got("terms")

    return {
        "invoice_number": number[0] if number else None,
        "revision": revision["v"] if revision else None,
        "vendor_name": vendor_name,
        "vendor_address": None,
        "invoice_date": date_value(date, "invoice_date"),
        "due_date": date_value(due, "due_date"),
        "currency": currency[0].upper() if currency else None,
        "line_items": items,
        "subtotal": subtotal,
        "tax_rate": (f"{float(rate) / 100:g}" if rate is not None else None),
        "tax_amount": tax,
        "shipping": shipping,
        "total": total,
        "payment_terms": terms[0] if terms else None,
        "notes": notes_entry[0] if notes_entry else None,
        "corrections": corrections,
    }


# ---------------------------------------------------------------------------- reasoning


def _context(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in messages:
        content = message.get("content") or ""
        if message.get("role") == "user" and content.startswith(CONTEXT_MARK):
            return json.loads(content[len(CONTEXT_MARK) :].strip())
    return {}


_WARNING_NOTES = {
    "PAYMENT_PRESSURE_LANGUAGE": "Urgency wording was noted; it did not change any check or the policy.",
    "TERMS_DATE_INCONSISTENT": "The due date does not match the stated terms; payment follows the due date shown.",
    "TAX_MISMATCH": "The stated tax differs from the stated rate; the total still reconciles.",
    "AMBIGUOUS_DATE": "A numeric date could be read two ways; the policy reading was used.",
    "MISSING_FIELD": "A non-essential field is missing.",
}


def propose(context: dict[str, Any]) -> dict[str, Any]:
    """The simulated VP: a pure function of the facts it is given."""
    invoice = context.get("invoice", {})
    findings = context.get("findings", [])
    errors = [f for f in findings if f["severity"] == "ERROR"]
    reviews = [f for f in findings if f["severity"] == "REVIEW"]
    warnings = [f for f in findings if f["severity"] == "WARNING"]
    scrutiny = bool(context.get("requires_scrutiny"))
    vendor, number = invoice.get("vendor_name", "the vendor"), invoice.get("invoice_number", "?")
    total, currency = invoice.get("total", "?"), invoice.get("currency", "")
    subject = f"{vendor} invoice {number} for {total} {currency}".strip()

    if errors:
        return _proposal(
            "reject",
            f"Reject {subject}: " + "; ".join(f["message"] for f in errors[:3]),
            [],
            [],
            [],
        )
    if reviews:
        return _proposal(
            "needs_review",
            f"Hold {subject} for a person: " + "; ".join(f["message"] for f in reviews[:3]),
            [],
            [],
            [],
        )

    notes = [_WARNING_NOTES.get(w["code"], w["message"]) for w in warnings]
    rationale = f"Approve {subject}: no blocking findings."
    if notes:
        rationale += " " + " ".join(notes)
    checks: list[str] = []
    if scrutiny:
        lookups = context.get("catalog_lookups", [])
        if lookups and all(row.get("status") == "ok" for row in lookups):
            checks.append("Every item was found in the catalog with enough stock.")
        checks.append(
            f"The total {total} was reconciled with the line items, tax and shipping by the automatic checks."
        )
        checks.append("No ERROR or REVIEW findings remain.")
        rationale += (
            f" The amount is above the policy threshold; extra checks were made for {vendor}."
        )
    return _proposal(
        "approve",
        rationale,
        [w["code"] for w in warnings],
        ["Policy: amounts above the threshold need extra scrutiny."] if scrutiny else [],
        checks,
    )


def _proposal(
    action: str, rationale: str, addressed: list[str], rules: list[str], checks: list[str]
) -> dict[str, Any]:
    return {
        "action": action,
        "rationale": rationale[:1400],
        "rules_applied": rules,
        "addressed_codes": sorted(set(addressed)),
        "scrutiny_checks": checks,
    }


def critique(payload: dict[str, Any]) -> dict[str, Any]:
    context, decision = payload.get("context", {}), payload.get("decision", {})
    findings = context.get("findings", [])
    issues: list[str] = []
    action = decision.get("action", "needs_review")
    blocking = [f for f in findings if f["severity"] in ("ERROR", "REVIEW")]
    if action == "approve":
        if blocking:
            issues.append("Approval proposed although blocking findings exist.")
        missing = sorted(
            {f["code"] for f in findings if f["severity"] == "WARNING"}
            - set(decision.get("addressed_codes", []))
        )
        if missing:
            issues.append(f"WARNING findings not addressed: {', '.join(missing)}.")
        if context.get("requires_scrutiny"):
            if not decision.get("scrutiny_checks"):
                issues.append(
                    "Amount is above the threshold but no specific extra checks are listed."
                )
            vendor = str(context.get("invoice", {}).get("vendor_name", "")).casefold().split()
            text = decision.get("rationale", "").casefold()
            if not any(w.strip(".,") in text for w in vendor if len(w) > 3):
                issues.append("The rationale does not name the vendor, so it is not specific.")
    suggested = "needs_review" if blocking else "approve"
    return {
        "agrees": not issues,
        "issues": issues,
        "suggested_action": suggested if issues else action,
    }


# ---------------------------------------------------------------------------- the provider


class SimulatedProvider(Provider):
    mode = "mock"
    name = "simulated"
    model = "simulated-reader"
    simulated = True

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        label = request.label.removesuffix("_repair")
        try:
            if label in ("extraction", "reflection"):
                text, _fmt = _document(request.messages)
                return reply(
                    read_invoice_text(text),
                    prompt_tokens=None,
                    completion_tokens=None,
                    cost_usd=None,
                )
            if label == "validation":
                items = _context(request.messages).get("items", [])
                return reply(
                    None,
                    tool_calls=[("lookup_inventory", {"items": items[:50]})],
                    prompt_tokens=None,
                    completion_tokens=None,
                    cost_usd=None,
                )
            if label in ("approval", "revision"):
                return reply(
                    propose(_context(request.messages)),
                    prompt_tokens=None,
                    completion_tokens=None,
                    cost_usd=None,
                )
            if label == "critique":
                return reply(
                    critique(_context(request.messages)),
                    prompt_tokens=None,
                    completion_tokens=None,
                    cost_usd=None,
                )
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("simulated_failure", f"The simulated reader failed: {exc}") from exc
        raise ProviderError(
            "simulated_unsupported", f"No simulated behaviour for '{request.label}'."
        )
