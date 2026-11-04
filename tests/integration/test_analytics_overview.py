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
