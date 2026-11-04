"""Versions, approvals and the payment gate.

This module is where the money rules are enforced, in SQL transactions:

* one payment per ``(workspace, payable)``, protected by a UNIQUE constraint;
* the payment gate reloads everything it needs from the database and re-runs the rules.
  It never trusts a caller-supplied "approved" flag, amount or findings list;
* an approval binds to the exact invoice content and policy it was made for.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from .config import Policy
from .findings import Finding, FindingCode, Severity, is_reviewer_resolvable, make_finding
from .identity import content_hash, payable_key
from .money import MoneyError, from_minor, to_minor
from .rules import InventoryIndex, validate_invoice
from .schemas import (
    ApprovalAction,
    ApprovalRecord,
    ApprovalStage,
    Eligibility,
    InvoiceDraft,
    Outcome,
    PaymentReceipt,
    RunLifecycle,
    RunResult,
    SourceDocument,
    SourceFormat,
    ValidationReport,
)
from .storage import Database, append_event, load_inventory_index, new_id, parse_iso, save_findings
from .textnorm import revision_rank


class Disposition(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    SUPERSEDES = "supersedes"  # explicit newer revision of an unpaid invoice
    SUPERSEDED = "superseded"  # an equal or newer version is already on file
    REVISION_AFTER_PAYMENT = "revision_after_payment"
    REVISION_CONFLICT = "revision_conflict"


@dataclass
class Registration:
    version_id: str
    payable_id: str | None
    disposition: Disposition
    related_version_id: str | None = None
    original_outcome: str | None = None
    findings: list[Finding] = field(default_factory=list)
    hold_reason: str | None = None


@dataclass(frozen=True)
class Route:
    """Where a run goes after validation and identity checks."""

    outcome: Outcome | None  # ``None`` means: continue to approval
    reason: str
    findings: list[Finding]


def route_after_validation(registration: Registration, report: ValidationReport) -> Route:
    """The one place that turns identity + findings into a business outcome.

    Order matters: a duplicate or superseded document is a disposition of the *submission*;
    the stored reason it was rejected (if any) stays visible through ``original_outcome``.
    """
    d = registration.disposition
    findings = [*report.findings, *registration.findings]
    if d is Disposition.DUPLICATE:
        was = registration.original_outcome
        reason = "Duplicate submission" + (
            f"; the first submission ended as {was}." if was else "."
        )
        return Route(Outcome.DUPLICATE, reason, findings)
    if d is Disposition.SUPERSEDED:
        return Route(
            Outcome.SUPERSEDED, "A newer revision of this invoice is already on file.", findings
        )
    if report.eligibility is Eligibility.REJECTED:
        return Route(Outcome.REJECTED, "Validation found errors that block payment.", findings)
    if d in (Disposition.REVISION_AFTER_PAYMENT, Disposition.REVISION_CONFLICT):
        why = (
            "Revision after payment needs reconciliation."
            if d is Disposition.REVISION_AFTER_PAYMENT
            else registration.hold_reason or "Conflicting versions need a person to decide."
        )
        return Route(Outcome.NEEDS_REVIEW, why, findings)
    if report.eligibility is Eligibility.NEEDS_REVIEW:
        return Route(Outcome.NEEDS_REVIEW, "A finding needs human review before payment.", findings)
    return Route(None, "Eligible for approval.", findings)


class LedgerError(Exception):
    """A rule refused the operation. ``code`` is stable for callers and tests."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------------------


def _store_document(conn: sqlite3.Connection, workspace_id: str, source: SourceDocument, now: str):
    if source.original_pdf is not None and (
        source.format is not SourceFormat.PDF
        or len(source.original_pdf) != source.size_bytes
        or hashlib.sha256(source.original_pdf).hexdigest() != source.byte_hash
    ):
        raise LedgerError("source_changed", "The original PDF does not match this document.")
    conn.execute(
        "INSERT OR IGNORE INTO documents (workspace_id, document_id, filename, format, size_bytes,"
        " byte_hash, source_text, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            workspace_id,
            source.document_id,
            source.filename,
            source.format.value,
            source.size_bytes,
            source.byte_hash,
            source.text,
            now,
        ),
    )
    if source.original_pdf is not None:
        conn.execute(
            "INSERT OR IGNORE INTO document_assets (workspace_id, document_id, original_pdf)"
            " VALUES (?,?,?)",
            (workspace_id, source.document_id, source.original_pdf),
        )


