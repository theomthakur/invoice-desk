"""Date checks. A date we cannot read stays unknown; it is never guessed."""

from __future__ import annotations

from datetime import date

from ..config import Policy
from ..findings import Finding, FindingCode, make_finding
from ..schemas import InvoiceDraft
from ..textnorm import DateParse, parse_date, terms_days


def check_dates(
    draft: InvoiceDraft, policy: Policy, today: date
) -> tuple[list[Finding], date | None, date | None]:
    """Findings plus the resolved ``(invoice_date, due_date)``."""
    findings: list[Finding] = []
    resolved: dict[str, DateParse] = {}

    for name, label, raw in (
        ("invoice_date", "Invoice date", draft.invoice_date_raw),
        ("due_date", "Due date", draft.due_date_raw),
    ):
        if raw is None or not raw.strip():
            continue  # absence is reported by the integrity rules
        parsed = parse_date(raw, policy.dates)
        resolved[name] = parsed
        if parsed.value is None:
            findings.append(
                make_finding(
                    FindingCode.UNRESOLVED_DATE,
                    f"{label} '{raw}' is not a readable calendar date ({parsed.reason}).",
                    field=name,
                    observed=raw,
                    evidence=raw,
                )
            )
        elif parsed.ambiguous:
            findings.append(
                make_finding(
                    FindingCode.AMBIGUOUS_DATE,
                    f"{label} '{raw}' could be month/day or day/month; read as "
                    f"{parsed.value.isoformat()} per policy ({policy.dates.slash_date_order}).",
                    field=name,
                    observed=raw,
                    expected=parsed.value.isoformat(),
                    evidence=raw,
                )
            )

    invoice_date = resolved["invoice_date"].value if "invoice_date" in resolved else None
    due_date = resolved["due_date"].value if "due_date" in resolved else None

    if invoice_date and due_date:
        if due_date < invoice_date:
            findings.append(
                make_finding(
                    FindingCode.DUE_BEFORE_INVOICE,
                    f"Due date {due_date.isoformat()} is before the invoice date "
                    f"{invoice_date.isoformat()}.",
                    field="due_date",
                    observed=due_date.isoformat(),
                    expected=f">= {invoice_date.isoformat()}",
                )
            )
        days = terms_days(draft.payment_terms)
        if days is not None:
            gap = (due_date - invoice_date).days
            if abs(gap - days) > policy.dates.terms_tolerance_days:
                findings.append(
                    make_finding(
                        FindingCode.TERMS_DATE_INCONSISTENT,
                        f"Terms '{draft.payment_terms}' imply about {days} days, but the due "
                        f"date is {gap} days after the invoice date.",
                        field="due_date",
                        observed=f"{gap} days",
                        expected=f"about {days} days",
                    )
                )
    if due_date and due_date < today:
        findings.append(
            make_finding(
                FindingCode.OVERDUE,
                f"Due date {due_date.isoformat()} is before {today.isoformat()}.",
                field="due_date",
                observed=due_date.isoformat(),
                expected=f">= {today.isoformat()}",
            )
        )
    return findings, invoice_date, due_date
