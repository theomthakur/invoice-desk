"""Local browser workspaces survive restarts without weakening session boundaries."""

from __future__ import annotations

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from invoice_flow.api import create_app
from invoice_flow.api.security import SESSION_COOKIE
from invoice_flow.runtime import App, AppConfig
from invoice_flow.storage import Database, create_run

ORIGIN = "http://testserver"
V1 = "/api/v1"
CONFIGURED_SECRET = "configured-signing-secret-0123456789"
SECRET_FILE = ".session-secret"


def context(folder: Path, **config) -> App:
    config.setdefault("data_dir", folder)
    return App.create(AppConfig(**config), db=Database(folder / "api.db"))


def login(client: TestClient) -> dict:
    response = client.post(f"{V1}/session", headers={"origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()


def remember_run(ctx: App, workspace_id: str) -> str:
    with ctx.db.transaction() as conn:
        return create_run(
            conn,
            workspace_id=workspace_id,
            now="2026-11-03T12:00:00.000000+00:00",
            lifecycle="completed",
        )


def test_default_local_restart_keeps_the_browser_workspace_history_and_csrf(tmp_path):
    first_ctx = context(tmp_path)
    with TestClient(create_app(first_ctx, start_worker=False), base_url=ORIGIN) as first:
        session = login(first)
        run_id = remember_run(first_ctx, session["workspace_id"])
        cookie = first.cookies.get(SESSION_COOKIE)

    with TestClient(create_app(context(tmp_path), start_worker=False), base_url=ORIGIN) as again:
        again.cookies.set(SESSION_COOKIE, cookie)
        recovered = again.get(f"{V1}/session")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["workspace_id"] == session["workspace_id"]
        assert [item["run_id"] for item in again.get(f"{V1}/history").json()["items"]] == [run_id]
        assert login(again)["workspace_id"] == session["workspace_id"]
        reset = again.post(
            f"{V1}/workspace/reset",
            headers={"origin": ORIGIN, "x-csrf-token": session["csrf_token"]},
        )
        assert reset.status_code == 200, reset.text


def test_restarted_local_sessions_keep_workspaces_isolated(tmp_path):
    ctx = context(tmp_path)
    api = create_app(ctx, start_worker=False)
    with TestClient(api, base_url=ORIGIN) as alice, TestClient(api, base_url=ORIGIN) as bob:
        a_session, b_session = login(alice), login(bob)
        a_run = remember_run(ctx, a_session["workspace_id"])
        b_run = remember_run(ctx, b_session["workspace_id"])
        a_cookie, b_cookie = alice.cookies.get(SESSION_COOKIE), bob.cookies.get(SESSION_COOKIE)

    restarted = create_app(context(tmp_path), start_worker=False)
    with (
        TestClient(restarted, base_url=ORIGIN) as alice,
        TestClient(restarted, base_url=ORIGIN) as bob,
    ):
        alice.cookies.set(SESSION_COOKIE, a_cookie)
        bob.cookies.set(SESSION_COOKIE, b_cookie)
        assert [item["run_id"] for item in alice.get(f"{V1}/history").json()["items"]] == [a_run]
        assert [item["run_id"] for item in bob.get(f"{V1}/history").json()["items"]] == [b_run]
        assert alice.get(f"{V1}/runs/{b_run}").status_code == 404
        assert bob.get(f"{V1}/runs/{a_run}").status_code == 404
        assert (
            bob.post(
                f"{V1}/workspace/reset",
                headers={"origin": ORIGIN, "x-csrf-token": a_session["csrf_token"]},
            ).status_code
            == 403
        )


def test_a_restart_does_not_extend_an_expired_session(tmp_path, monkeypatch):
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: 1_700_000_000)
    first_ctx = context(tmp_path, session_ttl_seconds=60)
    with TestClient(create_app(first_ctx, start_worker=False), base_url=ORIGIN) as first:
        session = login(first)
        remember_run(first_ctx, session["workspace_id"])
        cookie = first.cookies.get(SESSION_COOKIE)

    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: 1_700_000_061)
    api = create_app(context(tmp_path, session_ttl_seconds=60), start_worker=False)
    with TestClient(api, base_url=ORIGIN) as again:
        again.cookies.set(SESSION_COOKIE, cookie)
        response = again.get(f"{V1}/history")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "no_session"
        assert login(again)["workspace_id"] != session["workspace_id"]
        assert again.get(f"{V1}/history").json()["items"] == []


