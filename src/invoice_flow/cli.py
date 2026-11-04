"""Command line interface. It calls the same service as the HTTP API.

Exit codes: 0 = the command completed, including a valid rejection, review hold, duplicate or
superseded result; 1 = technical failure (unreadable file, provider failure, interrupted run);
2 = bad usage.

With ``--json`` only JSON goes to stdout. Human text, the trace and logs go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings, load_policy
from .findings import Severity
from .parsers import SourceError
from .runtime import App, AppConfig
from .schemas import RunLifecycle, RunResult
from .service import process_batch, process_invoice, run_checks
from .storage import Database, init_database, list_events, reset_database

_ICON = {
    Severity.ERROR: "ERROR  ",
    Severity.REVIEW: "REVIEW ",
    Severity.WARNING: "WARNING",
    Severity.INFO: "info   ",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py", description="Acme Corp invoice processing prototype."
    )
    parser.add_argument(
        "--invoice_path",
        "--invoice-path",
        dest="invoice_path",
        help="Invoice file to process (txt, pdf, json, csv, xml).",
    )
    parser.add_argument(
        "--batch",
        metavar="DIR",
        help="Process every supported file in a folder (versions are planned first).",
    )
    parser.add_argument(
        "--mode",
        choices=["mock", "live"],
        help="mock = labeled simulated reader (no key). live = OpenRouter model. "
        "Default: MODEL_MODE or mock.",
    )
    parser.add_argument(
        "--checks-only",
        action="store_true",
        help="Only the deterministic checks on structured files. Never pays.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable result on stdout.")
    parser.add_argument("--quiet", action="store_true", help="Do not print the step-by-step trace.")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run the HTTP API on 127.0.0.1:8000 (python -m invoice_flow.server for options).",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Create the database and seed missing starter stock. Keeps all data.",
    )
    parser.add_argument(
        "--reset-db",
        action="store_true",
        help="DELETE the whole local database. Requires --yes-delete-all-data.",
    )
    parser.add_argument(
        "--yes-delete-all-data", action="store_true", help="Confirmation required by --reset-db."
    )
    return parser


def _say(message: str = "") -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------------- checks-only


def _checks_payload(result) -> dict:
    report = result.report
    return {
        "mode": "deterministic_checks_only",
        "note": "No approval or payment was attempted.",
        "file": result.parsed.source.filename,
        "format": result.parsed.source.format.value,
        "needs_extraction": result.needs_extraction,
        "eligibility": report.eligibility.value if report else None,
        "requires_scrutiny": report.requires_scrutiny if report else None,
        "route": (
            {
                "outcome": result.route.outcome.value if result.route.outcome else None,
                "reason": result.route.reason,
            }
            if result.route
            else None
        ),
        "findings": [
            f.model_dump(mode="json")
            for f in (result.route.findings if result.route else report.findings if report else [])
        ],
        "invoice": report.normalized.model_dump(mode="json")
        if report and report.normalized
        else None,
    }


def _print_checks(payload: dict) -> None:
    _say(f"{payload['file']} ({payload['format']}): deterministic checks only, nothing was paid")
    if payload["needs_extraction"]:
        _say(
            "  This file is unstructured text. Reading it needs the model step "
            "(run without --checks-only)."
        )
        return
    route = payload["route"]
    _say(
        f"  eligibility: {payload['eligibility']}"
        + (
            f"  ->  {route['outcome'] or 'continue to approval'}: {route['reason']}"
            if route
            else ""
        )
    )
    for finding in payload["findings"]:
        severity = Severity(finding["severity"])
        where = f" [{finding['item_ref']}]" if finding.get("item_ref") else ""
        _say(f"  {_ICON[severity]} {finding['code']}{where}: {finding['message']}")


# ---------------------------------------------------------------------------------- full runs

_STAGE_LABEL = {
    "intake": "INTAKE",
    "extraction": "EXTRACT",
    "validation": "VALIDATE",
    "approval": "APPROVE",
    "critique": "CRITIC",
    "payment": "PAYMENT",
    "completion": "DONE",
    "error": "ERROR",
}


def _event_line(event: dict) -> str | None:
    p, t = event["payload"], event["type"]
    if t == "run_started":
        label = (
            "SIMULATED reader (no language model)"
            if p.get("simulated")
            else f"model {p.get('model')}"
        )
        return f"started in {p.get('mode')} mode with {label}"
    if t == "document_received":
        return f"received {p['filename']} ({p['format']}, {p['size_bytes']} bytes)"
    if t == "parsed_structured":
        return f"read structured data directly, {p['line_items']} line item(s), no model used"
    if t == "model_request":
        return f"model request {p['request_number']} ({p['label']})"
    if t == "model_error":
        return f"model error {p['code']}: {p['message']}"
    if t == "model_retry":
        return f"retrying after {p['wait_seconds']}s"
    if t == "tool_call":
        return f"tool {p['tool']}({p['arguments'][:70]}) -> {p['outcome']}"
    if t == "output_invalid":
        return f"model answer unusable: {'; '.join(p['problems'][:2])}"
    if t == "extracted":
        extra = " (re-read the document)" if p["reflected"] else ""
        return f"extracted {p['line_items']} line item(s), {p['corrections']} correction(s){extra}"
    if t == "reflection_result":
        return f"re-read: {p['decision']} - {p['classification']}"
    if t == "validated":
        return f"{p['eligibility']}: {p['counts']}"
    if t == "registered":
        return f"{p['disposition']} -> {p['route']}: {p['reason']}"
    if t == "proposal":
        return f"simulated VP proposes '{p['action']}': {p['rationale'][:110]}"
    if t == "critique":
        verdict = "agrees" if p["agrees"] else "objects: " + "; ".join(p["issues"][:2])
        return f"critic {verdict}"
    if t == "revision":
        return f"simulated VP revises to '{p['action']}'"
    if t == "final_decision":
        return f"final decision: {p['action']}"
    if t == "payment_made":
        return f"MOCK payment {p['amount']} {p['currency']} (simulated, no real bank)"
    if t in ("payment_refused", "gate_refused", "node_failed", "run_crashed", "run_not_started"):
        return f"{p.get('code', '')}: {p.get('message', '')}"
    if t == "run_finished":
        return f"{p['lifecycle']} / {p['outcome']}"
    return None


def _print_trace(app: App, result: RunResult) -> None:
    with app.db.read() as conn:
        events = list_events(conn, result.workspace_id, result.run_id, limit=500)
    for event in events:
        line = _event_line(event)
        if line:
            _say(f"  {_STAGE_LABEL.get(event['stage'], event['stage']):9} {line}")


def _print_result(app: App, result: RunResult, *, trace: bool) -> None:
    mode = (
        "SIMULATED (no language model)"
        if result.provider in ("simulated", "scripted")
        else f"live model {result.model}"
    )
    _say(f"\n{result.filename or 'invoice'}  [{mode}]")
    if trace:
        _print_trace(app, result)
    _say("")
    if result.invoice:
        inv = result.invoice
        _say(f"  {inv.vendor_name}  {inv.invoice_number}  total {inv.total} {inv.currency}")
    shown = [f for f in result.findings if f.severity is not Severity.INFO]
    for finding in shown[:12]:
        where = f" [{finding.item_ref}]" if finding.item_ref else ""
        _say(f"  {_ICON[finding.severity]} {finding.code.value}{where}: {finding.message}")
    outcome = result.outcome.value.upper() if result.outcome else result.lifecycle.value.upper()
    _say(f"\n  RESULT: {outcome}  -  {result.terminal_reason or ''}")
    if result.receipt:
        _say(f"  receipt {result.receipt.transaction_id} (simulated)")
    u = result.usage
    cost = u.estimated_cost_usd if u.estimated_cost_usd is not None else "unknown"
    _say(f"  model requests: {u.model_calls}  tool calls: {u.tool_calls}  cost: {cost}")


def _exit_for(result: RunResult) -> int:
    return 0 if result.lifecycle is RunLifecycle.COMPLETED else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = AppConfig.from_env()
    except ValueError as exc:
        _say(f"Configuration error: {exc}")
        return 2
    settings = Settings(app_env=config.app_env, data_dir=config.data_dir)
    db = Database(settings.db_path)

    if args.serve:
        from .server import main as serve

        return serve([])

    if args.reset_db:
        if not args.yes_delete_all_data:
            _say("Refusing to reset: add --yes-delete-all-data to delete the local database.")
            return 2
        reset_database(settings.db_path, confirm_delete_all_data=True)
        init_database(db)
        _say(f"Database reset and re-seeded: {settings.db_path}")
        return 0

    if args.init_db:
        init_database(db)
        _say(f"Database ready: {settings.db_path}")
        if not args.invoice_path and not args.batch:
            return 0

    if not args.invoice_path and not args.batch:
        parser.print_usage(sys.stderr)
        _say("error: --invoice_path (or --batch DIR) is required.")
        return 2
    if args.invoice_path and args.batch:
        _say("error: use either --invoice_path or --batch, not both.")
        return 2

    mode = args.mode or config.default_mode
    if mode not in ("mock", "live"):
        _say(f"error: MODEL_MODE must be 'mock' or 'live', not '{mode[:20]}'.")
        return 2

    try:
        if args.checks_only:
            if not args.invoice_path:
                _say("error: --checks-only works with --invoice_path.")
                return 2
            result = run_checks(
                Path(args.invoice_path), db=db, policy=load_policy(settings.policy_path)
            )
            payload = _checks_payload(result)
            if args.json:
                print(json.dumps(payload, indent=2))
            _print_checks(payload)
            return 0

        app = App.create(config, db=db)
        if args.batch:
            return _run_batch(app, Path(args.batch), mode, args)

        run = process_invoice(args.invoice_path, app=app, mode=mode)
        if args.json:
            print(run.model_dump_json(indent=2))
        _print_result(app, run, trace=not args.quiet)
        return _exit_for(run)
    except SourceError as exc:
        _say(f"Cannot read the file ({exc.code}): {exc.message}")
        return 1
    except Exception as exc:  # last resort: a stable message, not a traceback, for operators
        _say(f"Unexpected failure: {type(exc).__name__}: {exc}")
        return 1


def _run_batch(app: App, folder: Path, mode: str, args) -> int:
    if not folder.is_dir():
        _say(f"error: {folder} is not a folder.")
        return 2
    allowed = set(app.policy.limits.allowed_extensions)
    paths = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in allowed]
    if not paths:
        _say("error: no supported invoice files found.")
        return 2
    report = process_batch(paths, app=app, mode=mode)
    if args.json:
        print(json.dumps(report, indent=2))
    _say(f"\nBatch of {len(report['files'])} file(s) [{mode}]")
    for row in report["files"]:
        outcome = row["outcome"] or row["kind"]
        extra = f"  ({row['reason'][:70]})" if row.get("reason") and outcome != "paid" else ""
        amount = f"  {row['amount']} {row['currency']}" if row.get("amount") else ""
        _say(f"  {row['file']:30} {outcome:13}{amount}{extra}")
    _say(f"\n  counts: {report['counts']}")
    _say(f"  paid by currency (each payable once): {report['paid_by_currency'] or 'nothing'}")
    technical = [r for r in report["files"] if r["kind"] == "unreadable"]
    return 1 if technical else 0
