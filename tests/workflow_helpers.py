"""Helpers to run the real workflow against a scripted provider and a throwaway database."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

from invoice_flow.config import load_policy
from invoice_flow.parsers import load_source
from invoice_flow.providers import ProviderError, ScriptedProvider, reply
from invoice_flow.runtime import App, AppConfig
from invoice_flow.service import execute_run
from invoice_flow.storage import Database, list_events

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "invoices"
TODAY = date(2026, 11, 3)
CLOCK = lambda: datetime(2026, 11, 3, 12, 0, tzinfo=UTC)  # noqa: E731


def make_app(tmp_path, provider=None, **config) -> App:
    cfg = AppConfig(data_dir=tmp_path, **config)
    app = App.create(
        cfg,
        db=Database(tmp_path / "w.db", clock=CLOCK),
        policy=load_policy(),
        provider_factory=lambda mode: provider,
    )
    app.mock_payment = lambda vendor, amount: {"status": "success"}
    return app


def source(name: str):
    return load_source(SAMPLES / name, load_policy()).source


def custom_source(text: str, filename: str = "custom.txt"):
    """A document that is not one of the samples (hash and id derive from its text)."""
    import hashlib

    from invoice_flow.schemas import SourceDocument, SourceFormat

    digest = hashlib.sha256(text.encode()).hexdigest()
    return SourceDocument(
        document_id=f"doc_{digest[:16]}",
        filename=filename,
        format=SourceFormat.TXT,
        size_bytes=len(text),
        byte_hash=digest,
        text=text,
    )


def run_source(app: App, src, mode: str = "mock", workspace: str = "local", **kw):
    return execute_run(app, workspace_id=workspace, source=src, mode=mode, today=TODAY, **kw)


def run(app: App, name: str, mode: str = "mock", workspace: str = "local", **kw):
    return execute_run(
        app, workspace_id=workspace, source=source(name), mode=mode, today=TODAY, **kw
    )


def events(app: App, result) -> list[dict]:
    with app.db.read() as conn:
        return list_events(conn, result.workspace_id, result.run_id, limit=500)


def types(app: App, result) -> list[str]:
    return [e["type"] for e in events(app, result)]


def payments(app: App) -> list:
    with app.db.read() as conn:
        return conn.execute("SELECT * FROM payments").fetchall()


# ---------------------------------------------------------------------------- scripted answers

_EXTRACTION_KEYS = {
    "invoice_number": None,
    "revision": None,
    "vendor_name": None,
    "vendor_address": None,
    "invoice_date": None,
    "due_date": None,
    "currency": None,
    "line_items": [],
    "subtotal": None,
    "tax_rate": None,
    "tax_amount": None,
    "shipping": None,
    "total": None,
    "payment_terms": None,
    "notes": None,
    "corrections": [],
}


def extraction(**fields) -> dict:
    """A complete ExtractionOutput dict (the schema requires every key)."""
    data = dict(_EXTRACTION_KEYS)
    data.update(fields)
    data["line_items"] = [
        {
            "description": None,
            "quantity": None,
            "unit_price": None,
            "amount": None,
            "note": None,
            **li,
        }
        for li in data["line_items"]
    ]
    return data


def good_1001() -> dict:
    return extraction(
        invoice_number="INV-1001",
        vendor_name="Widgets Inc.",
        invoice_date="2026-01-15",
        due_date="2026-02-01",
        subtotal="5000.00",
        tax_rate="0",
        tax_amount="0.00",
        total="5000.00",
        payment_terms="Net 15",
        line_items=[
            {"description": "WidgetA", "quantity": "10", "unit_price": "250.00"},
            {"description": "WidgetB", "quantity": "5", "unit_price": "500.00"},
        ],
    )


def proposal(
    action="approve",
    rationale="Approve Widgets Inc. INV-1001 for 5000.00 USD.",
    addressed=None,
    checks=None,
) -> dict:
    return {
        "action": action,
        "rationale": rationale,
        "rules_applied": [],
        "addressed_codes": addressed or [],
        "scrutiny_checks": checks or [],
    }


def critique(agrees=True, issues=None, suggested="approve") -> dict:
    return {"agrees": agrees, "issues": issues or [], "suggested_action": suggested}


def lookup(items: list[str]):
    return reply(None, tool_calls=[("lookup_inventory", {"items": items})])


def happy_1001_steps() -> list:
    """extraction, catalog tool call, approval, critique."""
    return [
        reply(good_1001()),
        lookup(["WidgetA", "WidgetB"]),
        reply(proposal()),
        reply(critique()),
    ]


def scripted(*steps) -> ScriptedProvider:
    return ScriptedProvider(list(steps))


def labels(provider: ScriptedProvider) -> list[str]:
    return provider.labels


def err(code: str, retryable: bool = False, retry_after: float | None = 0.0) -> ProviderError:
    return ProviderError(code, f"scripted {code}", retryable=retryable, retry_after=retry_after)


def tool_result_payload(provider: ScriptedProvider, request_index: int) -> dict:
    """The JSON the application sent back to the model as a tool result."""
    for message in provider.requests[request_index].messages:
        if message.get("role") == "tool":
            return json.loads(message["content"])
    raise AssertionError("no tool result in that request")


def strip_paid_prints(text: str) -> str:
    return re.sub(r"^Paid .*\n", "", text, flags=re.M)