def store_document(
    conn: sqlite3.Connection, workspace_id: str, source: SourceDocument, now: str
) -> None:
    """Save an uploaded or loaded document in its owner's workspace (idempotent)."""
    _store_document(conn, workspace_id, source, now)


def load_document(
    conn: sqlite3.Connection, workspace_id: str, document_id: str
) -> SourceDocument | None:
    """A document the workspace owns, or ``None``. Never returns another workspace's file."""
    row = conn.execute(
        "SELECT d.*, a.original_pdf FROM documents d LEFT JOIN document_assets a"
        " ON a.workspace_id = d.workspace_id AND a.document_id = d.document_id"
        " WHERE d.workspace_id = ? AND d.document_id = ?",
        (workspace_id, document_id),
    ).fetchone()
    if row is None:
        return None
    return SourceDocument(
        document_id=row["document_id"],
        filename=row["filename"],
        format=SourceFormat(row["format"]),
        size_bytes=row["size_bytes"],
        byte_hash=row["byte_hash"],
        text=row["source_text"],
        original_pdf=row["original_pdf"],
    )


def flag_version(conn: sqlite3.Connection, workspace_id: str, version_id: str, flag: str) -> None:
    """Block payment of a stored version until a person resolves ``flag``. A paid version is
    never flagged (the payment already happened)."""
    conn.execute(
        "UPDATE invoice_versions SET identity_flag = ? WHERE workspace_id = ? AND version_id = ?"
        " AND identity_flag IS NULL"
        " AND version_id NOT IN (SELECT version_id FROM payments WHERE workspace_id = ?)",
        (flag, workspace_id, version_id, workspace_id),
    )


def _original_outcome(conn: sqlite3.Connection, workspace_id: str, version_id: str) -> str | None:
    row = conn.execute(
        "SELECT outcome FROM runs WHERE workspace_id = ? AND version_id = ? AND outcome IS NOT NULL"
        " AND outcome NOT IN ('duplicate','superseded') ORDER BY started_at DESC LIMIT 1",
        (workspace_id, version_id),
    ).fetchone()
    return row["outcome"] if row else None


_LEGACY_HOLD_REASON = (
    "Historical held invoices have an unresolved identity. Reprocess the original held "
    "source files before selecting any payable version."
)


def _has_unbound_legacy_holds(conn: sqlite3.Connection, workspace_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM runs WHERE workspace_id = ?"
            " AND outcome = 'needs_review' AND version_id IS NULL LIMIT 1",
            (workspace_id,),
        ).fetchone()
        is not None
    )


