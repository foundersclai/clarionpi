"""Unit tests for the OCR adapter port (``app.corpus.ocr``).

Deterministic and offline: the only test that needs the tesseract binary is skip-guarded, so
this suite is green on a machine with no OCR installed (the M1 bootstrap case).
"""

from __future__ import annotations

import shutil

import pytest

from app.core.config import get_settings
from app.corpus.ocr import (
    FakeOcr,
    NullOcr,
    OcrEngine,
    OcrPageResult,
    OcrUnavailable,
    TesseractOcr,
    get_ocr_engine,
)

from .pdf_builders import build_text_pdf


def test_nullocr_refuses_every_call() -> None:
    engine = NullOcr()
    assert isinstance(engine, OcrEngine)  # satisfies the runtime-checkable protocol
    with pytest.raises(OcrUnavailable, match="OCR_ENGINE=none"):
        engine.ocr_page(b"%PDF-1.7", 1)


def test_fakeocr_returns_mapped_text_and_records_calls() -> None:
    engine = FakeOcr(pages={2: "page two text"}, default_text="fallback", confidence=0.5)

    mapped = engine.ocr_page(b"", 2)
    assert mapped == OcrPageResult(text="page two text", confidence=0.5)

    defaulted = engine.ocr_page(b"", 5)
    assert defaulted == OcrPageResult(text="fallback", confidence=0.5)

    # Every call is recorded, in order, by 1-based page number.
    assert engine.calls == [2, 5]


def test_fakeocr_defaults() -> None:
    engine = FakeOcr()
    result = engine.ocr_page(b"", 1)
    assert result.text == ""
    assert result.confidence == 0.88


@pytest.mark.parametrize(
    ("engine_value", "expected_cls"),
    [("none", NullOcr), ("fake", FakeOcr)],
)
def test_get_ocr_engine_by_setting(
    monkeypatch: pytest.MonkeyPatch, engine_value: str, expected_cls: type
) -> None:
    monkeypatch.setenv("OCR_ENGINE", engine_value)
    get_settings.cache_clear()
    try:
        engine = get_ocr_engine()
        assert isinstance(engine, expected_cls)
    finally:
        get_settings.cache_clear()


def test_get_ocr_engine_unknown_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_ENGINE", "banana")
    get_settings.cache_clear()
    try:
        with pytest.raises(OcrUnavailable, match="banana"):
            get_ocr_engine()
    finally:
        get_settings.cache_clear()


@pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract binary not installed",
)
def test_tesseract_ocr_reads_a_rendered_page() -> None:
    engine = TesseractOcr()
    pdf_bytes = build_text_pdf(["HELLO WORLD"])
    result = engine.ocr_page(pdf_bytes, 1)
    assert "HELLO" in result.text
