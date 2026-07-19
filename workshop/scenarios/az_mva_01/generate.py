"""Deterministic generator for the owned-synthetic Arizona MVA demo scenario (roadmap slice WD-3).

Reads the reviewable synthetic source under ``source/`` and renders one **text-layer** PDF per
document (so ClarionPI's phase0 ingest takes its ``pdfplumber`` fast path — no OCR needed). The
output is the demo's upload corpus.

Properties this file guarantees (enforced by ``backend/tests/workshop/test_az_mva_scenario.py``):

* **Owned-synthetic** — content comes only from ``source/``; nothing is imported from ``samples/``
  or the test tree.
* **Deterministic** — no wall-clock, no randomness; reportlab runs in ``invariant`` mode, so
  regeneration is byte-identical (and the re-sent bill is byte-identical to the original, which is
  exactly what the dedup demo beat needs).
* **Model-free, no currency math** — every dollar figure is authored text in ``source/``; this file
  only draws strings. It performs no arithmetic and imports nothing from ``app``.

Regenerate the upload PDFs:  ``python workshop/scenarios/az_mva_01/generate.py``
"""

from __future__ import annotations

import io
from pathlib import Path

_SCENARIO_DIR = Path(__file__).resolve().parent
SOURCE_DIR = _SCENARIO_DIR / "source"
OUTPUT_DIR = _SCENARIO_DIR / "pdf"

# Page-break sentinel line inside a source file.
_PAGE_BREAK = "<<<PAGE>>>"
# Hard-wrap guard (source lines are authored well under this, so nothing actually wraps; a stray
# long line degrades to a wrap rather than overrunning the page).
_WRAP_COLS = 95
# Fixed layout so output is deterministic and the text layer is dense enough for the ingest floor.
_FONT_NAME = "Helvetica"
_FONT_SIZE = 10
_LEADING = 14
_MARGIN = 72

# Ordered upload set: output filename -> source filename. The final entry re-sends the ER bill
# (same source -> byte-identical PDF) to exercise the dedup / review-queue demo beat.
DOCUMENTS: list[tuple[str, str]] = [
    ("01_police_report.pdf", "01_police_report.txt"),
    ("02_er_note.pdf", "02_er_note.txt"),
    ("03_er_bill.pdf", "03_er_bill.txt"),
    ("04_ortho_notes.pdf", "04_ortho_notes.txt"),
    ("05_ortho_bill.pdf", "05_ortho_bill.txt"),
    ("06_pt_notes.pdf", "06_pt_notes.txt"),
    ("07_pt_bill.pdf", "07_pt_bill.txt"),
    ("08_er_bill_resend.pdf", "03_er_bill.txt"),
]


def _wrap(line: str) -> list[str]:
    """Hard-wrap one source line; an empty line is kept as a blank spacer line."""
    if line == "":
        return [""]
    return [line[start : start + _WRAP_COLS] for start in range(0, len(line), _WRAP_COLS)]


def render_pdf(source_text: str) -> bytes:
    """Render one source document (``<<<PAGE>>>``-delimited) to a deterministic text-layer PDF."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    _, height = letter
    buffer = io.BytesIO()
    # invariant=1 fixes reportlab's timestamps + document id -> byte-identical regeneration.
    pdf = canvas.Canvas(buffer, pagesize=letter, invariant=1)
    pdf.setTitle("ClarionPI workshop scenario (synthetic)")
    for page_text in source_text.split(f"\n{_PAGE_BREAK}\n"):
        pdf.setFont(_FONT_NAME, _FONT_SIZE)
        y = height - _MARGIN
        for raw_line in page_text.split("\n"):
            for line in _wrap(raw_line):
                pdf.drawString(_MARGIN, y, line)
                y -= _LEADING
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def build_scenario() -> dict[str, bytes]:
    """Render every document in-memory: ``{output_filename: pdf_bytes}``."""
    rendered: dict[str, bytes] = {}
    for out_name, src_name in DOCUMENTS:
        source_text = (SOURCE_DIR / src_name).read_text(encoding="utf-8")
        rendered[out_name] = render_pdf(source_text)
    return rendered


def write_scenario(out_dir: Path = OUTPUT_DIR) -> list[Path]:
    """Write the upload corpus to ``out_dir`` (created if absent); return the written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, data in build_scenario().items():
        path = out_dir / name
        path.write_bytes(data)
        written.append(path)
    return written


if __name__ == "__main__":
    for pdf_path in write_scenario():
        print(f"wrote {pdf_path.name} ({pdf_path.stat().st_size} bytes)")
