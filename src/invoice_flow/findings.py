"""Finding model and the stable finding-code table.

Severity decides the route:

* ``ERROR``   -> the invoice is rejected; nothing can be paid.
* ``REVIEW``  -> the invoice is held as ``needs_review``; payment is blocked until a person
  resolves the finding (only for codes marked reviewer-resolvable).
* ``WARNING`` -> payment is still possible, but the approval rationale must address it.
* ``INFO``    -> shown to the user only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Severity(StrEnum):
    ERROR = "ERROR"
    REVIEW = "REVIEW"
    WARNING = "WARNING"
    INFO = "INFO"


class FindingCode(StrEnum):
    # data integrity
    MISSING_FIELD = "MISSING_FIELD"
    SOURCE_FIELD_INVALID = "SOURCE_FIELD_INVALID"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    NEGATIVE_AMOUNT = "NEGATIVE_AMOUNT"
    # inventory
    UNKNOWN_ITEM = "UNKNOWN_ITEM"
    AMBIGUOUS_ITEM = "AMBIGUOUS_ITEM"
    ZERO_STOCK_ITEM = "ZERO_STOCK_ITEM"
    INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK"
    # arithmetic
    LINE_TOTAL_MISMATCH = "LINE_TOTAL_MISMATCH"
    SUBTOTAL_MISMATCH = "SUBTOTAL_MISMATCH"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    TAX_MISMATCH = "TAX_MISMATCH"
    # dates
    UNRESOLVED_DATE = "UNRESOLVED_DATE"
    AMBIGUOUS_DATE = "AMBIGUOUS_DATE"
    DUE_BEFORE_INVOICE = "DUE_BEFORE_INVOICE"
    TERMS_DATE_INCONSISTENT = "TERMS_DATE_INCONSISTENT"
    OVERDUE = "OVERDUE"
    # currency
    UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
    CURRENCY_ASSUMED = "CURRENCY_ASSUMED"
    # signals in the document text
    VENDOR_IDENTITY_CHANGE = "VENDOR_IDENTITY_CHANGE"
    PAYMENT_PRESSURE_LANGUAGE = "PAYMENT_PRESSURE_LANGUAGE"
    PAYMENT_INSTRUCTION_IN_DOCUMENT = "PAYMENT_INSTRUCTION_IN_DOCUMENT"
    EMBEDDED_INSTRUCTIONS = "EMBEDDED_INSTRUCTIONS"
    # extraction repair
    EXTRACTION_CORRECTION = "EXTRACTION_CORRECTION"
    CORRECTION_NOT_IN_SOURCE = "CORRECTION_NOT_IN_SOURCE"
    SUBSTANTIVE_CORRECTION = "SUBSTANTIVE_CORRECTION"
    VALUE_NOT_IN_SOURCE = "VALUE_NOT_IN_SOURCE"
    # identity / versions
    DUPLICATE_SUBMISSION = "DUPLICATE_SUBMISSION"
    SUPERSEDED_VERSION = "SUPERSEDED_VERSION"
    REVISION_AFTER_PAYMENT = "REVISION_AFTER_PAYMENT"
    REVISION_CONFLICT = "REVISION_CONFLICT"


@dataclass(frozen=True)
class FindingSpec:
    severity: Severity
    evidence_required: bool
    reviewer_resolvable: bool
    meaning: str


E, R, W, INFO = Severity.ERROR, Severity.REVIEW, Severity.WARNING, Severity.INFO

FINDING_SPECS: dict[FindingCode, FindingSpec] = {
    FindingCode.MISSING_FIELD: FindingSpec(
        E, False, False, "A required field (identity, dates, items or total) is absent."
    ),
    FindingCode.SOURCE_FIELD_INVALID: FindingSpec(
        E,
        True,
        False,
        "A source value could not be read safely (unparseable, conflicting, "
        "wrong type or non-finite).",
    ),
    FindingCode.INVALID_QUANTITY: FindingSpec(
        E, False, False, "A quantity is zero, negative or not a whole number."
    ),
    FindingCode.INVALID_AMOUNT: FindingSpec(
        E,
        False,
        False,
        "An amount is zero where a positive value is required, or finer than the currency allows.",
    ),
    FindingCode.NEGATIVE_AMOUNT: FindingSpec(
        E, False, False, "A price, tax, shipping, subtotal or total is negative."
    ),
    FindingCode.UNKNOWN_ITEM: FindingSpec(
        E, False, False, "The item is not in the inventory database."
    ),
    FindingCode.AMBIGUOUS_ITEM: FindingSpec(
        E, False, False, "The item name matches more than one inventory item."
    ),
    FindingCode.ZERO_STOCK_ITEM: FindingSpec(E, False, False, "The item exists but has no stock."),
    FindingCode.INSUFFICIENT_STOCK: FindingSpec(
        E, False, False, "The total quantity requested for an item exceeds stock."
    ),
    FindingCode.LINE_TOTAL_MISMATCH: FindingSpec(
        E, False, False, "quantity x unit price differs from the stated line amount."
    ),
    FindingCode.SUBTOTAL_MISMATCH: FindingSpec(
        E, False, False, "The stated subtotal differs from the sum of the lines."
    ),
    FindingCode.TOTAL_MISMATCH: FindingSpec(
        E, False, False, "The stated total differs from subtotal + tax + shipping."
    ),
    FindingCode.TAX_MISMATCH: FindingSpec(
        W, False, False, "The stated tax differs from subtotal x tax rate."
    ),
    FindingCode.UNRESOLVED_DATE: FindingSpec(
        E,
        True,
        False,
        "A date is present but cannot be read as a calendar date (for example 'yesterday').",
    ),
    FindingCode.AMBIGUOUS_DATE: FindingSpec(
        W, True, False, "A numeric date could be read as month/day or day/month."
    ),
    FindingCode.DUE_BEFORE_INVOICE: FindingSpec(
        E, False, False, "The due date is earlier than the invoice date."
    ),
    FindingCode.TERMS_DATE_INCONSISTENT: FindingSpec(
        W, False, False, "The due date does not match the stated payment terms."
    ),
    FindingCode.OVERDUE: FindingSpec(
        INFO, False, False, "The due date is before the processing date. Informational only."
    ),
    FindingCode.UNSUPPORTED_CURRENCY: FindingSpec(
        R, False, False, "Automatic payment supports USD only. No exchange rate was supplied."
    ),
    FindingCode.CURRENCY_ASSUMED: FindingSpec(
        INFO, False, False, "The source states no currency; the policy default was applied."
    ),
    FindingCode.VENDOR_IDENTITY_CHANGE: FindingSpec(
        R, True, True, "The document mentions a former vendor name. Verify before paying."
    ),
    FindingCode.PAYMENT_PRESSURE_LANGUAGE: FindingSpec(
        W, True, False, "Urgency or pressure wording. A risk signal, never proof of fraud."
    ),
    FindingCode.PAYMENT_INSTRUCTION_IN_DOCUMENT: FindingSpec(
        R, True, True, "The document gives or changes payment/bank instructions."
    ),
    FindingCode.EMBEDDED_INSTRUCTIONS: FindingSpec(
        R, True, True, "The document text tries to instruct the processing system."
    ),
    FindingCode.EXTRACTION_CORRECTION: FindingSpec(
        INFO, True, False, "A value was corrected during extraction, with source evidence."
    ),
    FindingCode.CORRECTION_NOT_IN_SOURCE: FindingSpec(
        R, True, True, "A correction cites evidence that does not appear in the source."
    ),
    FindingCode.SUBSTANTIVE_CORRECTION: FindingSpec(
        R, True, True, "A correction changed a value by more than a character-level repair."
    ),
    FindingCode.VALUE_NOT_IN_SOURCE: FindingSpec(
        R, True, True, "An extracted amount or quantity does not appear anywhere in the source."
    ),
    FindingCode.DUPLICATE_SUBMISSION: FindingSpec(
        INFO, False, False, "The same invoice content was already submitted."
    ),
    FindingCode.SUPERSEDED_VERSION: FindingSpec(
        INFO, False, False, "A newer explicit revision of this invoice exists."
    ),
    FindingCode.REVISION_AFTER_PAYMENT: FindingSpec(
        R, False, False, "A revision arrived after the invoice was paid. Needs reconciliation."
    ),
    FindingCode.REVISION_CONFLICT: FindingSpec(
        R, False, False, "Conflicting content for the same invoice with no explicit revision order."
    ),
}


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: FindingCode
    severity: Severity
    message: str
    field: str | None = None
    item_ref: str | None = None
    observed: str | None = None
    expected: str | None = None
    evidence: str | None = None

    @property
    def blocks_payment(self) -> bool:
        return self.severity in (Severity.ERROR, Severity.REVIEW)


def make_finding(
    code: FindingCode,
    message: str,
    *,
    severity: Severity | None = None,
    field: str | None = None,
    item_ref: str | None = None,
    observed: object = None,
    expected: object = None,
    evidence: str | None = None,
) -> Finding:
    spec = FINDING_SPECS[code]
    return Finding(
        code=code,
        severity=severity or spec.severity,
        message=message,
        field=field,
        item_ref=item_ref,
        observed=None if observed is None else str(observed),
        expected=None if expected is None else str(expected),
        evidence=evidence,
    )


def is_reviewer_resolvable(code: FindingCode) -> bool:
    return FINDING_SPECS[code].reviewer_resolvable


def has_severity(findings: list[Finding], severity: Severity) -> bool:
    return any(f.severity == severity for f in findings)
