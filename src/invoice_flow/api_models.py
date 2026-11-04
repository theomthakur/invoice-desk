"""HTTP payload models for ``/api/v1``.

Part 01 defines these so Part 02 (backend) and Part 03 (frontend) build against one contract.
Part 02 freezes the OpenAPI document from them; change a field only with the owners' agreement.

Rules that hold for every model here:
* money is a decimal *string*, never a JSON number;
* request models reject unknown fields, so a client cannot smuggle in an owner ID or a
  pre-approved flag;
* nothing here carries a secret, a filesystem path or a workspace ID chosen by the client.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from .findings import Finding
from .money import MoneyError, parse_decimal
from .schemas import (
    SCHEMA_VERSION,
    Outcome,
    RunEvent,
    RunLifecycle,
    RunResult,
    SourceFormat,
    StrictModel,
)

Token = Annotated[
    str, StringConstraints(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._\-]+$")
]
SafeId = Annotated[
    str, StringConstraints(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._\-]+$")
]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
CodeStr = Annotated[str, StringConstraints(pattern=r"^[A-Z_]{3,50}$")]


class ErrorDetail(StrictModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str


class ErrorResponse(StrictModel):
    """Every non-2xx response uses this shape."""

    error: ErrorDetail


# ---------------------------------------------------------------- session / config / samples


class SessionInfo(StrictModel):
    workspace_id: str
    persistence: Literal["local", "persistent", "ephemeral"]
    modes_enabled: list[str]
    csrf_token: str | None = None  # only returned by POST /session


class LimitsInfo(StrictModel):
    max_file_bytes: int
    max_pdf_pages: int
    max_line_items: int


class ConfigInfo(StrictModel):
    schema_version: str = SCHEMA_VERSION
    policy_version: str
    limits: LimitsInfo
    supported_extensions: list[str]
    enabled_modes: list[str]
    approval_threshold: str
    approval_currency: str
    payment_currencies: list[str]


class BatchAccepted(StrictModel):
    batch_id: str
    already_existed: bool = False


class SampleInfo(StrictModel):
    sample_id: SafeId
    name: str
    format: SourceFormat


class DocumentInfo(StrictModel):
    document_id: SafeId
    filename: str
    format: SourceFormat
    size_bytes: int


# ---------------------------------------------------------------- runs and batches


class CreateRunRequest(StrictModel):
    """Exactly one of ``document_id`` or ``sample_id``. Never a filesystem path."""

    document_id: SafeId | None = None
    sample_id: SafeId | None = None
    mode: Literal["mock", "live"] = "mock"
    request_token: Token  # a double click returns the same run

    @model_validator(mode="after")
    def _exactly_one_source(self) -> Self:
        if (self.document_id is None) == (self.sample_id is None):
            raise ValueError("provide exactly one of document_id or sample_id")
        return self


class RunAccepted(StrictModel):
    run_id: str
    lifecycle: RunLifecycle
    already_existed: bool = False


class CreateBatchRequest(StrictModel):
    """Supplied-sample batches only for now. ``None`` means all allowlisted samples."""

    sample_ids: list[SafeId] | None = Field(default=None, max_length=100)
    mode: Literal["mock", "live"] = "mock"
    request_token: Token


class BatchFileResult(StrictModel):
    file: str
    # "unreadable" (added in Part 02): the file could not be read, so nothing was decided for it.
    kind: Literal["process", "duplicate", "superseded", "conflict", "unreadable"]
    outcome: Outcome | None = None
    run_id: str | None = None
    related_file: str | None = None
    reason: str = ""
    amount: str | None = None
    currency: str | None = None

    @field_validator("amount")
    @classmethod
    def _amount_is_decimal_text(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                parse_decimal(value)
            except MoneyError as exc:
                raise ValueError("amount must be a decimal string") from exc
        return value


class BatchResult(StrictModel):
    schema_version: str = SCHEMA_VERSION
    batch_id: str
    lifecycle: RunLifecycle
    files: list[BatchFileResult]
    counts: dict[str, int]  # by outcome label
    paid_by_currency: dict[str, str]  # never summed across currencies

    @field_validator("paid_by_currency")
    @classmethod
    def _amounts_are_decimal_text(cls, value: dict[str, str]) -> dict[str, str]:
        for amount in value.values():
            try:
                parse_decimal(amount)
            except MoneyError as exc:
                raise ValueError("amounts must be decimal strings") from exc
        return value


class EventsPage(StrictModel):
    run_id: str
    lifecycle: RunLifecycle
    events: list[RunEvent]
    next_after_seq: int = Field(ge=0)


# ---------------------------------------------------------------- review


class ReviewRequest(StrictModel):
    """A simulated human reviewer's decision.

    ``version_id`` and ``content_hash`` prove the reviewer saw what is stored now; a stale
    request fails with 409. ``resolved_codes`` names the REVIEW findings the reviewer verified.
    The server re-checks everything: this request is a claim, not an authorization.
    """

    version_id: SafeId
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    decision: Literal["approve", "reject"]
    comment: Comment
    resolved_codes: list[CodeStr] = Field(default_factory=list, max_length=20)
    idempotency_token: Token


class ReviewResult(StrictModel):
    run: RunResult
    accepted: bool
    message: str


class ReviewOptions(StrictModel):
    """Server-provided review context; the payment gate still performs final validation."""

    version_id: SafeId | None
    content_hash: str | None
    resolvable_codes: list[CodeStr]
    blocked_codes: list[CodeStr]
    can_approve: bool
    can_reject: bool


class HandoffChoice(StrictModel):
    value: str
    label: str


class DepartmentAssignment(StrictModel):
    handoff_id: SafeId
    department: str
    department_label: str
    reason_code: str
    reason: str
    note: str
    created_at: datetime
    active: bool
    notification_sent: Literal[False] = False


class DepartmentRouting(StrictModel):
    assignment: DepartmentAssignment | None
    can_route: bool
    departments: list[HandoffChoice]
    reasons: list[HandoffChoice]


class DepartmentHandoffRequest(StrictModel):
    version_id: SafeId | None
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None
    department: Literal[
        "accounts_payable", "procurement", "vendor_management", "finance_operations"
    ]
    reason_code: Literal[
        "inventory_check",
        "vendor_verification",
        "payment_policy",
        "version_conflict",
        "additional_review",
    ]
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1500)] = ""
    idempotency_token: Token


class RetryRequest(StrictModel):
    mode: Literal["mock", "live"]
    request_token: Token


class CorrectionRequest(RetryRequest):
    document_id: SafeId


class AnalyticsAmount(StrictModel):
    currency: str
    amount: str
    receipts: int


class AnalyticsOverview(StrictModel):
    identified_invoices: int
    total_runs: int
    active_runs: int
    failed_runs: int
    review_runs: int
    flagged_active_versions: int
    simulated_receipts: int
    paid_by_currency: list[AnalyticsAmount]


class InvoiceLookupVersion(StrictModel):
    version_id: str
    revision: str | None
    status: str
    identity_flag: str | None
    run_id: str | None
    run_outcome: Outcome | None


class InvoiceLookupMatch(StrictModel):
    payable_id: str
    number: str
    vendor: str
    paid: bool
    payment_version_id: str | None
    versions: list[InvoiceLookupVersion]
    versions_truncated: bool


class InvoiceLookupResult(StrictModel):
    matches: list[InvoiceLookupMatch]
    truncated: bool


# ---------------------------------------------------------------- history and export


class HistoryItem(StrictModel):
    run_id: str
    filename: str | None = None
    lifecycle: RunLifecycle
    outcome: Outcome | None = None
    invoice_number: str | None = None
    vendor: str | None = None
    amount: str | None = None
    currency: str | None = None
    handoff_department: str | None = None
    started_at: datetime


class HistoryPage(StrictModel):
    items: list[HistoryItem]
    next_cursor: str | None = None


class ExportReport(StrictModel):
    """Sanitized JSON report for download. No credentials, no other workspace's data."""

    schema_version: str = SCHEMA_VERSION
    policy_version: str
    run: RunResult
    events: list[RunEvent]
    findings: list[Finding]
