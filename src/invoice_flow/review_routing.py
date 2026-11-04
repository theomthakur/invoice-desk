"""Persist internal demo handoffs without approving, paying or sending notifications."""

from __future__ import annotations

import hashlib
import json

from .identity import content_hash
from .ledger import LedgerError, load_version_state
from .runtime import App
from .schemas import RunResult
from .storage import append_event, new_id

DEPARTMENTS = {
    "accounts_payable": "Accounts payable",
    "procurement": "Procurement",
    "vendor_management": "Vendor management",
    "finance_operations": "Finance operations",
}
REASONS = {
    "inventory_check": "Inventory or purchasing details need checking",
    "vendor_verification": "Vendor identity needs verification",
    "payment_policy": "Currency or payment policy needs a decision",
    "version_conflict": "Conflicting invoice versions need reconciliation",
    "additional_review": "Additional departmental review is needed",
}


class RoutingError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _run(conn, workspace_id: str, run_id: str) -> RunResult:
    row = conn.execute(
        "SELECT result_json FROM runs WHERE workspace_id = ? AND run_id = ?",
        (workspace_id, run_id),
    ).fetchone()
    if row is None:
        raise RoutingError("not_found", "No such run.", 404)
    if not row["result_json"]:
        raise RoutingError("not_reviewable", "This run has no completed review result.")
    return RunResult.model_validate_json(row["result_json"])


def _can_route(conn, app: App, workspace_id: str, result: RunResult) -> bool:
    if result.outcome != "needs_review" or result.lifecycle != "completed":
        return False
    if not result.version_id:
        return True  # An unidentified legacy hold can still be assigned for investigation.
    try:
        state = load_version_state(
            conn, workspace_id, result.version_id, app.policy, app.db.clock().date()
        )
    except LedgerError:
        return False
    actual_hash = content_hash(state.report.normalized) if state.report.normalized else None
    if state.status != "active" or actual_hash != result.content_hash:
        return False
    return not conn.execute(
        "SELECT 1 FROM payments WHERE workspace_id = ? AND payable_id = ?",
        (workspace_id, state.payable_id),
    ).fetchone()


def _record(row, *, active: bool) -> dict:
    return {
        "handoff_id": row["handoff_id"],
        "department": row["department"],
        "department_label": DEPARTMENTS[row["department"]],
        "reason_code": row["reason_code"],
        "reason": REASONS[row["reason_code"]],
        "note": row["note"],
        "created_at": row["created_at"],
        "active": active,
        "notification_sent": False,
    }


def routing_info(app: App, workspace_id: str, run_id: str) -> dict:
    with app.db.read() as conn:
        current = _run(conn, workspace_id, run_id)
        allowed = _can_route(conn, app, workspace_id, current)
        row = conn.execute(
            "SELECT * FROM review_handoffs WHERE workspace_id = ? AND run_id = ?"
            " ORDER BY rowid DESC LIMIT 1",
            (workspace_id, run_id),
        ).fetchone()
    return {
        "assignment": _record(row, active=allowed) if row else None,
        "can_route": allowed,
        "departments": [{"value": k, "label": v} for k, v in DEPARTMENTS.items()],
        "reasons": [{"value": k, "label": v} for k, v in REASONS.items()],
    }


def record_handoff(
    app: App,
    *,
    workspace_id: str,
    run_id: str,
    version_id: str | None,
    content_hash_seen: str | None,
    department: str,
    reason_code: str,
    note: str,
    request_token: str,
) -> dict:
    if department not in DEPARTMENTS or reason_code not in REASONS:
        raise RoutingError("invalid_request", "Choose a listed department and reason.", 422)
    payload = [run_id, version_id, content_hash_seen, department, reason_code, note]
    fingerprint = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    with app.db.transaction() as conn:
        current = _run(conn, workspace_id, run_id)
        previous = conn.execute(
            "SELECT * FROM review_handoffs WHERE workspace_id = ? AND request_token = ?",
            (workspace_id, request_token),
        ).fetchone()
        if previous:
            if previous["request_hash"] != fingerprint:
                raise RoutingError("idempotency_conflict", "This request token was already used.")
            latest = conn.execute(
                "SELECT handoff_id FROM review_handoffs WHERE workspace_id = ? AND run_id = ?"
                " ORDER BY rowid DESC LIMIT 1",
                (workspace_id, run_id),
            ).fetchone()
            return _record(
                previous,
                active=bool(
                    latest
                    and latest["handoff_id"] == previous["handoff_id"]
                    and _can_route(conn, app, workspace_id, current)
                ),
            )
        if current.version_id != version_id or current.content_hash != content_hash_seen:
            raise RoutingError("stale_decision", "The invoice changed. Reopen it before routing.")
        if not _can_route(conn, app, workspace_id, current):
            raise RoutingError("not_reviewable", "This invoice is not waiting for review.")
        handoff_id, now = new_id("handoff"), app.db.now()
        conn.execute(
            "INSERT INTO review_handoffs"
            " (handoff_id,workspace_id,run_id,version_id,content_hash,department,reason_code,"
            " note,request_token,request_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                handoff_id,
                workspace_id,
                run_id,
                version_id,
                content_hash_seen,
                department,
                reason_code,
                note,
                request_token,
                fingerprint,
                now,
            ),
        )
        append_event(
            conn,
            run_id,
            "approval",
            "department_handoff",
            {
                "handoff_id": handoff_id,
                "department": department,
                "department_label": DEPARTMENTS[department],
                "reason_code": reason_code,
                "reason": REASONS[reason_code],
                "note": note,
                "message": f"Assigned to {DEPARTMENTS[department]} for review. "
                "Payment remains on hold. No notification was sent.",
                "simulated": True,
                "notification_sent": False,
            },
            now,
        )
        row = conn.execute(
            "SELECT * FROM review_handoffs WHERE handoff_id = ?", (handoff_id,)
        ).fetchone()
        return _record(row, active=True)
