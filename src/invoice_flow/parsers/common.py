"""Shared parser helpers: errors, safe file reading, and tolerant value readers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..config import Policy
from ..findings import Finding, FindingCode, make_finding
from ..money import MoneyError, parse_decimal
from ..schemas import InvoiceDraft, SourceDocument, SourceFormat

_EXTENSION_TO_FORMAT = {
    ".txt": SourceFormat.TXT,
    ".pdf": SourceFormat.PDF,
    ".json": SourceFormat.JSON,
    ".csv": SourceFormat.CSV,
    ".xml": SourceFormat.XML,
}


class SourceError(ValueError):
    """The file cannot be turned into a source document. The run ends as a technical failure
    with a stable ``code`` the caller can show to the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ParsedSource:
    source: SourceDocument
    draft: InvoiceDraft | None  # ``None`` for text/PDF, which need interpretation first


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ \-]")


def safe_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    cleaned = _SAFE_NAME.sub("_", base).strip(" .")
    if len(cleaned) > 120:
        # shorten the stem, never the extension: the type check depends on it
        suffix = Path(cleaned).suffix[:12]
        cleaned = cleaned[: 120 - len(suffix)].rstrip(" .") + suffix
    return cleaned or "document"


def format_for(path: Path, policy: Policy) -> SourceFormat:
    suffix = path.suffix.lower()
    if suffix not in policy.limits.allowed_extensions or suffix not in _EXTENSION_TO_FORMAT:
        raise SourceError(
            "unsupported_extension", f"File type '{suffix or 'none'}' is not supported."
        )
    return _EXTENSION_TO_FORMAT[suffix]


def read_file_bytes(
    path: str | Path, policy: Policy, root: Path | None = None
) -> tuple[Path, bytes]:
    """Read a file the *server* chose. Optionally confine it to ``root`` (no traversal or
    symlink escape). Never call this with a path that came from a model or a browser."""
    candidate = Path(path)
    if candidate.is_symlink():
        raise SourceError("not_a_file", "Symbolic links are not accepted.")
    resolved = candidate.resolve()
    if root is not None:
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise SourceError("outside_root", "File is outside the allowed folder.") from exc
    if not resolved.is_file():
        raise SourceError("not_a_file", f"No such file: {candidate.name}")
    size = resolved.stat().st_size
    if size > policy.limits.max_file_bytes:
        raise SourceError(
            "too_large", f"File is {size} bytes; the limit is {policy.limits.max_file_bytes}."
        )
    data = resolved.read_bytes()
    if not data.strip():
        raise SourceError("empty", "File is empty.")
    return resolved, data


def check_content_matches(fmt: SourceFormat, data: bytes) -> None:
    """Reject a file whose bytes contradict its extension (e.g. a PDF named .txt)."""
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:8]
    is_pdf = data.startswith(b"%PDF")
    if fmt is SourceFormat.PDF:
        if not is_pdf:
            raise SourceError("extension_mismatch", "File is named .pdf but is not a PDF.")
        return
    if is_pdf:
        raise SourceError("extension_mismatch", f"File is named .{fmt.value} but is a PDF.")
    if b"\x00" in data:
        raise SourceError("binary_content", "File contains binary data.")
    if fmt is SourceFormat.JSON and not head.startswith((b"{", b"[")):
        raise SourceError("extension_mismatch", "File is named .json but does not look like JSON.")
    if fmt is SourceFormat.XML and not head.startswith(b"<"):
        raise SourceError("extension_mismatch", "File is named .xml but does not look like XML.")


def decode_text(data: bytes, policy: Policy) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceError("bad_encoding", "File is not valid UTF-8 text.") from exc
    if len(text) > policy.limits.max_text_chars:
        raise SourceError(
            "too_large",
            f"Text is {len(text)} characters; the limit is {policy.limits.max_text_chars}.",
        )
    if not text.strip():
        raise SourceError("empty", "File has no readable text.")
    return text


def make_source(
    *,
    path: Path,
    fmt: SourceFormat,
    data: bytes,
    text: str,
    pages: list | None = None,
    notes: list[str] | None = None,
) -> SourceDocument:
    digest = sha256_hex(data)
    return SourceDocument(
        document_id=f"doc_{digest[:16]}",
        filename=safe_filename(path.name),
        format=fmt,
        size_bytes=len(data),
        byte_hash=digest,
        text=text,
        pages=pages or [],
        notes=notes or [],
        original_pdf=data if fmt is SourceFormat.PDF else None,
    )


# ---------------------------------------------------------------------------------------
# Tolerant readers. Bad values become findings; they never raise.
# ---------------------------------------------------------------------------------------


class Reader:
    """Collects ``SOURCE_FIELD_INVALID`` findings while reading loosely-typed values."""

    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def invalid(self, field: str, raw: object, why: str, item_ref: str | None = None) -> None:
        self.findings.append(
            make_finding(
                FindingCode.SOURCE_FIELD_INVALID,
                f"Could not read '{field}' safely ({why}).",
                field=field,
                item_ref=item_ref,
                observed=repr(raw)[:120],
                evidence=repr(raw)[:120],
            )
        )

    def money(self, raw: object, field: str, item_ref: str | None = None) -> Decimal | None:
        if raw is None:
            return None
        if isinstance(raw, str) and not raw.strip():
            return None
        try:
            return parse_decimal(raw)
        except MoneyError as exc:
            self.invalid(field, raw, exc.reason, item_ref)
            return None

    def text(self, raw: object, field: str, item_ref: str | None = None) -> str | None:
        if raw is None:
            return None
        if isinstance(raw, bool):
            self.invalid(field, raw, "boolean_not_text", item_ref)
            return None
        if isinstance(raw, (int, Decimal)):
            return format(raw, "f") if isinstance(raw, Decimal) else str(raw)
        if isinstance(raw, str):
            cleaned = raw.strip()
            return cleaned or None
        self.invalid(field, raw, "unsupported_type", item_ref)
        return None

    def currency(self, raw: object) -> str | None:
        value = self.text(raw, "currency")
        if value is None:
            return None
        from ..money import CURRENCY_SYMBOLS

        return CURRENCY_SYMBOLS.get(value, value.upper())

    def one_of(self, field: str, values: list[str | None]) -> str | None:
        """Single-valued field seen in several places: all non-empty values must agree."""
        distinct = {v for v in values if v}
        if len(distinct) > 1:
            self.invalid(field, sorted(distinct), "conflicting_values")
            return None
        return next(iter(distinct), None)
