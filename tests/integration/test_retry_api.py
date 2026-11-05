"""Retries are linked, owner-scoped and cannot pay the same payable twice."""

import threading

from tests.integration.test_api import alice as alice
from tests.integration.test_api import api as api
from tests.integration.test_api import bob as bob
from tests.integration.test_api import ctx as ctx


def interrupt(ctx, run_id):
    with ctx.db.transaction() as conn:
        conn.execute("UPDATE runs SET lifecycle='interrupted' WHERE run_id=?", (run_id,))


def test_retry_of_interrupted_paid_run_never_pays_twice(alice, bob, ctx):
    original = alice.run("invoice_1001.txt")
    assert original["outcome"] == "paid"
    interrupt(ctx, original["run_id"])
    path = f"/runs/{original['run_id']}/retry"
    payload = {"mode": "mock", "request_token": "retry-test-token-1"}
    assert bob.post(path, json=payload).status_code == 404
    assert alice.post(path, json=payload, csrf=None).status_code == 403
    created = alice.post(path, json=payload)
    assert created.status_code == 202, created.text
    replay = alice.post(path, json=payload)
    assert replay.status_code == 200
    assert replay.json()["run_id"] == created.json()["run_id"]
    child = alice.wait(created.json()["run_id"])
    assert child["attempt_of"] == original["run_id"]
    assert child["outcome"] != "paid"
    with ctx.db.read() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM payments WHERE workspace_id=?", (alice.workspace,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM run_links WHERE workspace_id=?", (alice.workspace,)
            ).fetchone()[0]
            == 1
        )


def test_retry_rejects_success_and_token_reuse(alice, ctx):
    first = alice.run("invoice_1001.txt")
    path = f"/runs/{first['run_id']}/retry"
    payload = {"mode": "mock", "request_token": "retry-test-token-2"}
    assert alice.post(path, json=payload).status_code == 409
    interrupt(ctx, first["run_id"])
    assert alice.post(path, json=payload).status_code == 202
    assert alice.post("/runs/run_other/retry", json=payload).status_code == 409


def test_concurrent_retry_clicks_create_only_one_run(alice, ctx):
    original = alice.run("invoice_1001.txt")
    interrupt(ctx, original["run_id"])
    path = f"/runs/{original['run_id']}/retry"
    payload = {"mode": "mock", "request_token": "retry-test-concurrent"}
    barrier = threading.Barrier(3)
    results = []

    def submit():
        barrier.wait()
        results.append(alice.post(path, json=payload))

    workers = [threading.Thread(target=submit) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join()
    assert sorted(response.status_code for response in results) == [200, 202]
    assert len({response.json()["run_id"] for response in results}) == 1
    alice.wait(results[0].json()["run_id"])
    with ctx.db.read() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM payments WHERE workspace_id=?", (alice.workspace,)
            ).fetchone()[0]
            == 1
        )


def test_queued_retry_keeps_parent_link_during_recovery(alice, ctx):
    from invoice_flow.api.jobs import JobRunner
    from invoice_flow.storage import create_run

    original = alice.run("invoice_1001.txt")
    with ctx.db.transaction() as conn:
        child = create_run(
            conn,
            workspace_id=alice.workspace,
            now=ctx.db.now(),
            document_id=original["document_id"],
            mode="mock",
            lifecycle="queued",
        )
        conn.execute(
            "INSERT INTO run_links (workspace_id,child_run_id,parent_run_id,kind)"
            " VALUES (?,?,?,'retry')",
            (alice.workspace, child, original["run_id"]),
        )
    runner = JobRunner(ctx)
    runner.recover()
    job = runner._queue.get_nowait()
    assert job.run_id == child
    assert job.attempt_of == original["run_id"]
    assert runner.result(alice.workspace, child).attempt_of == original["run_id"]


