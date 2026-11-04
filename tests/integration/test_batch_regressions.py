"""Batch decisions must survive resubmission and use the prepared source reading."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fpdf import FPDF

from conftest import TODAY
from invoice_flow.ledger import LedgerError, execute_mock_payment, record_approval, store_document
from invoice_flow.parsers import load_source
from invoice_flow.providers import ProviderError
from invoice_flow.providers.scripted import reply
from invoice_flow.providers.simulated import SimulatedProvider, read_invoice_text
from invoice_flow.schemas import ApprovalAction, ApprovalStage, Outcome, RunResult
from invoice_flow.service import (
    execute_run,
    process_batch,
    process_invoice,
    run_checks,
    submit_review,
)
from invoice_flow.storage import create_run, update_run
from workflow_helpers import make_app, payments, source


def write_invoice(path, *, quantity=1, revision=None):
    data = {
        "invoice_number": "BATCH-CONFLICT-17",
        "vendor": "Batch Parts Ltd.",
        "date": "2026-11-01",
        "due_date": "2026-11-30",
        "currency": "USD",
        "items": [{"item": "WidgetA", "quantity": quantity, "unit_price": 100}],
        "subtotal": quantity * 100,
        "total": quantity * 100,
    }
    if revision is not None:
        data["revision"] = revision
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def conflicting_batch(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    paths = [
        write_invoice(tmp_path / "a.json"),
        write_invoice(tmp_path / "b.json", quantity=2),
    ]
    report = process_batch(paths, app=app, today=TODAY)
    with app.db.read() as conn:
        runs = [
            RunResult.model_validate_json(
                conn.execute(
                    "SELECT result_json FROM runs WHERE run_id = ?", (row["run_id"],)
                ).fetchone()["result_json"]
            )
            for row in report["files"]
        ]
    return app, paths, runs


def review(app, run, *, resolved=None, seen=None):
    return submit_review(
        app,
        workspace_id="local",
        run_id=run.run_id,
        version_id=run.version_id,
        content_hash_seen=seen or run.content_hash,
        decision="approve",
        comment="Compared both source invoices and selected this payable version.",
        resolved_codes=resolved or [],
    )


def legacy_hold(app, path):
    """A historical batch-disposition row, with its source but no persisted invoice version."""
    parsed = load_source(path, app.policy)
    with app.db.transaction() as conn:
        store_document(conn, "local", parsed.source, app.db.now())
        run_id = create_run(
            conn,
            workspace_id="local",
            now=app.db.now(),
            document_id=parsed.source.document_id,
            mode="mock",
            policy_version=app.policy.policy_version,
            lifecycle="completed",
        )
    result = RunResult(
        run_id=run_id,
        workspace_id="local",
        lifecycle="completed",
        outcome="needs_review",
        terminal_reason="Versions of the same invoice cannot be ordered by revision label.",
        document_id=parsed.source.document_id,
        filename=parsed.source.filename,
        mode="mock",
        policy_version=app.policy.policy_version,
    )
    update_run(
        app.db,
        run_id,
        outcome="needs_review",
        terminal_reason=result.terminal_reason,
        result_json=result.model_dump_json(),
        finished_at=app.db.now(),
    )
    return run_id


def test_legacy_null_version_hold_is_bound_on_resubmission_and_can_be_reviewed(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    path = write_invoice(tmp_path / "legacy.json")
    old_run_id = legacy_hold(app, path)

    resubmitted = process_invoice(path, app=app, today=TODAY)

    assert resubmitted.outcome is Outcome.NEEDS_REVIEW
    assert payments(app) == []
    with app.db.read() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (old_run_id,)).fetchone()
        old = RunResult.model_validate_json(row["result_json"])
        held = conn.execute("SELECT identity_flag FROM invoice_versions").fetchone()
    assert row["version_id"] == old.version_id == resubmitted.version_id
    assert old.content_hash == resubmitted.content_hash
    assert old.invoice.total == Decimal("100.00")
    assert old.approval == []
    assert "REVISION_CONFLICT" in {finding.code.value for finding in old.findings}
    assert held["identity_flag"] == "revision_conflict"
    selected = review(app, old, resolved=["REVISION_CONFLICT"])
    assert selected.accepted, selected.message
    assert selected.run.outcome is Outcome.PAID
    assert len(payments(app)) == 1


def test_legacy_sibling_must_be_accounted_for_before_explicit_selection(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    paths = [
        write_invoice(tmp_path / "old-a.json"),
        write_invoice(tmp_path / "old-b.json", quantity=2),
    ]
    old_run_ids = [legacy_hold(app, path) for path in paths]

    first = process_invoice(paths[0], app=app, today=TODAY)

    assert first.outcome is Outcome.NEEDS_REVIEW
    assert any("Reprocess" in finding.message for finding in first.findings)
    assert not review(app, first, resolved=["REVISION_CONFLICT"]).accepted
    assert payments(app) == []
    second = process_invoice(paths[1], app=app, today=TODAY)
    assert second.outcome is Outcome.NEEDS_REVIEW
    selected = review(app, second, resolved=["REVISION_CONFLICT"])
    assert selected.accepted, selected.message
    assert len(payments(app)) == 1
    assert payments(app)[0]["amount_minor"] == 20000
    with app.db.read() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runs WHERE run_id IN (?,?) AND version_id IS NULL",
                old_run_ids,
            ).fetchone()[0]
            == 0
        )
    assert process_invoice(paths[0], app=app, today=TODAY).outcome is not Outcome.PAID
    assert process_invoice(paths[1], app=app, today=TODAY).outcome is not Outcome.PAID

    unrelated = write_invoice(tmp_path / "unrelated.json")
    data = json.loads(unrelated.read_text())
    data["invoice_number"] = "UNRELATED-AFTER-RESOLUTION"
    unrelated.write_text(json.dumps(data), encoding="utf-8")
    assert process_invoice(unrelated, app=app, today=TODAY).outcome is Outcome.PAID
    assert len(payments(app)) == 2


def test_unknown_legacy_hold_blocks_alternate_source_without_extracting_history(tmp_path):
    class NoHistoricalExtractionProvider(SimulatedProvider):
        def complete(self, request):
            if request.label in ("extraction", "reflection"):
                raise AssertionError("Historical sources must not be re-extracted automatically.")
            return super().complete(request)

    app = make_app(tmp_path, NoHistoricalExtractionProvider())
    old_source = tmp_path / "unread-old.txt"
    old_source.write_text("Unresolved historical source with unknown identity.", encoding="utf-8")
    old_run_id = legacy_hold(app, old_source)

    alternate = process_invoice(
        write_invoice(tmp_path / "alternate.json", quantity=2, revision="R8"),
        app=app,
        today=TODAY,
    )

    assert alternate.outcome is Outcome.NEEDS_REVIEW
    assert not review(app, alternate, resolved=["REVISION_CONFLICT"]).accepted
    assert payments(app) == []
    with app.db.read() as conn:
        assert (
            conn.execute("SELECT version_id FROM runs WHERE run_id = ?", (old_run_id,)).fetchone()[
                "version_id"
            ]
            is None
        )
        assert conn.execute("SELECT COUNT(*) FROM invoice_versions").fetchone()[0] == 1


def test_new_revision_cannot_bypass_an_existing_batch_identity_barrier(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    original = write_invoice(tmp_path / "original.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    process_batch([original, broken], app=app, today=TODAY)

    result = process_invoice(
        write_invoice(tmp_path / "newer.json", quantity=3, revision="R3"),
        app=app,
        today=TODAY,
    )

    assert result.outcome is Outcome.NEEDS_REVIEW
    assert not review(app, result, resolved=["REVISION_CONFLICT"]).accepted
    assert payments(app) == []


def test_preexisting_approval_cannot_pay_after_unknown_legacy_hold_is_discovered(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    checked = run_checks(
        write_invoice(tmp_path / "approved.json"), db=app.db, policy=app.policy, today=TODAY
    )
    approval = record_approval(
        app.db,
        app.policy,
        workspace_id="local",
        version_id=checked.registration.version_id,
        stage=ApprovalStage.FINAL,
        action=ApprovalAction.APPROVE,
        rationale="Approved before the historical hold was restored.",
        actor="simulated_reviewer",
    )
    legacy_hold(app, write_invoice(tmp_path / "restored.json", quantity=2))

    with pytest.raises(LedgerError) as caught:
        execute_mock_payment(
            app.db,
            app.policy,
            workspace_id="local",
            version_id=checked.registration.version_id,
            approval_id=approval.approval_id,
            mock_payment=app.mock_payment,
        )
    assert caught.value.code == "legacy_identity_hold"
    assert payments(app) == []


def test_legacy_hold_reuses_the_registered_versions_current_review_hash(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    registered = run_checks(
        write_invoice(tmp_path / "canonical.json", revision="R1"),
        db=app.db,
        policy=app.policy,
        today=TODAY,
    )
    legacy_path = write_invoice(tmp_path / "old-layout.json", revision="R2")
    old_run_id = legacy_hold(app, legacy_path)

    resubmitted = process_invoice(legacy_path, app=app, today=TODAY)

    with app.db.read() as conn:
        old = RunResult.model_validate_json(
            conn.execute("SELECT result_json FROM runs WHERE run_id = ?", (old_run_id,)).fetchone()[
                "result_json"
            ]
        )
    assert old.version_id == registered.registration.version_id
    assert old.content_hash == resubmitted.content_hash
    selected = review(app, old, resolved=["REVISION_CONFLICT"])
    assert selected.accepted, selected.message
    assert len(payments(app)) == 1


@pytest.mark.parametrize("outcome", [None, "duplicate"])
def test_unversioned_rows_without_an_unresolved_hold_do_not_block_payment(tmp_path, outcome):
    app = make_app(tmp_path, SimulatedProvider())
    old_run_id = legacy_hold(app, write_invoice(tmp_path / "old.json", quantity=2))
    update_run(app.db, old_run_id, outcome=outcome)

    result = process_invoice(write_invoice(tmp_path / "new.json"), app=app, today=TODAY)

    assert result.outcome is Outcome.PAID
    assert len(payments(app)) == 1


def test_reprocessing_a_batch_repairs_its_legacy_runs_for_explicit_review(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    paths = [
        write_invoice(tmp_path / "old-a.json"),
        write_invoice(tmp_path / "old-b.json", quantity=2),
    ]
    old_run_ids = [legacy_hold(app, path) for path in paths]

    batch = process_batch(paths, app=app, today=TODAY)

    assert all(row["outcome"] == "needs_review" for row in batch["files"])
    with app.db.read() as conn:
        old = [
            RunResult.model_validate_json(
                conn.execute("SELECT result_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()[
                    "result_json"
                ]
            )
            for run_id in old_run_ids
        ]
    assert all(run.version_id and run.content_hash for run in old)
    assert all(run.approval == [] for run in old)
    selected = review(app, old[1], resolved=["REVISION_CONFLICT"])
    assert selected.accepted, selected.message
    assert len(payments(app)) == 1


def test_unknown_legacy_identity_hold_is_limited_to_its_workspace(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    legacy_hold(app, write_invoice(tmp_path / "old.json", quantity=2))

    result = process_invoice(
        write_invoice(tmp_path / "other.json"),
        app=app,
        workspace_id="other-workspace",
        today=TODAY,
    )

    assert result.outcome is Outcome.PAID
    assert payments(app)[0]["workspace_id"] == "other-workspace"


def test_batch_conflicts_are_registered_and_resubmissions_stay_held(tmp_path):
    app, paths, runs = conflicting_batch(tmp_path)

    assert all(run.outcome is Outcome.NEEDS_REVIEW for run in runs)
    assert all(run.version_id and run.content_hash and run.invoice for run in runs)
    assert len({run.version_id for run in runs}) == 2
    with app.db.read() as conn:
        versions = conn.execute(
            "SELECT identity_flag FROM invoice_versions WHERE workspace_id = 'local'"
        ).fetchall()
        bindings = conn.execute("SELECT version_id FROM runs").fetchall()
    assert len(versions) == 2
    assert all(row["identity_flag"] == "revision_conflict" for row in versions)
    assert all(row["version_id"] for row in bindings)

    for path in paths:
        again = process_invoice(path, app=app, today=TODAY)
        assert again.outcome is Outcome.NEEDS_REVIEW
        assert "REVISION_CONFLICT" in {finding.code.value for finding in again.findings}
    assert payments(app) == []


def test_review_selects_one_conflicting_version_and_cannot_select_the_loser(tmp_path):
    app, paths, runs = conflicting_batch(tmp_path)
    assert all(run.version_id for run in runs)

    assert not review(app, runs[0]).accepted
    assert not review(app, runs[0], resolved=["REVISION_CONFLICT"], seen="stale-content").accepted
    assert payments(app) == []

    selected = review(app, runs[1], resolved=["REVISION_CONFLICT"])
    assert selected.accepted, selected.message
    assert selected.run.outcome is Outcome.PAID
    assert selected.run.receipt.amount == Decimal("200.00")
    assert not review(app, runs[0], resolved=["REVISION_CONFLICT"]).accepted
    for path in paths:
        assert process_invoice(path, app=app, today=TODAY).outcome is not Outcome.PAID
    paid = payments(app)
    assert len(paid) == 1
    assert paid[0]["version_id"] == runs[1].version_id
    with app.db.read() as conn:
        loser = conn.execute(
            "SELECT status FROM invoice_versions WHERE version_id = ?", (runs[0].version_id,)
        ).fetchone()
    assert loser["status"] == "superseded"


def test_new_revision_cannot_erase_an_unresolved_batch_conflict(tmp_path):
    app, _, _ = conflicting_batch(tmp_path)
    newest = write_invoice(tmp_path / "newer.json", quantity=3, revision="R3")

    result = process_invoice(newest, app=app, today=TODAY)

    assert result.outcome is Outcome.NEEDS_REVIEW
    assert "REVISION_CONFLICT" in {finding.code.value for finding in result.findings}
    assert payments(app) == []


def test_conflict_selection_cannot_clear_invoice_errors(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    valid = write_invoice(tmp_path / "a.json")
    invalid = write_invoice(tmp_path / "b.json", quantity=2)
    data = json.loads(invalid.read_text())
    data["total"] = 250
    invalid.write_text(json.dumps(data), encoding="utf-8")
    batch = process_batch([valid, invalid], app=app, today=TODAY)
    with app.db.read() as conn:
        invalid_run = RunResult.model_validate_json(
            conn.execute(
                "SELECT result_json FROM runs WHERE run_id = ?",
                (batch["files"][1]["run_id"],),
            ).fetchone()["result_json"]
        )

    assert invalid_run.version_id and invalid_run.content_hash
    assert not review(app, invalid_run, resolved=["REVISION_CONFLICT"]).accepted
    assert payments(app) == []
    with app.db.read() as conn:
        assert all(
            row["identity_flag"] == "revision_conflict" and row["status"] == "active"
            for row in conn.execute("SELECT identity_flag, status FROM invoice_versions")
        )


def test_conflict_selection_cannot_clear_another_batch_identity_hold(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    valid = write_invoice(tmp_path / "valid.json")
    unreadable = tmp_path / "broken.json"
    unreadable.write_text("{", encoding="utf-8")
    process_batch([valid, unreadable], app=app, today=TODAY)
    conflicting = process_invoice(
        write_invoice(tmp_path / "other.json", quantity=2), app=app, today=TODAY
    )

    assert conflicting.outcome is Outcome.NEEDS_REVIEW
    assert not review(app, conflicting, resolved=["REVISION_CONFLICT"]).accepted
    assert payments(app) == []
    with app.db.read() as conn:
        assert {
            row["identity_flag"]
            for row in conn.execute("SELECT identity_flag FROM invoice_versions")
        } == {"batch_hold", "revision_conflict"}


def test_conflict_selection_cannot_replace_an_already_paid_version(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    original = process_invoice(write_invoice(tmp_path / "a.json"), app=app, today=TODAY)
    assert original.outcome is Outcome.PAID
    conflicting = process_invoice(
        write_invoice(tmp_path / "b.json", quantity=2), app=app, today=TODAY
    )

    assert conflicting.outcome is Outcome.NEEDS_REVIEW
    assert not review(app, conflicting, resolved=["REVISION_CONFLICT"]).accepted
    assert len(payments(app)) == 1
    assert payments(app)[0]["version_id"] == original.version_id


def test_invented_revision_cannot_supersede_a_source_grounded_batch_sibling(tmp_path):
    text = (
        "Invoice Number: BATCH-CONFLICT-17\nVendor: Batch Parts Ltd.\n"
        "Invoice Date: 2026-11-01\nDue Date: 2026-11-30\nCurrency: USD\n"
        "WidgetA 2 $100 $200\nSubtotal: $200\nTotal: $200\n"
    )

    class InventedRevisionProvider(SimulatedProvider):
        def complete(self, request):
            if request.label in ("extraction", "reflection"):
                return reply({**read_invoice_text(text), "revision": "R9"})
            return super().complete(request)

    app = make_app(tmp_path, InventedRevisionProvider())
    path = tmp_path / "b.txt"
    path.write_text(text, encoding="utf-8")

    result = process_batch([write_invoice(tmp_path / "a.json"), path], app=app, today=TODAY)

    assert [row["outcome"] for row in result["files"]] == ["needs_review", "needs_review"]
    assert payments(app) == []
    with app.db.read() as conn:
        invented = conn.execute(
            "SELECT revision_rank FROM invoice_versions WHERE revision_label = 'R9'"
        ).fetchone()
    assert invented["revision_rank"] is None


class OneExtractionProvider(SimulatedProvider):
    def __init__(self):
        super().__init__()
        self.extractions = 0

    def complete(self, request):
        if request.label == "extraction":
            self.extractions += 1
            if self.extractions > 1:
                raise ProviderError("second_extraction", "The prepared reading was discarded.")
        return super().complete(request)


@pytest.mark.parametrize("suffix", ["txt", "pdf"])
def test_batch_uses_one_prepared_extraction_for_payment(tmp_path, suffix):
    provider = OneExtractionProvider()
    app = make_app(tmp_path, provider)
    text = (
        "Invoice Number: PREPARED-29\nVendor: Prepared Parts Ltd.\n"
        "Invoice Date: 2026-11-01\nDue Date: 2026-11-30\nCurrency: USD\n"
        "WidgetA 1 $100 $100\nSubtotal: $100\nTotal: $100\n"
    )
    path = tmp_path / f"prepared.{suffix}"
    if suffix == "pdf":
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        for line in text.splitlines():
            pdf.cell(w=0, h=8, text=line, new_x="LMARGIN", new_y="NEXT")
        pdf.output(path)
    else:
        path.write_text(text, encoding="utf-8")

    result = process_batch([path], app=app, today=TODAY)

    assert result["files"][0]["outcome"] == "paid", result
    assert provider.extractions == 1
    assert len(payments(app)) == 1
    assert payments(app)[0]["amount_minor"] == 10000


def test_prepared_reading_with_a_different_source_hash_cannot_pay(tmp_path):
    app = make_app(tmp_path, SimulatedProvider())
    previous = process_invoice(write_invoice(tmp_path / "previous.json"), app=app, today=TODAY)
    assert previous.draft is not None

    result = execute_run(
        app,
        workspace_id="local",
        source=source("invoice_1001.txt"),
        mode="mock",
        today=TODAY,
        prepared_draft=previous.draft,
        prepared_source_hash="different-source-hash",
    )

    assert result.outcome is not Outcome.PAID
    assert result.lifecycle.value == "failed"
    assert len(payments(app)) == 1
