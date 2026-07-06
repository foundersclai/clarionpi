"""Structural invariants over the ORM metadata — tenancy, money, and audit shape.

These assert design invariants directly against ``Base.metadata`` so a future column that
violates them fails the build (04 §2 + AGENTS boundaries).
"""

from __future__ import annotations

import sqlalchemy as sa

from app.models.orm import Base

# The one legitimate non-integer numeric column: an OCR confidence score, not currency.
_ALLOWED_FLOAT_COLUMNS = {("document_pages", "ocr_confidence")}


def test_every_table_except_firms_is_firm_scoped() -> None:
    for table in Base.metadata.sorted_tables:
        if table.name == "firms":
            assert "firm_id" not in table.columns, "firms is the tenant root; it has no firm_id"
            continue
        assert "firm_id" in table.columns, f"{table.name} is missing firm_id"
        col = table.columns["firm_id"]
        assert col.nullable is False, f"{table.name}.firm_id must be non-null"
        assert col.index is True, f"{table.name}.firm_id must be indexed"


def test_no_float_or_numeric_columns_except_ocr_confidence() -> None:
    offenders: list[str] = []
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, sa.Float | sa.Numeric):
                if (table.name, col.name) in _ALLOWED_FLOAT_COLUMNS:
                    continue
                offenders.append(f"{table.name}.{col.name} ({col.type})")
    assert not offenders, f"Float/Numeric columns are banned (money is integer cents): {offenders}"


def test_every_cents_column_is_integer() -> None:
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if col.name.endswith("_cents"):
                assert isinstance(col.type, sa.Integer), (
                    f"{table.name}.{col.name} is a money column and must be Integer cents, "
                    f"got {col.type}"
                )


def test_at_least_one_cents_column_exists() -> None:
    # Guards the test above from vacuously passing if the naming convention ever drifts.
    cents_cols = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.sorted_tables
        for c in t.columns
        if c.name.endswith("_cents")
    ]
    assert cents_cols, "expected at least one *_cents money column in the model"


def test_audit_events_is_append_only_no_updated_at() -> None:
    audit = Base.metadata.tables["audit_events"]
    assert "created_at" in audit.columns
    assert "updated_at" not in audit.columns, "audit_events is append-only by design"
