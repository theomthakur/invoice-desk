"""All 20 supplied files through the deterministic core, plus the CLI contract.

TXT/PDF files use stand-in extractions (tests/fixtures/drafts.py) because reading them is the
model's job in Part 02. Everything else here is the real code path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from conftest import ROOT, SAMPLES, TODAY
from fixtures.drafts import DRAFTS
from helpers import approve, pay, payments
from invoice_flow.cli import main
from invoice_flow.ledger import Disposition, LedgerError
from invoice_flow.parsers import load_source
from invoice_flow.preflight import BatchEntry, BatchKind, plan_batch
from invoice_flow.schemas import Outcome
from invoice_flow.service import run_checks
from invoice_flow.storage import Database, init_database

ALL_FILES = sorted(p.name for p in SAMPLES.glob("invoice_*"))
FIXED_CLOCK = lambda: datetime(2026, 11, 3, 12, 0, tzinfo=UTC)  # noqa: E731


def fresh_db(tmp_path, name="s.db") -> Database:
    database = Database(tmp_path / name, clock=FIXED_CLOCK)
    init_database(database)
    return database


def check(name, db, policy):
    return run_checks(SAMPLES / name, db=db, policy=policy, today=TODAY, draft=DRAFTS.get(name))


# What a fresh ledger should decide for each file on its own.
EXPECTED = {
    "invoice_1001.txt": Outcome.PAID,  # None route = continues to approval, then paid
    "invoice_1002.txt": Outcome.REJECTED,
    "invoice_1003.txt": Outcome.REJECTED,
    "invoice_1004.json": Outcome.PAID,
    "invoice_1004_revised.json": Outcome.PAID,
    "invoice_1005.json": Outcome.REJECTED,
    "invoice_1006.csv": Outcome.PAID,
    "invoice_1007.csv": Outcome.REJECTED,
    "invoice_1008.txt": Outcome.REJECTED,
    "invoice_1009.json": Outcome.REJECTED,
    "invoice_1010.txt": Outcome.PAID,
    "invoice_1011.pdf": Outcome.PAID,
    "invoice_1011.txt": Outcome.PAID,
    "invoice_1012.pdf": Outcome.NEEDS_REVIEW,
    "invoice_1012.txt": Outcome.NEEDS_REVIEW,
    "invoice_1013.json": Outcome.REJECTED,
    "invoice_1013.pdf": Outcome.REJECTED,
    "invoice_1014.xml": Outcome.NEEDS_REVIEW,
    "invoice_1015.csv": Outcome.PAID,
    "invoice_1016.json": Outcome.REJECTED,
}


def test_every_supplied_file_has_an_expectation():
    assert sorted(EXPECTED) == ALL_FILES and len(ALL_FILES) == 20


@pytest.mark.parametrize("name", ALL_FILES)
def test_each_file_alone_on_a_fresh_ledger(name, tmp_path, policy):
    result = check(name, fresh_db(tmp_path), policy)
    assert not result.needs_extraction, name
    route = result.route
    assert result.registration.disposition is Disposition.NEW
    expected = EXPECTED[name]
    if expected is Outcome.PAID:
        assert route.outcome is None, (name, route.reason)  # eligible -> goes on to approval
    else:
        assert route.outcome is expected, (name, route.reason)


def test_text_files_need_extraction_without_a_draft(tmp_path, policy):
    result = run_checks(
        SAMPLES / "invoice_1001.txt", db=fresh_db(tmp_path), policy=policy, today=TODAY
    )
    assert result.needs_extraction and result.report is None and result.registration is None


def test_eligible_samples_total_the_expected_amounts(tmp_path, policy):
    expected = {
        "invoice_1001.txt": "5000.00",
        "invoice_1004.json": "1890.00",
        "invoice_1006.csv": "2750.00",
        "invoice_1010.txt": "7185.00",
        "invoice_1011.txt": "3000.00",
        "invoice_1015.csv": "6500.00",
        "invoice_1004_revised.json": "5940.00",
    }
    for name, total in expected.items():
        report = check(name, fresh_db(tmp_path, f"{name}.db"), policy).report
        assert report.normalized.total == Decimal(total) and report.normalized.currency == "USD", (
            name
        )
        assert report.requires_scrutiny is False


# ------------------------------------------------------------- the whole set, one ledger


def load_all(policy):
    entries, parsed = [], {}
    for name in ALL_FILES:
        item = load_source(SAMPLES / name, policy)
        parsed[name] = item
        entries.append(BatchEntry(name, DRAFTS.get(name) or item.draft))
    return entries, parsed


def test_batch_preflight_over_all_20_files(policy):
    entries, _ = load_all(policy)
    plan = plan_batch(entries, policy)
    assert set(plan) == set(ALL_FILES)
    by_kind = {k: sorted(f for f, d in plan.items() if d.kind is k) for k in BatchKind}
    # alternate-format twins: exactly one file of each pair is a duplicate
    assert len(by_kind[BatchKind.DUPLICATE]) == 3
    assert {plan[f].related_file for f in by_kind[BatchKind.DUPLICATE]} <= set(ALL_FILES)
    # the explicit revision replaces the unpaid original
    assert by_kind[BatchKind.SUPERSEDED] == ["invoice_1004.json"]
    assert plan["invoice_1004_revised.json"].kind is BatchKind.PROCESS
    assert by_kind[BatchKind.CONFLICT] == []
    assert len(by_kind[BatchKind.PROCESS]) == 16  # 16 invoice identities
    # 20 files = 16 identities + 3 alternate formats + 1 superseded original - overlap check
    assert len(by_kind[BatchKind.PROCESS]) + 3 + 1 == 20


def test_whole_set_on_one_ledger_pays_each_identity_at_most_once(tmp_path, policy):
    db = fresh_db(tmp_path)
    entries, _ = load_all(policy)
    plan = plan_batch(entries, policy)
    outcomes = {}
    for name in ALL_FILES:
        disposition = plan[name]
        if disposition.kind is not BatchKind.PROCESS:
            continue
        result = check(name, db, policy)
        route = result.route
        if route.outcome is None:  # eligible: simulate a clean approval, then pay
            apr = approve(
                db, policy, result.registration.version_id, scrutiny=result.report.requires_scrutiny
            )
            pay(db, policy, result.registration.version_id, apr.approval_id)
            outcomes[name] = Outcome.PAID
        else:
            outcomes[name] = route.outcome
    paid = sorted(n for n, o in outcomes.items() if o is Outcome.PAID)
    # twins: the planner keeps the alphabetically first file as canonical, so .pdf wins over .txt
    assert paid == [
        "invoice_1001.txt",
        "invoice_1004_revised.json",
        "invoice_1006.csv",
        "invoice_1010.txt",
        "invoice_1011.pdf",
        "invoice_1015.csv",
    ]
    assert {n for n, o in outcomes.items() if o is Outcome.NEEDS_REVIEW} == {
        "invoice_1012.pdf",
        "invoice_1014.xml",
    }
    assert plan["invoice_1011.txt"].kind is plan["invoice_1012.txt"].kind is BatchKind.DUPLICATE
    assert {n for n, o in outcomes.items() if o is Outcome.REJECTED} >= {
        "invoice_1002.txt",
        "invoice_1003.txt",
        "invoice_1005.json",
        "invoice_1007.csv",
        "invoice_1008.txt",
        "invoice_1009.json",
        "invoice_1016.json",
    }
    rows = payments(db)
    assert len(rows) == len(paid) == 6
    assert len({r["payable_id"] for r in rows}) == 6  # one payment per payable
    total = sum(Decimal(r["amount_minor"]) for r in rows) / 100
    assert total == Decimal("5000") + Decimal("5940") + Decimal("2750") + Decimal("7185") + Decimal(
        "3000"
    ) + Decimal("6500")


def test_resubmitting_the_whole_set_pays_nothing_new(tmp_path, policy):
    db = fresh_db(tmp_path)
    for name in ("invoice_1001.txt", "invoice_1006.csv", "invoice_1015.csv"):
        result = check(name, db, policy)
        apr = approve(db, policy, result.registration.version_id)
        pay(db, policy, result.registration.version_id, apr.approval_id)
    before = len(payments(db))
    for name in ("invoice_1001.txt", "invoice_1006.csv", "invoice_1015.csv"):
        again = check(name, db, policy)
        assert again.route.outcome is Outcome.DUPLICATE
        assert "paid" in again.route.reason or again.registration.original_outcome in (None, "paid")
    assert len(payments(db)) == before == 3


def test_original_paid_first_then_revision_arrives(tmp_path, policy):
    db = fresh_db(tmp_path)
    first = check("invoice_1004.json", db, policy)
    pay(
        db,
        policy,
        first.registration.version_id,
        approve(db, policy, first.registration.version_id).approval_id,
    )
    revised = check("invoice_1004_revised.json", db, policy)
    assert revised.registration.disposition is Disposition.REVISION_AFTER_PAYMENT
    assert revised.route.outcome is Outcome.NEEDS_REVIEW
    rows = payments(db)
    assert len(rows) == 1 and rows[0]["amount_minor"] == 189000  # never the 4,050 delta
    with pytest.raises(LedgerError):
        pay(
            db,
            policy,
            revised.registration.version_id,
            approve(db, policy, revised.registration.version_id).approval_id,
        )


def test_revision_first_then_original_arrives(tmp_path, policy):
    db = fresh_db(tmp_path)
    revised = check("invoice_1004_revised.json", db, policy)
    original = check("invoice_1004.json", db, policy)
    assert original.registration.disposition is Disposition.SUPERSEDED
    assert original.route.outcome is Outcome.SUPERSEDED
    pay(
        db,
        policy,
        revised.registration.version_id,
        approve(db, policy, revised.registration.version_id).approval_id,
    )
    assert [r["amount_minor"] for r in payments(db)] == [594000]


def test_pdf_twin_of_a_paid_text_invoice_is_a_duplicate(tmp_path, policy):
    db = fresh_db(tmp_path)
    txt = check("invoice_1011.txt", db, policy)
    receipt = pay(
        db,
        policy,
        txt.registration.version_id,
        approve(db, policy, txt.registration.version_id).approval_id,
    )
    pdf = check("invoice_1011.pdf", db, policy)
    assert (
        pdf.route.outcome is Outcome.DUPLICATE
        and pdf.registration.version_id == txt.registration.version_id
    )
    assert pdf.registration.original_outcome in (None, "paid")
    assert len(payments(db)) == 1 and receipt.amount == Decimal("3000.00")


def test_duplicate_of_a_rejected_invoice_keeps_the_original_reason(tmp_path, policy):
    db = fresh_db(tmp_path)
    check("invoice_1013.json", db, policy)
    twin = check("invoice_1013.pdf", db, policy)
    assert twin.route.outcome is Outcome.DUPLICATE
    assert any(
        f.code.value == "INSUFFICIENT_STOCK" for f in twin.route.findings
    )  # reason still visible


# ------------------------------------------------------------------ CLI contract


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("POLICY_PATH", raising=False)
    return tmp_path


def test_cli_full_workflow_now_runs_in_labeled_mock_mode(cli_env, capsys):
    """Part 02: the full workflow exists. It must say it is simulated and make one payment."""
    code = main([f"--invoice_path={SAMPLES / 'invoice_1006.csv'}", "--mode", "mock", "--json"])
    out = capsys.readouterr()
    result = json.loads(out.out)  # stdout is exactly one JSON document
    assert code == 0 and result["outcome"] == "paid" and result["provider"] == "simulated"
    assert result["receipt"]["simulated"] is True and result["usage"]["estimated_cost_usd"] is None
    assert "SIMULATED" in out.err  # the human output labels it too
    assert len(__import__("re").findall(r"^Paid ", out.err, flags=__import__("re").M)) <= 1


def test_cli_live_mode_without_a_key_fails_visibly_and_pays_nothing(cli_env, capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    code = main([f"--invoice_path={SAMPLES / 'invoice_1006.csv'}", "--mode", "live", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result["lifecycle"] == "failed" and result["outcome"] is None
    assert "live_not_configured" in result["terminal_reason"] and result["receipt"] is None


def test_cli_live_mode_needs_a_spending_cap_even_with_a_key(cli_env, capsys, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-not-a-real-key-123456")
    monkeypatch.delenv("MODEL_BUDGET_USD", raising=False)
    code = main([f"--invoice_path={SAMPLES / 'invoice_1006.csv'}", "--mode", "live", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and "MODEL_BUDGET_USD" in result["terminal_reason"]
    assert result["usage"]["model_calls"] == 0  # no request was ever attempted


def test_cli_checks_only_json_is_pure_stdout(cli_env, capsys):
    code = main(["--checks-only", "--json", f"--invoice_path={SAMPLES / 'invoice_1007.csv'}"])
    out = capsys.readouterr()
    payload = json.loads(out.out)  # stdout is only JSON
    assert code == 0 and payload["eligibility"] == "rejected"
    assert (
        payload["mode"] == "deterministic_checks_only"
        and "No approval or payment" in payload["note"]
    )
    assert {f["code"] for f in payload["findings"]} >= {"INSUFFICIENT_STOCK", "TOTAL_MISMATCH"}
    assert "invoice_1007.csv" in out.err  # the human report goes to stderr


def test_cli_a_rejection_is_exit_zero(cli_env):
    assert main(["--checks-only", f"--invoice_path={SAMPLES / 'invoice_1016.json'}"]) == 0


def test_cli_unreadable_file_is_a_technical_failure(cli_env, capsys, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["--checks-only", f"--invoice_path={bad}"]) == 1
    assert "malformed" in capsys.readouterr().err
    assert main(["--checks-only", "--invoice_path=/no/such/file.json"]) == 1


def test_cli_usage_errors(cli_env, capsys):
    assert (
        main([]) == 2 and "--invoice_path (or --batch DIR) is required" in capsys.readouterr().err
    )
    assert main(["--reset-db"]) == 2  # refuses without explicit confirmation


def test_cli_reset_needs_confirmation_and_then_wipes(cli_env, capsys):
    path = cli_env / "data" / "invoice_flow.db"
    assert main(["--init-db"]) == 0 and path.exists()
    main(["--checks-only", f"--invoice_path={SAMPLES / 'invoice_1006.csv'}"])
    assert main(["--reset-db", "--yes-delete-all-data"]) == 0
    capsys.readouterr()
    first_run = main(["--checks-only", "--json", f"--invoice_path={SAMPLES / 'invoice_1006.csv'}"])
    payload = json.loads(capsys.readouterr().out)
    assert first_run == 0 and payload["route"]["outcome"] is None  # not a duplicate after the wipe


def test_cli_second_run_of_the_same_file_is_a_duplicate(cli_env, capsys):
    args = ["--checks-only", "--json", f"--invoice_path={SAMPLES / 'invoice_1015.csv'}"]
    main(args)
    capsys.readouterr()
    main(args)
    assert json.loads(capsys.readouterr().out)["route"]["outcome"] == "duplicate"


def test_cli_init_db_is_safe_to_repeat(cli_env):
    assert main(["--init-db"]) == 0 and main(["--init-db"]) == 0


def test_main_py_runs_as_a_script(tmp_path):
    env = {
        "DATA_DIR": str(tmp_path / "d"),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(ROOT / "src"),
    }
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "main.py"),
            "--checks-only",
            "--json",
            f"--invoice_path={SAMPLES / 'invoice_1006.csv'}",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=60,
    )
    assert result.returncode == 0 and json.loads(result.stdout)["eligibility"] == "eligible"
    missing = subprocess.run(
        [sys.executable, str(ROOT / "main.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=60,
    )
    assert missing.returncode == 2 and "Traceback" not in missing.stderr
