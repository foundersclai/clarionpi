"""Page windowing — the sliding excerpts the extractors read (corpus_extraction §4).

A :class:`Window` is a contiguous, overlapping run of pages joined into one prompt-ready text.
Two structural facts make anchor validation possible downstream:

* Windows carry **absolute, 1-based page numbers** (``start_page``/``end_page`` inclusive) and
  each page's text is prefixed ``"[PAGE n]"`` with the *real* page number — so a model can only
  cite pages it was actually shown, and an anchor is checked against ``[start_page, end_page]``.
* Consecutive windows share ``overlap`` pages (they step ``size - overlap``), so a record that
  straddles a page seam still lands *whole* in at least one window rather than being split
  across two windows that each see only half of it.

No arithmetic on document content happens here — this is pure page slicing + text joining.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.models.orm import DocumentPage


@dataclass(frozen=True)
class Window:
    """One extraction window: an inclusive, 1-based absolute page span plus its joined text.

    ``window_id`` is ``"{document_id}:{start_page}-{end_page}"`` — the stable idempotency key an
    :class:`~app.models.orm.ExtractionRun` records and the anti-fabrication target a
    :class:`~app.models.schemas.PageAnchor` carries. ``text`` is the concatenation of each
    page's body, each prefixed with a ``"[PAGE n]\\n"`` header carrying the absolute page number.
    """

    document_id: uuid.UUID
    start_page: int
    end_page: int
    window_id: str
    text: str


def _page_block(page: DocumentPage) -> str:
    """Render one page as ``"[PAGE n]\\n<body>"`` using the ABSOLUTE page number.

    A ``zero_text`` (image-only, un-OCR'd) page contributes its header with an empty body: the
    page still exists and its number must stay in the absolute numbering the model sees, so a
    later page's anchor lands on the right page.
    """
    return f"[PAGE {page.page_no}]\n{page.text}"


def build_windows(pages: Sequence[DocumentPage], *, size: int, overlap: int) -> list[Window]:
    """Slice ``pages`` into overlapping :class:`Window`s of at most ``size`` pages.

    Windows step ``size - overlap`` pages, so consecutive windows share ``overlap`` pages; the
    last window may be short. Every page appears in at least one window. Pages are windowed in
    ``page_no`` order (the caller's order is not trusted). An empty page list yields ``[]``.

    ``overlap`` must be strictly less than ``size`` (otherwise the step is ≤ 0 and the walk
    cannot advance) — a non-advancing window config is a programming error, so it raises
    :class:`ValueError` rather than looping forever.
    """
    if overlap >= size:
        raise ValueError(f"window overlap ({overlap}) must be < size ({size})")
    if not pages:
        return []

    ordered = sorted(pages, key=lambda p: p.page_no)
    step = size - overlap
    document_id = ordered[0].document_id

    windows: list[Window] = []
    start = 0
    n = len(ordered)
    while start < n:
        chunk = ordered[start : start + size]
        start_page = chunk[0].page_no
        end_page = chunk[-1].page_no
        window_id = f"{document_id}:{start_page}-{end_page}"
        text = "\n".join(_page_block(page) for page in chunk)
        windows.append(
            Window(
                document_id=document_id,
                start_page=start_page,
                end_page=end_page,
                window_id=window_id,
                text=text,
            )
        )
        # The chunk reached the last page — stop, so trailing overlap-only steps don't emit a
        # duplicate tail window that adds no new pages.
        if start + size >= n:
            break
        start += step
    return windows