def test_simultaneous_local_startups_share_one_fully_written_secret(tmp_path):
    ctx = context(tmp_path)
    barrier = Barrier(8)

    def start(_):
        barrier.wait(timeout=10)
        return create_app(ctx, start_worker=False)

    with ThreadPoolExecutor(max_workers=8) as pool:
        apps = list(pool.map(start, range(8)))
    with TestClient(apps[0], base_url=ORIGIN) as first:
        workspace_id = login(first)["workspace_id"]
        cookie = first.cookies.get(SESSION_COOKIE)
    for api in apps[1:]:
        with TestClient(api, base_url=ORIGIN) as other:
            other.cookies.set(SESSION_COOKIE, cookie)
            response = other.get(f"{V1}/session")
            assert response.status_code == 200, response.text
            assert response.json()["workspace_id"] == workspace_id
    assert sorted(p.name for p in tmp_path.glob(".session-secret*")) == [SECRET_FILE]


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner permissions")
def test_the_generated_local_secret_is_private_to_its_owner(tmp_path):
    create_app(context(tmp_path), start_worker=False)
    assert stat.S_IMODE((tmp_path / SECRET_FILE).stat().st_mode) == 0o600


@pytest.mark.parametrize("contents", [b"", b"too-short\n", b"x" * 200, b"\xff" * 43])
def test_invalid_persisted_local_secrets_fail_startup_instead_of_rotating(tmp_path, contents):
    path = tmp_path / SECRET_FILE
    path.write_bytes(contents)
    path.chmod(0o600)
    with pytest.raises(RuntimeError, match="local session secret"):
        create_app(context(tmp_path), start_worker=False)
    assert path.read_bytes() == contents


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner permissions")
def test_a_world_readable_local_secret_is_refused(tmp_path):
    path = tmp_path / SECRET_FILE
    path.write_text("x" * 43 + "\n", encoding="ascii")
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="local session secret"):
        create_app(context(tmp_path), start_worker=False)


@pytest.mark.skipif(
    os.name != "posix" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="requires POSIX file access checks for a non-root owner",
)
def test_an_unreadable_local_secret_fails_clearly(tmp_path):
    path = tmp_path / SECRET_FILE
    path.write_text("x" * 43 + "\n", encoding="ascii")
    path.chmod(0)
    try:
        with pytest.raises(RuntimeError, match="local session secret"):
            create_app(context(tmp_path), start_worker=False)
    finally:
        path.chmod(0o600)


def test_a_non_file_secret_location_is_refused(tmp_path):
    (tmp_path / SECRET_FILE).mkdir()
    with pytest.raises(RuntimeError, match="local session secret"):
        create_app(context(tmp_path), start_worker=False)


def test_a_symlink_cannot_substitute_the_local_secret(tmp_path):
    target = tmp_path / "other-secret"
    target.write_text("x" * 43 + "\n", encoding="ascii")
    target.chmod(0o600)
    (tmp_path / SECRET_FILE).symlink_to(target)
    with pytest.raises(RuntimeError, match="local session secret"):
        create_app(context(tmp_path), start_worker=False)


def test_configured_secrets_bypass_local_secret_storage(tmp_path):
    path = tmp_path / SECRET_FILE
    path.write_bytes(b"invalid persisted secret")
    api = create_app(context(tmp_path, session_secret=CONFIGURED_SECRET), start_worker=False)
    with TestClient(api, base_url=ORIGIN) as first:
        workspace_id = login(first)["workspace_id"]
        cookie = first.cookies.get(SESSION_COOKIE)
    restarted = create_app(context(tmp_path, session_secret=CONFIGURED_SECRET), start_worker=False)
    with TestClient(restarted, base_url=ORIGIN) as again:
        again.cookies.set(SESSION_COOKIE, cookie)
        assert again.get(f"{V1}/session").json()["workspace_id"] == workspace_id
    assert path.read_bytes() == b"invalid persisted secret"


def test_separate_local_data_directories_do_not_share_signing_keys(tmp_path):
    with TestClient(create_app(context(tmp_path / "one"), start_worker=False)) as first:
        login(first)
        cookie = first.cookies.get(SESSION_COOKIE)
    with TestClient(create_app(context(tmp_path / "two"), start_worker=False)) as other:
        other.cookies.set(SESSION_COOKIE, cookie)
        assert other.get(f"{V1}/session").status_code == 401


def test_production_still_requires_an_explicit_secret_even_with_a_local_file(tmp_path):
    create_app(context(tmp_path), start_worker=False)
    with pytest.raises(RuntimeError, match="SESSION_SECRET is required in production"):
        create_app(context(tmp_path, app_env="production"), start_worker=False)
