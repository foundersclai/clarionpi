# Marks backend/tests/scripts as a package so pytest's default (prepend) import mode gives
# it a stable dotted name. The scripts under test live in backend/scripts/ (NOT a package,
# by design — a later docs wave owns that tree), so test_s1_scorer.py loads them by file
# path via importlib rather than importing them as modules.
