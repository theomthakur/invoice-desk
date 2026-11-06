"""Model choices are bounded, persisted and isolated; these tests make no paid calls."""

import threading

import pytest
from fastapi.testclient import TestClient

from invoice_flow.api import create_app
from invoice_flow.api.jobs import JobRunner
from invoice_flow.runtime import DEFAULT_MODEL, App
from tests.integration.test_api import Browser, make_ctx

HAIKU = "anthropic/claude-haiku-4.5"
GPT = "openai/gpt-4o-mini"


def live_ctx(tmp_path):
    return make_ctx(
        tmp_path,
        openrouter_api_key="fake-test-key",
        model_budget_usd=1,
        allow_live_in_sessions=True,
    )


def test_live_config_presents_real_models_and_prefers_live(tmp_path):
    ctx = live_ctx(tmp_path)
    with TestClient(create_app(ctx, start_worker=False)) as client:
        info = client.get("/api/v1/config").json()
    assert info["default_mode"] == "live"
    assert info["default_model"] == DEFAULT_MODEL
    assert {m["id"] for m in info["model_options"]} == {DEFAULT_MODEL, HAIKU, GPT}
    assert "fake-test-key" not in str(info)


@pytest.mark.parametrize("path", ["/runs", "/batches"])
def test_unapproved_models_are_rejected_before_queueing(tmp_path, path):
    ctx = live_ctx(tmp_path)
    with TestClient(create_app(ctx, start_worker=False)) as client:
        browser = Browser(client.app)
        body = {"mode": "live", "model": "unapproved/model", "request_token": "bad-model"}
        if path == "/runs":
            body["sample_id"] = "invoice_1001.txt"
        response = browser.post(path, json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "model_unavailable"
    assert JobRunner(ctx).pending() == 0


def test_choice_survives_restart_and_is_used_without_mutating_shared_config(tmp_path, monkeypatch):
    ctx = live_ctx(tmp_path)
    with TestClient(create_app(ctx, start_worker=False)) as client:
        browser = Browser(client.app)
        for i, model in enumerate([HAIKU, GPT]):
            result = browser.post(
                "/runs",
                json={
                    "mode": "live",
                    "model": model,
                    "sample_id": "invoice_1001.txt",
                    "request_token": f"choice-{i}",
                },
            )
            assert result.status_code == 202
    recovered = JobRunner(ctx)
    recovered.recover()
    seen = []
    monkeypatch.setattr(
        "invoice_flow.api.jobs.execute_run", lambda app, **kw: seen.append(app.config.llm_model)
    )
    for _ in range(2):
        job = recovered._queue.get_nowait()
        recovered._run(job, threading.Event())
    assert seen == [HAIKU, GPT]
    assert ctx.config.llm_model == DEFAULT_MODEL


def test_reusing_token_with_different_model_conflicts(tmp_path):
    ctx = live_ctx(tmp_path)
    with TestClient(create_app(ctx, start_worker=False)) as client:
        browser = Browser(client.app)
        body = {
            "mode": "live",
            "model": HAIKU,
            "sample_id": "invoice_1001.txt",
            "request_token": "same-choice",
        }
        first = browser.post("/runs", json=body)
        assert first.status_code == 202
        again = browser.post("/runs", json=body)
        assert again.status_code == 200
        body["model"] = GPT
        assert browser.post("/runs", json=body).status_code == 409


def test_batch_choice_is_durable_and_old_schema_upgrades(tmp_path):
    ctx = live_ctx(tmp_path)
    with ctx.db.transaction() as conn:
        conn.execute("ALTER TABLE batches DROP COLUMN model")
    ctx = App.create(ctx.config, db=ctx.db, samples_dir=ctx.samples_dir)
    with TestClient(create_app(ctx, start_worker=False)) as client:
        browser = Browser(client.app)
        result = browser.post(
            "/batches",
            json={
                "mode": "live",
                "model": GPT,
                "sample_ids": ["invoice_1001.txt"],
                "request_token": "batch-choice",
            },
        )
        assert result.status_code == 202
        with ctx.db.read() as conn:
            row = conn.execute(
                "SELECT model FROM batches WHERE batch_id=?", (result.json()["batch_id"],)
            ).fetchone()
        assert row["model"] == GPT


def test_batch_worker_receives_saved_model(tmp_path):
    from invoice_flow.api.batches import BatchRunner
    from invoice_flow.storage import ensure_workspace

    ctx = live_ctx(tmp_path)
    with ctx.db.transaction() as conn:
        ensure_workspace(conn, "worker-model", "session", ctx.db.now())
    seen = []
    done = threading.Event()

    def process(ws, names, mode, cancel, model=None):
        seen.append((mode, model, ctx.for_model(model).config.llm_model))
        done.set()
        return {}

    runner = BatchRunner(ctx, process)
    runner.submit("worker-model", ["invoice_1001.txt"], "live", "worker-token", "hash", model=HAIKU)
    runner.start()
    try:
        assert done.wait(3)
        assert seen == [("live", HAIKU, HAIKU)]
        assert ctx.config.llm_model == DEFAULT_MODEL
    finally:
        runner.stop()
