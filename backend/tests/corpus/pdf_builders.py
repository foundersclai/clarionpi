"""Deterministic synthetic-PDF helpers for the page-pipeline suite.

reportlab is a dev dependency; these builders are fully deterministic — no randomness, no
wall-clock content — so tests are reproducible. reportlab is imported *inside* the functions
to keep the module import-light (importing this file must not drag reportlab into every test).

Three shapes:

* :func:`build_text_pdf` — one text page per input string (a real, extractable text layer).
* :func:`build_imageonly_pdf` — pages with vector graphics only and NO text operators, so
  ``pdfplumber.extract_text()`` returns empty (the OCR-fallback trigger). The module
  self-test at import asserts that property.
* :data:`CORRUPT_PDF_BYTES` — a byte string with a PDF header but a broken body.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

# A header-only, structurally-broken PDF: pdfplumber.open raises on it (unreadable_pdf path).
CORRUPT_PDF_BYTES: bytes = b"%PDF-1.7\nnot really a pdf"

# Wrap width for drawn text (characters) so long strings stay on the letter-size page.
_WRAP_COLS = 80


def _wrap(text: str, cols: int = _WRAP_COLS) -> list[str]:
    """Hard-wrap ``text`` every ``cols`` characters (each source line kept independent).

    An empty string yields no lines — the resulting page has no text operators, which is
    exactly what the mixed-document test wants for its blank page.
    """
    lines: list[str] = []
    for source_line in text.split("\n"):
        if source_line == "":
            continue
        for start in range(0, len(source_line), cols):
            lines.append(source_line[start : start + cols])
    return lines


def build_text_pdf(pages: Sequence[str]) -> bytes:
    """One letter-size page per string, each drawn as wrapped ``drawString`` lines.

    A blank string produces a genuinely empty page (no text operators) — used to force an
    OCR fallback for one page of an otherwise-text document.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    width, height = letter
    pdf = canvas.Canvas(buffer, pagesize=letter)
    for page_text in pages:
        y = height - 72
        for line in _wrap(page_text):
            pdf.drawString(72, y, line)
            y -= 14
        pdf.showPage()  # finalize this page even when it drew nothing
    pdf.save()
    return buffer.getvalue()


def build_imageonly_pdf(n_pages: int) -> bytes:
    """``n_pages`` letter-size pages of vector rectangles/lines only — NO text operators.

    ``pdfplumber.extract_text()`` returns empty for these, so the density floor routes them
    to OCR. (Genuine raster images would need a bundled asset; deterministic vector shapes
    exercise the same "no text layer" path with zero external inputs.)
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    for _ in range(n_pages):
        pdf.rect(72, 72, 200, 120, stroke=1, fill=0)
        pdf.line(72, 300, 400, 300)
        pdf.rect(300, 400, 150, 150, stroke=1, fill=1)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _selftest() -> None:
    """Assert the invariant the suite relies on: image-only pages extract to empty text."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(build_imageonly_pdf(1))) as pdf:
        extracted = pdf.pages[0].extract_text()
    assert not (extracted or "").strip(), "image-only page must yield empty extract_text()"


_selftest()
