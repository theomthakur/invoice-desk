"""Payment gate, approvals and version handling. Every money rule is tested at the database."""

from __future__ import annotations

import sqlite3
import threading
from decimal import Decimal

import pytest

from conftest import TODAY, make_draft
from helpers import (  # noqa: E402
    FINAL,
    REVIEW,
    approve,
    approve_and_pay,
    insert_approval,
    insert_placeholder_approval,
    pay,
    payments,
    submit,
)
from invoice_flow.findings import FindingCode as C
from invoice_flow.identity import content_hash
from invoice_flow.ledger import (
    Disposition,
    LedgerError,
    get_payment,
    list_approvals,
    load_version_state,
    record_approval,
    route_after_validation,
)
from invoice_flow.rules import validate_invoice
from invoice_flow.schemas import (
    ApprovalAction,
    ApprovalStage,
    Outcome,
)
from invoice_flow.storage import (
    LOCAL_WORKSPACE,
    load_inventory_index,
)

# ================================================================== the happy path


def test_valid_invoice_is_paid_once(db, policy):
    reg, receipt = approve_and_pay(db, policy, make_draft(lines=[("WidgetA", 4, "250.00")]))
    assert reg.disposition is Disposition.NEW
    assert (receipt.amount, receipt.currency, receipt.vendor) == (
        Decimal("1000.00"),
        "USD",
        "Test Vendor Inc.",
    )
    assert receipt.simulated is True and not receipt.already_paid
    assert len(payments(db)) == 1 and payments(db)[0]["amount_minor"] == 100000


def test_payment_does_not_deplete_stock(db, policy):
    approve_and_pay(db, policy, make_draft(lines=[("WidgetA", 15, "1")]))
    with db.read() as conn:
        assert load_inventory_index(conn).stock["WidgetA"] == 15  # snapshot, not a ledger


def test_repeat_request_returns_the_same_receipt(db, policy):
    reg, first = approve_and_pay(db, policy, make_draft())
    again = pay(db, policy, reg.version_id, first.approval_id)
    assert again.payment_id == first.payment_id and again.already_paid
    assert len(payments(db)) == 1


def test_mock_payment_is_called_exactly_once(db, policy):
    calls = []
    reg, _ = submit(db, policy, make_draft())
    apr = approve(db, policy, reg.version_id)
    spy = lambda vendor, amount: calls.append((vendor, amount)) or {"status": "success"}  # noqa: E731
    pay(db, policy, reg.version_id, apr.approval_id, mock_payment=spy)
    pay(db, policy, reg.version_id, apr.approval_id, mock_payment=spy)
    assert calls == [("Test Vendor Inc.", Decimal("500.00"))]


def test_failed_bank_call_leaves_no_payment_row(db, policy):
    reg, _ = submit(db, policy, make_draft())
    apr = approve(db, policy, reg.version_id)
    with pytest.raises(LedgerError) as caught:
        pay(
            db,
            policy,
            reg.version_id,
            apr.approval_id,
            mock_payment=lambda v, a: {"status": "error"},
        )
    assert caught.value.code == "payment_failed" and payments(db) == []  # rolled back
    ok = pay(db, policy, reg.version_id, apr.approval_id)  # and a retry can still succeed
    assert ok.amount == Decimal("500.00")


def test_bank_call_that_raises_leaves_no_payment_row(db, policy):
    reg, _ = submit(db, policy, make_draft())
    apr = approve(db, policy, reg.version_id)

    def boom(vendor, amount):
        raise TimeoutError("bank down")

    with pytest.raises(TimeoutError):
        pay(db, policy, reg.version_id, apr.approval_id, mock_payment=boom)
    assert payments(db) == []


# ================================================================== the gate defends itself


def test_no_approval_no_payment(db, policy):
    reg, _ = submit(db, policy, make_draft())
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, reg.version_id, "apr_does_not_exist")
    assert caught.value.code == "approval_missing" and payments(db) == []


def test_a_proposal_or_critique_cannot_release_payment(db, policy):
    reg, _ = submit(db, policy, make_draft())
    for stage in (ApprovalStage.APPROVAL, ApprovalStage.CRITIQUE):
        apr = approve(db, policy, reg.version_id, stage=stage)
        with pytest.raises(LedgerError) as caught:
            pay(db, policy, reg.version_id, apr.approval_id)
        assert caught.value.code == "approval_stage"
    assert payments(db) == []