def _bind_legacy_holds(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    legacy: list[sqlite3.Row],
    version_id: str,
    payable_id: str | None,
    source: SourceDocument,
    policy: Policy,
    now: str,
) -> None:
    """Bind old unversioned holds only when their exact source is submitted again.

    Historical source text is never extracted here. Without a payable identity, the old
    hold remains unbound and therefore continues to block an unsafe identity assumption.
    """
    matching = [row for row in legacy if row["legacy_byte_hash"] == source.byte_hash]
    if payable_id is None or not matching:
        return
    state = load_version_state(conn, workspace_id, version_id, policy, parse_iso(now).date())
    draft, report = state.draft, state.report
    findings = [
        *report.findings,
        make_finding(
            FindingCode.REVISION_CONFLICT,
            "This historical hold is now bound to a checked version. A person must select "
            "the payable version before payment.",
        ),
    ]
    for row in matching:
        try:
            previous = RunResult.model_validate_json(row["result_json"] or "")
        except ValueError:
            previous = RunResult(
                run_id=row["run_id"],
                workspace_id=workspace_id,
                lifecycle=RunLifecycle.COMPLETED,
                outcome=Outcome.NEEDS_REVIEW,
                document_id=row["document_id"],
                filename=row["legacy_filename"],
                mode=row["mode"],
                provider=row["provider"],
                model=row["model"],
            )
        result = previous.model_copy(
            update={
                "version_id": version_id,
                "payable_id": payable_id,
                "draft": draft,
                "invoice": report.normalized,
                "findings": findings,
                "requires_scrutiny": report.requires_scrutiny,
                "content_hash": content_hash(report.normalized) if report.normalized else None,
                "policy_version": policy.policy_version,
                "terminal_reason": "Historical hold reprocessed; explicit review is required.",
            }
        )
        conn.execute(
            "UPDATE runs SET version_id = ?, policy_version = ?, terminal_reason = ?,"
            " result_json = ? WHERE workspace_id = ? AND run_id = ? AND version_id IS NULL",
            (
                version_id,
                policy.policy_version,
                result.terminal_reason,
                result.model_dump_json(),
                workspace_id,
                row["run_id"],
            ),
        )
        save_findings(conn, workspace_id, row["run_id"], version_id, findings)
        append_event(
            conn,
            row["run_id"],
            "validation",
            "legacy_hold_registered",
            {"version_id": version_id, "policy_version": policy.policy_version},
            now,
        )


