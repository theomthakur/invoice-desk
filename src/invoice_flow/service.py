"""Application service shared by the CLI and the HTTP API.

One implementation, one policy, one payment gate. The CLI calls these functions directly; the
API runs the same functions on a background worker.

``run_checks`` (Part 01) is kept unchanged: deterministic checks only, never approves or pays.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .budget import RunBudget
from .config import Policy, load_policy
from .findings import FindingCode
from .identity import content_hash
from .ledger import (
    LedgerError,
    Registration,
    Route,
    execute_mock_payment,
    load_version_state,
    record_approval,
    register_version,
    route_after_validation,
    store_document,
)
from .llm import ModelClient
from .parsers import ParsedSource, SourceError, load_source
from .preflight import BatchEntry, BatchKind, plan_batch
from .providers.base import Provider, ProviderError
from .rules import validate_invoice
from .runtime import App, EventRecorder
from .schemas import (
    ApprovalAction,
    ApprovalStage,
    InvoiceDraft,
    Outcome,
    RunLifecycle,
    RunResult,
    SourceDocument,
    ValidationReport,
)
from .storage import (
    LOCAL_WORKSPACE,
    Database,
    create_run,
    ensure_workspace,
    init_database,
    load_inventory_index,
    save_findings,
    update_run,
)
from .tools.registry import ToolContext
from .workflow import (
    Flow,
    RunEnv,
    build_decide_graph,
    build_prepare_graph,
    build_result,
    failure_from,
    persist_result,
)


class WorkflowNotAvailable(NotImplementedError):
    """Kept for compatibility with Part 01 callers. The workflow is now available."""


# ---------------------------------------------------------------------------------- environment


def _make_env(
    app: App,
    *,
    workspace_id: str,
    run_id: str,
    source: SourceDocument,
    mode: str,
    provider: Provider,
    today: date | None,
    cancel: threading.Event | None = None,
    hold_reason: str | None = None,
    attempt_of: str | None = None,
) -> RunEnv:
    budget = app.config.new_budget()
    if cancel is not None:
        budget.cancel_event = cancel
    secrets = (
        app.config.openrouter_api_key,
        app.config.xai_api_key,
        app.config.session_secret,
        app.config.demo_access_code,
    )
    emit = EventRecorder(app.db, run_id, secrets)
    client = ModelClient(
        provider,
        budget,
        emit,
        max_output_tokens=app.config.max_output_tokens,
        request_timeout=app.config.request_timeout_seconds,
    )
    return RunEnv(
        app=app,
        workspace_id=workspace_id,
        run_id=run_id,
        source=source,
        mode=mode,
        provider=provider,
        budget=budget,
        emit=emit,
        client=client,
        tool_ctx=ToolContext(db=app.db, policy=app.policy, workspace_id=workspace_id),
        today=today or app.db.clock().date(),
        hold_reason=hold_reason,
        attempt_of=attempt_of,
    )


def _start_run(
    app: App,
    workspace_id: str,
    source: SourceDocument,
    mode: str,
    provider: Provider,
    attempt_of: str | None = None,
) -> str:
    with app.db.transaction() as conn:
        ensure_workspace(
            conn,
            workspace_id,
            "local" if workspace_id == LOCAL_WORKSPACE else "session",
            app.db.now(),
        )
        store_document(conn, workspace_id, source, app.db.now())
        run_id = create_run(
            conn,
            workspace_id=workspace_id,
            now=app.db.now(),
            document_id=source.document_id,
            mode=mode,
            provider=provider.name,
            model=provider.model,
            policy_version=app.policy.policy_version,
            lifecycle="running",
        )
    return run_id


def _fail_before_start(
    app: App, workspace_id: str, source: SourceDocument | None, mode: str, exc: BaseException
) -> RunResult:
    """Record a run that could not even begin (for example live mode without a key)."""
    failure = failure_from(exc)
    with app.db.transaction() as conn:
        ensure_workspace(
            conn,
            workspace_id,
            "local" if workspace_id == LOCAL_WORKSPACE else "session",
            app.db.now(),
        )
        if source is not None:
            store_document(conn, workspace_id, source, app.db.now())
        run_id = create_run(
            conn,
            workspace_id=workspace_id,
            now=app.db.now(),
            document_id=source.document_id if source else None,
            mode=mode,
            policy_version=app.policy.policy_version,
            lifecycle="failed",
        )
    result = RunResult(
        run_id=run_id,
        workspace_id=workspace_id,
        lifecycle=RunLifecycle.FAILED,
        terminal_reason=f"{failure['message']} ({failure['code']})",
        document_id=source.document_id if source else None,
        filename=source.filename if source else None,
        mode=mode,
        policy_version=app.policy.policy_version,
    )
    update_run(
        app.db,
        run_id,
        lifecycle="failed",
        terminal_reason=result.terminal_reason,
        result_json=result.model_dump_json(),
        finished_at=app.db.now(),
    )
    EventRecorder(app.db, run_id)("error", "run_not_started", failure)
    return result


def _provider_for(app: App, mode: str) -> Provider:
    if mode == "live":
        app.check_live_allowed()
    return app.provider_factory(mode)


# ---------------------------------------------------------------------------------- one invoice


def execute_run(
    app: App,
    *,
    workspace_id: str,
    source: SourceDocument,
    mode: str,
    run_id: str | None = None,
    today: date | None = None,
    cancel: threading.Event | None = None,
    hold_reason: str | None = None,
    attempt_of: str | None = None,
    prepared_draft: InvoiceDraft | None = None,
    prepared_source_hash: str | None = None,
) -> RunResult:
    """Run the full workflow for one stored document and persist the result.

    ``run_id`` may name a run row created earlier by the API (state ``queued``); otherwise a new
    row is created. Any failure is recorded on the run. Nothing is paid unless every gate passes.
    """
    if prepared_draft is not None and prepared_source_hash != source.byte_hash:
        exc = SourceError(
            "prepared_source_mismatch", "The prepared reading belongs to a different source."
        )
        if run_id is not None:
            return _finish_failed(app, workspace_id, run_id, source, mode, exc)
        return _fail_before_start(app, workspace_id, source, mode, exc)
    try:
        provider = _provider_for(app, mode)
    except (ProviderError, ValueError) as exc:
        if run_id is not None:
            return _finish_failed(app, workspace_id, run_id, source, mode, exc)
        return _fail_before_start(app, workspace_id, source, mode, exc)

    if run_id is None:
        run_id = _start_run(app, workspace_id, source, mode, provider, attempt_of)
    else:
        update_run(
            app.db,
            run_id,
            lifecycle="running",
            mode=mode,
            provider=provider.name,
            model=provider.model,
            policy_version=app.policy.policy_version,
        )
    env = _make_env(
        app,
        workspace_id=workspace_id,
        run_id=run_id,
        source=source,
        mode=mode,
        provider=provider,
        today=today,
        cancel=cancel,
        hold_reason=hold_reason,
        attempt_of=attempt_of,
    )
    env.emit(
        "intake",
        "run_started",
        {
            "mode": mode,
            "provider": provider.name,
            "model": provider.model,
            "simulated": provider.simulated,
            "limits": {
                "max_requests": env.budget.max_requests,
                "timeout_seconds": env.budget.timeout_seconds,
            },
        },
    )
    try:
        initial: Flow = {}
        if prepared_draft is not None:
            initial = {"draft": prepared_draft, "prepared_source_hash": prepared_source_hash}
        state: Flow = build_prepare_graph(env).invoke(initial)
        if not state.get("failure"):
            state = build_decide_graph(env).invoke(state)
        result = build_result(env, state, attempt_of)
        if result.outcome is Outcome.NEEDS_REVIEW and result.version_id:
            # Semantic duplicates can share a version while differing in revision metadata.
            # A review must display and hash the actual stored version, not the new claim.
            with app.db.transaction() as conn:
                current = load_version_state(
                    conn, workspace_id, result.version_id, app.policy, env.today
                )
                registration = state.get("registration")
                review_findings = [
                    *current.report.findings,
                    *(registration.findings if registration else []),
                ]
                save_findings(conn, workspace_id, result.run_id, result.version_id, review_findings)
            result = result.model_copy(
                update={
                    "draft": current.draft,
                    "invoice": current.report.normalized,
                    "findings": review_findings,
                    "requires_scrutiny": current.report.requires_scrutiny,
                    "content_hash": content_hash(current.report.normalized)
                    if current.report.normalized
                    else None,
                }
            )
    except Exception as exc:  # noqa: BLE001 - the worker must survive and the run must say why
        failure = failure_from(exc)
        env.emit("error", "run_crashed", failure)
        result = build_result(env, {"failure": failure})
    finally:
        provider.close()
    return persist_result(env, result)


def _finish_failed(
    app: App,
    workspace_id: str,
    run_id: str,
    source: SourceDocument | None,
    mode: str,
    exc: BaseException,
) -> RunResult:
    failure = failure_from(exc)
    result = RunResult(
        run_id=run_id,
        workspace_id=workspace_id,
        lifecycle=RunLifecycle.FAILED,
        terminal_reason=f"{failure['message']} ({failure['code']})",
        document_id=source.document_id if source else None,
        filename=source.filename if source else None,
        mode=mode,
        policy_version=app.policy.policy_version,
    )
    update_run(
        app.db,
        run_id,
        lifecycle="failed",
        terminal_reason=result.terminal_reason,
        result_json=result.model_dump_json(),
        finished_at=app.db.now(),
    )
    EventRecorder(app.db, run_id)("error", "run_not_started", failure)
    return result


def process_invoice(
    path: str | Path,
    *,
    app: App | None = None,
    mode: str | None = None,
    workspace_id: str = LOCAL_WORKSPACE,
    today: date | None = None,
    cancel: threading.Event | None = None,
) -> RunResult:
    """The CLI's entry point: read a trusted local file, run the whole workflow."""
    app = app or App.create()
    mode = mode or app.config.default_mode
    try:
        parsed = load_source(path, app.policy)
    except SourceError as exc:
        return _fail_before_start(app, workspace_id, None, mode, exc)
    return execute_run(
        app, workspace_id=workspace_id, source=parsed.source, mode=mode, today=today, cancel=cancel
    )


