"""Deterministic unit tests for the S1 OCR bake-off scorer + FC fixture generator.

Import mechanism: ``backend/scripts/`` is intentionally NOT a package (a later docs wave
owns that tree; there is no ``__init__.py`` there and it is not on ``sys.path`` — the venv
only installs ``app*``). So we load both scripts by FILE PATH via ``importlib.util``,
resolving the path from ``__file__`` (this test lives at ``backend/tests/scripts/``, so the
scripts are ``../../scripts/<name>.py``). This is cwd-independent and needs no change to the
repo's pytest config (there is none — ``make test`` runs ``pytest`` with ``backend/`` as
rootdir and default prepend import mode).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def _load_script(name: str) -> ModuleType:
    """Load a module from backend/scripts/<name>.py by file path (not by import name).

    The module is registered in ``sys.modules`` under a spike-scoped name BEFORE
    ``exec_module`` — ``@dataclass``'s annotation resolution looks the defining module up
    there, so an unregistered file-loaded module raises ``AttributeError`` on the first
    frozen dataclass otherwise.
    """
    mod_name = f"_s1_spike_{name}"
    path = _SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


s1 = _load_script("s1_scorer")
gen = _load_script("generate_fc_fixtures")


# --------------------------------------------------------------------------------------
# normalize_tokens
# --------------------------------------------------------------------------------------
def test_normalize_nfkc_ligature_decomposes() -> None:
    # U+FB01 LATIN SMALL LIGATURE FI -> "fi" under NFKC, so "The <fi>le" -> ["the","file"].
    assert s1.normalize_tokens("The ﬁle") == ["the", "file"]


def test_normalize_casefolds_and_splits_whitespace() -> None:
    assert s1.normalize_tokens("  HELLO\tWorld\n WORLD ") == ["hello", "world", "world"]


def test_normalize_nfkc_fullwidth_digits() -> None:
    # Full-width "1" (U+FF11) folds to ASCII "1" under NFKC.
    assert s1.normalize_tokens("１ code") == ["1", "code"]


# --------------------------------------------------------------------------------------
# page_text_coverage
# --------------------------------------------------------------------------------------
def test_coverage_identical_is_one() -> None:
    assert s1.page_text_coverage("alpha beta gamma", "alpha beta gamma") == 1.0


def test_coverage_missing_half_is_point_five() -> None:
    # Gold has 4 tokens; candidate recovers 2 -> recall 0.5.
    assert s1.page_text_coverage("a b c d", "a b") == 0.5


def test_coverage_empty_gold_is_one() -> None:
    assert s1.page_text_coverage("", "anything at all") == 1.0
    assert s1.page_text_coverage("   ", "") == 1.0


def test_coverage_respects_multiplicity() -> None:
    # Gold "the the" needs the candidate to have "the" twice for full recall.
    assert s1.page_text_coverage("the the", "the") == 0.5
    assert s1.page_text_coverage("the the", "the the") == 1.0


def test_coverage_ignores_extra_candidate_tokens() -> None:
    # Recall (not precision): extra candidate tokens do not reduce the score.
    assert s1.page_text_coverage("a b", "a b c d e") == 1.0


# --------------------------------------------------------------------------------------
# cell_f1
# --------------------------------------------------------------------------------------
def _grid(*rows: list[str]) -> list[list[str]]:
    return [list(r) for r in rows]


def test_cell_f1_perfect_grid_is_one() -> None:
    grid = _grid(["Date", "CPT", "Billed"], ["2024-03-01", "99213", "$185.00"])
    assert s1.cell_f1(grid, grid) == 1.0


def test_cell_f1_wrong_cent_dollar_cell_fails_that_cell() -> None:
    gold = _grid(["Billed"], ["$100.01"])
    candidate = _grid(["Billed"], ["$100.10"])
    # 1 of 2 cells correct on both sides -> P=R=0.5 -> F1=0.5 (the dollar cell missed).
    assert s1.cell_f1(gold, candidate) == 0.5


def test_cell_f1_dollar_exact_match_passes() -> None:
    gold = _grid(["Billed"], ["$100.10"])
    candidate = _grid(["Billed"], ["$100.10"])
    assert s1.cell_f1(gold, candidate) == 1.0


def test_cell_f1_case_different_text_cell_still_matches() -> None:
    gold = _grid(["ER Visit"], ["Office Visit"])
    candidate = _grid(["er visit"], ["OFFICE  VISIT"])  # case + spacing differences
    assert s1.cell_f1(gold, candidate) == 1.0


def test_cell_f1_ragged_candidate_missing_row_hits_recall() -> None:
    gold = _grid(["a", "b"], ["c", "d"])  # 4 gold cells
    candidate = _grid(["a", "b"])  # 2 candidate cells, both correct
    # TP=2, precision=2/2=1.0, recall=2/4=0.5 -> F1 = 2*1*0.5/1.5 = 0.6667.
    assert abs(s1.cell_f1(gold, candidate) - (2 / 3)) < 1e-9


def test_cell_f1_empty_both_is_one_and_one_sided_is_zero() -> None:
    assert s1.cell_f1([], []) == 1.0
    assert s1.cell_f1(_grid(["a"]), []) == 0.0
    assert s1.cell_f1([], _grid(["a"])) == 0.0


# --------------------------------------------------------------------------------------
# score_vendor_run
# --------------------------------------------------------------------------------------
def test_score_vendor_run_rollup() -> None:
    pages = [("a b c d", "a b c d"), ("w x y z", "w x")]  # cov 1.0 and 0.5
    tables = [(_grid(["$1.00"]), _grid(["$1.00"]))]  # F1 1.0
    score = s1.score_vendor_run(pages, tables)
    assert score.mean_page_coverage == 0.75
    assert score.min_page_coverage == 0.5
    assert score.table_f1 == 1.0
    assert score.pages_scored == 2
    assert score.tables_scored == 1


def test_score_vendor_run_no_tables_is_zero_f1() -> None:
    score = s1.score_vendor_run([("a", "a")], [])
    assert score.table_f1 == 0.0
    assert score.tables_scored == 0


# --------------------------------------------------------------------------------------
# decide
# --------------------------------------------------------------------------------------
def _score(cov: float) -> object:
    return s1.VendorScore(
        mean_page_coverage=cov,
        min_page_coverage=cov,
        table_f1=1.0,
        pages_scored=1,
        tables_scored=1,
    )


def test_decide_cheapest_passer_wins() -> None:
    scores = {"aws": _score(0.99), "gcp": _score(0.99)}
    fc1 = {"aws": 0.99, "gcp": 0.99}
    fc2 = {"aws": 0.96, "gcp": 0.97}
    cost = {"aws": 800, "gcp": 500}  # cents/1k pages
    assert s1.decide(scores, fc1, fc2, cost) == "gcp"


def test_decide_none_when_nobody_passes() -> None:
    scores = {"aws": _score(0.9)}
    fc1 = {"aws": 0.90}  # below FC-1 threshold
    fc2 = {"aws": 0.96}
    cost = {"aws": 500}
    assert s1.decide(scores, fc1, fc2, cost) is None


def test_decide_excludes_fc1_pass_but_fc2_fail() -> None:
    scores = {"aws": _score(0.99), "azure": _score(0.99)}
    fc1 = {"aws": 0.99, "azure": 0.99}
    fc2 = {"aws": 0.94, "azure": 0.96}  # aws fails FC-2 (< 0.95)
    cost = {"aws": 100, "azure": 900}  # aws cheaper but excluded
    assert s1.decide(scores, fc1, fc2, cost) == "azure"


def test_decide_cost_tie_breaks_lexicographically() -> None:
    scores = {"zeta": _score(0.99), "alpha": _score(0.99)}
    fc1 = {"zeta": 0.99, "alpha": 0.99}
    fc2 = {"zeta": 0.96, "alpha": 0.96}
    cost = {"zeta": 500, "alpha": 500}  # tie -> lexicographically-first name
    assert s1.decide(scores, fc1, fc2, cost) == "alpha"


# --------------------------------------------------------------------------------------
# generator smoke + determinism
# --------------------------------------------------------------------------------------
def test_generator_smoke_and_gold_labels(tmp_path: Path) -> None:
    out = tmp_path / "run"
    manifest_path = gen.generate(out, fc1_pages=3, fcb_docs=2, seed=7)
    assert manifest_path.exists()

    root = out / "fc_v1_synthetic"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["corpus"] == "FC-v1"
    assert manifest["phi"] is False

    # FC-1: gold file count == generated page count.
    fc1_pdfs = sorted((root / "fc1_clean_emr").glob("fc1_p*.pdf"))
    fc1_golds = sorted((root / "fc1_clean_emr" / "gold").glob("fc1_p*.txt"))
    assert len(fc1_pdfs) == 3
    assert len(fc1_golds) == len(fc1_pdfs)
    # Gold carries the synthetic/no-PHI provenance banner.
    assert "no PHI" in fc1_golds[0].read_text(encoding="utf-8")

    # FC-B: each csv parses and every amount cell matches the exact-dollar regex.
    dollar_re = re.compile(r"^\$[\d,]+\.\d{2}$")
    for csv_path in sorted((root / "fcb_bills" / "tables").glob("fcb_b*.csv")):
        rows = [row.split(",") for row in csv_path.read_text(encoding="utf-8").splitlines()]
        assert rows, "csv should not be empty"
        # Last cell of each non-header, non-total data row is a dollar amount; the TOTAL
        # row's last cell is too. Rather than re-parse quoting, load via the scorer's CSV
        # reader (handles quoted commas in $1,420.00) and check the Billed column.
        grid = _load_billed_column(csv_path)
        assert grid, "expected at least a header + one line"
        for amount in grid:
            assert dollar_re.match(amount), f"non-dollar Billed cell: {amount!r}"


def _load_billed_column(csv_path: Path) -> list[str]:
    """Return the Billed-column values (excluding header) using stdlib csv for quoting."""
    import csv as _csv

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(_csv.reader(handle))
    header = rows[0]
    billed_idx = header.index("Billed")
    return [r[billed_idx] for r in rows[1:] if len(r) > billed_idx and r[billed_idx]]


def test_generator_is_deterministic(tmp_path: Path) -> None:
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    gen.generate(out_a, fc1_pages=3, fcb_docs=2, seed=99)
    gen.generate(out_b, fc1_pages=3, fcb_docs=2, seed=99)
    man_a = json.loads((out_a / "fc_v1_synthetic" / "MANIFEST.json").read_text())
    man_b = json.loads((out_b / "fc_v1_synthetic" / "MANIFEST.json").read_text())
    # Same seed + sizes => identical per-file sha256 hashes (byte-reproducible PDFs).
    assert man_a["files"] == man_b["files"]


# --------------------------------------------------------------------------------------
# scorer CLI smoke
# --------------------------------------------------------------------------------------
def test_scorer_cli_smoke(tmp_path: Path, capsys: object) -> None:
    run = tmp_path / "run"
    (run / "gold").mkdir(parents=True)
    (run / "vendor_perfect").mkdir()
    (run / "vendor_lossy").mkdir()
    (run / "gold" / "p01.txt").write_text("alpha beta gamma delta", encoding="utf-8")
    (run / "vendor_perfect" / "p01.txt").write_text("alpha beta gamma delta", encoding="utf-8")
    (run / "vendor_lossy" / "p01.txt").write_text("alpha beta", encoding="utf-8")

    exit_code = s1.main(["--run-dir", str(run)])
    assert exit_code == 0

    captured = _read_capsys(capsys)
    assert "vendor_perfect" in captured
    assert "vendor_lossy" in captured
    assert "| vendor |" in captured  # markdown header row present


def _read_capsys(capsys: object) -> str:
    """Typed shim around pytest's capsys fixture (its type isn't exported for mypy)."""
    readouterr: Callable[[], object] = capsys.readouterr  # type: ignore[attr-defined]
    return str(readouterr().out)  # type: ignore[attr-defined]
