"""Windowing tests: exact spans, coverage, overlap sharing, short tail, zero_text, guards.

The window is the anti-fabrication unit — an anchor is later checked against a window's absolute
span — so these assert the spans and the ABSOLUTE ``[PAGE n]`` numbering exactly.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.corpus.extraction.windows import build_windows
from app.models.enums import TextSource
from app.models.orm import DocumentPage, Matter, User


def _mk_pages(
    db: Session,
    matter: Matter,
    *,
    n: int,
    zero_text_pages: set[int] | None = None,
) -> list[DocumentPage]:
    """Insert ``n`` DocumentPage rows (1..n) directly; return them in page order.

    A page in ``zero_text_pages`` gets empty text + ``zero_text=True`` (image-only, un-OCR'd).
    No PDFs are built — this wave windows page rows, it does not run the page pipeline.
    """
    document_id = uuid.uuid4()
    zero = zero_text_pages or set()
    pages: list[DocumentPage] = []
    for page_no in range(1, n + 1):
        is_zero = page_no in zero
        page = DocumentPage(
            firm_id=matter.firm_id,
            document_id=document_id,
            page_no=page_no,
            text="" if is_zero else f"content of page {page_no}",
            text_source=TextSource.NONE.value if is_zero else TextSource.TEXT_LAYER.value,
            zero_text=is_zero,
        )
        db.add(page)
        pages.append(page)
    db.commit()
    return pages


def test_ten_pages_size_four_overlap_one_exact_spans(
    db: Session, dev_user: User, matter: Matter
) -> None:
    pages = _mk_pages(db, matter, n=10)
    windows = build_windows(pages, size=4, overlap=1)
    assert [(w.start_page, w.end_page) for w in windows] == [(1, 4), (4, 7), (7, 10)]


def test_every_page_appears_in_at_least_one_window(
    db: Session, dev_user: User, matter: Matter
) -> None:
    pages = _mk_pages(db, matter, n=10)
    windows = build_windows(pages, size=4, overlap=1)
    covered: set[int] = set()
    for w in windows:
        covered.update(range(w.start_page, w.end_page + 1))
    assert covered == set(range(1, 11))


def test_consecutive_windows_share_the_overlap_pages(
    db: Session, dev_user: User, matter: Matter
) -> None:
    pages = _mk_pages(db, matter, n=10)
    windows = build_windows(pages, size=4, overlap=1)
    # overlap=1 → each window's end page is the next window's start page.
    for i in range(len(windows) - 1):
        assert windows[i].end_page == windows[i + 1].start_page


def test_last_window_may_be_short(db: Session, dev_user: User, matter: Matter) -> None:
    pages = _mk_pages(db, matter, n=5)
    windows = build_windows(pages, size=4, overlap=1)
    assert [(w.start_page, w.end_page) for w in windows] == [(1, 4), (4, 5)]
    # The tail window carries fewer than `size` pages but still ends on the last page.
    assert windows[-1].end_page == 5


def test_window_id_and_absolute_page_headers(db: Session, dev_user: User, matter: Matter) -> None:
    pages = _mk_pages(db, matter, n=4)
    document_id = pages[0].document_id
    windows = build_windows(pages, size=4, overlap=1)
    w = windows[0]
    assert w.window_id == f"{document_id}:1-4"
    # Headers carry ABSOLUTE page numbers so the model can only cite real page numbers.
    assert "[PAGE 1]\ncontent of page 1" in w.text
    assert "[PAGE 4]\ncontent of page 4" in w.text


def test_zero_text_page_contributes_header_with_empty_body(
    db: Session, dev_user: User, matter: Matter
) -> None:
    pages = _mk_pages(db, matter, n=2, zero_text_pages={2})
    windows = build_windows(pages, size=8, overlap=2)
    # Page 2 is image-only: its header stays (absolute numbering) but the body is empty.
    assert windows[0].text == "[PAGE 1]\ncontent of page 1\n[PAGE 2]\n"


def test_absolute_numbering_survives_a_second_window(
    db: Session, dev_user: User, matter: Matter
) -> None:
    # The second window must still cite absolute page numbers (7..10), not window-relative ones.
    pages = _mk_pages(db, matter, n=10)
    windows = build_windows(pages, size=4, overlap=1)
    last = windows[-1]
    assert last.start_page == 7
    assert "[PAGE 7]" in last.text
    assert "[PAGE 10]" in last.text
    assert "[PAGE 1]" not in last.text


def test_overlap_ge_size_raises_value_error(db: Session, dev_user: User, matter: Matter) -> None:
    pages = _mk_pages(db, matter, n=4)
    try:
        build_windows(pages, size=4, overlap=4)
    except ValueError as exc:
        assert "overlap" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected ValueError for overlap >= size")


def test_empty_page_list_returns_empty(db: Session, dev_user: User, matter: Matter) -> None:
    assert build_windows([], size=4, overlap=1) == []


def test_pages_are_windowed_in_page_order_regardless_of_input_order(
    db: Session, dev_user: User, matter: Matter
) -> None:
    pages = _mk_pages(db, matter, n=4)
    shuffled = [pages[2], pages[0], pages[3], pages[1]]
    windows = build_windows(shuffled, size=4, overlap=1)
    assert (windows[0].start_page, windows[0].end_page) == (1, 4)
    assert "[PAGE 1]" in windows[0].text.split("[PAGE 2]")[0]
