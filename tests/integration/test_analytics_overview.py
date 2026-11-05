"""Overview values come from the full workspace ledger, not a loaded history page."""

from tests.integration.test_api import alice as alice
from tests.integration.test_api import api as api
from tests.integration.test_api import bob as bob
from tests.integration.test_api import ctx as ctx


def test_overview_counts_runs_and_payments_separately_and_isolates_workspaces(alice, bob):
    empty = bob.get("/analytics/overview").json()
    assert empty["total_runs"] == 0
    assert empty["identified_invoices"] == 0
    assert empty["paid_by_currency"] == []
    first = alice.run("invoice_1001.txt")
    assert first["outcome"] == "paid"
    duplicate = alice.run("invoice_1001.txt")
    assert duplicate["outcome"] == "duplicate"
    result = alice.get("/analytics/overview").json()
    assert result["total_runs"] == 2
    assert result["identified_invoices"] == 1
    assert result["outcomes"] == {"paid": 1, "duplicate": 1}
    assert sum(day["count"] for day in result["daily_runs"]) == 2
    assert result["vendor_runs"] == [{"vendor": "Widgets Inc.", "count": 2}]
    assert result["simulated_receipts"] == 1
    assert result["paid_by_currency"] == [{"currency": "USD", "amount": "5000.00", "receipts": 1}]
    assert bob.get("/analytics/overview").json() == empty


def test_review_is_a_run_count_and_unknown_identity_is_not_invented(alice):
    run = alice.run("invoice_1012.txt")
    assert run["outcome"] == "needs_review"
    result = alice.get("/analytics/overview").json()
    assert result["review_runs"] == 1
    assert result["simulated_receipts"] == 0
    assert result["total_runs"] == 1


def test_receipts_remain_separate_by_currency(alice, ctx):
    assert alice.run("invoice_1001.txt")["outcome"] == "paid"
    with ctx.db.transaction() as conn:
        conn.execute("UPDATE payments SET currency='EUR' WHERE workspace_id=?", (alice.workspace,))
    assert alice.run("invoice_1006.csv")["outcome"] == "paid"
    result = alice.get("/analytics/overview").json()
    assert result["simulated_receipts"] == 2
    assert result["paid_by_currency"] == [
        {"currency": "EUR", "amount": "5000.00", "receipts": 1},
        {"currency": "USD", "amount": "2750.00", "receipts": 1},
    ]


def test_filters_match_runs_and_never_reuse_duplicate_receipts(alice, bob, ctx):
    paid = alice.run("invoice_1001.txt")
    rejected = alice.run("invoice_1007.csv")
    duplicate = alice.run("invoice_1001.txt")
    assert (paid["outcome"], rejected["outcome"], duplicate["outcome"]) == (
        "paid",
        "rejected",
        "duplicate",
    )
    filtered = alice.get(
        "/analytics/overview", params={"vendor": "MegaWidgets", "outcome": "rejected"}
    ).json()
    assert filtered["identified_invoices"] == 1
    assert filtered["total_runs"] == 1
    assert filtered["simulated_receipts"] == 0
    duplicates = alice.get("/analytics/overview", params={"outcome": "duplicate"}).json()
    assert duplicates["total_runs"] == 1
    assert duplicates["outcomes"] == {"duplicate": 1}
    assert duplicates["vendor_runs"] == [{"vendor": "Widgets Inc.", "count": 1}]
    assert duplicates["identified_invoices"] == 1
    assert duplicates["simulated_receipts"] == 0
    paid_only = alice.get("/analytics/overview", params={"outcome": "paid"}).json()
    assert paid_only["total_runs"] == 1
    assert paid_only["simulated_receipts"] == 1
    assert paid_only["paid_by_currency"][0]["amount"] == "5000.00"
    day = ctx.db.now()[:10]
    exact = alice.get(
        "/analytics/overview", params={"date_from": day, "date_to": day, "outcome": "paid"}
    ).json()
    assert exact["simulated_receipts"] == 1
    later = alice.get("/analytics/overview", params={"date_from": "9999-01-01"}).json()
    assert later["total_runs"] == 0 and later["identified_invoices"] == 0
    assert later["simulated_receipts"] == 0
    assert bob.get("/analytics/overview", params={"vendor": "Widgets"}).json()["total_runs"] == 0
    assert (
        alice.get(
            "/analytics/overview", params={"date_from": "2026-12-31", "date_to": "2026-01-01"}
        ).status_code
        == 422
    )
    assert (
        alice.get("/analytics/overview", params={"vendor": "' OR 1=1 --"}).json()["total_runs"] == 0
    )


