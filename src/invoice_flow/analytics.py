"""Current exception snapshots, separate from historical processing-attempt counts."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from sqlite3 import Connection

from .review_routing import DEPARTMENTS


def current_exceptions(
    conn: Connection,
    workspace_id: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    vendor: str | None = None,
    outcome: str | None = None,
) -> list[dict]:
    # Choose a current decision BEFORE applying display filters. A date/vendor filter
    # must never resurrect an old hold after the version was paid or rejected.
    sql = """
    WITH decisions AS (
      SELECT r.*, v.status AS version_status, v.payable_id, p.vendor_name,
        d.filename,
        ROW_NUMBER() OVER (
          PARTITION BY COALESCE(r.version_id, r.document_id, r.run_id)
          ORDER BY r.started_at DESC, r.rowid DESC
        ) AS position,
        (SELECT h.department FROM review_handoffs h
          WHERE h.workspace_id=r.workspace_id AND h.run_id=r.run_id
          ORDER BY h.rowid DESC LIMIT 1) AS department
      FROM runs r
      LEFT JOIN invoice_versions v ON v.workspace_id=r.workspace_id AND v.version_id=r.version_id
      LEFT JOIN payables p ON p.workspace_id=v.workspace_id AND p.payable_id=v.payable_id
      LEFT JOIN documents d ON d.workspace_id=r.workspace_id AND d.document_id=r.document_id
      WHERE r.workspace_id=? AND r.lifecycle='completed'
        AND r.outcome IN ('needs_review','rejected','paid')
    )
    SELECT * FROM decisions c WHERE position=1
      AND outcome IN ('needs_review','rejected')
      AND (version_id IS NULL OR version_status='active')
      AND NOT EXISTS (SELECT 1 FROM payments t
        WHERE t.workspace_id=c.workspace_id AND t.payable_id=c.payable_id)
    """
    params: list = [workspace_id]
    if date_from:
        sql += " AND date(started_at)>=?"
        params.append(date_from.isoformat())
    if date_to:
        sql += " AND date(started_at)<=?"
        params.append(date_to.isoformat())
    if vendor:
        sql += " AND instr(lower(coalesce(vendor_name,'')),lower(?))>0"
        params.append(vendor)
    if outcome:
        sql += " AND outcome=?"
        params.append(outcome)
    sql += " ORDER BY started_at DESC, run_id DESC"
    records = []
    for row in conn.execute(sql, params):
        payload = json.loads(row["result_json"]) if row["result_json"] else {}
        invoice = payload.get("invoice") or {}
        reasons = sorted(
            {
                str(item["code"])
                for item in payload.get("findings", [])
                if item.get("severity") in {"ERROR", "REVIEW"} and item.get("code")
            }
        )
        if not reasons:
            reasons = ["REVIEW_REQUIRED" if row["outcome"] == "needs_review" else "REJECTED"]
        department = row["department"] or "unassigned"
        records.append(
            {
                "run_id": row["run_id"],
                "version_id": row["version_id"],
                "invoice_number": invoice.get("invoice_number"),
                "vendor": row["vendor_name"] or invoice.get("vendor_name"),
                "filename": row["filename"],
                "outcome": row["outcome"],
                "department": department,
                "department_label": DEPARTMENTS.get(department, "Unassigned"),
                "reasons": reasons,
                "started_at": row["started_at"],
            }
        )
    return records


def exception_groups(records: list[dict]) -> dict:
    review = [record for record in records if record["outcome"] == "needs_review"]
    departments = Counter(record["department"] for record in review)
    reasons = Counter(code for record in records for code in record["reasons"])
    return {
        "current_review_count": len(review),
        "current_exception_count": len(records),
        "backlog_by_department": [
            {"key": key, "label": DEPARTMENTS.get(key, "Unassigned"), "count": count}
            for key, count in sorted(departments.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "exception_reasons": [
            {"key": code, "label": code.replace("_", " ").capitalize(), "count": count}
            for code, count in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
    }
