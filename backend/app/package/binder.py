"""The exhibit ``binder.pdf`` builder — collation + continuous Bates + index + bookmarks.

Consumes the M4 :class:`~app.package.manifest.DraftBinderManifest` (the ordered, integrity-checked
picks) and the object store, and produces the binder bytes plus a per-document Bates map. This is
the artifact realization of the manifest: the manifest is the *preview* (blocking semantics, EX
tokens), the binder is the *bytes*.

Build-time gates + integrity (all typed, raised before any page is written):

* the manifest's ``blocking`` list being non-empty raises :class:`BinderBlocked` (the M5 build
  gate: pending PHI / non-``ok`` integrity never ships);
* a requested included page beyond the source PDF's real page count raises
  :class:`BinderPageMissing` (an integrity double-check against the actual bytes, not just the
  stored ``page_count``).

Structure:

* an **index page first** ("Exhibit Index"): one line per entry — the exhibit's bare token id (or
  ``"—"`` pre-mint), the filename, and the entry's Bates range;
* **continuous Bates** across the whole binder, ``f"{bates_prefix}{n:05d}"`` starting ``00001``
  AFTER the (unstamped) index page, stamped bottom-right via a reportlab overlay merged onto each
  collated page — numbering is deterministic in manifest order (same manifest -> identical numbers);
* **bookmarks**: one outline entry per exhibit at its first collated page.

Determinism (inv 10): the index page + every Bates overlay are drawn with reportlab
``invariant=1`` (no wall-clock CreationDate, no random file id), and the pypdf writer's metadata
dates + file ``/ID`` are pinned — so identical inputs produce an identical sha256. This byte
stability is asserted in the tests (not merely content stability); the module pins everything
pypdf exposes to make it hold.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from pypdf import PageObject

from app.core.storage import ObjectStorage
from app.models.orm import CaseDocument, Matter
from app.package.manifest import DraftBinderManifest, ManifestEntry

# Pinned pypdf document metadata for byte determinism (inv 10). The date form is the PDF
# ``D:YYYYMMDDHHmmSSZ`` string; a fixed value keeps the sha256 stable build-to-build.
_PRODUCER = "ClarionPI"
_PINNED_PDF_DATE = "D:20260101000000Z"
# A fixed 16-byte file id (both halves equal) so pypdf does not embed a random/entropy-derived id.
_PINNED_FILE_ID = b"0" * 16


class BinderBlocked(Exception):
    """The manifest carries build-time blockers — the binder must not build (M5 build gate).

    Carries the manifest's human-readable ``reasons`` (pending PHI, non-``ok`` integrity) so the
    refusal names exactly why the package is not shippable.
    """

    def __init__(self, *, reasons: Sequence[str]) -> None:
        self.reasons = tuple(reasons)
        joined = "; ".join(self.reasons) if self.reasons else "(no reasons given)"
        super().__init__(f"binder build blocked: {joined}")


class BinderPageMissing(Exception):
    """A manifest-included page does not exist in the source PDF — an integrity double-check.

    The manifest validated against the stored ``page_count``; this fires when the *actual* PDF
    bytes have fewer pages than the pick claims (a re-ingest/corruption mismatch), naming the
    ``document_id`` and the 1-based ``page``.
    """

    def __init__(self, *, document_id: object, page: int) -> None:
        self.document_id = document_id
        self.page = page
        super().__init__(f"included page {page} missing from document {document_id}")


def _bates_label(prefix: str, n: int) -> str:
    """The Bates label for the ``n``-th page, e.g. ``_bates_label("CP", 1) == "CP00001"``."""
    return f"{prefix}{n:05d}"


def _index_pdf_bytes(*, entries: Sequence[tuple[str, str, str]]) -> bytes:
    """Render the single "Exhibit Index" page (reportlab, ``invariant=1`` for determinism).

    Each ``entries`` triple is ``(token_display, filename, bates_range)`` already formatted by the
    caller. The page lists one line per exhibit; it is drawn deterministically (no wall-clock, no
    random id) so it contributes stable bytes to the binder.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    width, height = letter
    pdf = canvas.Canvas(buffer, pagesize=letter, invariant=1)
    pdf.setProducer(_PRODUCER)

    y = height - 72
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(72, y, "Exhibit Index")
    y -= 30
    pdf.setFont("Helvetica", 10)
    for token_display, filename, bates_range in entries:
        line = f"{token_display}   {filename}   {bates_range}"
        pdf.drawString(72, y, line)
        y -= 16
        if y < 72:  # simple overflow guard: continue on a new page (still deterministic)
            pdf.showPage()
            pdf.setFont("Helvetica", 10)
            y = height - 72
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _bates_overlay_page(*, label: str, width: float, height: float) -> PageObject:
    """A single-page reportlab overlay stamping ``label`` bottom-right; return its pypdf page.

    Drawn with ``invariant=1`` and sized to the target page so the merge lands the Bates number in
    the bottom-right margin without shifting content. Returns the ``pypdf`` page object ready for
    :meth:`PageObject.merge_page`.
    """
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height), invariant=1)
    pdf.setProducer(_PRODUCER)
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - 36, 24, label)
    pdf.showPage()
    pdf.save()
    return PdfReader(io.BytesIO(buffer.getvalue())).pages[0]


