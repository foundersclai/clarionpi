#!/usr/bin/env python3
"""Drift gate between AGENTS.md / CONTRACTS.md and the actual repo tree.

Two checks, stdlib only (no third-party deps — this must run before the
backend venv is assumed to exist):

1. AGENTS.md must contain no unfilled `<placeholder>` markers. A leftover
   `<command>` or `<what lives here>` means the hub doc was never finished.
2. Every module path and contract doc listed in CONTRACTS.md's drift-matrix
   table must exist on disk. A row pointing at a deleted module or a renamed
   contract doc is a silent lie — this catches it at commit/CI time.

Exit 0 with "hub-check: OK (N modules)" on success; exit 1 with a specific
complaint otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"
CONTRACTS_MD = REPO_ROOT / "CONTRACTS.md"

# Matches placeholder tokens like <command>, <what lives here>, <VAR> — a
# literal `<` followed by non-`<`/`>` text and a closing `>`. Markdown link
# syntax `[text](path)` and HTML comments don't trip this.
PLACEHOLDER_RE = re.compile(r"<[^<>\n]+>")

# A markdown table row: | module path | contract doc | notes |
# We only care about the first two columns; "notes" is free text.
TABLE_ROW_RE = re.compile(r"^\|(?P<cells>.+)\|\s*$")


def check_agents_md_placeholders() -> list[str]:
    if not AGENTS_MD.exists():
        return [f"hub-check: FAIL — {AGENTS_MD} does not exist"]

    errors: list[str] = []
    text = AGENTS_MD.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in PLACEHOLDER_RE.finditer(line):
            errors.append(
                f"hub-check: FAIL — AGENTS.md:{lineno} has unfilled placeholder "
                f"{match.group(0)!r}"
            )
    return errors


def parse_contracts_table(text: str) -> list[tuple[str, str, str]]:
    """Return (module_path, contract_doc, notes) rows from the CONTRACTS.md table.

    Skips the header row, the `|---|---|---|` separator row, and any row whose
    first cell isn't a real path (e.g. leftover template text).
    """
    rows: list[tuple[str, str, str]] = []
    seen_header = False
    for line in text.splitlines():
        match = TABLE_ROW_RE.match(line.strip())
        if not match:
            continue
        cells = [c.strip() for c in match.group("cells").split("|")]
        if len(cells) < 2:
            continue
        first_cell = cells[0]

        if not seen_header:
            # The header row itself (e.g. "module path | contract doc | notes").
            seen_header = True
            continue
        if set(first_cell) <= {"-", ":"} and first_cell != "":
            # Separator row: ---|---|---
            continue
        if not first_cell:
            continue

        module_path = first_cell
        contract_doc = cells[1] if len(cells) > 1 else ""
        notes = cells[2] if len(cells) > 2 else ""
        rows.append((module_path, contract_doc, notes))
    return rows


def check_contracts_table() -> tuple[list[str], int]:
    if not CONTRACTS_MD.exists():
        return ([f"hub-check: FAIL — {CONTRACTS_MD} does not exist"], 0)

    errors: list[str] = []
    text = CONTRACTS_MD.read_text(encoding="utf-8")
    rows = parse_contracts_table(text)

    for module_path, contract_doc, _notes in rows:
        module_full = REPO_ROOT / module_path
        if not module_full.exists():
            errors.append(
                f"hub-check: FAIL — CONTRACTS.md lists module path "
                f"{module_path!r} which does not exist"
            )
        contract_full = REPO_ROOT / contract_doc
        if not contract_full.exists():
            errors.append(
                f"hub-check: FAIL — CONTRACTS.md lists contract doc "
                f"{contract_doc!r} which does not exist"
            )

    return (errors, len(rows))


def main() -> int:
    errors: list[str] = []

    errors.extend(check_agents_md_placeholders())
    contracts_errors, module_count = check_contracts_table()
    errors.extend(contracts_errors)

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"hub-check: OK ({module_count} modules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
