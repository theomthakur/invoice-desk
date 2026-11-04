"""Validation agent: a real tool-calling exchange with the catalog.

The model is asked to look the invoice's item names up with ``lookup_inventory``. The tool runs
in our code, against the real database, with arguments validated server-side. The result goes
into the approver's context as evidence.

The authoritative checks are Part 01's rules, run separately by code. This agent cannot change
them. It exists so the catalog evidence is gathered through the same typed, audited tool path a
live model would use, and so a model that skips an item or asks for something odd is visible in
the trace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..llm import ModelClient
from ..providers.base import ProviderRequest
from ..schemas import InvoiceDraft
from ..textnorm import item_key
from ..tools.registry import ToolContext
from .prompts import validation_messages


@dataclass
class CatalogEvidence:
    lookups: list[dict[str, Any]] = field(default_factory=list)
    requested_by_model: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)  # invoice items the model did not look up
    tool_used: bool = False


def gather_catalog_evidence(
    client: ModelClient, ctx: ToolContext, draft: InvoiceDraft
) -> CatalogEvidence:
    items = []
    for line in draft.line_items:
        name = (line.description or "").strip()
        if name and name not in items:
            items.append(name)
    evidence = CatalogEvidence()
    if not items:
        return evidence

    request = ProviderRequest(messages=validation_messages(items[:50]), label="validation")
    messages = client.run_tool_round(
        request, ["lookup_inventory"], ctx, stage="validation", require=True
    )
    for message in messages:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message["content"])
        except (ValueError, KeyError):
            continue
        if "error" in payload:
            continue
        evidence.tool_used = True
        for row in payload.get("results", []):
            evidence.lookups.append(row)
            evidence.requested_by_model.append(row.get("query", ""))

    looked_up = {item_key(q) for q in evidence.requested_by_model}
    evidence.missed = [name for name in items if item_key(name) not in looked_up]
    client._emit(
        "validation",
        "catalog_evidence",
        {
            "tool_used": evidence.tool_used,
            "items_on_invoice": len(items),
            "looked_up": len(evidence.lookups),
            "not_looked_up": evidence.missed[:10],
        },
    )
    return evidence
