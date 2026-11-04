"""Inventory lookup and stock rules.

Stock is a read-only validation snapshot. Paying an invoice never changes it.
Quantities are summed per resolved item before comparing with stock, so an invoice cannot
hide an over-order by splitting it across lines.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from ..findings import Finding, FindingCode, make_finding
from ..schemas import InvoiceDraft
from ..textnorm import item_key


@dataclass(frozen=True)
class InventoryIndex:
    """Immutable view of the inventory at one moment."""

    stock: dict[str, int]  # canonical item name -> units
    keys: dict[str, tuple[str, ...]] = field(default_factory=dict)  # normalized key -> items

    @classmethod
    def build(cls, stock: dict[str, int], aliases: dict[str, str] | None = None) -> InventoryIndex:
        keys: dict[str, set[str]] = defaultdict(set)
        for name in stock:
            keys[item_key(name)].add(name)
        for alias_key, name in (aliases or {}).items():
            if name in stock:
                keys[alias_key].add(name)
        return cls(stock=dict(stock), keys={k: tuple(sorted(v)) for k, v in keys.items()})

    def resolve(self, description: str | None) -> tuple[str | None, str]:
        """Return ``(canonical_name, status)``; status is ``ok``, ``unknown`` or ``ambiguous``.

        Only case, spacing, punctuation and a trailing "(qualifier)" are ignored. There is no
        fuzzy matching, so an unfamiliar product can never become a known one.
        """
        key = item_key(description or "")
        matches = self.keys.get(key, ()) if key else ()
        if len(matches) == 1:
            return matches[0], "ok"
        if len(matches) > 1:
            return None, "ambiguous"
        return None, "unknown"


def usable_quantity(value: Decimal | None) -> int | None:
    """A whole, positive quantity, or ``None``."""
    if value is None or value <= 0 or value != value.to_integral_value():
        return None
    return int(value)


def check_inventory(
    draft: InvoiceDraft, index: InventoryIndex
) -> tuple[list[Finding], dict[int, str | None]]:
    """Findings plus a ``line_no -> canonical item`` map used by normalization."""
    findings: list[Finding] = []
    resolved: dict[int, str | None] = {}
    requested: dict[str, int] = defaultdict(int)
    first_line: dict[str, int] = {}

    for line in draft.line_items:
        ref = f"line {line.line_no}"
        if not (line.description or "").strip():
            resolved[line.line_no] = None  # a missing description is reported elsewhere
            continue
        name, status = index.resolve(line.description)
        resolved[line.line_no] = name
        if status == "unknown":
            findings.append(
                make_finding(
                    FindingCode.UNKNOWN_ITEM,
                    f"'{line.description}' is not in the inventory.",
                    field="item",
                    item_ref=ref,
                    observed=line.description,
                )
            )
        elif status == "ambiguous":
            findings.append(
                make_finding(
                    FindingCode.AMBIGUOUS_ITEM,
                    f"'{line.description}' matches more than one inventory item.",
                    field="item",
                    item_ref=ref,
                    observed=line.description,
                )
            )
        elif name is not None:
            quantity = usable_quantity(line.quantity)
            if quantity is not None:
                requested[name] += quantity
                first_line.setdefault(name, line.line_no)

    for name, quantity in requested.items():
        available = index.stock[name]
        lines = sum(1 for n in resolved.values() if n == name)
        detail = f" across {lines} lines" if lines > 1 else ""
        if available == 0:
            findings.append(
                make_finding(
                    FindingCode.ZERO_STOCK_ITEM,
                    f"{name} has no stock; {quantity} requested{detail}.",
                    field="quantity",
                    item_ref=name,
                    observed=quantity,
                    expected=0,
                )
            )
        elif quantity > available:
            findings.append(
                make_finding(
                    FindingCode.INSUFFICIENT_STOCK,
                    f"{name}: {quantity} requested{detail}, only {available} in stock.",
                    field="quantity",
                    item_ref=name,
                    observed=quantity,
                    expected=available,
                )
            )
    return findings, resolved