def _storage_keys_for(db: Session, *, manifest: DraftBinderManifest) -> dict[uuid.UUID, str | None]:
    """Map each manifest entry's ``document_id`` to its :class:`CaseDocument.storage_key`.

    One query over the manifest's documents. A document with no stored blob (``storage_key IS
    NULL``) maps to ``None`` — the caller turns that into a :class:`BinderPageMissing` rather than
    collating a gap.
    """
    doc_ids = [entry.document_id for entry in manifest.entries]
    if not doc_ids:
        return {}
    rows = db.scalars(select(CaseDocument).where(CaseDocument.id.in_(doc_ids)))
    return {doc.id: doc.storage_key for doc in rows}


def _token_display(entry: ManifestEntry) -> str:
    """The bare token id for the index line (``EX_3``), or ``"—"`` when the entry is pre-mint."""
    if not entry.exhibit_token:
        return "—"
    # ``exhibit_token`` is the bracketed form (``[[EX_3]]``); the index shows the bare id.
    return entry.exhibit_token.strip("[]")


def build_binder_pdf(
    db: Session,
    storage: ObjectStorage,
    *,
    matter: Matter,
    manifest: DraftBinderManifest,
    bates_prefix: str,
) -> tuple[bytes, dict[str, tuple[int, int]]]:
    """Build the exhibit ``binder.pdf`` + the per-document Bates map (package_builder §3).

    Steps:

    1. **Gate.** ``manifest.blocking`` non-empty -> :class:`BinderBlocked` (build-time gate).
    2. **Bates plan.** Walk the manifest entries in order; for each, its included pages take the
       next continuous Bates numbers (starting ``00001`` after the index page). This is computed
       first so the index page can print each entry's range and the returned map is exact.
    3. **Index page** (unstamped, first) from the planned ranges.
    4. **Collate + stamp.** For each entry, ``storage.get(storage_key)`` -> a ``pypdf`` reader;
       take the included pages (1-based -> 0-based; a page beyond the reader length raises
       :class:`BinderPageMissing`); merge a bottom-right Bates overlay onto each; append to the
       writer; record the exhibit's first-page index for a bookmark.
    5. **Bookmarks.** One outline entry per exhibit (bare token id / filename) at its first page.
    6. **Pin + write.** Pinned producer/date metadata + a fixed file ``/ID`` for byte determinism.

    Returns ``(pdf_bytes, bates_by_document)`` where ``bates_by_document`` maps ``str(document_id)``
    to ``(start, end)`` inclusive 1-based Bates numbers (so :mod:`app.package.build` can persist /
    report them). An entry whose document has a missing ``storage_key`` is treated as a
    :class:`BinderPageMissing` on its first included page (there are no bytes to collate).
    """
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, ByteStringObject

    if manifest.blocking:
        raise BinderBlocked(reasons=manifest.blocking)

    # -- 2. Bates plan: continuous numbers per entry's included pages, in manifest order. --
    # bates_by_document: str(document_id) -> (start, end) inclusive 1-based Bates numbers.
    bates_by_document: dict[str, tuple[int, int]] = {}
    # index_lines: (token_display, filename, bates_range) for the index page, entry order.
    index_lines: list[tuple[str, str, str]] = []
    next_bates = 1
    for entry in manifest.entries:
        page_count = len(entry.included_pages)
        if page_count == 0:
            # A manifest that passed the gate has no empty-include ``ok`` entries, but be explicit:
            # an entry with nothing to collate contributes no Bates numbers and an em-dash range.
            index_lines.append((_token_display(entry), entry.filename, "—"))
            continue
        start = next_bates
        end = next_bates + page_count - 1
        next_bates = end + 1
        bates_by_document[str(entry.document_id)] = (start, end)
        index_lines.append(
            (
                _token_display(entry),
                entry.filename,
                f"{_bates_label(bates_prefix, start)}–{_bates_label(bates_prefix, end)}",
            )
        )

    writer = PdfWriter()

    # -- 3. Index page first (unstamped). --
    index_reader = PdfReader(io.BytesIO(_index_pdf_bytes(entries=index_lines)))
    for page in index_reader.pages:
        writer.add_page(page)

    # The manifest carries document_id + filename but not the storage key — resolve keys here so a
    # missing/failed blob is a typed BinderPageMissing rather than a silent gap.
    storage_keys = _storage_keys_for(db, manifest=manifest)

    # -- 4/5. Collate, stamp Bates, add one bookmark per exhibit. --
    bates_n = 1
    for entry in manifest.entries:
        if not entry.included_pages:
            continue
        storage_key = storage_keys.get(entry.document_id)
        if storage_key is None:
            # No bytes to collate — treat as the first included page missing (integrity failure).
            raise BinderPageMissing(document_id=entry.document_id, page=entry.included_pages[0])

        source = PdfReader(io.BytesIO(storage.get(storage_key)))
        source_len = len(source.pages)
        first_index = len(writer.pages)  # the writer index this exhibit's first page lands at

        for page_no in entry.included_pages:
            if page_no < 1 or page_no > source_len:
                raise BinderPageMissing(document_id=entry.document_id, page=page_no)
            src_page = source.pages[page_no - 1]  # 1-based -> 0-based
            # Add the source page to the writer FIRST, then merge the Bates overlay onto the
            # writer-owned page (pypdf deprecates merging onto a reader-owned page).
            page = writer.add_page(src_page)
            overlay = _bates_overlay_page(
                label=_bates_label(bates_prefix, bates_n),
                width=float(page.mediabox.width),
                height=float(page.mediabox.height),
            )
            page.merge_page(overlay)
            bates_n += 1

        writer.add_outline_item(_bookmark_title(entry), first_index)

    # -- 6. Pin metadata + file id for byte determinism (inv 10), then write. --
    writer.add_metadata(
        {
            "/Producer": _PRODUCER,
            "/Creator": _PRODUCER,
            "/CreationDate": _PINNED_PDF_DATE,
            "/ModDate": _PINNED_PDF_DATE,
        }
    )
    pinned_id = ByteStringObject(_PINNED_FILE_ID)
    writer._ID = ArrayObject([pinned_id, pinned_id])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), bates_by_document


def _bookmark_title(entry: ManifestEntry) -> str:
    """The outline title for an exhibit: bare token id + filename, or just the filename pre-mint."""
    token_display = _token_display(entry)
    if token_display == "—":
        return entry.filename or str(entry.document_id)
    return f"{token_display} — {entry.filename}" if entry.filename else token_display


__all__ = [
    "BinderBlocked",
    "BinderPageMissing",
    "build_binder_pdf",
]
