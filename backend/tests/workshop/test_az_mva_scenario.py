"""WD-3 — owned-synthetic Arizona MVA scenario: generator + demo-readiness guards.

Pure and offline (no DB, no network, no provider). The scenario is demo *input content*, so these
tests protect exactly what a live demo depends on:

* **BM-01** every generated PDF carries a readable text layer holding its classification cue,
* **BM-02** regeneration is byte-identical (deterministic reportlab, no wall-clock/random),
* **BM-03** the source is owned-synthetic — THE RULE: nothing from ``samples/`` or the test tree,
* **BM-04** every page clears the *real* ingest text-density floor (so phase0 takes the text-layer
  fast path, never OCR) and the re-sent bill is byte-identical (exact-match dedup fires).

The already-live phase0 ingest/classify/dedup path is unchanged by WD-3 and is covered by the M1
suite; these tests deliberately do not re-drive it.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
from pathlib import Path

import pdfplumber

from app.core.config import get_settings
from app.corpus.ingest.pages import density_ok

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCENARIO_DIR = _REPO_ROOT / "workshop" / "scenarios" / "az_mva_01"
_GENERATE_PY = _SCENARIO_DIR / "generate.py"
_SOURCE_DIR = _SCENARIO_DIR / "source"


def _load_generator():
    """Load the standalone workshop generator by path — it is not an ``app`` import."""
    spec = importlib.util.spec_from_file_location("wd3_az_mva_generate", _GENERATE_PY)
    assert spec and spec.loader, f"generator not found at {_GENERATE_PY}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Output filename -> (classification cue, a key fact) that MUST survive to the text layer.
# Matched case-insensitively against the whitespace-normalized extract (pdfplumber re-spaces lines).
_EXPECTED: dict[str, tuple[str, str]] = {
    "01_police_report.pdf": ("Arizona", "Collision"),
    "02_er_note.pdf": ("Emergency Department", "Rivas"),
    "03_er_bill.pdf": ("Itemized Statement of Charges", "18,750.00"),
    "04_ortho_notes.pdf": ("Orthopedic", "Rivas"),
    "05_ortho_bill.pdf": ("Itemized Statement of Charges", "6,400.00"),
    "06_pt_notes.pdf": ("Physical Therapy", "Rivas"),
    "07_pt_bill.pdf": ("Itemized Statement of Charges", "3,900.00"),
    "08_er_bill_resend.pdf": ("Itemized Statement of Charges", "18,750.00"),
}

# Real surnames/entities that appear in samples/ court records — none may leak into the
# owned-synthetic scenario prose (THE RULE, belt-and-suspenders over review).
_FORBIDDEN_TOKENS = [
    "Wichert",
    "Henze",
    "Gutierrez",
    "Hernandez",
    "Gonzalez",
    "Drury",
    "Kroger",
    "Andrews",
    "Autoliv",
    "Zimprich",
    "Mohave",
    "Landstar",
    "Synthea",
]


def _text_of(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def test_every_pdf_has_readable_text_layer_with_expected_cue() -> None:
    scenario = _load_generator().build_scenario()
    assert set(scenario) == set(_EXPECTED), "generated set must match the documented upload set"
    for name, (cue, fact) in _EXPECTED.items():
        normalized = _norm(_text_of(scenario[name]))
        assert cue.lower() in normalized, f"{name}: classification cue {cue!r} missing from text"
        assert fact.lower() in normalized, f"{name}: key fact {fact!r} missing from text"


def test_regeneration_is_byte_identical() -> None:
    gen = _load_generator()
    first, second = gen.build_scenario(), gen.build_scenario()
    assert first.keys() == second.keys()
    for name in first:
        assert first[name] == second[name], f"{name}: regeneration is not byte-identical"


def test_source_is_owned_synthetic_no_forbidden_provenance() -> None:
    source_files = sorted(_SOURCE_DIR.glob("*.txt"))
    assert source_files, "scenario source is missing"
    corpus = "\n".join(f.read_text(encoding="utf-8") for f in source_files)
    for token in _FORBIDDEN_TOKENS:
        assert token not in corpus, f"forbidden real-record token {token!r} in scenario source"
    # The generator authors its own bytes: its imports must be stdlib + reportlab only — never
    # `app`, `samples`, or the test tree. (AST, so a docstring mentioning samples/ is not a hit.)
    tree = ast.parse(_GENERATE_PY.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    allowed = {"__future__", "io", "pathlib", "reportlab"}
    assert roots <= allowed, f"generator may import stdlib + reportlab only, got: {roots}"


def test_generated_pdfs_clear_the_ingest_text_floor_and_duplicate_dedups() -> None:
    scenario = _load_generator().build_scenario()
    floor = get_settings().text_density_floor
    for name, pdf_bytes in scenario.items():
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            per_page = [page.extract_text() or "" for page in pdf.pages]
        assert per_page, f"{name}: produced no pages"
        for page_no, page_text in enumerate(per_page, start=1):
            assert density_ok(page_text, floor), f"{name} p{page_no}: below ingest density floor"
    # The re-sent ER bill is byte-identical to the original → phase0 exact-match dedup fires.
    original, resend = scenario["03_er_bill.pdf"], scenario["08_er_bill_resend.pdf"]
    assert resend == original
    assert hashlib.sha256(resend).hexdigest() == hashlib.sha256(original).hexdigest()
