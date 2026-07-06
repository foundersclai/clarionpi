"""The OCR adapter port — a swappable engine behind one small protocol.

Vendor choice is spike S1's call (corpus_ingest §8); at M1 the door has three engines:

* ``none`` — a typed refusal (:class:`NullOcr`): every call raises :class:`OcrUnavailable`.
  This is the default, so a machine with no OCR configured produces ``zero_text`` pages
  loudly rather than silently pretending to OCR.
* ``fake`` — a deterministic :class:`FakeOcr` for tests/dev: canned text + a fixed
  confidence, and it records which page numbers were asked so a test can assert the
  fallback actually fired.
* ``tesseract`` — a real local :class:`TesseractOcr` (optional binary): pypdfium2 renders
  the page, pytesseract reads words + per-word confidences.

Everything downstream depends only on :class:`OcrEngine` (the protocol) and
:func:`get_ocr_engine`, so swapping in the S1 vendor is a new class here, not a caller
change.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.core.config import Settings


class OcrUnavailable(Exception):
    """No usable OCR engine: none configured, or the tesseract binary is missing.

    The page pipeline treats this as "leave the page with no text" (``zero_text``), never as
    a hard failure — a single un-OCR-able page must not sink a batch.
    """


@dataclass(frozen=True)
class OcrPageResult:
    """One page's OCR output: the recognized text and an optional 0..1 confidence.

    ``confidence`` is a score, not currency, so a ``float`` is correct here — the same
    ``Float`` exemption the ORM spells out for ``document_pages.ocr_confidence`` (orm.py:144).
    ``None`` means "no confidence available" (e.g. a page with no recognized words).
    """

    text: str
    confidence: float | None


@runtime_checkable
class OcrEngine(Protocol):
    """The OCR port. ``page_no`` is 1-based (matching :class:`DocumentPage.page_no`)."""

    def ocr_page(self, pdf_bytes: bytes, page_no: int) -> OcrPageResult:
        """OCR page ``page_no`` (1-based) of ``pdf_bytes``; raise :class:`OcrUnavailable`."""
        ...


class NullOcr:
    """The ``none`` engine: OCR is not configured, so every call refuses loudly."""

    def ocr_page(self, pdf_bytes: bytes, page_no: int) -> OcrPageResult:
        raise OcrUnavailable("no OCR engine configured (OCR_ENGINE=none)")


class FakeOcr:
    """A deterministic test/dev engine — canned text, fixed confidence, recorded calls.

    ``pages`` maps a 1-based page number to the text to return; any page not in the map gets
    ``default_text``. Every ``ocr_page`` call appends its ``page_no`` to :attr:`calls`, so a
    test can assert the OCR fallback fired for exactly the pages it expected.
    """

    def __init__(
        self,
        pages: Mapping[int, str] | None = None,
        default_text: str = "",
        confidence: float | None = 0.88,
    ) -> None:
        self._pages: dict[int, str] = dict(pages or {})
        self._default_text = default_text
        self._confidence = confidence
        self.calls: list[int] = []

    def ocr_page(self, pdf_bytes: bytes, page_no: int) -> OcrPageResult:
        self.calls.append(page_no)
        return OcrPageResult(
            text=self._pages.get(page_no, self._default_text),
            confidence=self._confidence,
        )


class TesseractOcr:
    """Real local OCR: pypdfium2 renders the page, pytesseract reads it.

    Rendering the page at ``dpi`` (default 300) to a PIL image and running
    ``pytesseract.image_to_data`` yields per-word text + confidences. The result text is the
    words joined by spaces; the confidence is the mean of the non-negative per-word ``conf``
    values scaled from tesseract's 0..100 to 0..1, or ``None`` when no words were found.

    The tesseract binary is optional (and NOT installed on the M1 bootstrap machine), so
    :meth:`__init__` checks for it cheaply and raises :class:`OcrUnavailable` if it is absent
    — callers get the typed refusal, not a late crash mid-render.
    """

    def __init__(self, dpi: int = 300) -> None:
        self._dpi = dpi
        if shutil.which("tesseract") is None:
            raise OcrUnavailable("tesseract binary not found on PATH (OCR_ENGINE=tesseract)")
        import pytesseract

        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:  # pytesseract raises TesseractNotFoundError / others
            raise OcrUnavailable(f"tesseract not usable: {type(exc).__name__}") from exc

    def ocr_page(self, pdf_bytes: bytes, page_no: int) -> OcrPageResult:
        import pypdfium2 as pdfium
        import pytesseract

        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            page = pdf[page_no - 1]  # pypdfium2 is 0-based; page_no is 1-based
            # scale = dpi / 72 (PDF user space is 72 units per inch).
            bitmap = page.render(scale=self._dpi / 72.0)
            image = bitmap.to_pil()
        finally:
            pdf.close()

        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        words = [w for w in data.get("text", []) if w.strip()]
        confs = [float(c) for c in data.get("conf", []) if float(c) >= 0]
        text = " ".join(words)
        confidence = (sum(confs) / len(confs) / 100.0) if confs else None
        return OcrPageResult(text=text, confidence=confidence)


def get_ocr_engine(settings: Settings | None = None) -> OcrEngine:
    """Return the OCR engine named by ``settings.ocr_engine`` (defaults to process settings).

    ``none`` → :class:`NullOcr`, ``fake`` → :class:`FakeOcr`, ``tesseract`` →
    :class:`TesseractOcr`. An unknown value raises :class:`OcrUnavailable` naming it — a typo
    in ``OCR_ENGINE`` fails loudly at wiring time, not silently as "no OCR".
    """
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    engine = settings.ocr_engine
    if engine == "none":
        return NullOcr()
    if engine == "fake":
        return FakeOcr()
    if engine == "tesseract":
        return TesseractOcr()
    raise OcrUnavailable(f"unknown OCR engine {engine!r} (expected 'none', 'fake', or 'tesseract')")
