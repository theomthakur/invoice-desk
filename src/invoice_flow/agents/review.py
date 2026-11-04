"""Approver and critic: proposal -> critique -> at most one revision -> code gate.

Model output is advice. Code enforces what it can verify:
* the codes the approver says it addressed must really be in the findings;
* every WARNING finding must be addressed;
* above the scrutiny threshold, specific extra checks and a rationale that names this invoice;
* the final action can never be stronger than the rules allow.

If the critic still objects after one revision, the invoice goes to a person. The reasons are
short rationales, never hidden reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..agent_models import ApprovalProposal, Critique, OutputError
from ..findings import Finding, Severity
from ..llm import ModelClient
from ..providers.base import ProviderRequest
from ..schemas import ApprovalAction, NormalizedInvoice
from .prompts import approval_messages, critic_messages, revision_messages


@dataclass
class ReviewOutcome:
    action: ApprovalAction
    rationale: str
    proposal: ApprovalProposal | None
    critiques: list[Critique] = field(default_factory=list)
    revised: bool = False
    scrutiny_completed: bool = False
    reason: str = ""  # why the final action was chosen, in plain words


def _mentions_invoice(rationale: str, invoice: NormalizedInvoice) -> bool:
    """A specific rationale names the vendor, the invoice number or the amount."""
    text = rationale.casefold()
    vendor_words = [w for w in invoice.vendor_name.casefold().split() if len(w) > 3]
    total = f"{invoice.total:,.2f}"
    return (
        invoice.invoice_number.casefold() in text
        or str(invoice.total) in rationale
        or total in rationale
        or any(w.strip(".,") in text for w in vendor_words)
    )


def make_proposal_check(
    findings: list[Finding], requires_scrutiny: bool, invoice: NormalizedInvoice
):
    codes = {f.code.value for f in findings}
    warnings = {f.code.value for f in findings if f.severity is Severity.WARNING}

    def check(proposal: ApprovalProposal) -> None:
        problems: list[str] = []
        unknown = sorted(set(proposal.addressed_codes) - codes)
        if unknown:
            problems.append(f"addressed_codes names codes that are not in the findings: {unknown}")
        if proposal.action == "approve":
            missing = sorted(warnings - set(proposal.addressed_codes))
            if missing:
                problems.append(f"WARNING findings not addressed: {missing}")
            if requires_scrutiny:
                if not proposal.scrutiny_checks:
                    problems.append("the amount needs scrutiny: list the specific checks made")
                if not _mentions_invoice(proposal.rationale, invoice):
                    problems.append(
                        "the rationale must be specific: name the vendor, invoice number or amount"
                    )
        if problems:
            raise OutputError(problems)

    return check


def _proposal_dict(proposal: ApprovalProposal) -> dict[str, Any]:
    return proposal.model_dump()


def run_review(
    client: ModelClient,
    context: dict[str, Any],
    invoice: NormalizedInvoice,
    findings: list[Finding],
    requires_scrutiny: bool,
) -> ReviewOutcome:
    check = make_proposal_check(findings, requires_scrutiny, invoice)
    emit = client._emit

    request = ProviderRequest(messages=approval_messages(context), label="approval")
    proposal, _ = client.structured(request, ApprovalProposal, stage="approval", extra_check=check)
    emit(
        "approval",
        "proposal",
        {
            "action": proposal.action,
            "rationale": proposal.rationale,
            "addressed_codes": proposal.addressed_codes,
            "scrutiny_checks": proposal.scrutiny_checks,
        },
    )

    critiques: list[Critique] = []
    critique = _critique(client, context, proposal)
    critiques.append(critique)
    revised = False

    if not critique.agrees:
        # one revision, then the critic looks again
        revise_request = ProviderRequest(
            messages=revision_messages(context, _proposal_dict(proposal), critique.issues),
            label="revision",
        )
        proposal, _ = client.structured(
            revise_request, ApprovalProposal, stage="approval", extra_check=check
        )
        revised = True
        emit(
            "approval",
            "revision",
            {
                "action": proposal.action,
                "rationale": proposal.rationale,
                "addressed_codes": proposal.addressed_codes,
            },
        )
        critique = _critique(client, context, proposal)
        critiques.append(critique)

    if not critique.agrees:
        emit("critique", "unresolved", {"issues": critique.issues})
        return ReviewOutcome(
            action=ApprovalAction.NEEDS_REVIEW,
            rationale="The critic still objected after one revision: "
            + "; ".join(critique.issues[:3]),
            proposal=proposal,
            critiques=critiques,
            revised=revised,
            reason="The simulated VP and the critic did not reach agreement, so a person decides.",
        )

    action = ApprovalAction(proposal.action)
    return ReviewOutcome(
        action=action,
        rationale=proposal.rationale,
        proposal=proposal,
        critiques=critiques,
        revised=revised,
        scrutiny_completed=requires_scrutiny
        and bool(proposal.scrutiny_checks)
        and action is ApprovalAction.APPROVE,
        reason="The simulated VP and the critic agree.",
    )


def _critique(client: ModelClient, context: dict[str, Any], proposal: ApprovalProposal) -> Critique:
    request = ProviderRequest(
        messages=critic_messages(context, _proposal_dict(proposal)), label="critique"
    )
    critique, _ = client.structured(request, Critique, stage="critique")
    client._emit(
        "critique",
        "critique",
        {
            "agrees": critique.agrees,
            "issues": critique.issues,
            "suggested_action": critique.suggested_action,
        },
    )
    return critique