# ---------------------------------------------------------------------------------- batch


@dataclass
class BatchFile:
    name: str
    source: SourceDocument | None = None
    draft: InvoiceDraft | None = None
    error: str | None = None
    extraction_run: str | None = None
    report: ValidationReport | None = None


def process_batch(
    paths: list[Path],
    *,
    app: App | None = None,
    mode: str | None = None,
    workspace_id: str = LOCAL_WORKSPACE,
    today: date | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    """Prepare every file, plan versions, and only then decide.

    The identity barrier (shared contract rule 8): if a file could not be read, any other file
    that might be a version of the same invoice is held for review instead of being paid.
    Returns a plain dict the CLI and API turn into their own report formats.
    """
    app = app or App.create()
    mode = mode or app.config.default_mode
    files: list[BatchFile] = []
    for path in sorted(paths, key=lambda p: p.name):
        entry = BatchFile(name=path.name)
        try:
            parsed = load_source(path, app.policy)
            entry.source, entry.draft = parsed.source, parsed.draft
        except SourceError as exc:
            entry.error = f"{exc.code}: {exc.message}"
        files.append(entry)

    # Phase 1: read unstructured files with the model (extraction only, nothing is decided).
    provider = None
    for entry in files:
        if entry.source is None or entry.draft is not None:
            continue
        try:
            provider = provider or _provider_for(app, mode)
        except (ProviderError, ValueError) as exc:
            entry.error = f"{failure_from(exc)['code']}: {failure_from(exc)['message']}"
            continue
        entry.draft, entry.error = _extract_only(
            app, workspace_id, entry, mode, provider, today, cancel
        )
    if provider is not None:
        provider.close()

    ready = [f for f in files if f.draft is not None]
    with app.db.read() as conn:
        index = load_inventory_index(conn)
    entries = []
    for f in ready:
        f.report = validate_invoice(
            f.draft, f.source.text, index, app.policy, today or app.db.clock().date()
        )
        unsupported_revision = any(
            finding.code is FindingCode.VALUE_NOT_IN_SOURCE and finding.field == "revision"
            for finding in f.report.findings
        )
        # An unsupported claim is unorderable, rather than an original (revision rank 0).
        planning_draft = (
            f.draft.model_copy(update={"revision": "unverified"})
            if unsupported_revision
            else f.draft
        )
        entries.append(BatchEntry(f.name, planning_draft))
    plan = plan_batch(entries, app.policy)
    unresolved = [f for f in files if f.draft is None]

    # Make every prepared version visible to the payment authority before any decision.
    # Registering only a winning file would let later submissions forget batch conflicts.
    with app.db.transaction() as conn:
        ensure_workspace(
            conn,
            workspace_id,
            "local" if workspace_id == LOCAL_WORKSPACE else "session",
            app.db.now(),
        )
        for f in ready:
            register_version(
                conn,
                workspace_id=workspace_id,
                source=f.source,
                draft=f.draft,
                report=f.report,
                policy=app.policy,
                now=app.db.now(),
            )

    results: dict[str, dict] = {}
    for f in files:
        if f.draft is None:
            results[f.name] = {
                "file": f.name,
                "kind": "unreadable",
                "outcome": None,
                "run_id": f.extraction_run,
                "reason": f.error or "Could not be read.",
            }

    # Phase 2: decide, in an order that cannot change which version is payable.
    for f in ready:
        disposition = plan[f.name]
        row = {
            "file": f.name,
            "kind": disposition.kind.value,
            "related_file": disposition.related_file,
            "reason": disposition.reason,
        }
        hold = None
        if disposition.kind is BatchKind.PROCESS and unresolved:
            from .identity import payable_key

            key = payable_key(f.draft)
            # An unreadable sibling could be a newer revision of this payable: hold it.
            if any(_could_be_related(u, f, key) for u in unresolved):
                hold = (
                    "Another file in this batch could not be read and may be a revision "
                    "of this invoice. A person must decide."
                )
        if disposition.kind in (BatchKind.DUPLICATE, BatchKind.SUPERSEDED, BatchKind.CONFLICT):
            outcome = {
                BatchKind.DUPLICATE: Outcome.DUPLICATE,
                BatchKind.SUPERSEDED: Outcome.SUPERSEDED,
                BatchKind.CONFLICT: Outcome.NEEDS_REVIEW,
            }[disposition.kind]
            result = _record_disposition_run(
                app, workspace_id, f, mode, outcome, disposition.reason
            )
        else:
            result = execute_run(
                app,
                workspace_id=workspace_id,
                source=f.source,
                mode=mode,
                today=today,
                cancel=cancel,
                hold_reason=hold,
                prepared_draft=f.draft,
                prepared_source_hash=f.source.byte_hash,
            )
        row.update(
            {
                "outcome": result.outcome.value if result.outcome else None,
                "run_id": result.run_id,
                "lifecycle": result.lifecycle.value,
                "amount": str(result.receipt.amount) if result.receipt else None,
                "currency": result.receipt.currency if result.receipt else None,
            }
        )
        results[f.name] = row
    return summarize_batch(results)


def _could_be_related(unreadable: BatchFile, f: BatchFile, key) -> bool:
    """Conservative: we cannot see inside an unreadable file, so any of them might be."""
    return True


def _extract_only(app, workspace_id, entry, mode, provider, today, cancel):
    """Run just the extraction stage for a batch file. Returns ``(draft, error)``."""
    run_id = _start_run(app, workspace_id, entry.source, mode, provider)
    entry.extraction_run = run_id
    env = _make_env(
        app,
        workspace_id=workspace_id,
        run_id=run_id,
        source=entry.source,
        mode=mode,
        provider=provider,
        today=today,
        cancel=cancel,
    )
    env.emit(
        "intake",
        "run_started",
        {
            "mode": mode,
            "provider": provider.name,
            "model": provider.model,
            "simulated": provider.simulated,
            "stage": "batch_extraction",
        },
    )
    state = build_prepare_graph(env).invoke({})
    failure = state.get("failure")
    if failure:
        result = build_result(env, state)
        persist_result(env, result)
        return None, f"{failure['code']}: {failure['message']}"
    # Keep the extraction's usage and events as a completed audit run. The decision run
    # reuses this draft and validates it again without another extraction request.
    update_run(
        app.db,
        run_id,
        lifecycle="completed",
        outcome=None,
        terminal_reason="Extraction completed as part of a batch.",
        usage_json=json.dumps(env.budget.snapshot()),
        finished_at=app.db.now(),
    )
    env.emit("completion", "extraction_only_finished", {"usage": env.budget.snapshot()})
    return state["draft"], None


def _record_disposition_run(app, workspace_id, f, mode, outcome, reason) -> RunResult:
    """A duplicate, superseded or conflicting file gets a run record, but no model calls."""
    with app.db.transaction() as conn:
        ensure_workspace(
            conn,
            workspace_id,
            "local" if workspace_id == LOCAL_WORKSPACE else "session",
            app.db.now(),
        )
        store_document(conn, workspace_id, f.source, app.db.now())
        registration = register_version(
            conn,
            workspace_id=workspace_id,
            source=f.source,
            draft=f.draft,
            report=f.report,
            policy=app.policy,
            now=app.db.now(),
        )
        findings = [*f.report.findings, *registration.findings]
        run_id = create_run(
            conn,
            workspace_id=workspace_id,
            now=app.db.now(),
            document_id=f.source.document_id,
            mode=mode,
            policy_version=app.policy.policy_version,
            lifecycle="completed",
        )
        conn.execute(
            "UPDATE runs SET version_id = ? WHERE run_id = ?",
            (registration.version_id, run_id),
        )
        save_findings(conn, workspace_id, run_id, registration.version_id, findings)
    result = RunResult(
        run_id=run_id,
        workspace_id=workspace_id,
        lifecycle=RunLifecycle.COMPLETED,
        outcome=outcome,
        terminal_reason=reason,
        document_id=f.source.document_id,
        filename=f.source.filename,
        version_id=registration.version_id,
        payable_id=registration.payable_id,
        draft=f.draft,
        invoice=f.report.normalized,
        findings=findings,
        requires_scrutiny=f.report.requires_scrutiny,
        content_hash=content_hash(f.report.normalized) if f.report.normalized else None,
        mode=mode,
        policy_version=app.policy.policy_version,
    )
    update_run(
        app.db,
        run_id,
        lifecycle="completed",
        outcome=outcome.value,
        terminal_reason=reason,
        result_json=result.model_dump_json(),
        finished_at=app.db.now(),
    )
    EventRecorder(app.db, run_id)(
        "completion", "batch_disposition", {"outcome": outcome.value, "reason": reason}
    )
    return result


def summarize_batch(results: dict[str, dict]) -> dict:
    """Counts by outcome and amounts per currency. Currencies are never added together, and
    each payable is counted once (a duplicate or superseded file contributes nothing)."""
    from decimal import Decimal

    counts: dict[str, int] = {}
    paid: dict[str, Decimal] = {}
    for row in results.values():
        label = row["outcome"] or row["kind"]
        counts[label] = counts.get(label, 0) + 1
        if row["outcome"] == "paid" and row.get("amount"):
            paid[row["currency"]] = paid.get(row["currency"], Decimal(0)) + Decimal(row["amount"])
    return {
        "files": [results[k] for k in sorted(results)],
        "counts": counts,
        "paid_by_currency": {c: f"{a:.2f}" for c, a in sorted(paid.items())},
    }


# ---------------------------------------------------------------------------------- human review


@dataclass
class ReviewResult:
    accepted: bool
    message: str
    run: RunResult | None = None
    code: str | None = None


def submit_review(
    app: App,
    *,
    workspace_id: str,
    run_id: str,
    version_id: str,
    content_hash_seen: str,
    decision: str,
    comment: str,
    resolved_codes: list[str],
    actor: str = "simulated_reviewer",
) -> ReviewResult:
    """A simulated person decides on a held invoice.

    The request is a claim, not an authorization. The server reloads the run, checks it belongs
    to this workspace and is a hold, verifies the reviewer saw the current content, records the
    decision through the same approval rules, and only then asks the payment gate to pay.
    """
    with app.db.read() as conn:
        row = conn.execute(
            "SELECT result_json, outcome, version_id FROM runs"
            " WHERE workspace_id = ? AND run_id = ?",
            (workspace_id, run_id),
        ).fetchone()
    if row is None:
        return ReviewResult(False, "No such run.", code="not_found")
    if row["outcome"] != Outcome.NEEDS_REVIEW.value:
        return ReviewResult(False, "This run is not waiting for review.", code="not_reviewable")
    if row["version_id"] != version_id:
        return ReviewResult(
            False, "That is not the version this run is about.", code="stale_decision"
        )

    emit = EventRecorder(app.db, run_id)
    emit(
        "approval",
        "review_submitted",
        {
            "decision": decision,
            "resolved_codes": resolved_codes,
            "comment_chars": len(comment),
            "simulated_reviewer": True,
        },
    )
    policy: Policy = app.policy
    try:
        if decision == "reject":
            record_approval(
                app.db,
                policy,
                workspace_id=workspace_id,
                version_id=version_id,
                stage=ApprovalStage.REVIEW,
                action=ApprovalAction.REJECT,
                rationale=comment,
                actor=actor,
                run_id=run_id,
                expected_content_hash=content_hash_seen,
            )
            return _finish_review(
                app,
                workspace_id,
                run_id,
                Outcome.REJECTED,
                f"Rejected by the reviewer: {comment}",
                None,
            )
        scrutiny = _requires_scrutiny(app, workspace_id, version_id)
        approval = record_approval(
            app.db,
            policy,
            workspace_id=workspace_id,
            version_id=version_id,
            stage=ApprovalStage.REVIEW,
            action=ApprovalAction.APPROVE,
            rationale=comment,
            actor=actor,
            scrutiny_completed=scrutiny,
            resolved_codes=resolved_codes,
            run_id=run_id,
            expected_content_hash=content_hash_seen,
        )
        emit("payment", "payment_attempt", {"version_id": version_id, "after_review": True})
        receipt = execute_mock_payment(
            app.db,
            policy,
            workspace_id=workspace_id,
            version_id=version_id,
            approval_id=approval.approval_id,
            mock_payment=app.mock_payment,
        )
    except LedgerError as exc:
        emit("approval", "review_refused", {"code": exc.code, "message": exc.message})
        return ReviewResult(False, exc.message, code=exc.code)
    emit(
        "payment",
        "payment_made",
        {
            "payment_id": receipt.payment_id,
            "amount": str(receipt.amount),
            "currency": receipt.currency,
            "after_review": True,
            "simulated": True,
        },
    )
    return _finish_review(
        app, workspace_id, run_id, Outcome.PAID, f"Approved by the reviewer: {comment}", receipt
    )


def _requires_scrutiny(app: App, workspace_id: str, version_id: str) -> bool:
    with app.db.read() as conn:
        state = load_version_state(
            conn, workspace_id, version_id, app.policy, app.db.clock().date()
        )
    return state.report.requires_scrutiny


def _finish_review(
    app: App, workspace_id: str, run_id: str, outcome: Outcome, reason: str, receipt
) -> ReviewResult:
    with app.db.read() as conn:
        row = conn.execute(
            "SELECT result_json FROM runs WHERE workspace_id = ? AND run_id = ?",
            (workspace_id, run_id),
        ).fetchone()
    result = RunResult.model_validate_json(row["result_json"])
    with app.db.read() as conn:
        from .ledger import list_approvals

        approvals = (
            list_approvals(conn, workspace_id, result.version_id) if result.version_id else []
        )
    result = result.model_copy(
        update={
            "outcome": outcome,
            "terminal_reason": reason,
            "receipt": receipt,
            "approval": approvals,
        }
    )
    update_run(
        app.db,
        run_id,
        outcome=outcome.value,
        terminal_reason=reason,
        result_json=result.model_dump_json(),
    )
    EventRecorder(app.db, run_id)(
        "completion", "review_applied", {"outcome": outcome.value, "reason": reason}
    )
    return ReviewResult(True, reason, result)


# ---------------------------------------------------------------------------------- Part 01 API


@dataclass
class CheckResult:
    parsed: ParsedSource
    draft: InvoiceDraft | None
    report: ValidationReport | None
    registration: Registration | None
    route: Route | None
    needs_extraction: bool


def run_checks(
    path: str | Path,
    *,
    db: Database,
    policy: Policy | None = None,
    workspace_id: str = LOCAL_WORKSPACE,
    today: date | None = None,
    draft: InvoiceDraft | None = None,
    register: bool = True,
) -> CheckResult:
    """Deterministic checks only. Parses, validates and (optionally) registers one file.

    TXT and PDF files need an extraction step first (the model workflow, or a caller-supplied
    ``draft`` in tests). Without one this returns ``needs_extraction=True``. Never approves or pays.
    """
    policy = policy or load_policy()
    parsed = load_source(path, policy)
    draft = draft or parsed.draft
    if draft is None:
        return CheckResult(parsed, None, None, None, None, needs_extraction=True)
    init_database(db)
    when = today or db.clock().date()
    with db.transaction() as conn:
        index = load_inventory_index(conn)
        report = validate_invoice(draft, parsed.source.text, index, policy, when)
        registration = route = None
        if register:
            registration = register_version(
                conn,
                workspace_id=workspace_id,
                source=parsed.source,
                draft=draft,
                report=report,
                policy=policy,
                now=db.now(),
            )
            route = route_after_validation(registration, report)
    return CheckResult(parsed, draft, report, registration, route, needs_extraction=False)


__all__ = [
    "CheckResult",
    "ReviewResult",
    "RunBudget",
    "WorkflowNotAvailable",
    "execute_run",
    "process_batch",
    "process_invoice",
    "run_checks",
    "submit_review",
    "summarize_batch",
]
