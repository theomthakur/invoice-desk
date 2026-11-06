"""Persisted batch recovery, bounded processing and transactional request ownership."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from invoice_flow.api import create_app
from invoice_flow.api.batches import BatchRunner
from invoice_flow.api.jobs import QueueFull, RetryError
from invoice_flow.storage import ensure_workspace
from tests.integration.test_api import Browser, make_ctx, wait_batch
from workflow_helpers import payments


def test_queued_batch_survives_restart_without_a_second_payment(tmp_path):
    ctx = make_ctx(tmp_path)
    with TestClient(create_app(ctx, start_worker=False)) as client:
        browser = Browser(client.app)
        accepted = browser.post(
            "/batches",
            json={"sample_ids": ["invoice_1006.csv"], "request_token": "queued-batch-recovery"},
        )
        batch_id = accepted.json()["batch_id"]
        cookies = browser.client.cookies
        csrf = browser.csrf
        assert browser.get(f"/batches/{batch_id}").json()["lifecycle"] == "queued"
    second = create_app(make_ctx(tmp_path))
    with TestClient(second):
        browser = Browser(second, login=False)
        browser.client.cookies.update(cookies)
        assert wait_batch(browser, batch_id)["lifecycle"] == "completed"
        assert len(payments(ctx)) == 1
        browser.csrf = csrf
        duplicate = browser.post(
            "/batches",
            json={"sample_ids": ["invoice_1006.csv"], "request_token": "queued-batch-recovery"},
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["already_existed"] and duplicate.json()["batch_id"] == batch_id
        rerun = browser.post(
            "/batches",
            json={"sample_ids": ["invoice_1006.csv"], "request_token": "explicit-batch-rerun"},
        )
        assert wait_batch(browser, rerun.json()["batch_id"])["lifecycle"] == "completed"
        assert len(payments(ctx)) == 1


def test_running_batch_is_interrupted_after_restart_and_not_replayed(tmp_path):
    ctx = make_ctx(tmp_path)
    with TestClient(create_app(ctx, start_worker=False)) as client:
        browser = Browser(client.app)
        cookies = browser.client.cookies
        with ctx.db.transaction() as conn:
            conn.execute(
                "INSERT INTO batches (batch_id,workspace_id,lifecycle,mode,"
                "document_ids_json,created_at,finished_at,result_json) "
                "VALUES (?,?, 'running','mock',?,?,NULL,NULL)",
                ("bat_stale", browser.workspace, json.dumps(["invoice_1006.csv"]), ctx.db.now()),
            )
    with TestClient(create_app(make_ctx(tmp_path))) as client:
        again = Browser(client.app, login=False)
        again.client.cookies.update(cookies)
        result = again.get("/batches/bat_stale").json()
        assert result["lifecycle"] == "interrupted"
        assert "restart" in result["terminal_reason"]
        assert payments(ctx) == []


def runner_for(ctx, process):
    with ctx.db.transaction() as conn:
        ensure_workspace(conn, "batch-ws", "session", ctx.db.now())
    return BatchRunner(ctx, process)


def wait_state(ctx, batch_id):
    for _ in range(100):
        with ctx.db.read() as conn:
            row = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if row["lifecycle"] in {"completed", "failed", "interrupted"}:
            return row
        time.sleep(0.01)
    raise AssertionError("Batch did not finish")


def test_concurrent_repeated_token_creates_one_batch_and_rejects_new_payload(tmp_path):
    ctx = make_ctx(tmp_path)
    runner = runner_for(ctx, lambda *args: {})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: runner.submit("batch-ws", ["a"], "mock", "same-token", "payload"),
                range(2),
            )
        )
    assert results[0][0] == results[1][0]
    assert sorted(result[1] for result in results) == [False, True]
    with pytest.raises(RetryError):
        runner.submit("batch-ws", ["b"], "mock", "same-token", "different")
    with ctx.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM batches").fetchone()[0] == 1


def test_batch_queue_capacity_does_not_consume_the_rejected_request_token(tmp_path):
    ctx = make_ctx(tmp_path, queue_capacity=1)
    runner = runner_for(ctx, lambda *args: {})
    runner.submit("batch-ws", ["a"], "mock", "first", "a")
    with pytest.raises(QueueFull):
        runner.submit("batch-ws", ["b"], "mock", "second", "b")
    with ctx.db.read() as conn:
        assert (
            conn.execute("SELECT count(*) FROM request_tokens WHERE token='second'").fetchone()[0]
            == 0
        )


def test_failed_batch_does_not_kill_the_next_queued_batch_or_echo_secrets(tmp_path):
    ctx = make_ctx(tmp_path)

    def process(ws, names, mode, cancel):
        if names == ["bad"]:
            raise ValueError("private source data must never be echoed")
        return {"files": []}

    runner = runner_for(ctx, process)
    bad, _ = runner.submit("batch-ws", ["bad"], "mock", "bad-token", "bad")
    good, _ = runner.submit("batch-ws", ["good"], "mock", "good-token", "good")
    runner.start()
    try:
        failed = wait_state(ctx, bad)
        assert failed["lifecycle"] == "failed"
        assert "private source" not in failed["result_json"]
        assert wait_state(ctx, good)["lifecycle"] == "completed"
    finally:
        runner.stop()


def test_shutdown_cancels_an_active_batch_and_records_interruption(tmp_path):
    ctx = make_ctx(tmp_path)
    started = threading.Event()

    def process(ws, names, mode, cancel):
        started.set()
        assert cancel.wait(2)
        return {"files": []}

    runner = runner_for(ctx, process)
    batch_id, _ = runner.submit("batch-ws", ["a"], "mock", "cancel-token", "a")
    runner.start()
    assert started.wait(2)
    runner.stop()
    assert wait_state(ctx, batch_id)["lifecycle"] == "interrupted"


@pytest.mark.parametrize("state", ["queued", "running"])
def test_workspace_reset_cannot_delete_a_batch_before_it_creates_individual_runs(tmp_path, state):
    ctx = make_ctx(tmp_path)
    app = create_app(ctx, start_worker=False)
    with TestClient(app):
        browser = Browser(app)
        with ctx.db.transaction() as conn:
            conn.execute(
                "INSERT INTO batches (batch_id,workspace_id,lifecycle,mode,"
                "document_ids_json,created_at,finished_at,result_json) "
                "VALUES (?, ?, ?, 'mock', ?, ?, NULL, NULL)",
                (
                    "bat_reset_guard",
                    browser.workspace,
                    state,
                    json.dumps(["invoice_1006.csv"]),
                    ctx.db.now(),
                ),
            )
        response = browser.post("/workspace/reset")
        assert response.status_code == 409
        with ctx.db.read() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM batches WHERE batch_id=?", ("bat_reset_guard",)
                ).fetchone()[0]
                == 1
            )
            assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