def test_outcomes_separate_failed_runs_and_flags_are_broken_down(alice, ctx):
    paid = alice.run("invoice_1001.txt")
    review = alice.run("invoice_1012.txt")
    extraction = alice.run("invoice_1007.csv")
    assert (paid["outcome"], review["outcome"]) == ("paid", "needs_review")
    with ctx.db.transaction() as conn:
        # Stand-in for a batch extraction run, which completes without an outcome.
        conn.execute(
            "UPDATE runs SET outcome=NULL, version_id=NULL WHERE workspace_id=? AND run_id=?",
            (alice.workspace, extraction["run_id"]),
        )
        conn.execute(
            "UPDATE runs SET lifecycle='failed', outcome=NULL WHERE workspace_id=? AND run_id=?",
            (alice.workspace, review["run_id"]),
        )
        conn.execute(
            "UPDATE invoice_versions SET identity_flag='revision_conflict'"
            " WHERE workspace_id=? AND version_id NOT IN"
            " (SELECT version_id FROM payments WHERE workspace_id=?)",
            (alice.workspace, alice.workspace),
        )
    result = alice.get("/analytics/overview").json()
    assert result["outcomes"] == {"extraction_only": 1, "failed": 1, "paid": 1}
    assert result["failed_runs"] == 1
    assert result["identity_flags"] == [{"flag": "revision_conflict", "count": 2}]
    assert result["flagged_active_versions"] == 2
    paid_only = alice.get("/analytics/overview", params={"outcome": "paid"}).json()
    assert paid_only["identity_flags"] == []
    assert paid_only["flagged_active_versions"] == 0


def test_current_backlog_deduplicates_attempts_and_follows_reassignment(alice, bob):
    from tests.integration.test_review_routing import body

    held = alice.run("invoice_1012.txt")
    assert alice.run("invoice_1012.txt")["outcome"] == "duplicate"
    overview = alice.get("/analytics/overview").json()
    assert overview["review_runs"] == 1
    assert overview["current_review_count"] == 1
    assert overview["backlog_by_department"] == [
        {"key": "unassigned", "label": "Unassigned", "count": 1}
    ]
    for index, department in enumerate(["procurement", "vendor_management"]):
        response = alice.post(
            f"/runs/{held['run_id']}/routing",
            json=body(held, department=department, idempotency_token=f"analytics-handoff-{index}"),
        )
        assert response.status_code == 200, response.text
    overview = alice.get("/analytics/overview").json()
    assert overview["backlog_by_department"] == [
        {"key": "vendor_management", "label": "Vendor management", "count": 1}
    ]
    query = {"view": "backlog", "key": "vendor_management"}
    matching = alice.get("/analytics/records", params=query).json()
    assert matching["record_count"] == 1
    assert matching["records"][0]["run_id"] == held["run_id"]
    assert matching["records"][0]["department_label"] == "Vendor management"
    assert bob.get("/analytics/records", params=query).json()["record_count"] == 0
    assert (
        alice.get("/analytics/records", params={"view": "backlog", "key": "procurement"}).json()[
            "record_count"
        ]
        == 0
    )


