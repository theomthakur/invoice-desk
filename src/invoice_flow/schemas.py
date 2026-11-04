"""Domain contracts shared by every part of the application.

Two layers, kept apart on purpose:

* ``InvoiceDraft``      what a parser or model *claims* the source says. It tolerates bad
                        values so they can become findings instead of crashes.
* ``NormalizedInvoice`` a checked invoice. Only this can reach the payment gate.

Money is ``Decimal`` here and a decimal *string* in JSON. Floats and booleans are rejected.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from .findings import Finding
from .money import MoneyError, parse_decimal

SCHEMA_VERSION = "1"


class StrictModel(BaseModel):
    """Rejects unknown fields and re-validates on assignment, so no code path can put an
    unchecked value (a float, a bad string) into a money field."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _coerce_money(value: object) -> object:
    if value is None:
        return None
    try:
        return parse_decimal(value)
    except MoneyError as exc:
        raise ValueError(f"invalid amount ({exc.reason})") from exc


# A finite, exact number. Strings like "$1,200.50" are accepted; floats and bools are not.
Amount = Annotated[Decimal, BeforeValidator(_coerce_money), Field(allow_inf_nan=False)]


class SourceFormat(StrEnum):
    TXT = "txt"
    PDF = "pdf"
    JSON = "json"
    CSV = "csv"
    XML = "xml"


class Outcome(StrEnum):
    """Business result of a completed run. A valid rejection is not a failure."""

    PAID = "paid"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"
    SUPERSEDED = "superseded"


class RunLifecycle(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class Eligibility(StrEnum):
    """What the deterministic rules allow, before any approval step."""

    ELIGIBLE = "eligible"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------------------
# Source documents
# ---------------------------------------------------------------------------------------


class PageText(StrictModel):
    page: int = Field(ge=1)
    text: str


class SourceDocument(StrictModel):
    document_id: str
    filename: str
    format: SourceFormat
    size_bytes: int = Field(ge=0)
    byte_hash: str
    text: str
    pages: list[PageText] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    original_pdf: bytes | None = Field(default=None, exclude=True, repr=False)


# ---------------------------------------------------------------------------------------
# Draft (claims) layer
# ---------------------------------------------------------------------------------------


class Correction(StrictModel):
    """A repair applied while extracting. ``evidence`` must be a snippet of the source."""

    field: str
    item_ref: str | None = None
    original: str
    corrected: str
    evidence: str


class LineItemDraft(StrictModel):
    line_no: int = Field(ge=1)
    description: str | None = None
    quantity: Amount | None = None
    unit_price: Amount | None = None
    amount: Amount | None = None
    note: str | None = None


class InvoiceDraft(StrictModel):
    origin: Literal["structured", "model"] = "structured"
    invoice_number: str | None = None
    revision: str | None = None
    vendor_name: str | None = None
    vendor_address: str | None = None
    invoice_date_raw: str | None = None
    due_date_raw: str | None = None
    currency: str | None = None
    line_items: list[LineItemDraft] = Field(default_factory=list)
    subtotal: Amount | None = None
    tax_rate: Amount | None = None
    tax_amount: Amount | None = None
    shipping: Amount | None = None
    total: Amount | None = None
    payment_terms: str | None = None
    notes: str | None = None
    corrections: list[Correction] = Field(default_factory=list)
    parse_findings: list[Finding] = Field(default_factory=list)


# ---------------------------------------------------------------------------------------
# Checked layer
# ---------------------------------------------------------------------------------------


class NormalizedLine(StrictModel):
    line_no: int
    description: str
    sku: str | None
    quantity: int
    unit_price: Amount
    amount: Amount
    note: str | None = None


class NormalizedInvoice(StrictModel):
    invoice_number: str
    revision: str | None
    vendor_name: str
    invoice_date: date | None
    due_date: date
    currency: str
    lines: list[NormalizedLine]
    subtotal: Amount
    tax: Amount
    shipping: Amount
    total: Amount
    payment_terms: str | None = None


class ValidationReport(StrictModel):
    schema_version: str = SCHEMA_VERSION
    policy_version: str
    policy_fingerprint: str
    findings: list[Finding]
    eligibility: Eligibility
    requires_scrutiny: bool
    normalized: NormalizedInvoice | None
    semantic_fingerprint: str | None = None


# ---------------------------------------------------------------------------------------
# Approval, payment and run records
# ---------------------------------------------------------------------------------------


class ApprovalStage(StrEnum):
    APPROVAL = "approval"  # approver proposal
    CRITIQUE = "critique"
    FINAL = "final"  # the decision the payment gate honours
    REVIEW = "review"  # simulated human reviewer


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    NEEDS_REVIEW = "needs_review"


class ApprovalRecord(StrictModel):
    approval_id: str
    workspace_id: str
    version_id: str
    stage: ApprovalStage
    action: ApprovalAction
    rationale: str
    actor: str
    policy_fingerprint: str
    content_hash: str
    scrutiny_completed: bool
    resolved_codes: list[str] = Field(default_factory=list)
    created_at: datetime


class PaymentReceipt(StrictModel):
    payment_id: str
    transaction_id: str
    workspace_id: str
    payable_id: str
    version_id: str
    approval_id: str
    vendor: str
    amount: Amount
    currency: str
    simulated: Literal[True] = True
    created_at: datetime
    already_paid: bool = False


class RunEvent(StrictModel):
    seq: int = Field(ge=1)
    timestamp: datetime
    stage: Literal[
        "intake",
        "extraction",
        "validation",
        "approval",
        "critique",
        "payment",
        "completion",
        "error",
    ]
    type: str
    payload: dict[str, object] = Field(default_factory=dict)


class Usage(StrictModel):
    """Model usage for one run. Part 02 added ``tool_calls`` and ``usage_complete`` (additive,
    defaults keep Part 01 payloads valid). ``estimated_cost_usd`` is ``None`` when unknown;
    unknown is never reported as zero."""

    model_calls: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usage_complete: bool = True
    estimated_cost_usd: str | None = None


class RunResult(StrictModel):
    """Everything a client needs about one run. Part 02 fills the model-related parts."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    workspace_id: str
    lifecycle: RunLifecycle
    outcome: Outcome | None = None
    terminal_reason: str | None = None
    document_id: str | None = None
    filename: str | None = None
    payable_id: str | None = None
    version_id: str | None = None
    invoice: NormalizedInvoice | None = None
    draft: InvoiceDraft | None = None
    findings: list[Finding] = Field(default_factory=list)
    requires_scrutiny: bool = False
    approval: list[ApprovalRecord] = Field(default_factory=list)
    receipt: PaymentReceipt | None = None
    mode: str | None = None
    provider: str | None = None
    model: str | None = None
    usage: Usage = Field(default_factory=Usage)
    policy_version: str | None = None
    content_hash: str | None = None  # the hash a reviewer must echo to prove they saw this version
    review_summary: dict[str, object] | None = None  # proposal, critique and revision, concise
    attempt_of: str | None = None  # run this one retried, if any
    started_at: datetime | None = None
    finished_at: datetime | None = None