def test_a_rejection_cannot_release_payment(db, policy):
    reg, _ = submit(db, policy, make_draft())
    apr = record_approval(
        db,
        policy,
        workspace_id=LOCAL_WORKSPACE,
        version_id=reg.version_id,
        stage=FINAL,
        action=ApprovalAction.REJECT,
        rationale="no",
        actor="t",
    )
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, reg.version_id, apr.approval_id)
    assert caught.value.code == "not_approved"


def test_forged_approval_on_an_invoice_with_errors_is_refused(db, policy):
    reg, report = submit(db, policy, make_draft(lines=[("WidgetA", 99, "1")]))  # exceeds stock
    assert report.normalized is not None
    forged = insert_approval(db, policy, reg.version_id)
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, reg.version_id, forged)
    assert caught.value.code == "has_errors" and payments(db) == []


def test_approval_cannot_be_recorded_for_an_invoice_with_errors(db, policy):
    reg, _ = submit(db, policy, make_draft(lines=[("WidgetA", 99, "1")]))
    with pytest.raises(LedgerError) as caught:
        approve(db, policy, reg.version_id)
    assert caught.value.code == "not_approvable"


def test_invalid_invoice_with_no_normalized_form_cannot_be_approved_or_paid(db, policy):
    reg, report = submit(db, policy, make_draft(due="yesterday"))
    assert report.normalized is None
    with pytest.raises(LedgerError) as caught:
        approve(db, policy, reg.version_id)
    assert caught.value.code == "not_approvable"


def test_approval_binds_to_invoice_content(db, policy):
    reg, _ = submit(db, policy, make_draft())
    apr = approve(db, policy, reg.version_id)
    # Someone swaps in a *different but fully valid* invoice (3 x 250 = 750, no errors) after
    # approval. Only the content binding can stop this; the rules alone would accept it.
    tampered = make_draft(lines=[("WidgetA", 3, "250.00")])
    with db.read() as conn:
        assert (
            validate_invoice(
                tampered, "", load_inventory_index(conn), policy, TODAY
            ).eligibility.value
            == "eligible"
        )
    with db.transaction() as conn:
        conn.execute(
            "UPDATE invoice_versions SET draft_json = ? WHERE version_id = ?",
            (tampered.model_dump_json(), reg.version_id),
        )
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, reg.version_id, apr.approval_id)
    assert caught.value.code == "content_changed" and payments(db) == []


def test_approval_binds_to_policy(db, policy):
    reg, _ = submit(db, policy, make_draft())
    apr = approve(db, policy, reg.version_id)
    stricter = policy.model_copy(
        update={
            "approval": policy.approval.model_copy(update={"scrutiny_threshold": Decimal("100")})
        }
    )
    assert stricter.fingerprint != policy.fingerprint
    with pytest.raises(LedgerError) as caught:
        pay(db, stricter, reg.version_id, apr.approval_id)
    assert caught.value.code == "policy_changed"


def test_payment_revalidates_against_current_inventory(db, policy):
    reg, _ = submit(db, policy, make_draft(lines=[("WidgetA", 10, "1")]))
    apr = approve(db, policy, reg.version_id)
    with db.transaction() as conn:  # stock drops between approval and payment
        conn.execute("UPDATE inventory SET stock = 3 WHERE item = 'WidgetA'")
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, reg.version_id, apr.approval_id)
    assert caught.value.code == "has_errors" and payments(db) == []


def test_scrutiny_is_required_above_the_threshold(db, policy):
    draft = make_draft(lines=[("WidgetA", 11, "1000.00")])  # 11,000 > 10,000
    reg, report = submit(db, policy, draft)
    assert report.requires_scrutiny
    with pytest.raises(LedgerError) as caught:
        approve(db, policy, reg.version_id, scrutiny=False)
    assert caught.value.code == "scrutiny_required"
    forged = insert_approval(db, policy, reg.version_id, scrutiny=0)  # bypass record_approval
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, reg.version_id, forged)
    assert caught.value.code == "scrutiny_required"
    good = approve(db, policy, reg.version_id, scrutiny=True)
    assert pay(db, policy, reg.version_id, good.approval_id).amount == Decimal("11000.00")


def test_exactly_at_threshold_needs_no_scrutiny(db, policy):
    _, receipt = approve_and_pay(db, policy, make_draft(lines=[("WidgetA", 10, "1000.00")]))
    assert receipt.amount == Decimal("10000.00")


