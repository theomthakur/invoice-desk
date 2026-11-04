"""Text signals. These raise a flag for a person to look at; none of them proves fraud.

The document is data. Wording inside it can never authorize payment or change a rule.
"""

from __future__ import annotations

import re

from ..config import Policy
from ..findings import Finding, FindingCode, Severity, make_finding
from ..textnorm import collapse_ws

_CONTEXT = 40


def _first_match(patterns: list[str], text: str) -> tuple[str, int] | None:
    matches = []
    for pattern in patterns:
        found = list(re.finditer(pattern, text, flags=re.IGNORECASE))
        if found:
            matches.append(found)
    if not matches:
        return None
    first = min((m[0] for m in matches), key=lambda m: m.start())
    start, end = max(0, first.start() - _CONTEXT), min(len(text), first.end() + _CONTEXT)
    count = sum(len(m) for m in matches)
    return collapse_ws(text[start:end]), count


def check_signals(text: str, policy: Policy) -> list[Finding]:
    signals = policy.signals
    findings: list[Finding] = []

    def add(code: FindingCode, patterns: list[str], message: str, severity: Severity | None = None):
        if hit := _first_match(patterns, text):
            snippet, count = hit
            suffix = f" ({count} matches)" if count > 1 else ""
            findings.append(
                make_finding(
                    code, message + suffix, severity=severity, evidence=snippet, observed=snippet
                )
            )

    add(
        FindingCode.VENDOR_IDENTITY_CHANGE,
        signals.vendor_identity_change,
        "The document mentions a former vendor name; verify the vendor before paying.",
        Severity.REVIEW if signals.review_on_vendor_identity_change else Severity.WARNING,
    )
    add(
        FindingCode.PAYMENT_PRESSURE_LANGUAGE,
        signals.payment_pressure,
        "The document uses urgency or pressure wording. A risk signal, not proof of fraud.",
    )
    add(
        FindingCode.PAYMENT_INSTRUCTION_IN_DOCUMENT,
        signals.payment_instruction,
        "The document contains payment or bank-detail instructions. Confirm them with the "
        "vendor through a known channel.",
    )
    add(
        FindingCode.EMBEDDED_INSTRUCTIONS,
        signals.embedded_instructions,
        "The document text tries to instruct the processing system. It was treated as data.",
    )
    return findings