def register_version(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    source: SourceDocument,
    draft: InvoiceDraft,
    report: ValidationReport,
    policy: Policy,
    now: str,
) -> Registration:
    """Record a submission and decide how it relates to what is already on file.

    Call inside a write transaction. Identical content is a duplicate and creates no new
    version. A revision of an unpaid invoice supersedes the older versions. A revision of a
    paid invoice, or two different versions with no order between them, are flagged so the
    payment gate refuses them.
    """
    _store_document(conn, workspace_id, source, now)
    fingerprint = report.semantic_fingerprint or ""
    legacy = conn.execute(
        "SELECT r.*, d.byte_hash AS legacy_byte_hash, d.filename AS legacy_filename"
        " FROM runs r LEFT JOIN documents d"
        " ON d.workspace_id = r.workspace_id AND d.document_id = r.document_id"
        " WHERE r.workspace_id = ? AND r.outcome = 'needs_review' AND r.version_id IS NULL",
        (workspace_id,),
    ).fetchall()

    same = conn.execute(
        "SELECT version_id, payable_id, status, identity_flag FROM invoice_versions"
        " WHERE workspace_id = ? AND semantic_fingerprint = ?",
        (workspace_id, fingerprint),
    ).fetchone()
    if same is not None:
        if legacy:
            conn.execute(
                "UPDATE invoice_versions SET identity_flag = 'revision_conflict'"
                " WHERE workspace_id = ? AND payable_id = ? AND status = 'active'"
                " AND identity_flag IS NULL"
                " AND version_id NOT IN (SELECT version_id FROM payments WHERE workspace_id = ?)",
                (workspace_id, same["payable_id"], workspace_id),
            )
            _bind_legacy_holds(
                conn,
                workspace_id=workspace_id,
                legacy=legacy,
                version_id=same["version_id"],
                payable_id=same["payable_id"],
                source=source,
                policy=policy,
                now=now,
            )
            same = conn.execute(
                "SELECT version_id, payable_id, status, identity_flag FROM invoice_versions"
                " WHERE workspace_id = ? AND version_id = ?",
                (workspace_id, same["version_id"]),
            ).fetchone()
        if same["status"] == "active" and same["identity_flag"] == "revision_conflict":
            legacy_reason = (
                _LEGACY_HOLD_REASON if _has_unbound_legacy_holds(conn, workspace_id) else None
            )
            return Registration(
                version_id=same["version_id"],
                payable_id=same["payable_id"],
                disposition=Disposition.REVISION_CONFLICT,
                findings=[
                    make_finding(
                        FindingCode.REVISION_CONFLICT,
                        legacy_reason
                        or "This invoice still has conflicting versions. A person "
                        "must select the payable version before any payment.",
                    )
                ],
                hold_reason=legacy_reason,
            )
        if same["status"] == "superseded":
            return Registration(
                version_id=same["version_id"],
                payable_id=same["payable_id"],
                disposition=Disposition.SUPERSEDED,
            )
        return Registration(
            version_id=same["version_id"],
            payable_id=same["payable_id"],
            disposition=Disposition.DUPLICATE,
            related_version_id=same["version_id"],
            original_outcome=_original_outcome(conn, workspace_id, same["version_id"]),
            findings=[
                make_finding(
                    FindingCode.DUPLICATE_SUBMISSION,
                    "The same invoice content was already submitted; no second payment "
                    "is possible.",
                    observed=source.filename,
                )
            ],
        )

    key = payable_key(draft)
    unsupported_revision = any(
        f.code is FindingCode.VALUE_NOT_IN_SOURCE and f.field == "revision" for f in report.findings
    )
    rank = None if unsupported_revision else revision_rank(draft.revision)
    version_id = new_id("ver")
    payable_id = key.payable_id if key else None
    total_minor = None
    if report.normalized is not None:
        try:
            total_minor = to_minor(report.normalized.total, report.normalized.currency)
        except MoneyError:
            total_minor = None

    status, flag, disposition, related = "active", None, Disposition.NEW, None
    if legacy:
        flag, disposition = "revision_conflict", Disposition.REVISION_CONFLICT
    extra: list[Finding] = []

    if key is not None:
        conn.execute(
            "INSERT OR IGNORE INTO payables (workspace_id, payable_id, vendor_key, number_key,"
            " vendor_name, number, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                workspace_id,
                payable_id,
                key.vendor_key,
                key.number_key,
                draft.vendor_name or "",
                draft.invoice_number or "",
                now,
            ),
        )
        others = conn.execute(
            "SELECT version_id, revision_rank, status, identity_flag FROM invoice_versions"
            " WHERE workspace_id = ? AND payable_id = ?",
            (workspace_id, payable_id),
        ).fetchall()
        paid = (
            conn.execute(
                "SELECT 1 FROM payments WHERE workspace_id = ? AND payable_id = ?",
                (workspace_id, payable_id),
            ).fetchone()
            is not None
        )

        if others:
            unresolved_conflict = bool(legacy) or any(
                o["status"] == "active" and o["identity_flag"] is not None for o in others
            )
            other_ranks = [o["revision_rank"] for o in others]
            orderable = rank is not None and all(r is not None for r in other_ranks)
            newest = max((r for r in other_ranks if r is not None), default=None)
            related = others[0]["version_id"]
            if unresolved_conflict or not orderable or rank == newest:
                disposition, flag = Disposition.REVISION_CONFLICT, "revision_conflict"
                extra.append(
                    make_finding(
                        FindingCode.REVISION_CONFLICT,
                        "Different content for the same invoice with no explicit revision order. "
                        "Held for review.",
                        observed=draft.revision or "no revision label",
                    )
                )
                # nothing for this payable may be paid until a person sorts it out
                conn.execute(
                    "UPDATE invoice_versions SET identity_flag = 'revision_conflict'"
                    " WHERE workspace_id = ? AND payable_id = ? AND status = 'active'"
                    " AND identity_flag IS NULL"
                    " AND version_id NOT IN (SELECT version_id FROM payments"
                    "                         WHERE workspace_id = ?)",
                    (workspace_id, payable_id, workspace_id),
                )
            elif rank > newest:
                if paid:
                    disposition, flag = Disposition.REVISION_AFTER_PAYMENT, "revision_after_payment"
                    extra.append(
                        make_finding(
                            FindingCode.REVISION_AFTER_PAYMENT,
                            "This revision arrived after the invoice was paid. It needs "
                            "reconciliation; no automatic payment or credit is made.",
                            observed=draft.revision,
                        )
                    )
                else:
                    disposition = Disposition.SUPERSEDES
                    conn.execute(
                        "UPDATE invoice_versions SET status = 'superseded'"
                        " WHERE workspace_id = ? AND payable_id = ?",
                        (workspace_id, payable_id),
                    )
                    extra.append(
                        make_finding(
                            FindingCode.SUPERSEDED_VERSION,
                            "This revision replaces the earlier unpaid version.",
                            observed=draft.revision,
                        )
                    )
            else:  # rank < newest
                disposition, status = Disposition.SUPERSEDED, "superseded"
                extra.append(
                    make_finding(
                        FindingCode.SUPERSEDED_VERSION,
                        "A newer revision of this invoice is already on file.",
                        observed=draft.revision or "original",
                    )
                )
    else:
        flag = "missing_identity"

    conn.execute(
        "INSERT INTO invoice_versions (version_id, workspace_id, payable_id, document_id,"
        " byte_hash,"
        " semantic_fingerprint, revision_label, revision_rank, status, identity_flag, currency,"
        " total_minor, draft_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            version_id,
            workspace_id,
            payable_id,
            source.document_id,
            source.byte_hash,
            fingerprint,
            draft.revision,
            rank,
            status,
            flag,
            report.normalized.currency if report.normalized else None,
            total_minor,
            draft.model_dump_json(),
            now,
        ),
    )
    _bind_legacy_holds(
        conn,
        workspace_id=workspace_id,
        legacy=legacy,
        version_id=version_id,
        payable_id=payable_id,
        source=source,
        policy=policy,
        now=now,
    )
    legacy_reason = _LEGACY_HOLD_REASON if _has_unbound_legacy_holds(conn, workspace_id) else None
    if legacy:
        extra = [f for f in extra if f.code is not FindingCode.REVISION_CONFLICT]
        extra.append(
            make_finding(
                FindingCode.REVISION_CONFLICT,
                legacy_reason
                or "A historical hold requires explicit selection of the "
                "payable version before payment.",
            )
        )
    return Registration(version_id, payable_id, disposition, related, None, extra, legacy_reason)


