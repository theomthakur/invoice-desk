from __future__ import annotations

import random

import pytest

from conftest import make_draft
from fixtures.drafts import DRAFTS
from invoice_flow.preflight import BatchEntry, BatchKind, plan_batch


def entries(*pairs):
    return [BatchEntry(file, draft) for file, draft in pairs]


def kinds(plan):
    return {f: d.kind for f, d in plan.items()}


def test_every_file_gets_a_disposition(policy):
    plan = plan_batch(
        entries(("a.json", make_draft(number="A")), ("b.json", make_draft(number="B"))), policy
    )
    assert kinds(plan) == {"a.json": BatchKind.PROCESS, "b.json": BatchKind.PROCESS}


def test_alternate_format_twins_become_duplicates(policy):
    plan = plan_batch(
        entries(
            ("inv_1011.txt", DRAFTS["invoice_1011.txt"]),
            ("inv_1011.pdf", DRAFTS["invoice_1011.pdf"]),
        ),
        policy,
    )
    assert sorted(kinds(plan).values(), key=str) == [BatchKind.DUPLICATE, BatchKind.PROCESS]
    duplicate = next(d for d in plan.values() if d.kind is BatchKind.DUPLICATE)
    assert duplicate.related_file in plan and "Same invoice content" in duplicate.reason


def test_revision_supersedes_original_regardless_of_file_order(policy):
    original = make_draft(lines=[("WidgetA", 2, "250.00")])
    revised = make_draft(lines=[("WidgetA", 2, "250.00"), ("WidgetB", 1, "500.00")], revision="R1")
    for order in (
        [("a_orig.json", original), ("z_rev.json", revised)],
        [("a_rev.json", revised), ("z_orig.json", original)],
    ):
        plan = plan_batch(entries(*order), policy)
        rev_file = next(f for f, d in order if d.revision == "R1")
        orig_file = next(f for f, d in order if d.revision is None)
        assert plan[rev_file].kind is BatchKind.PROCESS
        assert (
            plan[orig_file].kind is BatchKind.SUPERSEDED
            and plan[orig_file].related_file == rev_file
        )


def test_filename_sort_never_decides_which_version_is_paid(policy):
    # three versions, names chosen so alphabetical order is the reverse of revision order
    v0 = make_draft(lines=[("WidgetA", 1, "250.00")])
    v1 = make_draft(lines=[("WidgetA", 2, "250.00")], revision="R1")
    v2 = make_draft(lines=[("WidgetA", 3, "250.00")], revision="R2")
    plan = plan_batch(entries(("a.json", v2), ("b.json", v1), ("c.json", v0)), policy)
    assert plan["a.json"].kind is BatchKind.PROCESS
    assert plan["b.json"].kind is plan["c.json"].kind is BatchKind.SUPERSEDED


def test_same_revision_different_content_is_a_conflict_for_everyone(policy):
    a = make_draft(lines=[("WidgetA", 2, "250.00")])
    b = make_draft(lines=[("WidgetA", 3, "250.00")])
    plan = plan_batch(entries(("a.json", a), ("b.json", b)), policy)
    assert set(kinds(plan).values()) == {BatchKind.CONFLICT}


def test_unorderable_revision_labels_are_a_conflict(policy):
    a = make_draft(lines=[("WidgetA", 2, "250.00")])
    b = make_draft(lines=[("WidgetA", 3, "250.00")], revision="amended")
    assert set(kinds(plan_batch(entries(("a.json", a), ("b.json", b)), policy)).values()) == {
        BatchKind.CONFLICT
    }


def test_missing_identity_is_never_grouped(policy):
    a, b = make_draft(vendor=""), make_draft(vendor="", lines=[("WidgetB", 1, "500.00")])
    assert set(kinds(plan_batch(entries(("a.json", a), ("b.json", b)), policy)).values()) == {
        BatchKind.PROCESS
    }


def test_a_duplicate_of_one_revision_does_not_hide_the_others(policy):
    v0 = make_draft(lines=[("WidgetA", 1, "250.00")])
    v1 = make_draft(lines=[("WidgetA", 2, "250.00")], revision="R1")
    plan = plan_batch(entries(("v0.json", v0), ("v0_copy.pdf", v0), ("v1.json", v1)), policy)
    assert plan["v1.json"].kind is BatchKind.PROCESS
    assert {plan["v0.json"].kind, plan["v0_copy.pdf"].kind} == {
        BatchKind.SUPERSEDED,
        BatchKind.DUPLICATE,
    }


def test_plan_is_independent_of_input_order(policy):
    items = [
        ("1011.txt", DRAFTS["invoice_1011.txt"]),
        ("1011.pdf", DRAFTS["invoice_1011.pdf"]),
        ("1012.txt", DRAFTS["invoice_1012.txt"]),
        ("1012.pdf", DRAFTS["invoice_1012.pdf"]),
        ("o.json", make_draft(number="X")),
        ("r.json", make_draft(number="X", revision="R1", lines=[("WidgetB", 1, "500")])),
    ]
    baseline = kinds(plan_batch(entries(*items), policy))
    rng = random.Random(7)
    for _ in range(10):
        shuffled = items[:]
        rng.shuffle(shuffled)
        assert kinds(plan_batch(entries(*shuffled), policy)) == baseline


@pytest.mark.parametrize("count", [0, 1])
def test_tiny_batches(policy, count):
    es = entries(*[(f"{i}.json", make_draft(number=str(i))) for i in range(count)])
    assert len(plan_batch(es, policy)) == count