def test_missing_identity_can_never_be_paid(db, policy):
    reg, report = submit(db, policy, make_draft(vendor=""))
    assert reg.payable_id is None and report.normalized is None
    with pytest.raises(LedgerError):
        pay(db, policy, reg.version_id, insert_placeholder_approval(db, policy, reg.version_id))


def test_non_usd_is_never_paid_even_with_a_forged_review(db, policy):
    reg, report = submit(db, policy, make_draft(currency="EUR"))
    assert route_after_validation(reg, report).outcome is Outcome.NEEDS_REVIEW
    with pytest.raises(LedgerError) as caught:
        approve(db, policy, reg.version_id, stage=REVIEW, resolved_codes=["UNSUPPORTED_CURRENCY"])
    assert caught.value.code == "not_resolvable"  # a click cannot invent an exchange rate
    forged = insert_approval(
        db, policy, reg.version_id, stage="review", resolved='["UNSUPPORTED_CURRENCY"]'
    )
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, reg.version_id, forged)
    assert caught.value.code == "unsupported_currency" and payments(db) == []


# ================================================================== human review


def test_review_finding_blocks_automatic_approval(db, policy):
    reg, report = submit(db, policy, make_draft(), text="QuickShip (formerly FastShip Ltd.)")
    assert route_after_validation(reg, report).outcome is Outcome.NEEDS_REVIEW
    with pytest.raises(LedgerError) as caught:
        approve(db, policy, reg.version_id, stage=FINAL)
    assert caught.value.code == "review_required"
    forged = insert_approval(db, policy, reg.version_id, stage="final")
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, reg.version_id, forged)
    assert caught.value.code == "review_required"


def test_reviewer_can_resolve_a_resolvable_finding_and_the_invoice_is_paid(db, policy):
    reg, _ = submit(db, policy, make_draft(), text="QuickShip (formerly FastShip Ltd.)")
    with pytest.raises(LedgerError) as caught:  # must name what was verified
        approve(db, policy, reg.version_id, stage=REVIEW, resolved_codes=[])
    assert caught.value.code == "review_incomplete"
    apr = approve(
        db,
        policy,
        reg.version_id,
        stage=REVIEW,
        resolved_codes=["VENDOR_IDENTITY_CHANGE"],
        rationale="Confirmed rename with the vendor by phone.",
    )
    assert pay(db, policy, reg.version_id, apr.approval_id).amount == Decimal("500.00")


def test_reviewer_cannot_override_errors(db, policy):
    reg, _ = submit(db, policy, make_draft(lines=[("WidgetC", 1, "1")]), text="formerly Foo")
    with pytest.raises(LedgerError) as caught:
        approve(db, policy, reg.version_id, stage=REVIEW, resolved_codes=["VENDOR_IDENTITY_CHANGE"])
    assert caught.value.code == "not_approvable"


def test_stale_review_decision_is_rejected(db, policy):
    reg, report = submit(db, policy, make_draft())
    with pytest.raises(LedgerError) as caught:
        approve(db, policy, reg.version_id, expected_content_hash="0" * 64)
    assert caught.value.code == "stale_decision"
    approve(db, policy, reg.version_id, expected_content_hash=content_hash(report.normalized))


def test_decision_requires_a_rationale(db, policy):
    reg, _ = submit(db, policy, make_draft())
    with pytest.raises(LedgerError) as caught:
        approve(db, policy, reg.version_id, rationale="   ")
    assert caught.value.code == "rationale_required"


def test_approvals_are_listed_in_order(db, policy):
    reg, _ = submit(db, policy, make_draft())
    approve(db, policy, reg.version_id, stage=ApprovalStage.APPROVAL)
    approve(db, policy, reg.version_id, stage=ApprovalStage.CRITIQUE)
    approve(db, policy, reg.version_id, stage=FINAL)
    with db.read() as conn:
        stages = [a.stage for a in list_approvals(conn, LOCAL_WORKSPACE, reg.version_id)]
    assert stages == [ApprovalStage.APPROVAL, ApprovalStage.CRITIQUE, FINAL]


# ============================================================ duplicates, revisions, concurrency


def test_same_content_twice_is_a_duplicate_with_the_first_outcome(db, policy):
    first, _ = submit(db, policy, make_draft(), text="a", name="one.txt")
    second, report = submit(db, policy, make_draft(), text="a-different-layout", name="two.pdf")
    assert second.disposition is Disposition.DUPLICATE and second.version_id == first.version_id
    assert route_after_validation(second, report).outcome is Outcome.DUPLICATE
    assert any(f.code is C.DUPLICATE_SUBMISSION for f in second.findings)


