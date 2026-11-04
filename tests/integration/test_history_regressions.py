"""History traversal retains rows that share a clock tick and validates its cursors."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from invoice_flow.api import create_app
from invoice_flow.runtime import App, AppConfig
from invoice_flow.storage import Database

V1 = "/api/v1"
ORIGIN = "http://testserver"
TIED = "2026-11-03T12:00:00.000000+00:00"
OLDER = "2026-11-03T11:00:00.000000+00:00"
NEWER = "2026-11-03T13:00:00.000000+00:00"
RUN_A = "run_00000000000000000001"
RUN_B = "run_00000000000000000002"
RUN_C = "run_00000000000000000003"
RUN_D = "run_00000000000000000004"


@pytest.fixture
def history_api(tmp_path):
    ctx = App.create(
        AppConfig(data_dir=tmp_path, session_secret="history-regression-secret-0123456789"),
        db=Database(tmp_path / "history.db"),
    )
    api = create_app(ctx, start_worker=False)
    with TestClient(api, base_url=ORIGIN) as client:
        session = client.post(f"{V1}/session", headers={"origin": ORIGIN}).json()
        yield ctx, client, session["workspace_id"]


def add_runs(ctx, workspace_id, records):
    with ctx.db.transaction() as conn:
        conn.executemany(
            "INSERT INTO runs (run_id, workspace_id, lifecycle, started_at)"
            " VALUES (?,?,'completed',?)",
            [(run_id, workspace_id, timestamp) for run_id, timestamp in records],
        )


@pytest.mark.parametrize("page_size", [1, 2])
def test_tied_timestamps_are_stably_paginated_without_lost_or_repeated_runs(history_api, page_size):
    ctx, client, workspace_id = history_api
    add_runs(ctx, workspace_id, [(RUN_A, TIED), (RUN_C, TIED), (RUN_B, TIED)])
    seen = []
    cursor = None
    for _ in range(4):
        params = {"limit": page_size}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get(f"{V1}/history", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        seen.extend(item["run_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [RUN_C, RUN_B, RUN_A]
    assert cursor is None


def test_history_orders_by_time_before_the_run_id_tie_breaker(history_api):
    ctx, client, workspace_id = history_api
    add_runs(
        ctx,
        workspace_id,
        [(RUN_A, NEWER), (RUN_B, TIED), (RUN_C, TIED), (RUN_D, OLDER)],
    )
    page = client.get(f"{V1}/history", params={"limit": 2}).json()
    rest = client.get(f"{V1}/history", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert [item["run_id"] for item in page["items"] + rest["items"]] == [
        RUN_A,
        RUN_C,
        RUN_B,
        RUN_D,
    ]
    assert rest["next_cursor"] is None


def test_a_newer_run_does_not_shift_the_next_history_page(history_api):
    ctx, client, workspace_id = history_api
    add_runs(ctx, workspace_id, [(RUN_A, TIED), (RUN_B, TIED), (RUN_C, TIED)])
    page = client.get(f"{V1}/history", params={"limit": 1}).json()
    add_runs(ctx, workspace_id, [(RUN_D, NEWER)])
    rest = client.get(f"{V1}/history", params={"limit": 3, "cursor": page["next_cursor"]}).json()
    assert [item["run_id"] for item in rest["items"]] == [RUN_B, RUN_A]
    assert rest["next_cursor"] is None


def test_a_cursor_from_another_workspace_never_exposes_its_history(history_api):
    ctx, alice, a_workspace = history_api
    add_runs(ctx, a_workspace, [(RUN_A, TIED), (RUN_B, TIED), (RUN_C, TIED)])
    cursor = alice.get(f"{V1}/history", params={"limit": 1}).json()["next_cursor"]
    with TestClient(alice.app, base_url=ORIGIN) as bob:
        session = bob.post(f"{V1}/session", headers={"origin": ORIGIN}).json()
        add_runs(ctx, session["workspace_id"], [(RUN_D, OLDER)])
        response = bob.get(f"{V1}/history", params={"cursor": cursor})
        assert response.status_code == 200, response.text
        assert [item["run_id"] for item in response.json()["items"]] == [RUN_D]


def test_legacy_timestamp_cursors_remain_usable(history_api):
    ctx, client, workspace_id = history_api
    add_runs(ctx, workspace_id, [(RUN_A, NEWER), (RUN_B, TIED), (RUN_C, OLDER)])
    response = client.get(f"{V1}/history", params={"cursor": TIED})
    assert response.status_code == 200, response.text
    assert [item["run_id"] for item in response.json()["items"]] == [RUN_C]


def encoded_cursor(value):
    encoded = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return "h1." + encoded


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "not-a-cursor",
        "h2.unknown-version",
        "h1.@@@",
        "h1.____",
        encoded_cursor({"started_at": TIED, "run_id": RUN_A}),
        encoded_cursor([TIED]),
        encoded_cursor([TIED, RUN_A, "extra"]),
        encoded_cursor([None, RUN_A]),
        encoded_cursor([TIED, 5]),
        encoded_cursor(["2026-02-30T12:00:00+00:00", RUN_A]),
        encoded_cursor(["2026-11-03T12:00:00", RUN_A]),
        encoded_cursor(["0001-01-01T00:00:00+01:00", RUN_A]),
        encoded_cursor([TIED, "run_' OR 1=1 --"]),
        encoded_cursor([TIED, "run_" + "a" * 100]),
        "x" * 1000,
    ],
)
def test_malformed_history_cursors_use_the_bounded_api_error_contract(history_api, cursor):
    _, client, _ = history_api
    response = client.get(f"{V1}/history", params={"cursor": cursor})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["retryable"] is False
    assert error["request_id"].startswith("req_")
