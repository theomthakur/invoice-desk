"""Shared helpers for ledger-level tests (conftest puts tests/ on the import path)."""

from __future__ import annotations

import hashlib

from conftest import TODAY
from invoice_flow.identity import content_hash
from invoice_flow.ledger import (
    execute_mock_payment,
    load_version_state,
    record_approval,
    register_version,
)
from invoice_flow.rules import validate_invoice
from invoice_flow.schemas import ApprovalAction, ApprovalStage, SourceDocument, SourceFormat
from invoice_flow.storage import LOCAL_WORKSPACE, ensure_workspace, load_inventory_index, new_id

FINAL, REVIEW, APPROVE = ApprovalStage.FINAL, ApprovalStage.REVIEW, ApprovalAction.APPROVE


def source_for(text: str, name: str = "inv.txt") -> SourceDocument:
    digest = hashlib.sha256(f"{name}|{text}".encode()).hexdigest()
    return SourceDocument(
        document_id=f"doc_{digest[:16]}",
        filename=name,
        format=SourceFormat.TXT,
        size_bytes=len(text),
        byte_hash=digest,
        text=text,
    )


def submit(db, policy, draft, *, text="", ws=LOCAL_WORKSPACE, name="inv.txt"):
    """Validate and register one submission, like the service does."""
    with db.transaction() as conn:
        ensure_workspace(conn, ws, "local" if ws == LOCAL_WORKSPACE else "session", db.now())
        report = validate_invoice(draft, text, load_inventory_index(conn), policy, TODAY)
        reg = register_version(
            conn,
            workspace_id=ws,
            source=source_for(text, name),
            draft=draft,
            report=report,
            policy=policy,
            now=db.now(),
        )
    return reg, report


def approve(db, policy, version_id, *, ws=LOCAL_WORKSPACE, stage=FINAL, scrutiny=False, **kw):
    return record_approval(
        db,
        policy,
        workspace_id=ws,
        version_id=version_id,
        stage=stage,
        action=APPROVE,
        rationale=kw.pop("rationale", "Checks passed."),
        actor="test",
        scrutiny_completed=scrutiny,
        **kw,
    )


def pay(db, policy, version_id, approval_id, *, ws=LOCAL_WORKSPACE, **kw):
    return execute_mock_payment(
        db,
        policy,
        workspace_id=ws,
        version_id=version_id,
        approval_id=approval_id,
        mock_payment=kw.pop("mock_payment", lambda vendor, amount: {"status": "success"}),
        **kw,
    )


def approve_and_pay(db, policy, draft, **kw):
    reg, report = submit(db, policy, draft, **kw)
    ws = kw.get("ws", LOCAL_WORKSPACE)
    approval = approve(db, policy, reg.version_id, scrutiny=report.requires_scrutiny, ws=ws)
    return reg, pay(db, policy, reg.version_id, approval.approval_id, ws=ws)


def payments(db, ws=LOCAL_WORKSPACE):
    with db.read() as conn:
        return conn.execute("SELECT * FROM payments WHERE workspace_id = ?", (ws,)).fetchall()


def insert_approval(
    db,
    policy,
    version_id,
    *,
    ws=LOCAL_WORKSPACE,
    stage="final",
    action="approve",
    scrutiny=1,
    resolved="[]",
    content=None,
    fingerprint=None,
):
    """Write an approval row directly, bypassing record_approval, to test the gate alone."""
    with db.transaction() as conn:
        state = load_version_state(conn, ws, version_id, policy, TODAY)
        approval_id = new_id("apr")
        conn.execute(
            "INSERT INTO approval_decisions (approval_id, workspace_id, version_id, run_id, stage,"
            " action, rationale, actor, policy_fingerprint, content_hash, scrutiny_completed,"
            " resolved_codes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id,
                ws,
                version_id,
                None,
                stage,
                action,
                "forged",
                "attacker",
                fingerprint or policy.fingerprint,
                content or content_hash(state.report.normalized),
                scrutiny,
                resolved,
                db.now(),
            ),
        )
    return approval_id


def insert_placeholder_approval(db, policy, version_id):
    """Approval row for an invoice that cannot be normalized (content hash is a placeholder)."""
    approval_id = new_id("apr")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO approval_decisions (approval_id, workspace_id, version_id, stage, action,"
            " rationale, actor, policy_fingerprint, content_hash, scrutiny_completed,"
            " resolved_codes,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id,
                LOCAL_WORKSPACE,
                version_id,
                "final",
                "approve",
                "x",
                "t",
                policy.fingerprint,
                "x",
                1,
                "[]",
                db.now(),
            ),
        )
    return approval_id
