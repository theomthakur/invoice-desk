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
