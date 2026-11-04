"""PDF text extraction. Interpretation of the text is Part 02's job (a model, with evidence)."""

from __future__ import annotations

import io
import math

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError

from ..config import Policy
from ..schemas import PageText
from .common import SourceError


def pdf_page_count(data: bytes) -> int:
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return len(pdf.pages)
    except Exception as exc:
        raise SourceError("corrupt_pdf", "The original PDF could not be inspected.") from exc


def render_pdf_page(data: bytes, page_number: int, policy: Policy) -> bytes:
    """Rasterize one original page with bounded dimensions; never embed PDF scripts."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > policy.limits.max_pdf_pages:
                raise SourceError("too_many_pages", "The PDF exceeds the page limit.")
            if not 1 <= page_number <= len(pdf.pages):
                raise SourceError("page_not_found", "No such page in this PDF.")
            page = pdf.pages[page_number - 1]
            if not all(math.isfinite(v) and v > 0 for v in (page.width, page.height)):
                raise SourceError("invalid_page_size", "The PDF page has invalid dimensions.")
            resolution = min(120.0, 1440 * 72 / page.width, 1920 * 72 / page.height)
            image = page.to_image(resolution=resolution, force_mediabox=True)
            try:
                output = io.BytesIO()
                image.original.save(output, format="PNG")
                return output.getvalue()
            finally:
                image.original.close()
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("preview_failed", "The PDF page could not be rendered.") from exc


def extract_pdf_text(data: bytes, policy: Policy) -> tuple[str, list[PageText]]:
    """Return the full text and per-page text, or raise a clear :class:`SourceError`.

    A PDF with no text layer (a scan) is *unsupported*; it is never returned as an empty
    success. There is no OCR in this prototype.
    """
    pages: list[PageText] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > policy.limits.max_pdf_pages:
                raise SourceError(
                    "too_many_pages",
                    f"PDF has {len(pdf.pages)} pages; the limit is {policy.limits.max_pdf_pages}.",
                )
            for number, page in enumerate(pdf.pages, start=1):
                pages.append(PageText(page=number, text=(page.extract_text() or "").strip()))
    except SourceError:
        raise
    except PDFPasswordIncorrect as exc:
        raise SourceError("encrypted_pdf", "PDF is password protected.") from exc
    except PDFSyntaxError as exc:
        raise SourceError("corrupt_pdf", "PDF could not be read.") from exc
    except Exception as exc:  # pdfminer raises many types for damaged files
        if "password" in str(exc).lower() or "encrypt" in str(exc).lower():
            raise SourceError("encrypted_pdf", "PDF is encrypted.") from exc
        raise SourceError("corrupt_pdf", "PDF could not be read.") from exc

    text = "\n\n".join(page.text for page in pages if page.text)
    if not text.strip():
        raise SourceError(
            "no_text_layer",
            "PDF has no extractable text (likely a scan). OCR is not supported in this prototype.",
        )
    if len(text) > policy.limits.max_text_chars:
        raise SourceError("too_large", "PDF text exceeds the configured limit.")
    return text, pages
