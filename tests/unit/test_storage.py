from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from conftest import make_draft
from helpers import approve, pay, payments, submit
from invoice_flow.storage import (
    LOCAL_WORKSPACE,
    STARTER_INVENTORY,
    Database,
    add_alias,
    append_event,
    claim_request_token,
    create_run,
    ensure_workspace,
    init_database,
    list_events,
    load_inventory_index,
    reset_database,
    reset_workspace,
    save_findings,
)


def test_seed_matches_the_required_starter_stock(db):
    with db.read() as conn:
        stock = {r["item"]: r["stock"] for r in conn.execute("SELECT item, stock FROM inventory")}
    assert stock == {"WidgetA": 15, "WidgetB": 10, "GadgetX": 5, "FakeItem": 0} == STARTER_INVENTORY


def test_init_is_idempotent_and_never_overwrites_state(db, policy):
    approve_receipt = None
    reg, _ = submit(db, policy, make_draft())
    approve_receipt = approve(db, policy, reg.version_id)
    pay(db, policy, reg.version_id, approve_receipt.approval_id)
    with db.transaction() as conn:
        conn.execute("UPDATE inventory SET stock = 7 WHERE item = 'WidgetA'")
    init_database(db)
    init_database(db)
    with db.read() as conn:
        assert load_inventory_index(conn).stock["WidgetA"] == 7  # an operator's change survives
    assert len(payments(db)) == 1  # so does the payment history


def test_reset_requires_explicit_confirmation(tmp_path):
    path = tmp_path / "x.db"
    database = Database(path)
    init_database(database)
    with pytest.raises(PermissionError):
        reset_database(path, confirm_delete_all_data=False)
    assert path.exists()
    reset_database(path, confirm_delete_all_data=True)
    assert not path.exists()


def test_foreign_keys_are_enforced(db):
    with db.transaction() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO runs (run_id, workspace_id, lifecycle, started_at) VALUES (?,?,?,?)",
            ("run_x", "no_such_workspace", "running", db.now()),
        )


def test_check_constraints_reject_bad_enums_and_money(db):
    with db.transaction() as conn:
        ensure_workspace(conn, "w", "session", db.now())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs (run_id, workspace_id, lifecycle, started_at)"
                " VALUES ('r','w','bogus','t')"
            )
    with db.transaction() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE inventory SET stock = -1 WHERE item = 'WidgetA'")


def test_transactions_roll_back_on_error(db):
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("UPDATE inventory SET stock = 1 WHERE item = 'WidgetA'")
        raise RuntimeError("boom")
    with db.read() as conn:
        assert load_inventory_index(conn).stock["WidgetA"] == 15


def test_sql_injection_in_values_is_inert(db, policy):
    nasty = "x'); DROP TABLE inventory; --"
    reg, _ = submit(db, policy, make_draft(vendor=nasty, number=nasty))
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 4
        assert conn.execute("SELECT vendor_name FROM payables").fetchone()[0] == nasty
    assert reg.payable_id


def test_alias_is_added_by_an_operator_not_a_model(db, policy):
    add_alias(db, "Gizmo", "WidgetA")
    with db.read() as conn:
        assert load_inventory_index(conn).resolve("gizmo") == ("WidgetA", "ok")


def test_event_sequence_is_gapless_and_ordered(db):
    with db.transaction() as conn:
        ensure_workspace(conn, "w", "session", db.now())
        run = create_run(conn, workspace_id="w", now=db.now())
        seqs = [append_event(conn, run, "intake", "started", {"i": i}, db.now()) for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]
    with db.read() as conn:
        assert [e["seq"] for e in list_events(conn, "w", run)] == [1, 2, 3, 4, 5]
        assert [e["seq"] for e in list_events(conn, "w", run, after_seq=3)] == [4, 5]
        assert len(list_events(conn, "w", run, limit=2)) == 2
        assert list_events(conn, "w", run, after_seq=99) == []


def test_events_are_invisible_to_other_workspaces(db):
    with db.transaction() as conn:
        for ws in ("a", "b"):
            ensure_workspace(conn, ws, "session", db.now())
        run_a = create_run(conn, workspace_id="a", now=db.now())
        append_event(conn, run_a, "intake", "started", {"secret": "a-only"}, db.now())
    with db.read() as conn:
        assert len(list_events(conn, "a", run_a)) == 1
        assert list_events(conn, "b", run_a) == []  # guessing the ID gains nothing


def test_request_token_makes_a_double_click_return_the_same_run(db):
    with db.transaction() as conn:
        ensure_workspace(conn, "w", "session", db.now())
        first = create_run(conn, workspace_id="w", now=db.now())
        assert claim_request_token(conn, "w", "tok-1", first, db.now()) == first
        second = create_run(conn, workspace_id="w", now=db.now())
        assert claim_request_token(conn, "w", "tok-1", second, db.now()) == first
        other = create_run(conn, workspace_id="x_w", now=db.now()) if False else None  # noqa: F841


def test_findings_are_saved_per_run(db, index, policy):
    from invoice_flow.rules import validate_invoice

    report = validate_invoice(
        make_draft(lines=[("WidgetA", 99, "1")]),
        "",
        index,
        policy,
        datetime(2026, 11, 3, tzinfo=UTC).date(),
    )
    with db.transaction() as conn:
        run = create_run(conn, workspace_id=LOCAL_WORKSPACE, now=db.now())
        save_findings(conn, LOCAL_WORKSPACE, run, None, report.findings)
        save_findings(conn, LOCAL_WORKSPACE, run, None, report.findings)  # re-save replaces
        rows = conn.execute("SELECT code FROM findings WHERE run_id = ?", (run,)).fetchall()
    assert [r["code"] for r in rows].count("INSUFFICIENT_STOCK") == 1


def test_workspace_reset_only_touches_that_workspace(db, policy):
    a, _ = submit(db, policy, make_draft(), ws="ws_a")
    approve(db, policy, a.version_id, ws="ws_a")
    submit(db, policy, make_draft(number="OTHER"), ws="ws_b")
    reset_workspace(db, "ws_a")
    with db.read() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM invoice_versions WHERE workspace_id='ws_a'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM invoice_versions WHERE workspace_id='ws_b'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM approval_decisions WHERE workspace_id='ws_a'"
            ).fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 4  # shared data kept
    again, _ = submit(db, policy, make_draft(), ws="ws_a")  # and the workspace is reusable
    assert again.payable_id


def test_workspace_reset_is_blocked_during_an_active_run(db):
    with db.transaction() as conn:
        ensure_workspace(conn, "w", "session", db.now())
        create_run(conn, workspace_id="w", now=db.now(), lifecycle="running")
    with pytest.raises(RuntimeError):
        reset_workspace(db, "w")


def test_workspace_reset_removes_a_workspaces_payments_only(db, policy):
    reg, _ = submit(db, policy, make_draft(), ws="ws_a")
    pay(
        db,
        policy,
        reg.version_id,
        approve(db, policy, reg.version_id, ws="ws_a").approval_id,
        ws="ws_a",
    )
    reg_b, _ = submit(db, policy, make_draft(), ws="ws_b")
    pay(
        db,
        policy,
        reg_b.version_id,
        approve(db, policy, reg_b.version_id, ws="ws_b").approval_id,
        ws="ws_b",
    )
    reset_workspace(db, "ws_a")
    assert payments(db, "ws_a") == [] and len(payments(db, "ws_b")) == 1


def test_wal_mode_and_busy_timeout(db):
    with db.read() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