# ---------------------------------------------------------------------------------------
# Current validation of a stored version
# ---------------------------------------------------------------------------------------


@dataclass
class VersionState:
    version_id: str
    payable_id: str | None
    status: str
    identity_flag: str | None
    draft: InvoiceDraft
    source_text: str
    report: ValidationReport


def conflict_selection_available(
    conn: sqlite3.Connection, workspace_id: str, state: VersionState
) -> bool:
    """Whether a reviewer can select this version to resolve an unpaid identity conflict.

    Selection resolves only the competing-version hold. Other identity holds, an existing
    payment, supersession, incomplete content, and deterministic errors still block it.
    """
    if (
        state.status != "active"
        or state.identity_flag != "revision_conflict"
        or state.payable_id is None
        or state.report.normalized is None
        or state.report.eligibility is Eligibility.REJECTED
        or _has_unbound_legacy_holds(conn, workspace_id)
    ):
        return False
    if conn.execute(
        "SELECT 1 FROM payments WHERE workspace_id = ? AND payable_id = ?",
        (workspace_id, state.payable_id),
    ).fetchone():
        return False
    return (
        conn.execute(
            "SELECT 1 FROM invoice_versions WHERE workspace_id = ? AND payable_id = ?"
            " AND status = 'active' AND identity_flag IS NOT NULL"
            " AND identity_flag != 'revision_conflict'",
            (workspace_id, state.payable_id),
        ).fetchone()
        is None
    )


def load_version_state(
    conn: sqlite3.Connection,
    workspace_id: str,
    version_id: str,
    policy: Policy,
    today: date,
    index: InventoryIndex | None = None,
) -> VersionState:
    row = conn.execute(
        "SELECT v.version_id, v.payable_id, v.status, v.identity_flag, v.draft_json, d.source_text"
        " FROM invoice_versions v JOIN documents d"
        "   ON d.workspace_id = v.workspace_id AND d.document_id = v.document_id"
        " WHERE v.workspace_id = ? AND v.version_id = ?",
        (workspace_id, version_id),
    ).fetchone()
    if row is None:
        raise LedgerError("not_found", "No such invoice version in this workspace.")
    draft = InvoiceDraft.model_validate_json(row["draft_json"])
    report = validate_invoice(
        draft, row["source_text"], index or load_inventory_index(conn), policy, today
    )
    return VersionState(
        version_id=row["version_id"],
        payable_id=row["payable_id"],
        status=row["status"],
        identity_flag=row["identity_flag"],
        draft=draft,
        source_text=row["source_text"],
        report=report,
    )


