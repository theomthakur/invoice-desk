"""What the model is asked to return, and how it is checked.

Every model answer is validated against one of these classes before it is used. The classes
reject unknown fields. They are deliberately small: the model interprets and explains, and code
decides (the rules, the approval gate and the payment gate are not model-controlled).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .money import MoneyError, parse_decimal
from .schemas import Correction, InvoiceDraft, LineItemDraft


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutputError(ValueError):
    """The model's answer is not usable. ``problems`` are short, safe-to-send descriptions."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# ------------------------------------------------------------------ extraction


class ExtractedLine(_Out):
    description: str | None
    quantity: str | None
    unit_price: str | None
    amount: str | None
    note: str | None


class ExtractedCorrection(_Out):
    field: str
    item_ref: str | None
    original: str
    corrected: str
    evidence: str


class ExtractionOutput(_Out):
    invoice_number: str | None
    revision: str | None
    vendor_name: str | None
    vendor_address: str | None
    invoice_date: str | None
    due_date: str | None
    currency: str | None
    line_items: list[ExtractedLine]
    subtotal: str | None
    tax_rate: str | None
    tax_amount: str | None
    shipping: str | None
    total: str | None
    payment_terms: str | None
    notes: str | None
    corrections: list[ExtractedCorrection]

    def to_draft(self) -> InvoiceDraft:
        """Convert to a draft, or raise :class:`OutputError` listing every unreadable value."""
        problems: list[str] = []

        def money(value: str | None, name: str) -> Decimal | None:
            if value is None or not value.strip():
                return None
            try:
                return parse_decimal(value)
            except MoneyError as exc:
                problems.append(f"{name} '{value[:40]}' is not a valid number ({exc.reason})")
                return None

        lines = []
        for number, line in enumerate(self.line_items, start=1):
            ref = f"line {number}"
            lines.append(
                LineItemDraft(
                    line_no=number,
                    description=(line.description or "").strip() or None,
                    quantity=money(line.quantity, f"{ref} quantity"),
                    unit_price=money(line.unit_price, f"{ref} unit_price"),
                    amount=money(line.amount, f"{ref} amount"),
                    note=(line.note or "").strip() or None,
                )
            )
        draft_kwargs = dict(
            origin="model",
            invoice_number=(self.invoice_number or "").strip() or None,
            revision=(self.revision or "").strip() or None,
            vendor_name=(self.vendor_name or "").strip() or None,
            vendor_address=(self.vendor_address or "").strip() or None,
            invoice_date_raw=(self.invoice_date or "").strip() or None,
            due_date_raw=(self.due_date or "").strip() or None,
            currency=(self.currency or "").strip().upper() or None,
            line_items=lines,
            subtotal=money(self.subtotal, "subtotal"),
            tax_rate=money(self.tax_rate, "tax_rate"),
            tax_amount=money(self.tax_amount, "tax_amount"),
            shipping=money(self.shipping, "shipping"),
            total=money(self.total, "total"),
            payment_terms=(self.payment_terms or "").strip() or None,
            notes=(self.notes or "").strip() or None,
            corrections=[Correction(**c.model_dump()) for c in self.corrections],
        )
        if problems:
            raise OutputError(problems)
        return InvoiceDraft(**draft_kwargs)


# ------------------------------------------------------------------ validation agent


class ItemCheck(_Out):
    query: str
    status: Literal["ok", "unknown", "ambiguous"]
    item: str | None


class ValidationNotes(_Out):
    items: list[ItemCheck]
    concerns: list[str] = Field(max_length=10)


# ------------------------------------------------------------------ approval and critique

ActionLiteral = Literal["approve", "reject", "needs_review"]


class ApprovalProposal(_Out):
    action: ActionLiteral
    rationale: str = Field(min_length=1, max_length=1500)
    rules_applied: list[str] = Field(max_length=12)
    addressed_codes: list[str] = Field(max_length=30)
    scrutiny_checks: list[str] = Field(max_length=8)


class Critique(_Out):
    agrees: bool
    issues: list[str] = Field(max_length=10)
    suggested_action: ActionLiteral


# ------------------------------------------------------------------ helpers


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema in the "strict" shape providers expect: every property required, no extras."""
    schema = model.model_json_schema()

    # Strict provider modes reject many validation keywords. We enforce them locally anyway.
    unsupported = (
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "default",
        "title",
    )

    def fix(node: Any) -> None:
        if isinstance(node, dict):
            for key in unsupported:
                if key not in ("properties",):
                    node.pop(key, None)
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for item in node:
                fix(item)

    fix(schema)
    return schema


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_model_json(text: str | None) -> Any:
    if text is None or not text.strip():
        raise OutputError(["the reply was empty"])
    body = text.strip()
    if match := _FENCE.match(body):
        body = match.group(1)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise OutputError(
            [f"the reply is not valid JSON ({exc.msg} at position {exc.pos})"]
        ) from exc


def validate_output(model: type[BaseModel], text: str | None) -> Any:
    data = parse_model_json(text)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in e['loc']) or 'reply'}: {e['msg']}" for e in exc.errors()[:6]
        ]
        raise OutputError(problems) from exc
