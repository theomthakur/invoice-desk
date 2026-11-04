from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from conftest import make_draft
from helpers import approve, pay, submit
from invoice_flow.config import Policy, load_policy
from invoice_flow.findings import FINDING_SPECS, FindingCode, Severity
from invoice_flow.schemas import InvoiceDraft, LineItemDraft, NormalizedInvoice
from invoice_flow.tools import TOOL_SPECS, ToolContext, ToolError, call_tool

# ------------------------------------------------------------------ tools


@pytest.fixture
def ctx(db, policy):
    return ToolContext(db=db, policy=policy, workspace_id="local")


def test_lookup_inventory_resolves_exactly(ctx):
    out = call_tool(
        ctx, "lookup_inventory", {"items": ["Widget A", "FakeItem", "WidgetC", "widget"]}
    )
    got = {r["query"]: (r["status"], r["item"], r["stock"]) for r in out["results"]}
    assert got == {
        "Widget A": ("ok", "WidgetA", 15),
        "FakeItem": ("ok", "FakeItem", 0),
        "WidgetC": ("unknown", None, None),
        "widget": ("unknown", None, None),
    }


def test_tools_accept_json_text_arguments(ctx):
    assert call_tool(ctx, "lookup_inventory", '{"items": ["WidgetB"]}')["results"][0]["stock"] == 10
    assert call_tool(ctx, "get_approval_policy", "")["additional_scrutiny_above"] == "10000.00"


def test_policy_tool_states_the_boundary_and_the_limits_of_a_model(ctx):
    out = call_tool(ctx, "get_approval_policy", {})
    assert out["automatic_payment_currencies"] == ["USD"] and "exactly equal" in out["boundary"]
    assert any("cannot clear" in rule for rule in out["rules"])
    json.dumps(out)  # must be serializable for the provider


@pytest.mark.parametrize(
    ("name", "args", "code"),
    [
        ("drop_database", {}, "unknown_tool"),
        ("lookup_inventory", "not json", "bad_arguments"),
        ("lookup_inventory", "[1]", "bad_arguments"),
        ("lookup_inventory", {}, "bad_arguments"),
        ("lookup_inventory", {"items": []}, "bad_arguments"),
        ("lookup_inventory", {"items": ["x"] * 51}, "bad_arguments"),
        ("lookup_inventory", {"items": [123]}, "bad_arguments"),
        ("lookup_inventory", {"items": ["WidgetA"], "sql": "DROP TABLE x"}, "bad_arguments"),
        ("get_approval_policy", {"workspace_id": "other"}, "bad_arguments"),
        ("get_invoice_history", {"payable_id": "1; DROP TABLE payments"}, "bad_arguments"),
        ("get_invoice_history", {"payable_id": "../etc/passwd"}, "bad_arguments"),
    ],
)
def test_bad_tool_calls_are_rejected_with_a_safe_message(ctx, name, args, code):
    with pytest.raises(ToolError) as caught:
        call_tool(ctx, name, args)
    assert caught.value.code == code


def test_a_model_cannot_choose_the_workspace(db, policy):
    reg, _ = submit(db, policy, make_draft(), ws="ws_a")
    pay(
        db,
        policy,
        reg.version_id,
        approve(db, policy, reg.version_id, ws="ws_a").approval_id,
        ws="ws_a",
    )
    call = {"payable_id": reg.payable_id}
    mine = call_tool(ToolContext(db, policy, "ws_a"), "get_invoice_history", call)
    theirs = call_tool(ToolContext(db, policy, "ws_b"), "get_invoice_history", call)
    assert mine["paid"] is True and len(mine["versions"]) == 1
    assert theirs["paid"] is False and theirs["versions"] == []  # a guessed id reveals nothing


def test_every_tool_publishes_a_strict_json_schema():
    assert set(TOOL_SPECS) == {"lookup_inventory", "get_approval_policy", "get_invoice_history"}
    for spec in TOOL_SPECS.values():
        schema = spec.json_schema()
        assert schema.get("additionalProperties") is False, spec.name
        json.dumps(schema)


# ------------------------------------------------------------------ config