def _unresolved_review_codes(findings: list[Finding]) -> set[FindingCode]:
    return {f.code for f in findings if f.severity == Severity.REVIEW}


# ---------------------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------------------


def _row_to_approval(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row["approval_id"],
        workspace_id=row["workspace_id"],
        version_id=row["version_id"],
        stage=ApprovalStage(row["stage"]),
        action=ApprovalAction(row["action"]),
        rationale=row["rationale"],
        actor=row["actor"],
        policy_fingerprint=row["policy_fingerprint"],
        content_hash=row["content_hash"],
        scrutiny_completed=bool(row["scrutiny_completed"]),
        resolved_codes=json.loads(row["resolved_codes"]),
        created_at=parse_iso(row["created_at"]),
    )


def record_approval(
    db: Database,
    policy: Policy,
    *,
    workspace_id: str,
    version_id: str,
    stage: ApprovalStage,
    action: ApprovalAction,
    rationale: str,
    actor: str,
    scrutiny_completed: bool = False,
    resolved_codes: list[str] | None = None,
    run_id: str | None = None,
    expected_content_hash: str | None = None,
) -> ApprovalRecord:
    """Store a decision bound to the version's *current* content and the current policy.

    ``expected_content_hash`` lets a reviewer prove they looked at what is stored now; a stale
    decision fails. An ``approve`` on an invoice with ERROR findings is refused outright, and
    a reviewer may only resolve REVIEW findings that policy marks as human-resolvable.
    """
    if not rationale.strip():
        raise LedgerError("rationale_required", "A decision needs a written rationale.")
    resolved = sorted(set(resolved_codes or []))
    with db.transaction() as conn:
        state = load_version_state(conn, workspace_id, version_id, policy, db.clock().date())
        report = state.report
        if report.normalized is None:
            if action is ApprovalAction.APPROVE:
                raise LedgerError("not_approvable", "The invoice is incomplete or invalid.")
            current_hash = "unnormalized"
        else:
            current_hash = content_hash(report.normalized)
        if expected_content_hash is not None and expected_content_hash != current_hash:
            raise LedgerError("stale_decision", "The invoice changed since it was reviewed.")

        if action is ApprovalAction.APPROVE:
            if report.eligibility is Eligibility.REJECTED:
                raise LedgerError(
                    "not_approvable", "Invoices with ERROR findings cannot be approved."
                )
            if _has_unbound_legacy_holds(conn, workspace_id):
                raise LedgerError("legacy_identity_hold", _LEGACY_HOLD_REASON)
            selecting_conflict = (
                stage is ApprovalStage.REVIEW
                and FindingCode.REVISION_CONFLICT.value in resolved
                and expected_content_hash is not None
                and conflict_selection_available(conn, workspace_id, state)
            )
            if state.status != "active" or (state.identity_flag and not selecting_conflict):
                raise LedgerError(
                    "not_approvable", "This version cannot be approved (identity hold)."
                )
            pending = _unresolved_review_codes(report.findings)
            if selecting_conflict:
                pending.add(FindingCode.REVISION_CONFLICT)
            if stage is ApprovalStage.REVIEW:
                valid_codes = {c for c in pending if is_reviewer_resolvable(c)}
                if selecting_conflict:
                    valid_codes.add(FindingCode.REVISION_CONFLICT)
                bad = set(resolved) - {c.value for c in valid_codes}
                if bad:
                    raise LedgerError(
                        "not_resolvable", f"Cannot resolve: {', '.join(sorted(bad))}."
                    )
                missing = {c for c in pending if c.value not in resolved}
                if missing:
                    raise LedgerError(
                        "review_incomplete",
                        f"Unresolved: {', '.join(sorted(c.value for c in missing))}.",
                    )
            elif stage is ApprovalStage.FINAL and pending:
                raise LedgerError("review_required", "A person must resolve the REVIEW findings.")
            if report.requires_scrutiny and not scrutiny_completed:
                raise LedgerError(
                    "scrutiny_required", "This amount needs completed additional scrutiny."
                )
            if selecting_conflict:
                # Selection and its content-bound approval are one transaction. A stale
                # sibling review cannot reactivate an alternative after this choice.
                conn.execute(
                    "UPDATE invoice_versions SET status = 'superseded'"
                    " WHERE workspace_id = ? AND payable_id = ? AND version_id != ?"
                    " AND status = 'active'",
                    (workspace_id, state.payable_id, version_id),
                )
                conn.execute(
                    "UPDATE invoice_versions SET identity_flag = NULL"
                    " WHERE workspace_id = ? AND version_id = ?"
                    " AND identity_flag = 'revision_conflict'",
                    (workspace_id, version_id),
                )

        approval_id = new_id("apr")
        now = db.now()
        conn.execute(
            "INSERT INTO approval_decisions (approval_id, workspace_id, version_id, run_id, stage,"
            " action, rationale, actor, policy_fingerprint, content_hash, scrutiny_completed,"
            " resolved_codes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id,
                workspace_id,
                version_id,
                run_id,
                stage.value,
                action.value,
                rationale.strip(),
                actor,
                policy.fingerprint,
                current_hash,
                1 if scrutiny_completed else 0,
                json.dumps(resolved),
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM approval_decisions WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        return _row_to_approval(row)


def list_approvals(
    conn: sqlite3.Connection, workspace_id: str, version_id: str
) -> list[ApprovalRecord]:
    rows = conn.execute(
        "SELECT * FROM approval_decisions WHERE workspace_id = ? AND version_id = ?"
        " ORDER BY created_at, rowid",
        (workspace_id, version_id),
    ).fetchall()
    return [_row_to_approval(r) for r in rows]


# ---------------------------------------------------------------------------------------
# Payment gate
# ---------------------------------------------------------------------------------------


def _receipt(row: sqlite3.Row, *, already_paid: bool) -> PaymentReceipt:
    return PaymentReceipt(
        payment_id=row["payment_id"],
        transaction_id=row["transaction_id"],
        workspace_id=row["workspace_id"],
        payable_id=row["payable_id"],
        version_id=row["version_id"],
        approval_id=row["approval_id"],
        vendor=row["vendor"],
        amount=from_minor(row["amount_minor"], row["currency"]),
        currency=row["currency"],
        created_at=parse_iso(row["created_at"]),
        already_paid=already_paid,
    )


def get_payment(
    conn: sqlite3.Connection, workspace_id: str, payable_id: str
) -> PaymentReceipt | None:
    row = conn.execute(
        "SELECT * FROM payments WHERE workspace_id = ? AND payable_id = ?",
        (workspace_id, payable_id),
    ).fetchone()
    return _receipt(row, already_paid=True) if row else None


def execute_mock_payment(
    db: Database,
    policy: Policy,
    *,
    workspace_id: str,
    version_id: str,
    approval_id: str,
    mock_payment=None,
    check_active: Callable[[], None] | None = None,
) -> PaymentReceipt:
    """Pay a version once, or refuse with a :class:`LedgerError`.

    Everything is reloaded inside one ``BEGIN IMMEDIATE`` transaction: the stored draft and
    source are re-validated against current inventory and policy, and the approval must match
    the invoice's current content and policy fingerprint. The caller supplies identifiers
    only, never an amount or an "approved" flag.
    """
    from .tools.payment import mock_payment as default_mock_payment

    pay = mock_payment or default_mock_payment
    with db.transaction() as conn:
        if check_active is not None:
            check_active()
        state = load_version_state(conn, workspace_id, version_id, policy, db.clock().date())
        if state.payable_id is None:
            raise LedgerError("no_identity", "An invoice without vendor and number cannot be paid.")

        existing = get_payment(conn, workspace_id, state.payable_id)
        if existing is not None:
            if existing.version_id == version_id:
                return existing  # a valid repeat: same invoice, same receipt
            raise LedgerError(
                "already_paid_other_version",
                "This invoice was already paid for a different version. No second payment.",
            )

        if _has_unbound_legacy_holds(conn, workspace_id):
            raise LedgerError("legacy_identity_hold", _LEGACY_HOLD_REASON)

        if state.status != "active":
            raise LedgerError("not_active", "This version was superseded by a newer revision.")
        if state.identity_flag:
            raise LedgerError("identity_hold", f"Held: {state.identity_flag.replace('_', ' ')}.")

        report = state.report
        invoice = report.normalized
        if invoice is None:
            raise LedgerError("invalid_invoice", "The invoice is incomplete or invalid.")
        if report.eligibility is Eligibility.REJECTED:
            raise LedgerError("has_errors", "The invoice has ERROR findings.")
        if invoice.currency not in policy.payment.auto_pay_currencies:
            raise LedgerError(
                "unsupported_currency", f"{invoice.currency} cannot be paid automatically."
            )

        approval_row = conn.execute(
            "SELECT * FROM approval_decisions WHERE workspace_id = ? AND approval_id = ?",
            (workspace_id, approval_id),
        ).fetchone()
        if approval_row is None or approval_row["version_id"] != version_id:
            raise LedgerError("approval_missing", "No matching approval for this invoice version.")
        approval = _row_to_approval(approval_row)
        if approval.action is not ApprovalAction.APPROVE:
            raise LedgerError("not_approved", "The recorded decision is not an approval.")
        if approval.stage not in (ApprovalStage.FINAL, ApprovalStage.REVIEW):
            raise LedgerError(
                "approval_stage", "Only a final or reviewer decision can release payment."
            )
        if approval.policy_fingerprint != policy.fingerprint:
            raise LedgerError("policy_changed", "Policy changed since approval; approve again.")
        if approval.content_hash != content_hash(invoice):
            raise LedgerError("content_changed", "The invoice changed since approval.")
        if report.requires_scrutiny and not approval.scrutiny_completed:
            raise LedgerError(
                "scrutiny_required", "This amount needs completed additional scrutiny."
            )

        pending = _unresolved_review_codes(report.findings)
        if pending:
            if approval.stage is not ApprovalStage.REVIEW:
                raise LedgerError("review_required", "A person must resolve the REVIEW findings.")
            unresolved = {c for c in pending if c.value not in approval.resolved_codes}
            not_allowed = {c for c in pending if not is_reviewer_resolvable(c)}
            if unresolved or not_allowed:
                codes = ", ".join(sorted(c.value for c in unresolved | not_allowed))
                raise LedgerError("review_incomplete", f"Unresolved: {codes}.")

        amount_minor = to_minor(invoice.total, invoice.currency)
        if amount_minor <= 0:
            raise LedgerError("invalid_amount", "Payment amount must be positive.")

        payment_id = new_id("pmt")
        transaction_id = new_id("txn")
        now = db.now()
        try:
            conn.execute(
                "INSERT INTO payments (payment_id, workspace_id, payable_id, version_id,"
                " approval_id, transaction_id, vendor, amount_minor, currency, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    payment_id,
                    workspace_id,
                    state.payable_id,
                    version_id,
                    approval_id,
                    transaction_id,
                    invoice.vendor_name,
                    amount_minor,
                    invoice.currency,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:  # lost a race that the checks above could not see
            raise LedgerError("already_paid", "A payment already exists for this invoice.") from exc
        if check_active is not None:
            check_active()
        result = pay(invoice.vendor_name, invoice.total)
        if not isinstance(result, dict) or result.get("status") != "success":
            raise LedgerError("payment_failed", "The payment function did not report success.")
        row = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        return _receipt(row, already_paid=False)
