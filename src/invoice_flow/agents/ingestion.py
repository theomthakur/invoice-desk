"""Ingestion agent: turn untrusted TXT/PDF text into an evidence-bearing draft.

Structured formats (JSON, CSV, XML) never reach this agent; code already read them.

Self-correction works from the source, not from agreement between answers:
1. extract once;
2. check the draft against the document (every number must appear in it) and against its own
   arithmetic;
3. if something is off, ask the model to re-read ONCE and quote the figures;
4. adopt the re-read only if it is better grounded in the document. If the document itself states
   figures that do not add up, the original is kept and validation reports it as the vendor's error.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..agent_models import ExtractionOutput
from ..config import Policy
from ..findings import Finding, FindingCode
from ..llm import ModelClient, StructuredOutputError
from ..providers.base import ProviderError, ProviderRequest
from ..rules.arithmetic import check_arithmetic
from ..rules.grounding import check_grounding
from ..rules.integrity import effective_currency
from ..schemas import InvoiceDraft, SourceDocument
from .prompts import extraction_messages, reflection_messages

_ARITH = {
    FindingCode.LINE_TOTAL_MISMATCH,
    FindingCode.SUBTOTAL_MISMATCH,
    FindingCode.TOTAL_MISMATCH,
}
_GROUNDING_BAD = {
    FindingCode.VALUE_NOT_IN_SOURCE,
    FindingCode.CORRECTION_NOT_IN_SOURCE,
    FindingCode.SUBSTANTIVE_CORRECTION,
}


@dataclass
class ExtractionResult:
    draft: InvoiceDraft
    reflected: bool = False
    adopted_reread: bool = False
    # "none" | "invoice_error" (the document states inconsistent figures) | "unresolved"
    inconsistency: str = "none"
    notes: list[str] = field(default_factory=list)


def _issues(draft: InvoiceDraft, text: str, policy: Policy) -> tuple[list[Finding], list[Finding]]:
    grounding = [f for f in check_grounding(draft, text, policy) if f.code in _GROUNDING_BAD]
    currency = effective_currency(draft, policy)
    arithmetic: list[Finding] = []
    if currency and len(currency) == 3:
        try:
            arithmetic = [f for f in check_arithmetic(draft, currency, policy) if f.code in _ARITH]
        except KeyError:  # unknown currency: arithmetic is not checkable here
            arithmetic = []
    return grounding, arithmetic


def _score(grounding: list[Finding], arithmetic: list[Finding]) -> tuple[int, int]:
    return len(grounding), len(arithmetic)


def _describe(findings: list[Finding]) -> list[str]:
    return [f"{f.code.value}: {f.message}" for f in findings]


def extract_invoice(
    client: ModelClient, source: SourceDocument, policy: Policy
) -> ExtractionResult:
    """Run the ingestion agent. Raises the model client's errors unchanged; nothing is hidden."""
    text, fmt = source.text, source.format.value

    def convertible(output: ExtractionOutput) -> None:
        output.to_draft()  # unreadable numbers trigger the one allowed repair request

    request = ProviderRequest(messages=extraction_messages(text, fmt), label="extraction")
    output, _ = client.structured(
        request, ExtractionOutput, stage="extraction", extra_check=convertible
    )
    draft = output.to_draft()

    grounding, arithmetic = _issues(draft, text, policy)
    client._emit(
        "extraction",
        "extraction_checked",
        {
            "grounding_issues": len(grounding),
            "arithmetic_issues": len(arithmetic),
            "line_items": len(draft.line_items),
            "corrections": len(draft.corrections),
        },
    )
    if not grounding and not arithmetic:
        return ExtractionResult(draft)

    # one re-read, only if the budget allows it; otherwise report what we have
    if client.budget.remaining_requests() < 1:
        client._emit("extraction", "reflection_skipped", {"reason": "request budget exhausted"})
        return ExtractionResult(
            draft, inconsistency="unresolved", notes=["No budget left to re-read the document."]
        )

    issues = _describe(grounding + arithmetic)
    reread_request = ProviderRequest(
        messages=reflection_messages(text, fmt, draft, issues), label="reflection"
    )
    try:
        output2, _ = client.structured(
            reread_request, ExtractionOutput, stage="extraction", extra_check=convertible
        )
        draft2 = output2.to_draft()
    except (StructuredOutputError, ProviderError) as exc:
        # A failed re-read must not hide the first reading. Budget exhaustion and cancellation
        # are NOT caught here: they propagate and end the run visibly.
        client._emit(
            "extraction",
            "reflection_failed",
            {
                "error": getattr(exc, "code", type(exc).__name__),
            },
        )
        return ExtractionResult(
            draft,
            reflected=True,
            inconsistency="unresolved",
            notes=["The re-read failed; the first reading is kept and checked by the rules."],
        )

    grounding2, arithmetic2 = _issues(draft2, text, policy)
    before, after = _score(grounding, arithmetic), _score(grounding2, arithmetic2)
    adopted = after < before  # strictly better grounded, or equally grounded with fewer sum errors
    chosen = draft2 if adopted else draft
    chosen_grounding, chosen_arithmetic = (
        (grounding2, arithmetic2) if adopted else (grounding, arithmetic)
    )

    if chosen_grounding:
        inconsistency = "unresolved"
    elif chosen_arithmetic:
        inconsistency = "invoice_error"  # every figure is in the document, yet they disagree
    else:
        inconsistency = "none"

    client._emit(
        "extraction",
        "reflection_result",
        {
            "before": {"ungrounded": before[0], "arithmetic": before[1]},
            "after": {"ungrounded": after[0], "arithmetic": after[1]},
            "decision": "adopted the re-read" if adopted else "kept the first reading",
            "classification": {
                "none": "transcription error fixed",
                "invoice_error": "the document states figures that do not add up",
                "unresolved": "some figures could not be found in the document",
            }[inconsistency],
        },
    )
    return ExtractionResult(
        chosen, reflected=True, adopted_reread=adopted, inconsistency=inconsistency
    )