def test_policy_fingerprint_changes_when_any_rule_changes(policy):
    base = policy.fingerprint
    assert load_policy().fingerprint == base  # stable across loads
    for update in (
        {"approval": policy.approval.model_copy(update={"scrutiny_threshold": Decimal("9999")})},
        {"arithmetic": policy.arithmetic.model_copy(update={"tolerance_minor_units": 2})},
        {"payment": policy.payment.model_copy(update={"auto_pay_currencies": ["USD", "EUR"]})},
        {"policy_version": "other"},
    ):
        assert policy.model_copy(update=update).fingerprint != base


def test_bad_policy_files_fail_at_load_time(tmp_path):
    text = (load_policy.__globals__["DEFAULT_POLICY_PATH"]).read_text()
    cases = {
        "bad regex": text.replace(r"'\burgent(?:ly)?\b'", "'(unclosed'"),
        "unknown key": text + "\nsurprise: 1\n",
        "negative threshold": text.replace('"10000.00"', '"-5"'),
        "unknown currency": text.replace(
            "auto_pay_currencies: [USD]", "auto_pay_currencies: [XYZ]"
        ),
        "bad date order": text.replace("slash_date_order: mdy", "slash_date_order: ymd"),
    }
    for label, body in cases.items():
        path = tmp_path / f"{label.replace(' ', '_')}.yaml"
        path.write_text(body)
        with pytest.raises(Exception):  # noqa: B017,PT011 - any validation error is correct
            load_policy(path)
        assert label


def test_default_policy_values(policy):
    assert policy.approval.scrutiny_threshold == Decimal("10000.00")
    assert policy.payment.auto_pay_currencies == ["USD"]
    assert policy.signals.review_on_vendor_identity_change is True
    assert isinstance(policy, Policy)


# ------------------------------------------------------------------ findings table


def test_every_finding_code_has_a_spec_and_a_consistent_route():
    assert set(FINDING_SPECS) == set(FindingCode)
    for code, spec in FINDING_SPECS.items():
        assert spec.meaning
        if spec.reviewer_resolvable:
            assert spec.severity is Severity.REVIEW, code  # only REVIEW items can be cleared
        if spec.severity in (Severity.ERROR, Severity.REVIEW) and code.name.endswith("_MISMATCH"):
            assert not spec.reviewer_resolvable, code  # arithmetic errors are never click-cleared


def test_errors_are_never_reviewer_resolvable():
    assert not [
        c
        for c, s in FINDING_SPECS.items()
        if s.severity is Severity.ERROR and s.reviewer_resolvable
    ]


def test_currency_hold_is_not_resolvable_by_a_click():
    assert not FINDING_SPECS[FindingCode.UNSUPPORTED_CURRENCY].reviewer_resolvable


# ------------------------------------------------------------------ schemas


@pytest.mark.parametrize("bad", [1.5, True, float("nan"), "abc", "1e3", [1], {"a": 1}])
def test_money_fields_reject_unsafe_values(bad):
    with pytest.raises(ValidationError):
        LineItemDraft(line_no=1, quantity=bad)


def test_money_fields_accept_exact_values():
    line = LineItemDraft(line_no=1, quantity="2", unit_price="$1,250.50", amount=Decimal("2501.00"))
    assert (line.quantity, line.unit_price) == (Decimal(2), Decimal("1250.50"))


def test_assignment_is_validated_too():
    draft = make_draft()
    with pytest.raises(ValidationError):
        draft.total = 12.5  # a float can never be assigned in afterwards
    draft.total = "12.50"
    assert draft.total == Decimal("12.50")


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        InvoiceDraft(invoice_number="x", approved=True)
    with pytest.raises(ValidationError):
        LineItemDraft(line_no=1, discount="10%")


def test_money_serializes_as_a_string_never_a_float(index, policy):
    from datetime import date

    from invoice_flow.rules import validate_invoice

    report = validate_invoice(
        make_draft(lines=[("WidgetA", 2, "250.10")]), "", index, policy, date(2026, 11, 3)
    )
    payload = json.loads(report.model_dump_json())
    total = payload["normalized"]["total"]
    assert isinstance(total, str) and total == "500.20"
    assert isinstance(payload["normalized"]["lines"][0]["unit_price"], str)


def test_normalized_invoice_requires_checked_fields():
    with pytest.raises(ValidationError):
        NormalizedInvoice(invoice_number="x")
