"""Internal handoffs preserve ownership, audit history and payment gates."""

import pytest
from fastapi.testclient import TestClient

from invoice_flow.api import create_app
from tests.integration.test_api import ORIGIN, Browser, make_ctx, review_body


@pytest.fixture
def browsers(tmp_path):
    ctx = make_ctx(tmp_path)
    api = create_app(ctx)
    with TestClient(api, base_url=ORIGIN):
        yield ctx, Browser(api), Browser(api)


def body(run, **changes):
    payload = {
        "version_id": run["version_id"],
        "content_hash": run["content_hash"],
        "department": "vendor_management",
        "reason_code": "vendor_verification",
        "idempotency_token": "handoff-first-request",
    }
    return payload | changes


def test_handoff_is_durable_idempotent_and_does_not_authorize_payment(browsers):
    ctx, alice, _ = browsers
    run = alice.run("invoice_1012.txt")
    path = f"/runs/{run['run_id']}/routing"
    first = alice.post(path, json=body(run))
    assert first.status_code == 200, first.text
    assert first.json()["active"] is True
    assert first.json()["notification_sent"] is False
    assert first.json()["note"] == ""
    assert alice.post(path, json=body(run)).json() == first.json()
    assert alice.get(path).json()["assignment"] == first.json()
    assert alice.get(f"/runs/{run['run_id']}").json()["outcome"] == "needs_review"
    with ctx.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_handoffs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM approval_decisions").fetchone()[0] == 0
        events = conn.execute(
            "SELECT type FROM run_events WHERE run_id = ?", (run["run_id"],)
        ).fetchall()
        assert sum(row[0] == "department_handoff" for row in events) == 1
    response = alice.get(f"/runs/{run['run_id']}/events")
    assert response.status_code == 200, response.text
    handoffs = [e for e in response.json()["events"] if e["type"] == "department_handoff"]
    assert len(handoffs) == 1
    assert handoffs[0]["payload"]["notification_sent"] is False
    assert "No notification was sent" in handoffs[0]["payload"]["message"]
    assert alice.post(path, json=body(run, note="Changed")).status_code == 409


def test_routing_enforces_ownership_csrf_and_validated_options(browsers):
    _, alice, bob = browsers
    run = alice.run("invoice_1012.txt")
    path = f"/runs/{run['run_id']}/routing"
    assert bob.get(path).status_code == 404
    assert bob.post(path, json=body(run)).status_code == 404
    assert alice.post(path, json=body(run), csrf=None).status_code == 403
    assert alice.post(path, json=body(run, department="unknown")).status_code == 422
    assert alice.post(path, json=body(run, reason_code="unknown")).status_code == 422


def test_stale_or_paid_invoice_cannot_be_routed(browsers):
    _, alice, _ = browsers
    held = alice.run("invoice_1012.txt")
    assert (
        alice.post(
            f"/runs/{held['run_id']}/routing", json=body(held, content_hash="0" * 64)
        ).status_code
        == 409
    )
    assert alice.get(f"/runs/{held['run_id']}/routing").json()["assignment"] is None
    paid = alice.run("invoice_1001.txt")
    assert paid["outcome"] == "paid"
    assert alice.get(f"/runs/{paid['run_id']}/routing").json()["can_route"] is False
    assert alice.post(f"/runs/{paid['run_id']}/routing", json=body(paid)).status_code == 409


def test_reassignment_keeps_history_and_approval_closes_assignment(browsers):
    _, alice, _ = browsers
    run = alice.run("invoice_1012.txt")
    path = f"/runs/{run['run_id']}/routing"
    first = alice.post(path, json=body(run)).json()
    second = alice.post(
        path, json=body(run, department="procurement", idempotency_token="handoff-second-request")
    ).json()
    assert first["handoff_id"] != second["handoff_id"]
    assert alice.get(path).json()["assignment"] == second
    assert alice.post(path, json=body(run)).json()["active"] is False
    reviewed = alice.post(f"/runs/{run['run_id']}/review", json=review_body(run))
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["run"]["outcome"] == "paid"
    current = alice.get(path).json()
    assert current["assignment"]["active"] is False
    assert current["can_route"] is False
    assert alice.post(path, json=body(run)).json()["active"] is False
