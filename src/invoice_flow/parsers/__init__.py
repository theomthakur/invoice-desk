"""Document parsing. Structured formats map straight to a draft; TXT and PDF stop at text."""

from __future__ import annotations

from pathlib import Path

from ..config import Policy
from ..schemas import SourceFormat
from .common import (
    ParsedSource,
    SourceError,
    check_content_matches,
    decode_text,
    format_for,
    make_source,
    read_file_bytes,
)
from .csv_ import parse_csv_invoice
from .json_ import parse_json_invoice
from .pdf import extract_pdf_text
from .xml_ import parse_xml_invoice

__all__ = ["ParsedSource", "SourceError", "draft_from_source", "load_source", "parse_bytes"]


def parse_bytes(path: Path, data: bytes, policy: Policy) -> ParsedSource:
    """Parse already-read bytes. ``path`` supplies the name and extension only."""
    fmt = format_for(path, policy)
    check_content_matches(fmt, data)
    lines_max = policy.limits.max_line_items

    if fmt is SourceFormat.PDF:
        text, pages = extract_pdf_text(data, policy)
        return ParsedSource(
            make_source(path=path, fmt=fmt, data=data, text=text, pages=pages), None
        )

    text = decode_text(data, policy)
    source = make_source(path=path, fmt=fmt, data=data, text=text)
    if fmt is SourceFormat.TXT:
        return ParsedSource(source, None)
    if fmt is SourceFormat.JSON:
        return ParsedSource(source, parse_json_invoice(text, lines_max))
    if fmt is SourceFormat.CSV:
        return ParsedSource(source, parse_csv_invoice(text, policy.limits.max_csv_rows, lines_max))
    return ParsedSource(source, parse_xml_invoice(data, lines_max))


def draft_from_source(source, policy: Policy):
    """Re-derive the structured draft of a stored document (``None`` for TXT and PDF).

    Stored documents keep their decoded text, so a queued run can be executed later without the
    original upload. The result is identical to what :func:`parse_bytes` produced.
    """
    lines_max = policy.limits.max_line_items
    if source.format is SourceFormat.JSON:
        return parse_json_invoice(source.text, lines_max)
    if source.format is SourceFormat.CSV:
        return parse_csv_invoice(source.text, policy.limits.max_csv_rows, lines_max)
    if source.format is SourceFormat.XML:
        return parse_xml_invoice(source.text.encode("utf-8"), lines_max)
    return None


def load_source(path: str | Path, policy: Policy, root: Path | None = None) -> ParsedSource:
    """Read and parse a file the server chose. See :func:`read_file_bytes` for the safety rules."""
    resolved, data = read_file_bytes(path, policy, root)
    return parse_bytes(resolved, data, policy)