def test_duplicate_of_a_paid_invoice_cannot_pay_again(db, policy):
    reg, first = approve_and_pay(db, policy, make_draft())
    dup, _ = submit(db, policy, make_draft(), name="again.pdf")
    assert dup.version_id == reg.version_id
    apr = approve(db, policy, dup.version_id)
    assert pay(db, policy, dup.version_id, apr.approval_id).payment_id == first.payment_id
    assert len(payments(db)) == 1


def test_original_paid_then_revision_is_held_not_paid(db, policy):
    original = make_draft(lines=[("WidgetA", 2, "250.00")])
    revised = make_draft(lines=[("WidgetA", 2, "250.00"), ("WidgetB", 1, "500.00")], revision="R1")
    reg, receipt = approve_and_pay(db, policy, original)
    rev, report = submit(db, policy, revised)
    assert rev.disposition is Disposition.REVISION_AFTER_PAYMENT
    assert route_after_validation(rev, report).outcome is Outcome.NEEDS_REVIEW
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, rev.version_id, insert_approval(db, policy, rev.version_id))
    assert caught.value.code == "already_paid_other_version"
    assert len(payments(db)) == 1 and payments(db)[0]["amount_minor"] == 50000  # never a delta


def test_unpaid_original_is_superseded_by_a_revision(db, policy):
    original = make_draft(lines=[("WidgetA", 2, "250.00")])
    revised = make_draft(lines=[("WidgetA", 2, "250.00"), ("WidgetB", 1, "500.00")], revision="R1")
    old, _ = submit(db, policy, original)
    old_approval = approve(db, policy, old.version_id)  # approved before the revision arrived
    new, _ = submit(db, policy, revised)
    assert new.disposition is Disposition.SUPERSEDES
    with pytest.raises(LedgerError) as caught:  # the old approval can no longer pay
        pay(db, policy, old.version_id, old_approval.approval_id)
    assert caught.value.code == "not_active"
    apr = approve(db, policy, new.version_id)
    assert pay(db, policy, new.version_id, apr.approval_id).amount == Decimal("1000.00")
    assert len(payments(db)) == 1


def test_revision_arriving_first_then_original(db, policy):
    original = make_draft(lines=[("WidgetA", 2, "250.00")])
    revised = make_draft(lines=[("WidgetA", 2, "250.00"), ("WidgetB", 1, "500.00")], revision="R1")
    new, _ = submit(db, policy, revised)
    old, report = submit(db, policy, original)
    assert old.disposition is Disposition.SUPERSEDED
    assert route_after_validation(old, report).outcome is Outcome.SUPERSEDED
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, old.version_id, insert_approval(db, policy, old.version_id))
    assert caught.value.code == "not_active"
    apr = approve(db, policy, new.version_id)
    assert pay(db, policy, new.version_id, apr.approval_id).amount == Decimal("1000.00")


def test_conflicting_versions_without_a_revision_order_block_everything(db, policy):
    a, _ = submit(db, policy, make_draft(lines=[("WidgetA", 2, "250.00")]))
    b, report = submit(db, policy, make_draft(lines=[("WidgetA", 3, "250.00")]))
    assert b.disposition is Disposition.REVISION_CONFLICT
    assert route_after_validation(b, report).outcome is Outcome.NEEDS_REVIEW
    for version in (a.version_id, b.version_id):
        with pytest.raises(LedgerError) as caught:
            pay(db, policy, version, insert_approval(db, policy, version))
        assert caught.value.code == "identity_hold"
    assert payments(db) == []


def test_unorderable_revision_label_is_a_conflict(db, policy):
    submit(db, policy, make_draft())
    odd, _ = submit(
        db, policy, make_draft(lines=[("WidgetA", 3, "250.00")], revision="final-final")
    )
    assert odd.disposition is Disposition.REVISION_CONFLICT


def test_stale_conflict_flag_does_not_touch_an_already_paid_version(db, policy):
    reg, receipt = approve_and_pay(db, policy, make_draft())
    submit(db, policy, make_draft(lines=[("WidgetA", 3, "250.00")]))  # same rank, new content
    with db.read() as conn:
        assert get_payment(conn, LOCAL_WORKSPACE, reg.payable_id).payment_id == receipt.payment_id
        flag = conn.execute(
            "SELECT identity_flag FROM invoice_versions WHERE version_id = ?", (reg.version_id,)
        ).fetchone()["identity_flag"]
    assert flag is None


