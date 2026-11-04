"""Lookup never crosses workspaces or creates invoice actions."""

from tests.integration.test_api import alice as alice
from tests.integration.test_api import api as api
from tests.integration.test_api import bob as bob
from tests.integration.test_api import ctx as ctx


def test_lookup_finds_paid_version_and_isolates_other_workspaces(alice, bob):
    run = alice.run("invoice_1001.txt")
    assert run["outcome"] == "paid"
    response = alice.get("/invoices/lookup", params={"number": "INV 1001"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["matches"]) == 1
    match = body["matches"][0]
    assert match["number"] == "INV-1001"
    assert match["vendor"] == "Widgets Inc."
    assert match["paid"] is True
    assert match["payment_version_id"] == run["version_id"]
    assert match["versions"][0]["run_id"] == run["run_id"]
    assert bob.get("/invoices/lookup", params={"number": "INV-1001"}).json()["matches"] == []
    assert alice.get("/invoices/lookup", params={"number": "unknown"}).json()["matches"] == []
    assert alice.get("/invoices/lookup", params={"number": "---"}).status_code == 422


def test_lookup_preserves_distinct_vendors_and_version_holds(alice, ctx):
    run = alice.run("invoice_1012.txt")
    assert run["outcome"] == "needs_review"
    with ctx.db.transaction() as conn:
        conn.execute(
            "INSERT INTO payables (workspace_id,payable_id,vendor_key,number_key,"
            "vendor_name,number,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                alice.workspace,
                "pay_second",
                "other",
                "inv1012",
                "Other Vendor",
                "INV-1012",
                "2026-01-01",
            ),
        )
    matches = alice.get("/invoices/lookup", params={"number": "INV-1012"}).json()["matches"]
    assert len(matches) == 2
    held = next(m for m in matches if m["payable_id"] == run["payable_id"])
    assert held["paid"] is False
    assert held["versions"][0]["run_outcome"] == "needs_review"
    assert held["versions"][0]["run_id"] == run["run_id"]