def test_paid_hold_leaves_current_backlog_and_exception_drilldown(alice):
    from tests.integration.test_api import review_body

    held = alice.run("invoice_1012.txt")
    query = {"view": "exceptions", "key": "VENDOR_IDENTITY_CHANGE"}
    assert alice.get("/analytics/records", params=query).json()["record_count"] == 1
    response = alice.post(f"/runs/{held['run_id']}/review", json=review_body(held))
    assert response.status_code == 200, response.text
    assert response.json()["run"]["outcome"] == "paid"
    current = alice.get("/analytics/overview").json()
    assert current["current_review_count"] == 0
    assert current["current_exception_count"] == 0
    assert current["exception_reasons"] == []
    assert alice.get("/analytics/records", params=query).json()["record_count"] == 0


def test_date_filter_never_resurrects_old_review_before_a_later_rejection(alice, ctx):
    from tests.integration.test_api import review_body

    held = alice.run("invoice_1012.txt")
    with ctx.db.transaction() as conn:
        conn.execute(
            "INSERT INTO runs (run_id,workspace_id,document_id,version_id,lifecycle,outcome,"
            "started_at,finished_at,result_json) SELECT 'run_old_analytics_hold',workspace_id,"
            "document_id,version_id,lifecycle,outcome,'2026-01-01T00:00:00Z',"
            "'2026-01-01T00:01:00Z',result_json FROM runs WHERE run_id=?",
            (held["run_id"],),
        )
    response = alice.post(
        f"/runs/{held['run_id']}/review", json=review_body(held, decision="reject")
    )
    assert response.status_code == 200, response.text
    assert alice.get("/analytics/overview").json()["current_review_count"] == 0
    filtered = alice.get(
        "/analytics/overview", params={"date_to": "2026-01-01", "outcome": "needs_review"}
    ).json()
    assert filtered["review_runs"] == 1  # Historical attempt is still honestly reported.
    assert filtered["current_review_count"] == 0
    assert filtered["current_exception_count"] == 0


def test_exception_reasons_count_once_per_record_and_ignore_information(alice, ctx):
    import json

    held = alice.run("invoice_1012.txt")
    payload = held.copy()
    blocking = next(f for f in held["findings"] if f["code"] == "VENDOR_IDENTITY_CHANGE")
    payload["findings"] = [
        blocking,
        blocking,
        {**blocking, "code": "INFORMATION", "severity": "INFO"},
    ]
    with ctx.db.transaction() as conn:
        conn.execute(
            "UPDATE runs SET result_json=? WHERE run_id=?", (json.dumps(payload), held["run_id"])
        )
    overview = alice.get("/analytics/overview").json()
    assert overview["exception_reasons"] == [
        {"key": "VENDOR_IDENTITY_CHANGE", "label": "Vendor identity change", "count": 1}
    ]
    records = alice.get(
        "/analytics/records", params={"view": "exceptions", "key": "VENDOR_IDENTITY_CHANGE"}
    ).json()
    assert records["record_count"] == 1 and records["next_offset"] is None
    assert records["records"][0]["reasons"] == ["VENDOR_IDENTITY_CHANGE"]


def test_current_exception_pages_are_bounded_and_superseded_versions_are_excluded(alice, ctx):
    held = alice.run("invoice_1012.txt")
    with ctx.db.transaction() as conn:
        conn.execute(
            "UPDATE invoice_versions SET status='superseded' WHERE version_id=?",
            (held["version_id"],),
        )
        for index in range(27):
            conn.execute(
                "INSERT INTO runs (run_id,workspace_id,lifecycle,outcome,started_at,finished_at)"
                " VALUES (?,?,'completed','needs_review','2026-11-04T00:00:00Z',"
                "'2026-11-04T00:01:00Z')",
                (f"run_unidentified_{index:02d}", alice.workspace),
            )
    assert alice.get("/analytics/overview").json()["current_review_count"] == 27
    query = {"view": "backlog", "key": "unassigned", "limit": 25}
    first = alice.get("/analytics/records", params=query).json()
    second = alice.get(
        "/analytics/records", params={**query, "offset": first["next_offset"]}
    ).json()
    assert first["record_count"] == 27 and len(first["records"]) == 25
    assert len(second["records"]) == 2 and second["next_offset"] is None
    assert not {r["run_id"] for r in first["records"]} & {r["run_id"] for r in second["records"]}
    assert alice.get("/analytics/records", params={**query, "limit": 101}).status_code == 422
    assert alice.get("/analytics/records", params={**query, "offset": -1}).status_code == 422
    assert (
        alice.get(
            "/analytics/records",
            params={**query, "date_from": "2026-12-31", "date_to": "2026-01-01"},
        ).status_code
        == 422
    )


