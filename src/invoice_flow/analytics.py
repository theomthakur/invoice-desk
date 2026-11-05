"""Current exception snapshots, separate from historical processing-attempt counts."""

from __future__ import annotations

import json
from datetime import date
from sqlite3 import Connection

from .review_routing import DEPARTMENTS


def _current_query(
    workspace_id: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    vendor: str | None = None,
    outcome: str | None = None,
) -> tuple[str, list]:
    # Select current decisions before display filters, so old holds cannot reappear.
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
    , current AS (SELECT * FROM decisions c WHERE position=1
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

    sql += """), enriched AS (SELECT current.*,
      COALESCE(department,'unassigned') AS assigned_department,
      CASE WHEN EXISTS (SELECT 1 FROM json_each(COALESCE(result_json,'{}'),'$.findings') j
        WHERE json_extract(j.value,'$.severity') IN ('ERROR','REVIEW')
          AND json_extract(j.value,'$.code') IS NOT NULL)
      THEN (SELECT json_group_array(code) FROM (
        SELECT DISTINCT json_extract(j.value,'$.code') AS code
        FROM json_each(COALESCE(result_json,'{}'),'$.findings') j
        WHERE json_extract(j.value,'$.severity') IN ('ERROR','REVIEW')
          AND json_extract(j.value,'$.code') IS NOT NULL ORDER BY code))
      ELSE json_array(CASE WHEN outcome='needs_review' THEN 'REVIEW_REQUIRED' ELSE 'REJECTED' END)
      END AS reasons_json FROM current) """
    return sql, params


def _record(row) -> dict:
    payload = json.loads(row["result_json"]) if row["result_json"] else {}
    invoice = payload.get("invoice") or {}
    reasons = json.loads(row["reasons_json"])
    department = row["department"] or "unassigned"
    return {
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


def current_exception_page(
    conn: Connection,
    workspace_id: str,
    *,
    view: str,
    key: str,
    limit: int,
    offset: int = 0,
    position: tuple[str, str | None] | None = None,
    **filters,
) -> dict:
    """Filter and paginate in SQLite. Parse full result JSON only for returned records."""
    sql, params = _current_query(workspace_id, **filters)
    if view == "backlog":
        condition = "outcome='needs_review' AND assigned_department=?"
    else:
        condition = "EXISTS (SELECT 1 FROM json_each(reasons_json) WHERE value=?)"
    sql += " , matching AS (SELECT * FROM enriched WHERE " + condition + ") "
    params.append(key)
    count = conn.execute(sql + "SELECT count(*) FROM matching", params).fetchone()[0]
    page_sql = "SELECT * FROM matching"
    page_params = list(params)
    if position:
        timestamp, run_id = position
        if run_id is None:
            page_sql += " WHERE started_at<?"
            page_params.append(timestamp)
        else:
            page_sql += " WHERE (started_at,run_id)<(?,?)"
            page_params.extend((timestamp, run_id))
    page_sql += " ORDER BY started_at DESC,run_id DESC LIMIT ? OFFSET ?"
    page_params.extend((limit + 1, offset))
    rows = conn.execute(sql + page_sql, page_params).fetchall()
    return {
        "records": [_record(row) for row in rows[:limit]],
        "record_count": count,
        "has_more": len(rows) > limit,
    }


def current_exception_groups(conn: Connection, workspace_id: str, **filters) -> dict:
    """Aggregate current counts without materializing every invoice result in Python."""
    sql, params = _current_query(workspace_id, **filters)
    counts = conn.execute(
        sql + "SELECT count(*),sum(outcome='needs_review') FROM enriched", params
    ).fetchone()
    departments = conn.execute(
        sql + "SELECT assigned_department,count(*) AS n FROM enriched "
        "WHERE outcome='needs_review' GROUP BY assigned_department "
        "ORDER BY n DESC,assigned_department",
        params,
    )
    backlog = [
        {"key": r[0], "label": DEPARTMENTS.get(r[0], "Unassigned"), "count": r[1]}
        for r in departments
    ]
    reasons = conn.execute(
        sql + "SELECT j.value,count(*) AS n FROM enriched,json_each(reasons_json) j "
        "GROUP BY j.value ORDER BY n DESC,j.value",
        params,
    )
    return {
        "current_review_count": counts[1] or 0,
        "current_exception_count": counts[0],
        "backlog_by_department": backlog,
        "exception_reasons": [
            {"key": r[0], "label": r[0].replace("_", " ").capitalize(), "count": r[1]}
            for r in reasons
        ],
    }