def test_database_itself_refuses_a_second_payment_for_a_payable(db, policy):
    reg, receipt = approve_and_pay(db, policy, make_draft())
    with db.transaction() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO payments (payment_id, workspace_id, payable_id, version_id, approval_id,"
            " transaction_id, vendor, amount_minor, currency, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "pmt_x",
                LOCAL_WORKSPACE,
                receipt.payable_id,
                reg.version_id,
                "apr_x",
                "txn_other",
                "V",
                1,
                "USD",
                db.now(),
            ),
        )


def test_two_simultaneous_payment_attempts_pay_once(db, policy):
    reg, _ = submit(db, policy, make_draft())
    apr = approve(db, policy, reg.version_id)
    results, errors = [], []
    barrier = threading.Barrier(2)

    def attempt():
        try:
            barrier.wait(timeout=5)
            results.append(pay(db, policy, reg.version_id, apr.approval_id))
        except Exception as exc:  # noqa: BLE001 - recorded and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(timeout=20) for t in threads]
    assert errors == [] and len(results) == 2
    assert len(payments(db)) == 1
    assert sorted(r.already_paid for r in results) == [False, True]
    assert results[0].payment_id == results[1].payment_id


def test_many_simultaneous_attempts_across_two_versions_pay_at_most_once(db, policy):
    # the same payable submitted as two conflicting versions, both "approved", raced
    a, _ = submit(db, policy, make_draft(lines=[("WidgetA", 2, "250.00")]))
    aa = approve(db, policy, a.version_id)
    outcomes: list[str] = []

    def attempt():
        try:
            pay(db, policy, a.version_id, aa.approval_id)
            outcomes.append("ok")
        except LedgerError as exc:
            outcomes.append(exc.code)

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert len(outcomes) == 6 and len(payments(db)) == 1


# ================================================================== workspace isolation


def test_each_workspace_has_its_own_payables_and_duplicates(db, policy):
    _, a = approve_and_pay(db, policy, make_draft(), ws="ws_a")
    reg_b, report = submit(db, policy, make_draft(), ws="ws_b")
    assert reg_b.disposition is Disposition.NEW  # not a duplicate of someone else's invoice
    apr = approve(db, policy, reg_b.version_id, ws="ws_b")
    b = pay(db, policy, reg_b.version_id, apr.approval_id, ws="ws_b")
    assert a.payment_id != b.payment_id
    assert len(payments(db, "ws_a")) == 1 and len(payments(db, "ws_b")) == 1


def test_a_workspace_cannot_touch_another_workspaces_version(db, policy):
    reg, _ = submit(db, policy, make_draft(), ws="ws_a")
    approval = approve(db, policy, reg.version_id, ws="ws_a")
    submit(db, policy, make_draft(), ws="ws_b")
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, reg.version_id, approval.approval_id, ws="ws_b")
    assert caught.value.code == "not_found"
    with pytest.raises(LedgerError) as caught:
        approve(db, policy, reg.version_id, ws="ws_b")
    assert caught.value.code == "not_found"
    assert payments(db, "ws_a") == [] and payments(db, "ws_b") == []


def test_approval_from_another_workspace_is_not_accepted(db, policy):
    a, _ = submit(db, policy, make_draft(), ws="ws_a")
    b, _ = submit(db, policy, make_draft(), ws="ws_b")
    foreign = approve(db, policy, a.version_id, ws="ws_a")
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, b.version_id, foreign.approval_id, ws="ws_b")
    assert caught.value.code == "approval_missing"


def test_unknown_version_is_not_found(db, policy):
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, "ver_nope", "apr_nope")
    assert caught.value.code == "not_found"


def test_new_version_is_registered_in_a_clean_state(db, policy):
    reg, _ = submit(db, policy, make_draft())
    with db.read() as conn:
        state = load_version_state(conn, LOCAL_WORKSPACE, reg.version_id, policy, TODAY)
    assert state.status == "active" and state.identity_flag is None and state.payable_id


def test_same_workspace_approval_for_other_version_is_rejected_by_identity(db, policy):
    first, _ = submit(db, policy, make_draft(number="REVIEW-A"))
    second, _ = submit(db, policy, make_draft(number="REVIEW-B"))
    approval = approve(db, policy, first.version_id)
    with pytest.raises(LedgerError) as caught:
        pay(db, policy, second.version_id, approval.approval_id)
    assert caught.value.code == "approval_missing"
    assert payments(db) == []