def test_correction_preserves_source_and_enforces_workspace_and_idempotency(alice, bob, ctx):
    from conftest import SAMPLES

    original = alice.run("invoice_1007.csv")
    assert original["outcome"] == "rejected"
    raw = (SAMPLES / "invoice_1007.csv").read_bytes()
    same = alice.post("/documents", files={"file": ("same.csv", raw, "text/csv")}).json()[
        "document_id"
    ]
    path = f"/runs/{original['run_id']}/correction"
    payload = {"mode": "mock", "request_token": "correction-token-1", "document_id": same}
    assert alice.post(path, json=payload).status_code == 409
    changed = raw.replace(b"15525", b"15635") + b"\n"
    other = bob.post("/documents", files={"file": ("other.csv", changed, "text/csv")}).json()[
        "document_id"
    ]
    assert alice.post(path, json={**payload, "document_id": other}).status_code == 404
    uploaded = alice.post("/documents", files={"file": ("corrected.csv", changed, "text/csv")})
    assert uploaded.status_code == 201, uploaded.text
    payload["document_id"] = uploaded.json()["document_id"]
    assert alice.post(path, json=payload, csrf=None).status_code == 403
    created = alice.post(path, json=payload)
    assert created.status_code == 202, created.text
    assert alice.post(path, json=payload).json()["run_id"] == created.json()["run_id"]
    child = alice.wait(created.json()["run_id"])
    assert child["attempt_of"] == original["run_id"]
    assert child["relation_kind"] == "correction"
    assert alice.get(f"/runs/{original['run_id']}/source").status_code == 200
    with ctx.db.read() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM run_links WHERE workspace_id=?", (alice.workspace,)
            ).fetchone()[0]
            == 1
        )


def test_correction_rejects_non_rejected_parent(alice):
    paid = alice.run("invoice_1001.txt")
    payload = {
        "mode": "mock",
        "request_token": "correction-invalid-parent",
        "document_id": paid["document_id"],
    }
    assert alice.post(f"/runs/{paid['run_id']}/correction", json=payload).status_code == 409


def test_unrelated_valid_correction_is_held_and_cannot_pay(alice, ctx):
    from conftest import SAMPLES

    original = alice.run("invoice_1007.csv")
    assert original["outcome"] == "rejected"
    clean = (SAMPLES / "invoice_1001.txt").read_bytes()
    upload = alice.post("/documents", files={"file": ("different.txt", clean, "text/plain")})
    path = f"/runs/{original['run_id']}/correction"
    response = alice.post(
        path,
        json={
            "mode": "mock",
            "request_token": "unrelated-correction",
            "document_id": upload.json()["document_id"],
        },
    )
    assert response.status_code == 202, response.text
    child = alice.wait(response.json()["run_id"])
    assert child["outcome"] == "needs_review"
    assert child["receipt"] is None
    assert "could not be matched" in child["terminal_reason"]
    assert not alice.get(f"/runs/{child['run_id']}/review-options").json()["can_approve"]
    with ctx.db.read() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM payments WHERE workspace_id=?", (alice.workspace,)
            ).fetchone()[0]
            == 0
        )


def test_unrelated_correction_cannot_pay_after_resolving_its_vendor_warning(alice, ctx):
    from conftest import SAMPLES
    from tests.integration.test_api import review_body
    from workflow_helpers import payments

    original = alice.run("invoice_1007.csv")
    assert original["outcome"] == "rejected"
    raw = (SAMPLES / "invoice_1012.txt").read_bytes()
    uploaded = alice.post("/documents", files={"file": ("unrelated.txt", raw, "text/plain")})
    assert uploaded.status_code == 201, uploaded.text
    created = alice.post(
        f"/runs/{original['run_id']}/correction",
        json={
            "mode": "mock",
            "request_token": "correction-with-vendor-warning",
            "document_id": uploaded.json()["document_id"],
        },
    )
    assert created.status_code == 202, created.text
    child = alice.wait(created.json()["run_id"])
    assert child["outcome"] == "needs_review"
    assert child["invoice"]["total"] == "9975.00"
    assert "VENDOR_IDENTITY_CHANGE" in {finding["code"] for finding in child["findings"]}
    assert child["receipt"] is None

    # Bypass the UI and attempt to approve after resolving the otherwise-clearable warning.
    response = alice.post(f"/runs/{child['run_id']}/review", json=review_body(child))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "not_approvable"
    assert payments(ctx) == []
    persisted = alice.get(f"/runs/{child['run_id']}").json()
    assert persisted["outcome"] == "needs_review" and persisted["receipt"] is None
    assert "could not be matched" in persisted["terminal_reason"]
    assert not alice.get(f"/runs/{child['run_id']}/review-options").json()["can_approve"]
    with ctx.db.read() as conn:
        flag = conn.execute(
            "SELECT identity_flag FROM invoice_versions WHERE version_id=?", (child["version_id"],)
        ).fetchone()[0]
    assert flag == "correction_identity"
