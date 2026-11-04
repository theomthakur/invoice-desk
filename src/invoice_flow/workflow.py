"""The invoice workflow, as two LangGraph graphs.

``prepare``  intake -> extract (text/PDF only) -> validate
``decide``   register -> catalog -> review -> gate -> finish

They are separate so a batch can prepare every file, plan versions, and only then decide
(shared contract rule 8). A single invoice simply runs one after the other.

Safety properties, all enforced in code rather than asked of the model:
* rejections and holds are decided by the rules and cost no model request;
* the model proposes and critiques; ``ledger.record_approval`` and ``ledger.execute_mock_payment``
  re-check everything before any payment;
* any failure is recorded and visible; there is no fallback to another model or to mock mode;
* a run that fails or is cancelled never produces an approval or a payment.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .agents.ingestion import ExtractionResult, extract_invoice
from .agents.prompts import approval_context
from .agents.review import ReviewOutcome, run_review
from .agents.validation import CatalogEvidence, gather_catalog_evidence
from .budget import BudgetExceeded, RunBudget, RunCancelled
from .findings import Finding, Severity
from .identity import content_hash
from .ledger import (
    Disposition,
    LedgerError,
    Registration,
    Route,
    execute_mock_payment,
    flag_version,
    list_approvals,
    record_approval,
    register_version,
    route_after_validation,
)
from .llm import ModelClient, StructuredOutputError
from .parsers import SourceError, draft_from_source
from .providers.base import Provider, ProviderError, redact
from .rules import validate_invoice
from .runtime import App, EventRecorder
from .schemas import (
    ApprovalAction,
    ApprovalStage,
    InvoiceDraft,
    Outcome,
    PaymentReceipt,
    RunLifecycle,
    RunResult,
    SourceDocument,
    Usage,
    ValidationReport,
)
from .storage import load_inventory_index, save_findings, update_run, utc_now
from .tools.registry import ToolContext, call_tool

ACTOR = "simulated_vp"


class Flow(TypedDict, total=False):
    draft: InvoiceDraft | None
    prepared_source_hash: str
    extraction: ExtractionResult | None
    report: ValidationReport | None
    registration: Registration | None
    route: Route | None
    catalog: CatalogEvidence | None
    review: ReviewOutcome | None
    outcome: Outcome | None
    reason: str
    receipt: PaymentReceipt | None
    failure: dict[str, Any] | None
    phase: str


@dataclass
class RunEnv:
    app: App
    workspace_id: str
    run_id: str
    source: SourceDocument
    mode: str
    provider: Provider
    budget: RunBudget
    emit: EventRecorder
    client: ModelClient
    tool_ctx: ToolContext
    today: date
    hold_reason: str | None = None  # batch: stop before approval and hold for a person
    attempt_of: str | None = None


# ---------------------------------------------------------------------------------- failures


def failure_from(exc: BaseException) -> dict[str, Any]:
    """Map an exception to a stored failure. Messages are scrubbed of anything key-shaped."""
    failure = _failure_from(exc)
    failure["message"] = redact(str(failure["message"]))
    return failure


def _failure_from(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, RunCancelled):
        return {"kind": "cancelled", "code": "cancelled", "message": "The run was cancelled."}
    if isinstance(exc, BudgetExceeded):
        return {"kind": "budget", "code": f"budget_{exc.kind}", "message": exc.message}
    if isinstance(exc, ProviderError):
        return {"kind": "technical", "code": exc.code, "message": exc.message}
    if isinstance(exc, StructuredOutputError):
        return {
            "kind": "technical",
            "code": "bad_model_output",
            "message": f"The model did not return a usable answer for {exc.label}.",
        }
    if isinstance(exc, SourceError):
        return {"kind": "technical", "code": exc.code, "message": exc.message}
    if isinstance(exc, LedgerError):
        return {"kind": "technical", "code": exc.code, "message": exc.message}
    return {
        "kind": "technical",
        "code": "internal_error",
        "message": f"An unexpected error occurred ({type(exc).__name__}).",
    }


_CAUGHT = (
    RunCancelled,
    BudgetExceeded,
    ProviderError,
    StructuredOutputError,
    SourceError,
    LedgerError,
)


def _guard(env: RunEnv, name: str, fn):
    """Run a node. Known failures are recorded in the state; unknown ones too, but generic."""

    def node(state: Flow) -> Flow:
        if state.get("failure"):
            return {}
        try:
            return fn(state)
        except _CAUGHT as exc:
            failure = failure_from(exc)
        except Exception as exc:  # noqa: BLE001 - a bug must end the run visibly, not crash the worker
            failure = failure_from(exc)
        failure["node"] = name
        env.emit("error", "node_failed", {"node": name, **failure})
        return {"failure": failure}

    return node


def _counts(findings: list[Finding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out[f.severity.value] = out.get(f.severity.value, 0) + 1
    return out


# ---------------------------------------------------------------------------------- prepare


def _stored_draft(env: RunEnv) -> InvoiceDraft | None:
    with env.app.db.read() as conn:
        row = conn.execute(
            "SELECT draft_json FROM invoice_versions WHERE workspace_id = ? AND byte_hash = ?"
            " ORDER BY created_at LIMIT 1",
            (env.workspace_id, env.source.byte_hash),
        ).fetchone()
    return InvoiceDraft.model_validate_json(row["draft_json"]) if row else None


def build_prepare_graph(env: RunEnv):
    def intake(state: Flow) -> Flow:
        s = env.source
        env.emit(
            "intake",
            "document_received",
            {
                "filename": s.filename,
                "format": s.format.value,
                "size_bytes": s.size_bytes,
                "hash": s.byte_hash[:16],
                "pages": len(s.pages) or None,
            },
        )
        if state.get("draft") is not None and state.get("prepared_source_hash"):
            if state["prepared_source_hash"] != s.byte_hash:
                raise SourceError(
                    "prepared_source_mismatch", "Prepared reading is for another file."
                )
            env.emit(
                "intake",
                "reused_prepared_extraction",
                {"reason": "batch reading bound to this document", "model_used": False},
            )
            return {"draft": state["draft"]}
        draft = draft_from_source(s, env.app.policy)
        if draft is not None:
            env.emit(
                "intake",
                "parsed_structured",
                {
                    "line_items": len(draft.line_items),
                    "parse_findings": len(draft.parse_findings),
                    "model_used": False,
                },
            )
            return {"draft": draft}
        # A byte-identical file already read in this workspace needs no second model request.
        # The stored reading is checked against this exact text again during validation.
        stored = _stored_draft(env)
        if stored is not None:
            env.emit(
                "intake",
                "reused_stored_extraction",
                {"reason": "identical file already read in this workspace", "model_used": False},
            )
        return {"draft": stored}

    def extract(state: Flow) -> Flow:
        result = extract_invoice(env.client, env.source, env.app.policy)
        env.emit(
            "extraction",
            "extracted",
            {
                "line_items": len(result.draft.line_items),
                "reflected": result.reflected,
                "adopted_reread": result.adopted_reread,
                "inconsistency": result.inconsistency,
                "corrections": len(result.draft.corrections),
            },
        )
        return {"draft": result.draft, "extraction": result}

    def validate(state: Flow) -> Flow:
        with env.app.db.read() as conn:
            index = load_inventory_index(conn)
        report = validate_invoice(state["draft"], env.source.text, index, env.app.policy, env.today)
        env.emit(
            "validation",
            "validated",
            {
                "eligibility": report.eligibility.value,
                "counts": _counts(report.findings),
                "codes": sorted(
                    {f.code.value for f in report.findings if f.severity.value != "INFO"}
                ),
                "requires_scrutiny": report.requires_scrutiny,
            },
        )
        return {"report": report}

    def needs_extraction(state: Flow) -> str:
        if state.get("failure"):
            return "end"
        return "validate" if state.get("draft") is not None else "extract"

    def after(state: Flow) -> str:
        return "end" if state.get("failure") else "next"

    graph = StateGraph(Flow)
    graph.add_node("intake", _guard(env, "intake", intake))
    graph.add_node("extract", _guard(env, "extract", extract))
    graph.add_node("validate", _guard(env, "validate", validate))
    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake", needs_extraction, {"extract": "extract", "validate": "validate", "end": END}
    )
    graph.add_conditional_edges("extract", after, {"next": "validate", "end": END})
    graph.add_edge("validate", END)
    return graph.compile()


# ---------------------------------------------------------------------------------- decide


def _active_elsewhere(conn, env: RunEnv, version_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM runs WHERE workspace_id = ? AND version_id = ? AND run_id != ?"
        " AND lifecycle IN ('queued','running')",
        (env.workspace_id, version_id, env.run_id),
    ).fetchone()
    return row is not None


def build_decide_graph(env: RunEnv):
    policy = env.app.policy

    def register(state: Flow) -> Flow:
        report, draft = state["report"], state["draft"]
        with env.app.db.transaction() as conn:
            reg = register_version(
                conn,
                workspace_id=env.workspace_id,
                source=env.source,
                draft=draft,
                report=report,
                policy=policy,
                now=env.app.db.now(),
            )
            resumed = False
            if (
                reg.disposition is Disposition.DUPLICATE
                and reg.original_outcome is None
                and not _active_elsewhere(conn, env, reg.version_id)
            ):
                # The same content was registered before but never reached an outcome (an
                # earlier attempt failed or was interrupted). Resume it: this is a retry, not
                # a second submission.
                reg = dataclasses.replace(reg, disposition=Disposition.NEW, findings=[])
                resumed = True
            route = route_after_validation(reg, report)
            hold_reason = env.hold_reason
            link = conn.execute(
                "SELECT l.kind, v.payable_id FROM run_links l"
                " JOIN runs r ON r.run_id=l.parent_run_id AND r.workspace_id=l.workspace_id"
                " LEFT JOIN invoice_versions v ON v.version_id=r.version_id"
                " AND v.workspace_id=r.workspace_id"
                " WHERE l.workspace_id=? AND l.child_run_id=?",
                (env.workspace_id, env.run_id),
            ).fetchone()
            is_correction = bool(link and link["kind"] == "correction")
            if is_correction and link["payable_id"] and link["payable_id"] == reg.payable_id:
                hold_reason = None
            if hold_reason and route.outcome is None:
                route = Route(Outcome.NEEDS_REVIEW, hold_reason, route.findings)
                flag_version(
                    conn,
                    env.workspace_id,
                    reg.version_id,
                    "correction_identity" if is_correction else "batch_hold",
                )
            save_findings(conn, env.workspace_id, env.run_id, reg.version_id, route.findings)
            conn.execute(
                "UPDATE runs SET version_id = ?, document_id = ? WHERE run_id = ?",
                (reg.version_id, env.source.document_id, env.run_id),
            )
        env.emit(
            "validation",
            "registered",
            {
                "disposition": reg.disposition.value,
                "resumed_earlier_attempt": resumed,
                "payable_id": reg.payable_id,
                "version_id": reg.version_id,
                "route": route.outcome.value if route.outcome else "continue_to_approval",
                "reason": route.reason,
            },
        )
        update: Flow = {"registration": reg, "route": route}
        if route.outcome is not None:
            update["outcome"], update["reason"] = route.outcome, route.reason
        return update

    def after_register(state: Flow) -> str:
        if state.get("failure") or state.get("outcome") is not None:
            return "finish"
        if state["report"].normalized is None:
            return "unnormalized"
        return "catalog"

    def unnormalized(state: Flow) -> Flow:
        return {
            "outcome": Outcome.REJECTED,
            "reason": "The invoice could not be put into a checked form.",
        }

    def catalog(state: Flow) -> Flow:
        return {"catalog": gather_catalog_evidence(env.client, env.tool_ctx, state["draft"])}

    def review(state: Flow) -> Flow:
        report, route, reg = state["report"], state["route"], state["registration"]
        policy_summary = call_tool(env.tool_ctx, "get_approval_policy", {})
        history = (
            call_tool(env.tool_ctx, "get_invoice_history", {"payable_id": reg.payable_id})
            if reg.payable_id
            else None
        )
        env.emit(
            "approval",
            "context_loaded",
            {
                "findings": len(route.findings),
                "requires_scrutiny": report.requires_scrutiny,
                "catalog_lookups": len(state["catalog"].lookups) if state.get("catalog") else 0,
            },
        )
        context = approval_context(
            report.normalized,
            route.findings,
            report.requires_scrutiny,
            policy_summary,
            state["catalog"].lookups if state.get("catalog") else [],
            history,
        )
        outcome = run_review(
            env.client, context, report.normalized, route.findings, report.requires_scrutiny
        )
        return {"review": outcome}

    def gate(state: Flow) -> Flow:
        env.budget.check_active()
        review_out, reg = state["review"], state["registration"]
        db = env.app.db

        def audit(
            stage: ApprovalStage, action: ApprovalAction, text: str, scrutiny: bool
        ) -> str | None:
            try:
                record = record_approval(
                    db,
                    policy,
                    workspace_id=env.workspace_id,
                    version_id=reg.version_id,
                    stage=stage,
                    action=action,
                    rationale=text or "(no rationale)",
                    actor=ACTOR,
                    scrutiny_completed=scrutiny,
                    run_id=env.run_id,
                )
                return record.approval_id
            except LedgerError as exc:
                env.emit("approval", "audit_skipped", {"stage": stage.value, "code": exc.code})
                return None

        if review_out.proposal is not None:
            audit(
                ApprovalStage.APPROVAL,
                ApprovalAction(review_out.proposal.action),
                review_out.proposal.rationale,
                bool(review_out.proposal.scrutiny_checks),
            )
        if review_out.critiques:
            last = review_out.critiques[-1]
            audit(
                ApprovalStage.CRITIQUE,
                ApprovalAction(last.suggested_action),
                "; ".join(last.issues) or "The critic agrees.",
                False,
            )

        action = review_out.action
        if action is ApprovalAction.APPROVE:
            try:
                final = record_approval(
                    db,
                    policy,
                    workspace_id=env.workspace_id,
                    version_id=reg.version_id,
                    stage=ApprovalStage.FINAL,
                    action=ApprovalAction.APPROVE,
                    rationale=review_out.rationale,
                    actor=ACTOR,
                    scrutiny_completed=review_out.scrutiny_completed,
                    run_id=env.run_id,
                )
            except LedgerError as exc:
                env.emit("approval", "gate_refused", {"code": exc.code, "message": exc.message})
                return {
                    "outcome": Outcome.NEEDS_REVIEW,
                    "reason": f"The approval gate refused the decision ({exc.code}).",
                }
            env.emit(
                "approval",
                "final_decision",
                {
                    "action": "approve",
                    "approval_id": final.approval_id,
                    "scrutiny_completed": final.scrutiny_completed,
                },
            )
            env.emit("payment", "payment_attempt", {"version_id": reg.version_id})
            env.budget.check_active()
            try:
                receipt = execute_mock_payment(
                    db,
                    policy,
                    workspace_id=env.workspace_id,
                    version_id=reg.version_id,
                    approval_id=final.approval_id,
                    mock_payment=env.app.mock_payment,
                    check_active=env.budget.check_active,
                )
            except LedgerError as exc:
                env.emit("payment", "payment_refused", {"code": exc.code, "message": exc.message})
                return {
                    "outcome": Outcome.NEEDS_REVIEW,
                    "reason": f"The payment gate refused payment ({exc.code}): {exc.message}",
                }
            env.emit(
                "payment",
                "payment_made",
                {
                    "payment_id": receipt.payment_id,
                    "transaction_id": receipt.transaction_id,
                    "amount": str(receipt.amount),
                    "currency": receipt.currency,
                    "already_paid": receipt.already_paid,
                    "simulated": True,
                },
            )
            return {
                "outcome": Outcome.PAID,
                "receipt": receipt,
                "reason": "Approved by the simulated VP and critic; mock payment made.",
            }

        stage_action = (
            ApprovalAction.REJECT
            if action is ApprovalAction.REJECT
            else ApprovalAction.NEEDS_REVIEW
        )
        audit(ApprovalStage.FINAL, stage_action, review_out.rationale, False)
        env.emit("approval", "final_decision", {"action": stage_action.value})
        if action is ApprovalAction.REJECT:
            return {"outcome": Outcome.REJECTED, "reason": review_out.rationale}
        return {"outcome": Outcome.NEEDS_REVIEW, "reason": review_out.rationale}

    def finish(state: Flow) -> Flow:
        return {}

    def after(name: str):
        def route_fn(state: Flow) -> str:
            return "finish" if state.get("failure") else name

        return route_fn

    graph = StateGraph(Flow)
    for name, fn in (
        ("register", register),
        ("unnormalized", unnormalized),
        ("catalog", catalog),
        ("review", review),
        ("gate", gate),
        ("finish", finish),
    ):
        graph.add_node(name, _guard(env, name, fn))
    graph.add_edge(START, "register")
    graph.add_conditional_edges(
        "register",
        after_register,
        {"finish": "finish", "unnormalized": "unnormalized", "catalog": "catalog"},
    )
    graph.add_edge("unnormalized", "finish")
    graph.add_conditional_edges(
        "catalog", after("review"), {"review": "review", "finish": "finish"}
    )
    graph.add_conditional_edges("review", after("gate"), {"gate": "gate", "finish": "finish"})
    graph.add_edge("gate", "finish")
    graph.add_edge("finish", END)
    return graph.compile()


# ---------------------------------------------------------------------------------- results


def build_result(env: RunEnv, state: Flow, attempt_of: str | None = None) -> RunResult:
    """Turn the final graph state into a stored result. The only place outcomes are finalized."""
    failure = state.get("failure")
    report: ValidationReport | None = state.get("report")
    route: Route | None = state.get("route")
    reg: Registration | None = state.get("registration")
    outcome: Outcome | None = state.get("outcome")
    reason = state.get("reason", "")
    lifecycle = RunLifecycle.COMPLETED

    if failure:
        kind = failure["kind"]
        in_decide = reg is not None and report is not None and report.normalized is not None
        if kind == "cancelled":
            lifecycle, outcome = RunLifecycle.INTERRUPTED, None
            reason = failure["message"]
        elif kind == "budget" and in_decide:
            # The invoice passed the rules but the model budget ran out: hand it to a person.
            outcome = Outcome.NEEDS_REVIEW
            reason = f"{failure['message']} A person must decide."
        else:
            lifecycle, outcome = RunLifecycle.FAILED, None
            reason = f"{failure['message']} ({failure['code']})"

    approvals = []
    if reg is not None:
        with env.app.db.read() as conn:
            approvals = list_approvals(conn, env.workspace_id, reg.version_id)

    review_out: ReviewOutcome | None = state.get("review")
    review_summary = None
    if review_out is not None:
        review_summary = {
            "simulated": True,
            "action": review_out.action.value,
            "rationale": review_out.rationale,
            "revised": review_out.revised,
            "agreed": bool(review_out.critiques and review_out.critiques[-1].agrees),
            "critiques": [c.model_dump() for c in review_out.critiques],
            "proposal": review_out.proposal.model_dump() if review_out.proposal else None,
            "reason": review_out.reason,
        }

    findings = route.findings if route else (report.findings if report else [])
    draft = state.get("draft")
    usage = env.budget.snapshot()
    return RunResult(
        run_id=env.run_id,
        workspace_id=env.workspace_id,
        lifecycle=lifecycle,
        outcome=outcome,
        terminal_reason=reason or None,
        document_id=env.source.document_id,
        filename=env.source.filename,
        payable_id=reg.payable_id if reg else None,
        version_id=reg.version_id if reg else None,
        invoice=report.normalized if report else None,
        draft=draft,
        findings=findings,
        requires_scrutiny=report.requires_scrutiny if report else False,
        approval=approvals,
        receipt=state.get("receipt"),
        mode=env.mode,
        provider=env.provider.name,
        model=env.provider.model,
        usage=Usage(**usage),
        policy_version=env.app.policy.policy_version,
        content_hash=content_hash(report.normalized) if report and report.normalized else None,
        review_summary=review_summary,
        attempt_of=attempt_of or env.attempt_of,
        started_at=None,
        finished_at=utc_now(),
    )


def persist_result(env: RunEnv, result: RunResult) -> RunResult:
    """Write the terminal state of a run, then the completion event."""
    update_run(
        env.app.db,
        env.run_id,
        lifecycle=result.lifecycle.value,
        outcome=result.outcome.value if result.outcome else None,
        terminal_reason=result.terminal_reason,
        version_id=result.version_id,
        document_id=result.document_id,
        provider=result.provider,
        model=result.model,
        usage_json=result.usage.model_dump_json(),
        policy_version=result.policy_version,
        finished_at=result.finished_at.isoformat() if result.finished_at else None,
        result_json=result.model_dump_json(),
    )
    env.emit(
        "completion",
        "run_finished",
        {
            "lifecycle": result.lifecycle.value,
            "outcome": result.outcome.value if result.outcome else None,
            "reason": result.terminal_reason,
            "usage": result.usage.model_dump(),
        },
    )
    return result


def dump_state_for_debug(state: Flow) -> str:  # pragma: no cover - debugging helper
    return json.dumps({k: str(v)[:200] for k, v in state.items()})


__all__ = [
    "Flow",
    "RunEnv",
    "Severity",
    "build_decide_graph",
    "build_prepare_graph",
    "build_result",
    "failure_from",
    "persist_result",
]
