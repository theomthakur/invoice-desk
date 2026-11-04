"""Review choices describe the currently payable version, including explicit conflict selection."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from conftest import TODAY
from invoice_flow.api import create_app
from invoice_flow.runtime import App, AppConfig
from invoice_flow.schemas import RunResult
from invoice_flow.service import process_batch
from invoice_flow.storage import Database

V1 = "/api/v1"
ORIGIN = "http://testserver"


@pytest.fixture
def conflict_review(tmp_path):
    ctx = App.create(
        AppConfig(data_dir=tmp_path, session_secret="review-regression-secret-0123456789"),
        db=Database(tmp_path / "review.db", clock=lambda: datetime(2026, 11, 3, tzinfo=UTC)),
    )
    ctx.mock_payment = lambda vendor, amount: {"status": "success"}
    with TestClient(create_app(ctx, start_worker=False), base_url=ORIGIN) as browser:
        session = browser.post(f"{V1}/session", headers={"origin": ORIGIN}).json()
        paths = []
        for quantity in (1, 2):
            path = tmp_path / f"choice-{quantity}.json"
            path.write_text(
                json.dumps(
                    {
                        "invoice_number": "REVIEW-CHOICE-28",
                        "vendor": "Choice Supplies Ltd.",
                        "date": "2026-11-01",
                        "due_date": "2026-11-30",
                        "currency": "USD",
                        "items": [{"item": "WidgetA", "quantity": quantity, "unit_price": 100}],
                        "subtotal": quantity * 100,
                        "total": quantity * 100,
                    }
                ),
                encoding="utf-8",
            )
            paths.append(path)
        report = process_batch(paths, app=ctx, workspace_id=session["workspace_id"], today=TODAY)
        with ctx.db.read() as conn:
            runs = [
                RunResult.model_validate_json(
                    conn.execute(
                        "SELECT result_json FROM runs WHERE run_id = ?", (row["run_id"],)
                    ).fetchone()["result_json"]
                )
                for row in report["files"]
            ]
        yield ctx, browser, session, runs


def options(browser, run):
    response = browser.get(f"{V1}/runs/{run.run_id}/review-options")
    assert response.status_code == 200, response.text
    return response.json()


def approve(browser, session, run, *, token, resolved_codes):
    return browser.post(
        f"{V1}/runs/{run.run_id}/review",
        headers={"origin": ORIGIN, "x-csrf-token": session["csrf_token"]},
        json={
            "version_id": run.version_id,
            "content_hash": run.content_hash,
            "decision": "approve",
            "comment": "Compared the original invoices and selected this version.",
            "resolved_codes": resolved_codes,
            "idempotency_token": token,
        },
    )


def test_valid_unpaid_conflicts_offer_explicit_version_selection(conflict_review):
    _, browser, _, runs = conflict_review
    for run in runs:
        choices = options(browser, run)
        assert choices["can_approve"] is True
        assert choices["can_reject"] is True
        assert choices["resolvable_codes"] == ["REVISION_CONFLICT"]
        assert choices["blocked_codes"] == []


def test_superseded_conflict_choices_stop_offering_approval_after_selection(conflict_review):
    _, browser, session, runs = conflict_review
    response = approve(
        browser, session, runs[1], token="select-version-0001", resolved_codes=["REVISION_CONFLICT"]
    )
    assert response.status_code == 200, response.text
    choices = options(browser, runs[0])
    assert choices["can_approve"] is False
    assert "REVISION_CONFLICT" not in choices["resolvable_codes"]


def test_a_failed_bank_call_leaves_the_selected_version_reviewable_without_a_stale_conflict(
    conflict_review,
):
    ctx, browser, session, runs = conflict_review
    ctx.mock_payment = lambda vendor, amount: {"status": "failed"}
    response = approve(
        browser, session, runs[1], token="select-version-0002", resolved_codes=["REVISION_CONFLICT"]
    )
    assert response.status_code == 422, response.text
    choices = options(browser, runs[1])
    assert choices["can_approve"] is True
    assert choices["resolvable_codes"] == []
    assert choices["blocked_codes"] == []
    assert options(browser, runs[0])["can_approve"] is False
    ctx.mock_payment = lambda vendor, amount: {"status": "success"}
    retried = approve(browser, session, runs[1], token="retry-payment-0001", resolved_codes=[])
    assert retried.status_code == 200, retried.text
    assert retried.json()["run"]["outcome"] == "paid"


def test_another_identity_hold_prevents_offering_conflict_selection(conflict_review):
    ctx, browser, _, runs = conflict_review
    with ctx.db.transaction() as conn:
        conn.execute(
            "UPDATE invoice_versions SET identity_flag = 'revision_after_payment'"
            " WHERE version_id = ?",
            (runs[0].version_id,),
        )
    choices = options(browser, runs[1])
    assert choices["can_approve"] is False
    assert "REVISION_CONFLICT" in choices["blocked_codes"]


def test_current_validation_errors_prevent_offering_conflict_selection(conflict_review):
    ctx, browser, _, runs = conflict_review
    with ctx.db.transaction() as conn:
        conn.execute("UPDATE inventory SET stock = 0 WHERE item = 'WidgetA'")
    choices = options(browser, runs[1])
    assert choices["can_approve"] is False
    assert "REVISION_CONFLICT" in choices["blocked_codes"]