def test_cursor_does_not_skip_records_when_a_previous_page_is_resolved(alice, ctx):
    with ctx.db.transaction() as conn:
        for index in range(5):
            conn.execute(
                "INSERT INTO runs (run_id,workspace_id,lifecycle,outcome,started_at,finished_at)"
                " VALUES (?,?,'completed','needs_review','2026-11-04T00:00:00Z',"
                "'2026-11-04T00:01:00Z')",
                (f"run_cursor_{index:02d}", alice.workspace),
            )
    query = {"view": "backlog", "key": "unassigned", "limit": 2}
    first = alice.get("/analytics/records", params=query).json()
    assert [r["run_id"] for r in first["records"]] == ["run_cursor_04", "run_cursor_03"]
    with ctx.db.transaction() as conn:
        conn.execute("UPDATE runs SET outcome='paid' WHERE run_id='run_cursor_04'")
    second = alice.get(
        "/analytics/records", params={**query, "cursor": first["next_cursor"]}
    ).json()
    assert [r["run_id"] for r in second["records"]] == ["run_cursor_02", "run_cursor_01"]
    assert second["record_count"] == 4
    last = alice.get("/analytics/records", params={**query, "cursor": second["next_cursor"]}).json()
    assert [r["run_id"] for r in last["records"]] == ["run_cursor_00"]
    assert last["next_cursor"] is None
    assert (
        alice.get("/analytics/records", params={**query, "cursor": "h1.invalid"}).status_code == 422
    )


def test_lifecycle_filters_include_failures_and_pending_work_without_business_outcomes(alice, ctx):
    with ctx.db.transaction() as conn:
        for index, state in enumerate(["failed", "interrupted", "queued", "running"]):
            conn.execute(
                "INSERT INTO runs (run_id,workspace_id,lifecycle,started_at) VALUES (?,?,?,?)",
                (f"run_lifecycle_{index}", alice.workspace, state, ctx.db.now()),
            )
    failed = alice.get("/analytics/overview", params={"outcome": "failed"}).json()
    assert failed["total_runs"] == 2 and failed["failed_runs"] == 2
    active = alice.get("/analytics/overview", params={"outcome": "in_progress"}).json()
    assert active["total_runs"] == 2 and active["active_runs"] == 2
    assert active["current_review_count"] == failed["current_review_count"] == 0
    assert (
        alice.get(
            "/analytics/records",
            params={"view": "backlog", "key": "unassigned", "outcome": "failed"},
        ).json()["record_count"]
        == 0
    )


def test_vendor_filter_and_chart_include_drafts_without_a_payable_identity(alice, ctx):
    import json

    with ctx.db.transaction() as conn:
        conn.execute(
            "INSERT INTO runs (run_id,workspace_id,lifecycle,outcome,started_at,result_json) "
            "VALUES (?,?,'completed','rejected',?,?)",
            (
                "run_vendor_draft",
                alice.workspace,
                ctx.db.now(),
                json.dumps({"draft": {"vendor_name": "Harbor Parts"}, "findings": []}),
            ),
        )
    data = alice.get("/analytics/overview", params={"vendor": "Harbor"}).json()
    assert data["total_runs"] == data["current_exception_count"] == 1
    assert data["identified_invoices"] == 0
    assert data["vendor_runs"] == [{"vendor": "Harbor Parts", "count": 1}]
    matches = alice.get(
        "/analytics/records", params={"view": "exceptions", "key": "REJECTED", "vendor": "Harbor"}
    ).json()
    assert matches["record_count"] == 1
    assert matches["records"][0]["vendor"] == "Harbor Parts"
