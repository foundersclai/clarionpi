#!/usr/bin/env python3
"""S1 OCR bake-off blind scorer (spike protocol: backlog/pi/11_spike_briefs.md §2).

Pure scoring functions + a thin CLI. The scoring definitions here implement the S1
spike brief EXACTLY:

  * Page-text coverage  = token-level RECALL vs the gold transcript, after Unicode
    (NFKC) + casefold + whitespace normalization, with multiplicity (multiset recall).
    Thresholds: >= 0.98 on FC-1, >= 0.95 on FC-2.
  * Table fidelity      = position-aligned cell F1 >= 0.97, with dollar-amount cells
    matched EXACTLY (a wrong cent fails the cell — no tolerance).
  * Decision rule       = winner passes BOTH coverage thresholds at the lowest $/1K
    pages (integer cents); nobody passing => None.

Everything is deterministic and offline. No vendor identity is embedded in any score
(the brief's blind-scoring discipline lives in the CLI's I/O layout, not here).

Ownership: created for M1 Wave D spike S1. This is a spike tool, not app code — it lives
under backend/scripts/ (not a package) and is not imported by app.*.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# --- Thresholds from 11_spike_briefs §2 (callers apply them; exposed as constants) ---
FC1_THRESHOLD = 0.98
FC2_THRESHOLD = 0.95
TABLE_F1_THRESHOLD = 0.97

# A dollar-amount cell: optional leading '$', digits/commas, then exactly two decimals.
# Dollar cells are compared EXACT (whitespace-stripped only) — money discipline: a wrong
# cent fails the cell.
_DOLLAR_RE = re.compile(r"^\$?[\d,]+\.\d{2}$")


# --------------------------------------------------------------------------------------
# Token-level page-text coverage (recall)
# --------------------------------------------------------------------------------------
def normalize_tokens(text: str) -> list[str]:
    """Unicode NFKC + casefold, then whitespace-split into tokens.

    NFKC folds compatibility forms (e.g. the "fi" ligature, full-width digits) so OCR
    that emits a canonical variant is not penalised for a cosmetic codepoint difference.
    Casefold is the aggressive, locale-independent lowercasing. Whitespace-split collapses
    all runs of whitespace and drops leading/trailing whitespace.
    """
    return unicodedata.normalize("NFKC", text).casefold().split()


def page_text_coverage(gold: str, candidate: str) -> float:
    """Token-level recall of *candidate* against *gold*, counting multiplicity.

    Recall = (sum over tokens of min(gold_count, candidate_count)) / total gold tokens.
    Using multiset (Counter) intersection means a gold "the the" only counts as recalled
    twice if the candidate also has it twice. An empty gold transcript scores 1.0 (nothing
    to recover — vacuously complete), matching the brief's recall framing.
    """
    gold_tokens = Counter(normalize_tokens(gold))
    gold_total = sum(gold_tokens.values())
    if gold_total == 0:
        return 1.0
    candidate_tokens = Counter(normalize_tokens(candidate))
    matched = sum((gold_tokens & candidate_tokens).values())
    return matched / gold_total


# --------------------------------------------------------------------------------------
# Table cell-level F1
# --------------------------------------------------------------------------------------
def _collapse_spaces(text: str) -> str:
    """Whitespace-strip + collapse internal whitespace runs to single spaces."""
    return " ".join(text.split())


def _cell_matches(gold_cell: str, candidate_cell: str) -> bool:
    """True iff two cells are equal under the cell's matching rule.

    Dollar-amount cells (either side looks like a dollar amount) match EXACT after only
    whitespace-strip/collapse — a wrong cent fails. All other cells match after
    NFKC + casefold + whitespace-collapse (so "ER Visit" == "er  visit").
    """
    gold_norm = _collapse_spaces(gold_cell)
    cand_norm = _collapse_spaces(candidate_cell)
    if _DOLLAR_RE.match(gold_norm) or _DOLLAR_RE.match(cand_norm):
        # Money cell: exact string (post whitespace-collapse), no case/Unicode folding.
        return gold_norm == cand_norm
    fold = unicodedata.normalize("NFKC", gold_norm).casefold()
    cand_fold = unicodedata.normalize("NFKC", cand_norm).casefold()
    return fold == cand_fold


def _cell_at(grid: Sequence[Sequence[str]], r: int, c: int) -> str | None:
    """Return the cell at (r, c) if the ragged *grid* has one there, else None."""
    if r < 0 or r >= len(grid):
        return None
    row = grid[r]
    if c < 0 or c >= len(row):
        return None
    return row[c]


def _count_cells(grid: Sequence[Sequence[str]]) -> int:
    """Total number of populated cells across a ragged grid."""
    return sum(len(row) for row in grid)


def cell_f1(gold: list[list[str]], candidate: list[list[str]]) -> float:
    """Position-aligned cell F1 between two tables (rows x cells).

    A candidate cell at (r, c) is a true positive iff a gold cell exists at (r, c) and the
    two match under ``_cell_matches``. Precision = TP / candidate cells, recall = TP / gold
    cells, F1 = harmonic mean. Empty-vs-empty is 1.0; if exactly one side is empty the F1
    is 0.0 (a missing table or a spurious table is a total miss, not a free pass).
    """
    gold_cells = _count_cells(gold)
    cand_cells = _count_cells(candidate)
    if gold_cells == 0 and cand_cells == 0:
        return 1.0
    if gold_cells == 0 or cand_cells == 0:
        return 0.0

    true_positives = 0
    n_rows = min(len(gold), len(candidate))
    for r in range(n_rows):
        n_cols = min(len(gold[r]), len(candidate[r]))
        for c in range(n_cols):
            gold_cell = _cell_at(gold, r, c)
            cand_cell = _cell_at(candidate, r, c)
            if (
                gold_cell is not None
                and cand_cell is not None
                and _cell_matches(gold_cell, cand_cell)
            ):
                true_positives += 1

    precision = true_positives / cand_cells
    recall = true_positives / gold_cells
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# --------------------------------------------------------------------------------------
# Per-vendor rollup
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class VendorScore:
    """Frozen per-vendor rollup over one FC run.

    ``table_f1`` is the mean cell-F1 across the scored tables (0.0 if none scored, so an
    absent-tables run cannot masquerade as passing the table bar).
    """

    mean_page_coverage: float
    min_page_coverage: float
    table_f1: float
    pages_scored: int
    tables_scored: int


def score_vendor_run(
    pages: list[tuple[str, str]],
    tables: list[tuple[list[list[str]], list[list[str]]]],
) -> VendorScore:
    """Roll up per-page coverage and per-table F1 for one vendor's run.

    *pages* is a list of (gold, candidate) transcript pairs; *tables* is a list of (gold,
    candidate) cell grids. Mean/min coverage are taken over the pages; ``table_f1`` is the
    mean over the tables. An empty page list yields 1.0/1.0 coverage (nothing failed);
    an empty table list yields 0.0 table F1 (nothing demonstrated).
    """
    coverages = [page_text_coverage(gold, cand) for gold, cand in pages]
    if coverages:
        mean_cov = sum(coverages) / len(coverages)
        min_cov = min(coverages)
    else:
        mean_cov = 1.0
        min_cov = 1.0

    f1s = [cell_f1(gold, cand) for gold, cand in tables]
    table_f1 = sum(f1s) / len(f1s) if f1s else 0.0

    return VendorScore(
        mean_page_coverage=mean_cov,
        min_page_coverage=min_cov,
        table_f1=table_f1,
        pages_scored=len(pages),
        tables_scored=len(tables),
    )


# --------------------------------------------------------------------------------------
# Decision rule
# --------------------------------------------------------------------------------------
def decide(
    scores: Mapping[str, VendorScore],
    fc1_cov: Mapping[str, float],
    fc2_cov: Mapping[str, float],
    dollars_per_1k: Mapping[str, int],
) -> str | None:
    """Pick the winning vendor per the §2 decision rule, or None if nobody qualifies.

    A vendor *passes* iff its FC-1 coverage >= FC1_THRESHOLD AND its FC-2 coverage >=
    FC2_THRESHOLD. Among passers, the winner is the one with the lowest ``dollars_per_1k``
    (integer cents — money discipline, no float compare). Cost ties are broken
    **lexicographically by vendor name** here so the function is deterministic; the brief's
    real tiebreak is confidence calibration, which is qualitative and recorded in RESULTS.md
    rather than encoded in code.

    ``scores`` is accepted for signature completeness / auditability (it carries the table
    F1 and per-page min the reviewer inspects) but the coverage-threshold decision is driven
    by the explicit FC-1/FC-2 maps, which are the numbers the brief thresholds on.
    """
    passers = [
        vendor
        for vendor in scores
        if fc1_cov.get(vendor, 0.0) >= FC1_THRESHOLD and fc2_cov.get(vendor, 0.0) >= FC2_THRESHOLD
    ]
    if not passers:
        return None
    # Lowest integer-cents cost, then lexicographic name — fully deterministic.
    return min(passers, key=lambda vendor: (dollars_per_1k.get(vendor, sys.maxsize), vendor))


# --------------------------------------------------------------------------------------
# CLI (thin: I/O + formatting only; all scoring lives in the pure functions above)
# --------------------------------------------------------------------------------------
def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_csv_grid(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [list(row) for row in csv.reader(handle)]


def _discover_vendors(run_dir: Path) -> list[str]:
    """Vendor sub-directories under *run_dir*, excluding the gold/tables reserved dirs."""
    reserved = {"gold", "tables"}
    return sorted(
        child.name for child in run_dir.iterdir() if child.is_dir() and child.name not in reserved
    )


def _score_run_dir(run_dir: Path) -> dict[str, VendorScore]:
    """Score every vendor dir in *run_dir* against ``gold/`` transcripts and ``tables/``.

    Layout:
      <run_dir>/gold/<page_id>.txt        gold transcripts (the labeler's ground truth)
      <run_dir>/<vendor>/<page_id>.txt    each vendor's transcript for the same page
      <run_dir>/tables/<table_id>.csv     gold cell grids (optional)
      <run_dir>/<vendor>/<table_id>.csv   each vendor's cell grid (optional)
    """
    gold_dir = run_dir / "gold"
    if not gold_dir.is_dir():
        raise SystemExit(f"no gold/ directory under {run_dir}")

    gold_pages = {p.stem: _read_text(p) for p in sorted(gold_dir.glob("*.txt"))}
    gold_tables_dir = run_dir / "tables"
    gold_tables = (
        {p.stem: _read_csv_grid(p) for p in sorted(gold_tables_dir.glob("*.csv"))}
        if gold_tables_dir.is_dir()
        else {}
    )

    results: dict[str, VendorScore] = {}
    for vendor in _discover_vendors(run_dir):
        vendor_dir = run_dir / vendor
        pages: list[tuple[str, str]] = []
        for page_id, gold_text in gold_pages.items():
            candidate_path = vendor_dir / f"{page_id}.txt"
            candidate_text = _read_text(candidate_path) if candidate_path.exists() else ""
            pages.append((gold_text, candidate_text))

        tables: list[tuple[list[list[str]], list[list[str]]]] = []
        for table_id, gold_grid in gold_tables.items():
            candidate_path = vendor_dir / f"{table_id}.csv"
            candidate_grid = _read_csv_grid(candidate_path) if candidate_path.exists() else []
            tables.append((gold_grid, candidate_grid))

        results[vendor] = score_vendor_run(pages, tables)
    return results


def _format_markdown(results: Mapping[str, VendorScore]) -> str:
    """Render the per-vendor results table the brief wants pasted into RESULTS.md."""
    header = (
        "| vendor | mean page cov | min page cov | table F1 | "
        "pages | tables | FC-1 pass | table pass |"
    )
    divider = "|---|---|---|---|---|---|---|---|"
    lines = [header, divider]
    for vendor in sorted(results):
        score = results[vendor]
        fc1_pass = "yes" if score.mean_page_coverage >= FC1_THRESHOLD else "no"
        table_pass = "yes" if score.table_f1 >= TABLE_F1_THRESHOLD else "no"
        lines.append(
            f"| {vendor} | {score.mean_page_coverage:.4f} | {score.min_page_coverage:.4f} "
            f"| {score.table_f1:.4f} | {score.pages_scored} | {score.tables_scored} "
            f"| {fc1_pass} | {table_pass} |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Blind-score an S1 OCR bake-off run directory and print a per-vendor "
            "markdown results table. Cost and thresholds are the caller's concern; this "
            "CLI reports raw coverage/F1 so grading stays vendor-blind."
        )
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        type=Path,
        help="run directory containing gold/ + one sub-dir per (anonymised) vendor",
    )
    args = parser.parse_args(argv)

    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        raise SystemExit(f"run-dir does not exist: {run_dir}")

    results = _score_run_dir(run_dir)
    print(_format_markdown(results))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
