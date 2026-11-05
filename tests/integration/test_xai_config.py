"""Expose provider readiness without exposing credentials or making model calls."""

from fastapi.testclient import TestClient

from invoice_flow.api import create_app
from invoice_flow.runtime import App, AppConfig, EventRecorder
from invoice_flow.storage import create_run, ensure_workspace


def test_xai_config_is_public_but_key_is_not(tmp_path):
    key = "xai-private-contract-placeholder-123456"
    cfg = AppConfig(
        data_dir=tmp_path,
        llm_provider="xai",
        xai_api_key=key,
        model_budget_usd=1,
        allow_live_in_sessions=True,
        session_secret="xai-session-placeholder-123456789",
    )
    app = App.create(cfg)
    with TestClient(create_app(app, start_worker=False)) as client:
        response = client.get("/api/v1/config")
        assert response.status_code == 200
        assert response.json()["model_provider"] == "xai"
        assert response.json()["enabled_modes"] == ["mock", "live"]
        assert key not in response.text
    with app.db.transaction() as conn:
        ensure_workspace(conn, "test", "local", app.db.now())
        run_id = create_run(conn, workspace_id="test", mode="live", now=app.db.now())
    EventRecorder(app.db, run_id, (key,))("approval", "model_error", {"message": key})
    with app.db.read() as conn:
        rows = conn.execute(
            "SELECT payload_json FROM run_events WHERE run_id = ?", (run_id,)
        ).fetchall()
    assert key not in str([dict(row) for row in rows])
